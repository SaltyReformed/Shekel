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
from app.utils.dates import display_today
from app.utils.money import round_money
from tests.test_routes._statement_forms import (
    RuleFormReader,
    match_item,
    one_pass,
    record_line,
    rule_form_controls,
    rule_item,
)
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    an_account_whose_books_hide_a_line,
    a_rule,
    an_envelope,
    an_unexplained_outflow,
    a_merchant,
    a_purchase,
    a_reviewed_token,
    a_transaction,
    an_import,
)


def _review_url(account_id):
    """Return the review page's URL for *account_id*."""
    return f"/accounts/{account_id}/statements/review"


def _merchant_rule_model():
    """Return the stored-rule model, imported where it is used.

    The module-level imports here are the route's own subjects; this one is a
    storage check inside two cases, and the file's convention is to reach for
    it at the point of use.

    Returns:
        The :class:`~app.models.merchant_rule.MerchantRule` class.
    """
    from app.models.merchant_rule import (  # pylint: disable=import-outside-toplevel
        MerchantRule,
    )
    return MerchantRule


def _merchants_url(account_id):
    """Return the QUEUE's merchant-rule POST URL for *account_id*.

    The register has a door of its own (plan step ``bank_import:X-gf-2``): the
    two answer with different screens, so the URL is what says which surface a
    submission came from.
    """
    return f"/accounts/{account_id}/statements/review/merchants"


def _apply_form_controls(page):
    """Return what a browser would submit for the APPLY form, untouched.

    The money form's own version of :func:`~tests.test_routes
    ._statement_forms.rule_form_controls`, and it
    exists for the sharper case: pressing Apply having touched nothing must
    write nothing, and a hand-written ``destination=""`` grades the reader's
    idea of the default rather than the template's.
    """
    reader = RuleFormReader(
        prefixes=(
            "destination-", "envelope_name-", "category_id-", "apply",
            # Ruling **bank_import:R-GW**'s tick.  It is in this list for the SAME reason
            # the destination select is: an untouched Apply must write no
            # deposit either, and the only honest way to check that is to read
            # what the template rendered.  An unticked checkbox is dropped by
            # the reader above, so its absence here IS the assertion.
            "record_income-",
        ),
    )
    reader.feed(page)
    return reader.controls


class _TickedMatchReader(HTMLParser):
    """Collect the APPLY form's match controls, keeping REPEATED names.

    :class:`~tests.test_routes._statement_forms.RuleFormReader`'s
    twin for the one field that is submitted more
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
        could explain, which is the state the merchant rule offers to RECORD
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

        # **The SENTENCE is the pass's own since plan step
        # ``bank_import:X-ge-1``**, where it was one line hardcoded in the
        # template.  That mattered because every tier reports its own refusals
        # now -- a candidate refused for want of the merchant, one refused for
        # the day window, an exact figure the window refused -- and ruling
        # R-GH's automatic door withholds on the SAME derivation, so a screen
        # composing its own wording would describe one limit two ways.
        # ON the line in the create card, where the WRONG act is cheapest...
        # The needle lost its DIRECTION at plan step ``bank_import:X-gj-2b-3``
        # (ruling **bank_import:R-II**): the same pipeline now files merchant
        # credits, and *as new spending* was false of those.  Re-pointed rather
        # than dropped, for the reason the negative assertion in
        # ``test_a_SEARCH_GAP_is_reported_as_the_rule_s_and_printed_ONCE``
        # states about a needle that no longer exists.
        assert "Before recording this, match it against rows you already hold" in said
        assert "would not choose between the candidates for you" in said
        # ...and NOT as a bare number in the bounds panel any more.
        assert "line(s) have a row of yours" not in said
        assert b'data-proposal-class=' not in body

        # ...and on the line in the hand-build list, where the RIGHT act is --
        # which is a surface of its own since plan step
        # ``bank_import:X-gf-3b`` (ruling **bank_import:R-HC**).  **The
        # assertion FOLLOWED the render rather than being dropped**: it is the
        # half of this case that proves one derivation reaches both places the
        # owner can act, and a case that stopped checking the second half
        # because the second half moved would be the shape
        # ``docs/testing-standards.md`` calls a test that has quietly stopped
        # testing what it names.
        workbench = " ".join(auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements/match",
        ).data.decode().split())
        assert "One of your own rows is close enough to this" in workbench


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
            data=match_item(lines=[line], transactions=[txn]),
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
            data=match_item(lines=[line], transactions=[txn]),
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
            data=match_item(lines=[line], transactions=[salary, allowance]),
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
        """The unit of work is the request, so "nothing was changed" is true.

        **The refusal it uses is the STALE SCREEN**, and that changed at plan
        step ``bank_import:X-gj-1b``.  This posted the payroll gap with no
        consent at all until then; the card renders that figure as a hidden
        field now, so a browser always submits one and "the owner ticked
        nothing" is no longer a state this surface can be in.  What IS
        reachable is a page that stated a difference and rows that have moved
        under it -- the same N-336 shape, one press later -- so that is what
        this refuses on.
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
            data=match_item(
                lines=[line], transactions=[salary, allowance],
                residual="1006.00",
            ),
        )

        assert b"reviewed against a difference of +1,006.00" in response.data
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

        data = one_pass(
            *(
                match_item(index=index, lines=[line], transactions=[row])
                for index, (line, row) in enumerate(pairs)
            ),
            record_line(swipe, destination=envelope.id),
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

        data = match_item(index=0, lines=[pairs[0][0]], transactions=[pairs[0][1]])
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
        """The ruled failure rule, on the screen the owner is looking at.

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

        # **A STALE figure since plan step ``bank_import:X-gj-1b``.**  The
        # card states its difference as a hidden field now, so the refusal
        # this pass needs is a page whose rows moved under it rather than an
        # owner who ticked nothing -- the same N-239 group, refused one press
        # later.  The point of the case is unchanged: one item refuses and the
        # other still lands.
        data = one_pass(
            match_item(
                index=0, lines=[bad_line], transactions=bad_rows,
                residual="1006.00",
            ),
            match_item(index=1, lines=[pairs[0][0]], transactions=[pairs[0][1]]),
        )

        response = auth_client.post(
            _review_url(seed_user["account"].id), data=data,
        )

        assert response.status_code == 200
        assert b"1 applied, 1 refused" in response.data
        assert b"reviewed against a difference of" in response.data
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
            data=record_line(
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
            data=one_pass(
                match_item(lines=[line], transactions=[row]),
                record_line(swipe, destination=envelope.id),
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




class TestTheDepositArmOnTheWire:
    """Ruling **bank_import:R-GW** end to end: what the template renders, the door takes.

    The service suite grades the door and the review set; this closes the loop
    the project's own lesson names -- *a form submits every control it renders,
    and a hand-picked payload shipped a primary arm that was DEAD in a
    browser*.  A checkbox name and a Marshmallow field name have no
    compile-time relationship at all.
    """

    @staticmethod
    def _a_deposit(seed_user, amount="0.15", posted_on=None):
        """Record one unexplained line of money coming IN.

        Args:
            seed_user: The seeded user bundle.
            amount: Signed, POSITIVE into the account.
            posted_on: The day the bank credited it.

        Returns:
            The staged line.
        """
        return a_bank_line(
            seed_user, an_import(seed_user), amount=amount,
            posted_on=posted_on or seed_user["bootstrap_period"].start_date,
            description="DIVIDEND EARNED (Dividend Earned)",
            merchant="Dividend Earned",
        )

    def test_the_page_renders_a_tick_for_an_unexplained_deposit(
        self, auth_client, db, seed_user,
    ):
        """The control has to EXIST before anything else here means anything."""
        line = self._a_deposit(seed_user)
        db.session.commit()

        page = auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode()

        assert f'name="record_income-{line.id}"' in page
        assert "Nothing of yours accounts for these" in page

    def test_an_UNTOUCHED_apply_records_no_deposit(
        self, auth_client, db, seed_user,
    ):
        """R-FP over the new arm, read off the template rather than typed.

        The tick is not in the submitted payload because a browser drops an
        unticked checkbox -- so its ABSENCE from ``submitted`` is the
        assertion, and the row count is what proves the absence mattered.
        """
        self._a_deposit(seed_user)
        db.session.commit()

        page = auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode()
        submitted = _apply_form_controls(page)
        response = auth_client.post(
            _review_url(seed_user["account"].id), data=submitted,
        )

        assert not [
            name for name in submitted if name.startswith("record_income-")
        ]
        assert response.status_code == 200
        assert db.session.query(StatementMatch).count() == 0
        assert db.session.query(Transaction).count() == 0

    def test_a_TICKED_deposit_is_recorded_and_the_receipt_says_so(
        self, auth_client, db, seed_user,
    ):
        """The scraped form plus the one tick a browser would add."""
        line = self._a_deposit(seed_user, amount="0.15")
        db.session.commit()

        page = auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode()
        submitted = _apply_form_controls(page)
        # What ticking the box in a browser does, and nothing else.
        submitted[f"record_income-{line.id}"] = "record"
        response = auth_client.post(
            _review_url(seed_user["account"].id), data=submitted,
        )

        assert response.status_code == 200
        row = db.session.query(Transaction).one()
        assert row.estimated_amount == Decimal("0.15")
        assert row.category_id is None
        body = response.data.decode()
        assert "recorded as money that arrived" in body
        assert "Nothing moved." not in body

    def test_a_recorded_deposit_LEAVES_the_card(
        self, auth_client, db, seed_user,
    ):
        """The answer IS the screen, so the line must be gone from it."""
        line = self._a_deposit(seed_user)
        db.session.commit()

        page = auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode()
        submitted = _apply_form_controls(page)
        submitted[f"record_income-{line.id}"] = "record"
        body = auth_client.post(
            _review_url(seed_user["account"].id), data=submitted,
        ).data.decode()

        assert f'name="record_income-{line.id}"' not in body

    def test_a_line_PAST_the_calendar_renders_a_SENTENCE_and_no_tick(
        self, auth_client, db, seed_user,
    ):
        """A control whose submission can never succeed is not rendered.

        The door refuses a day no saved period covers, so the screen says so
        instead -- the *chooser whose submission always fails* shape this
        package has closed five times.
        """
        beyond = seed_user["bootstrap_period"].end_date + timedelta(days=400)
        line = self._a_deposit(seed_user, posted_on=beyond)
        db.session.commit()

        page = auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode()

        assert f'name="record_income-{line.id}"' not in page
        assert f"No pay period covers {beyond}" in page

    def test_the_ROW_IT_CREATES_renders_on_the_screens_that_show_rows(
        self, auth_client, db, seed_user,
    ):
        """A NULL-category transaction is a shape this app has never held.

        Measured 2026-08-27 on the developer's own dev database: **0 of 1,044**
        transactions carry a NULL ``category_id``, and the only other writer of
        one is a matched group's residual (**R-FN**), which production has
        never run either.  So this door is about to make an unrendered row
        shape ORDINARY, and "does the grid survive it" is this step's question
        rather than the grid's.

        A FIRING control: it fails on a ``500`` from any surface that assumes a
        row has a category, which is the whole class of defect it exists for.
        """
        line = self._a_deposit(seed_user)
        db.session.commit()

        page = auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode()
        submitted = _apply_form_controls(page)
        submitted[f"record_income-{line.id}"] = "record"
        auth_client.post(_review_url(seed_user["account"].id), data=submitted)

        assert db.session.query(Transaction).one().category_id is None
        for url in (
            "/grid",
            f"/accounts/{seed_user['account'].id}/details",
            _review_url(seed_user["account"].id),
        ):
            assert auth_client.get(url).status_code == 200, url

    def test_the_SAFEGUARD_renders_where_the_books_already_hold_income(
        self, auth_client, db, seed_user,
    ):
        """The sentence that stands between the owner and a duplicate.

        Measured on the developer's own data: three payroll deposits worth
        `$7,838.92` render no near-miss sentence, because their app rows sit
        outside every matcher tier's bound -- so this is the only per-line
        signal they get, and a route case is what says it reaches the page.
        """
        a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True,
        )
        line = self._a_deposit(seed_user, amount="2600.00")
        db.session.commit()

        page = auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode()

        # **The word *income* left this sentence at plan step
        # ``bank_import:X-gj-2b-3``**: the rows it counts are every ARRIVING
        # row the books hold that no line explains, which since ruling
        # **bank_import:R-II** includes a stored REFUND -- a negative purchase,
        # whose cash is positive.  The service-composed twin of this sentence
        # was corrected at ``bank_import:X-gj-2b`` and the two templates that
        # spell it were not, which is what left the screen saying *income*
        # about a set that holds purchases.
        assert (
            "This pay period already holds 1 row(s) totalling $2,473.38 "
            "your records say arrived and no bank line explains" in page
        )
        assert "Salary" in page
        assert f'name="record_income-{line.id}"' in page

    def test_the_safeguard_is_SILENT_on_the_lines_the_step_exists_for(
        self, auth_client, db, seed_user,
    ):
        """A `$0.15` dividend cannot be a `$2,473.38` salary row.

        The other half of the same control: an alarm on every row is the one
        that teaches an owner to stop reading alarms.
        """
        a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True,
        )
        self._a_deposit(seed_user, amount="0.15")
        db.session.commit()

        page = auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode()

        assert "This pay period already holds" not in page

    def test_a_line_the_bank_dates_in_the_FUTURE_renders_no_tick(
        self, auth_client, db, seed_user,
    ):
        """A control whose submission can never succeed is not rendered.

        Pay periods project about two years forward, so a future-dated line
        resolves a pay period and used to render a tick -- which the settle
        verb then refused (**R-EJ**) only AFTER the door had written and
        settled the row.  Found by adversarial financial review 2026-08-27.
        """
        ahead = display_today() + timedelta(days=3)
        line = self._a_deposit(seed_user, posted_on=ahead)
        db.session.commit()

        page = auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode()

        assert f'name="record_income-{line.id}"' not in page
        assert "has not happened yet" in page

    def test_a_FUTURE_line_forced_through_the_door_writes_NOTHING(
        self, auth_client, db, seed_user,
    ):
        """The door's half, which a stale page or a crafted body reaches.

        The refusal has to fire BEFORE the row exists.  It fired after until
        2026-08-27, so a refused act left a settled `$0.15` income row for the
        batch's SAVEPOINT to take back -- a dependency this package declines,
        and one a caller outside the batch does not have at all.
        """
        ahead = display_today() + timedelta(days=3)
        line = self._a_deposit(seed_user, posted_on=ahead)
        db.session.commit()

        response = auth_client.post(
            _review_url(seed_user["account"].id),
            data={f"record_income-{line.id}": "record"},
        )

        assert response.status_code == 200
        assert "has not happened yet" in response.data.decode()
        assert db.session.query(Transaction).count() == 0
        assert db.session.query(StatementMatch).count() == 0

    def test_ANOTHER_owners_line_is_refused(
        self, auth_client, db, seed_user, second_user,
    ):
        """A firing control against an IDOR on a door that MOVES MONEY."""
        theirs = a_bank_line(
            second_user, an_import(second_user), amount="500.00",
        )
        db.session.commit()

        response = auth_client.post(
            _review_url(seed_user["account"].id),
            data={f"record_income-{theirs.id}": "record"},
        )

        assert response.status_code == 200
        assert db.session.query(Transaction).count() == 0
        assert db.session.query(StatementMatch).count() == 0


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
        assert b"Nothing of yours accounts for these" in response.data
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
            data=record_line(line, destination=""),
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
            data=record_line(line, destination=envelope.id),
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
            data=record_line(
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
        """The ruled failure rule, on the slip the FORM ITSELF produces.

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
            data=one_pass(
                match_item(lines=[good_line], transactions=[good_row]),
                record_line(line, destination="new", name="Lowe's"),
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
            data=record_line(
                line, destination="new", name="Payroll",
                category_id=seed_user["categories"]["Groceries"].id,
            ),
        )

        assert response.status_code == 200
        # The refusal's WORDING moved at plan step ``bank_import:X-gj-2b-2``:
        # it used to say only money LEAVING may become a purchase, and an
        # inflow whose merchant carries a spending answer now may (a refund).
        # What is refused here is a deposit NO rule claims, which is the case
        # this test stages -- so the subject is unchanged and only the sentence
        # is.
        assert b"not a refund" in response.data
        db.session.expire_all()
        assert db.session.query(Transaction).filter(
            Transaction.name == "Payroll",
        ).count() == 0
        assert db.session.query(StatementMatch).count() == 0


class TestTheStandingRuleSection:
    """Where your merchants go: the control, its door, and what it may not do.

    Plan step **bank_import:X-f6a-3d**.  The route's own subjects: ownership,
    the form payload, and -- the one that matters most -- that a stated rule
    reaches the SCREEN as a suggestion and never as a selected control.
    """

    def test_the_page_offers_a_rule_row_per_merchant(
        self, auth_client, db, seed_user,
    ):
        """The card is what makes the whole leaf reachable.

        Without it the answers exist only in the database and the 91 leftover
        lines still ask 91 questions -- which is the same defect the hand-build
        form was added to fix two leaves earlier.
        """
        an_envelope(seed_user)
        an_unexplained_outflow(seed_user)
        db.session.commit()

        response = auth_client.get(_review_url(seed_user["account"].id))

        assert response.status_code == 200
        assert b"Merchants you have not answered for" in response.data
        assert b'name="rule_merchant-0"' in response.data
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
        an_envelope(seed_user)
        an_unexplained_outflow(seed_user)
        db.session.commit()

        response = auth_client.get(_review_url(seed_user["account"].id))
        body = response.data.decode()

        marker = body.index('name="rule-0"')
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
        envelope = an_envelope(seed_user)
        an_unexplained_outflow(seed_user)
        db.session.commit()

        response = auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=rule_item(
                0, a_merchant(seed_user, "Amazon").id,
                answer=f"t:{envelope.template_id}",
            ),
        )

        assert response.status_code == 200
        assert b"Amazon goes in Groceries." in response.data
        assert b"changed no money" in response.data

    def test_a_stated_rule_SUGGESTS_and_does_not_select(
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

        So the rule is rendered BESIDE the control and the control still
        opens on the do-nothing arm.  Delete that separation and this fails.
        """
        envelope = an_envelope(seed_user)
        line = an_unexplained_outflow(seed_user)
        db.session.commit()
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=rule_item(
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

    def test_pressing_APPLY_with_a_rule_and_no_tick_records_nothing(
        self, auth_client, db, seed_user,
    ):
        """The suggestion is inert until the owner acts on it.

        The other half of the test above, and the one that grades the WIRE
        rather than the markup: a browser submitting the rendered defaults of
        every control -- which is what pressing Apply without touching anything
        does -- must write no purchase.
        """
        envelope = an_envelope(seed_user)
        line = an_unexplained_outflow(seed_user)
        db.session.commit()
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=rule_item(
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
        envelope = an_envelope(seed_user)
        line = an_unexplained_outflow(seed_user)
        db.session.commit()
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=rule_item(
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
            data=record_line(line, destination=swept),
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
        an_envelope(seed_user)
        an_unexplained_outflow(seed_user, merchant="Capital One Credit Card")
        db.session.commit()

        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=rule_item(
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
        assert "Your records may already hold these" in body
        # THE CONTROLS THEMSELVES, gone: no destination select, no envelope
        # name box and no category select for this line, so a browser has
        # nothing to submit for it.  **The APPLY form's controls, not the
        # page's text**: "-- a new envelope --" still appears on the page as an
        # ANSWER the rule section offers, which is a different control on a
        # different form posting to a door that moves no money.
        #
        # **EMPTY, where this expected ``{"apply": "hand"}`` until plan step
        # ``bank_import:X-gf-3b``.**  That entry was never this case's subject:
        # it was the hand-build form's hidden index, which shared the page and
        # therefore the scrape.  With the form on a surface of its own (ruling
        # **bank_import:R-HC**) the money form for a barred line submits
        # NOTHING AT ALL, which is the stronger statement of exactly what this
        # case is about -- ruling **R-GJ**'s refusal being structural rather
        # than a control the owner is trusted not to touch.
        assert _apply_form_controls(response.data.decode()) == {}
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
        an_envelope(seed_user)
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
        envelope = an_envelope(seed_user)
        an_unexplained_outflow(seed_user, merchant="Alpha")
        an_unexplained_outflow(seed_user, merchant="Beta", sequence=1)
        db.session.commit()
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=rule_item(
                0, a_merchant(seed_user, "Alpha").id,
                answer=f"t:{envelope.template_id}",
            ),
        )

        response = auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=one_pass(
                rule_item(0, a_merchant(seed_user, "Alpha").id, answer=f"t:{envelope.template_id}"),
                rule_item(1, a_merchant(seed_user, "Beta").id, answer="never"),
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
        an_envelope(seed_user)
        an_unexplained_outflow(seed_user, merchant="Lowe's")
        db.session.commit()
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=rule_item(0, a_merchant(seed_user, "Lowe's").id, answer="new", name="Yard & Garden",
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

        ``fk_merchant_rules_template_account`` makes it unwritable; this
        is what makes the refusal a sentence rather than a 500 with a logged
        traceback.
        """
        an_envelope(seed_user)
        an_unexplained_outflow(seed_user)
        foreign = a_transaction(
            seed_second_user, name="Theirs", is_envelope=True,
        )
        db.session.commit()

        response = auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=rule_item(
                0, a_merchant(seed_user, "Amazon").id,
                answer=f"t:{foreign.template_id}",
            ),
        )

        assert response.status_code == 200
        assert b"no recurring envelope on this account" in response.data
        from app.models.merchant_rule import (  # pylint: disable=import-outside-toplevel
            MerchantRule,
        )
        assert db.session.query(MerchantRule).count() == 0

    def test_a_merchant_this_ACCOUNT_never_saw_is_refused_on_screen(
        self, auth_client, db, seed_user,
    ):
        """The scope check, reaching the owner as a designed 400.

        The section renders exactly the merchants this account's recorded lines
        name, so a statement about another is a crafted request -- and the
        table would otherwise take a rule for any string at all.
        """
        an_envelope(seed_user)
        an_unexplained_outflow(seed_user)
        db.session.commit()

        # An id no merchant on this account carries.  It used to be a STRING
        # this account's lines had never named; since plan step
        # ``bank_import:X-gd-1`` the hidden input carries a row, so what a
        # crafted body can name is another account's merchant or a number that
        # is nobody's -- and the sentence is what both get instead of an
        # ``IntegrityError`` reaching the owner as "Something went wrong".
        response = auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=rule_item(0, 9_999_999, answer="never"),
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
        an_envelope(seed_user)
        an_unexplained_outflow(seed_user)
        theirs = a_merchant(
            seed_second_user, "Theirs Alone",
            account=seed_second_user["account"],
        )
        db.session.commit()

        real = auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=rule_item(0, theirs.id, answer="never"),
        )
        nobodys = auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=rule_item(0, 9_999_999, answer="never"),
        )

        assert real.status_code == nobodys.status_code == 400
        assert real.data == nobodys.data
        from app.models.merchant_rule import (  # pylint: disable=import-outside-toplevel
            MerchantRule,
        )
        assert db.session.query(MerchantRule).count() == 0

    def test_a_CRAFTED_answer_is_refused_rather_than_a_500(
        self, auth_client, db, seed_user,
    ):
        """The field reads one of four things and nothing else.

        ``t:007`` names no template here for the same reason ``007`` names no
        envelope one card down: a second, laxer reading of a row id on a screen
        that decides where money is filed is what plan step X-ae removed.
        """
        an_envelope(seed_user)
        an_unexplained_outflow(seed_user)
        db.session.commit()

        response = auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=rule_item(
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
            data=rule_item(
                0, a_merchant(seed_user, "Amazon").id,
                answer="never",
            ),
        )

        assert response.status_code == 404

    def test_a_NEW_ENVELOPE_rule_prefills_the_name_and_the_CATEGORY(
        self, auth_client, db, seed_user,
    ):
        """Decided on the SERVER, so no-script and the sweep agree.

        A first version set these two from JavaScript when the sweep ran, which
        left the no-script path filing the rule's line under the MERCHANT's
        name instead of the one the owner stated -- one rule about what a
        created envelope is called, in two places, disagreeing on the path that
        has no scripting at all.
        """
        category = seed_user["categories"]["Groceries"]
        an_envelope(seed_user)
        an_unexplained_outflow(seed_user, merchant="Lowe's")
        db.session.commit()
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=rule_item(
                0, a_merchant(seed_user, "Lowe's").id, answer="new", name="Yard & Garden",
                category_id=category.id,
            ),
        )

        body = auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode()

        # Pinned to the CREATE FORM's own controls.  Both values are emitted
        # by the rule section too, so a bare substring passed with either
        # site broken; adversarial test-quality review 2026-08-19 measured
        # that each was individually ungraded.
        line_id = db.session.query(BankStatementLine).one().id
        marker = body.index(f'name="envelope_name-{line_id}"')
        assert 'value="Yard &amp; Garden"' in body[marker:marker + 200]
        marker = body.index(f'name="category_id-{line_id}"')
        control = body[marker:body.index("</select>", marker)]
        assert f'<option value="{category.id}" selected>' in control
        assert control.count("selected") == 1

    def test_answering_UNSET_for_a_merchant_that_HAS_a_rule_changes_nothing(
        self, auth_client, db, seed_user,
    ):
        """A rule is never un-stated, including by a stale page (**R-GS**).

        The screen renders *I have not said* only where there is no rule, so a
        browser cannot send this -- but a page rendered before the rule existed
        can, and a crafted body always can.  It has to be a NO-OP rather than a
        delete, because the delete is the door ruling R-GS removed and this is
        the only remaining way to reach it.
        """
        from app.models.merchant_rule import (  # pylint: disable=import-outside-toplevel
            MerchantRule,
        )

        envelope = an_envelope(seed_user)
        an_unexplained_outflow(seed_user)
        db.session.commit()
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=rule_item(
                0, a_merchant(seed_user, "Amazon").id,
                answer=f"t:{envelope.template_id}",
            ),
        )

        response = auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=rule_item(
                0, a_merchant(seed_user, "Amazon").id,
                answer="unset",
            ),
        )

        assert response.status_code == 200
        assert db.session.query(MerchantRule).one().template_id == (
            envelope.template_id
        )

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

        So this one reads the rendered page, submits every control the QUEUE's
        rule form actually contains at the value it actually carries, and
        asserts the round trip is a NO-OP.  Both merchants render *I have not
        said*, which the route drops before the door, so nothing is recorded
        and nothing is refused.

        **The ANSWERED half of this moved to the register with the rows it is
        about** (plan step ``bank_import:X-gf-2``): a merchant with an answer
        has no row on this screen at all, so the case that grades a stored
        answer round-tripping is
        ``test_statement_register.TestTheRestatePost``'s, against the form that
        renders one.
        """
        an_envelope(seed_user)
        an_unexplained_outflow(seed_user)
        an_unexplained_outflow(seed_user, merchant="Walmart", sequence=1)
        db.session.commit()

        page = auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode()
        submitted = rule_form_controls(page)
        response = auth_client.post(
            _merchants_url(seed_user["account"].id), data=submitted,
        )

        # The form really did carry both merchants and all four fields each.
        assert sorted(
            key for key in submitted if key.startswith("rule_merchant-")
        ) == ["rule_merchant-0", "rule_merchant-1"]
        assert all(
            submitted[f"rule-{index}"] == "unset" for index in (0, 1)
        )
        assert response.status_code == 200
        assert b"Nothing changed" in response.data
        assert b"were not recorded" not in response.data
        assert db.session.query(_merchant_rule_model()).count() == 0

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
        an_envelope(seed_user)
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

    def test_a_rule_that_cannot_REACH_this_line_says_why_and_ticks_nothing(
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
        an_unexplained_outflow(seed_user)
        db.session.commit()
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=rule_item(
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

    def test_an_UNANSWERED_merchant_opens_on_I_have_not_said(
        self, auth_client, db, seed_user,
    ):
        """What a browser submits for a merchant with no rule.

        The half of ruling **R-GS** that has to keep working: *I have not said*
        is still the control's opening state where nothing has been said, and
        it still means *state nothing*.  Read off the page rather than
        asserted from the template, because what is under test is the value a
        browser would post.
        """
        an_envelope(seed_user)
        an_unexplained_outflow(seed_user)
        db.session.commit()

        page = auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode()

        assert rule_form_controls(page)["rule-0"] == "unset"

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
        an_envelope(seed_user, name="Open Envelope")
        an_unexplained_outflow(seed_user, merchant="Alpha")
        an_unexplained_outflow(seed_user, merchant="Beta", sequence=1)
        db.session.commit()
        template_id = db.session.query(Transaction).filter(
            Transaction.name == "Open Envelope",
        ).one().template_id
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=one_pass(
                rule_item(0, a_merchant(seed_user, "Alpha").id, answer=f"t:{template_id}"),
                rule_item(1, a_merchant(seed_user, "Beta").id, answer="new", name="Beta Fund",
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
        an_envelope(seed_user)
        an_unexplained_outflow(seed_user, merchant="Lowe's", amount="-30.00")
        an_unexplained_outflow(seed_user, merchant="Lowe's", amount="-45.00")
        db.session.commit()
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=rule_item(0, a_merchant(seed_user, "Lowe's").id, answer="new", name="Yard & Garden",
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
        page says why, and the rule row says which two of its own options are
        refused so that refusal is not the first the owner hears of it.
        """
        an_envelope(seed_user)
        an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category=_CARD_PAYMENT,
        )
        db.session.commit()

        response = auth_client.get(_review_url(seed_user["account"].id))
        body = " ".join(response.data.decode().split())

        assert self._destinations_offered(response.data.decode()) == []
        # **The GROUP, not a card heading** (ruling **bank_import:R-HB**, plan
        # step ``bank_import:X-gf-3b-2``): a barred line groups under the
        # evidence that its money is already held, and carries no control.
        #
        # This assertion named "Payments waiting for their home" until that
        # step, and it was **passing for the wrong reason** -- the merchant
        # card's own prose contained the same words across a line break, and
        # `body` is whitespace-normalised, so the needle matched a SENTENCE
        # rather than the card it was meant to grade.  It stayed green through
        # the commit that deleted that card outright.
        assert "Your records may already hold these" in body
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
        an_envelope(seed_user)
        barred = an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category=_CARD_PAYMENT,
        )
        ordinary = an_unexplained_outflow(
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
        envelope = an_envelope(seed_user)
        line = an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category=_CARD_PAYMENT,
        )
        db.session.commit()

        response = auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=rule_item(
                0, a_merchant(seed_user, "Capital One Credit Card").id,
                answer=f"t:{envelope.template_id}",
            ),
        )
        body = " ".join(response.data.decode().split())

        assert "cannot be filed in a budget line" in body
        from app.models.merchant_rule import (  # pylint: disable=import-outside-toplevel
            MerchantRule,
        )
        assert db.session.query(MerchantRule).count() == 0
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
        an_envelope(seed_user)
        an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category=_CARD_PAYMENT,
        )
        db.session.commit()

        response = auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=rule_item(
                0, a_merchant(seed_user, "Capital One Credit Card").id,
                answer="never",
            ),
        )
        body = " ".join(response.data.decode().split())

        assert "Capital One Credit Card is never a purchase." in body
        from app.models.merchant_rule import (  # pylint: disable=import-outside-toplevel
            MerchantRule,
        )
        stored = db.session.query(MerchantRule).one()
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
        envelope = an_envelope(seed_user)
        line = an_unexplained_outflow(seed_user, merchant="Capital One Credit Card")
        db.session.commit()
        stale = _apply_form_controls(auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode())
        stale[f"destination-{line.id}"] = str(envelope.id)
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=rule_item(
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

class TestWhyARuleDidNotFileALineItReaches:
    """Finding **N-359** at the route tier, plan step ``bank_import:X-gf-3a``.

    The service test grades the verdict; this grades what a browser is handed,
    which is the half the finding was actually about: the reason lived on
    ``RuleFiling``, whose only rendering is the import's transient FLASH, so a
    line the owner's own rule was supposed to have handled arrived on this page
    with nothing saying so.

    **The arm chosen here is the one that rendered NOWHERE.**  A line withheld
    for a SEARCH gap at least got the unattributed *check the match form below*
    sentence; a line withheld because its rule's destination is a row this
    statement already explains as a whole got nothing at all -- while still
    carrying its placement sentence and still being counted by the one-click
    sweep.
    """

    @staticmethod
    def _group_card(body, evidence):
        """Return just the queue group card for *evidence*.

        **An assertion about ONE group has to read that group** (ruling
        **bank_import:R-HB**, plan step ``bank_import:X-gf-3b-2``).  The queue
        is one list now, so a body-wide assertion cannot say WHICH group a
        line was placed in -- and the group is the whole claim this step makes
        about a line.

        **Bounded by the card's own id at BOTH ends**, which is the shape
        ``test_statement_workbench.py``'s ``_never_showed_panel`` already
        uses.  A first version sliced from the group HEADING to the next
        ``data-queue-group``, and an adversarial review measured that it
        bounded nothing: with one group on the page there is no next
        occurrence after the heading, so the slice ran to ``</html>`` --
        8,795 of 18,246 characters, swallowing the bounds panel, the apply
        button and the register pointer. Every assertion "about the card" was
        a page-wide assertion.

        Args:
            body: The whitespace-normalised review page.
            evidence: The group's ``Evidence`` value, e.g. ``"unfinished"``.

        Returns:
            That group's markup alone.

        Raises:
            AssertionError: When the group is not on the page at all, which a
                slice would otherwise report as an empty string that every
                negative assertion passes against.
        """
        opens = body.find(f'id="queue-group-{evidence}"')
        assert opens != -1, f"no {evidence} group on the page"
        rest = body.find('id="queue-group-', opens + 1)
        tail = body.find('<div class="card mb-3">', opens)
        ends = min(x for x in (rest, tail, len(body)) if x != -1)
        return body[opens:ends]

    def _a_collision(self, seed_user, db):
        """Stage a rule whose destination this statement explains as a whole.

        The envelope carries one `$180.00` purchase, so its own cash leg is
        `-$180.00` (ruling **R-FM**: an unposted purchase is INCLUDED), and a
        bank line of that figure pairs with it one-to-one.  The Amazon swipe
        beside it is what the rule reaches.
        """
        day = seed_user["bootstrap_period"].start_date
        envelope = a_transaction(
            seed_user, name="Groceries", amount="500.00", is_envelope=True,
        )
        a_purchase(seed_user, envelope, amount="180.00")
        a_bank_line(
            seed_user, an_import(seed_user), amount="-180.00", posted_on=day,
            description="POINT OF SALE DEBIT L340 KROGER", sequence_in_group=9,
        )
        swipe = an_unexplained_outflow(
            seed_user, merchant="Amazon", amount="-57.96",
        )
        a_rule(seed_user, "Amazon", template_id=envelope.template_id)
        db.session.commit()
        return envelope, swipe

    def test_the_page_says_the_rule_will_not_file_it_and_why(
        self, auth_client, db, seed_user,
    ):
        """THE FIRING CONTROL.  Nothing on this page said it before.

        Both halves are asserted: that the screen names the withholding at all,
        and that the sentence it prints is the one ruling **R-GH**'s door
        withholds on rather than a second wording of the same rule.
        """
        envelope, _ = self._a_collision(seed_user, db)

        page = auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode()
        body = " ".join(page.split())

        assert "Proposed matches" in body
        assert "Your rules will not record this one by themselves" in body
        assert (
            "this statement explains that budget line on its own, and a "
            "purchase filed inside it makes that match impossible to accept, "
            "so the line it explains would stay unexplained" in body
        )
        # ...and the advice is the one that fits THIS reason: the remedy for a
        # destination the statement already explains is to accept that match,
        # not to go looking in the hand-build form.
        assert (
            "Accept that match first, or file this line somewhere else."
            in body
        )
        # ...and the placement sentence still says WHERE, so the two read
        # together rather than the warning replacing the context for it.
        assert "You file Amazon in" in body
        assert envelope.name in body

    def test_an_ORDINARY_rule_reached_line_is_NOT_warned_about(
        self, auth_client, db, seed_user,
    ):
        """The control the case above is read against.

        Without it a screen that printed the withholding sentence on every
        rule-reached line -- or on every line at all -- would satisfy every
        assertion above.  Same rule, same merchant, same envelope; the only
        difference is that no line of this statement explains that envelope.
        """
        day = seed_user["bootstrap_period"].start_date
        envelope = a_transaction(
            seed_user, name="Groceries", amount="500.00", is_envelope=True,
        )
        a_purchase(seed_user, envelope, amount="180.00")
        an_unexplained_outflow(seed_user, merchant="Amazon", amount="-57.96")
        a_rule(seed_user, "Amazon", template_id=envelope.template_id)
        db.session.commit()

        body = " ".join(auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode().split())

        assert "You file Amazon in" in body
        assert "Your rules will not record this one by themselves" not in body
        assert "count that money twice" not in body

    def test_a_SEARCH_GAP_is_reported_as_the_rule_s_and_printed_ONCE(
        self, auth_client, db, seed_user,
    ):
        """One line, one warning, and it is the RULE's wording.

        The other withholding reason, and the case the template's ``elif``
        exists for: here the withheld sentence and the search gap are the SAME
        string -- ``rule_verdicts`` asks ``search_gap`` first -- so a screen
        printing both would print one sentence twice, and a screen printing
        only the gap would go back to saying nothing about the rule.  Both
        halves are asserted, because each is satisfied by a different bug.

        **The crowded-day arm is the one that can be ARRANGED**: 33 candidate
        rows share the line's own day against a bound of 32, so the group
        search skips it.  The other two arms measure zero on real data and are
        graded on the published bound in ``test_verdict.py``.
        """
        day = seed_user["bootstrap_period"].start_date
        envelope = a_transaction(
            seed_user, name="Groceries", amount="500.00", is_envelope=True,
        )
        for index in range(33):
            a_transaction(
                seed_user, name=f"Bill {index}", amount=f"{index + 11}.00",
                status=StatusEnum.DONE, settled_on=day,
            )
        an_unexplained_outflow(seed_user, merchant="Amazon", amount="-57.96")
        a_rule(seed_user, "Amazon", template_id=envelope.template_id)
        db.session.commit()

        body = " ".join(auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode().split())

        # **The UNFINISHED group**, and this case is what makes it reachable:
        # the crowded day means this pass cannot say the line has no
        # counterpart, so its evidence is neither "already held" nor "nothing
        # found".  It is empty on the developer's own data and this is the
        # arrangement that proves the predicate is real.
        card = self._group_card(body, "unfinished")
        assert "Your rules will not record this one by themselves" in card
        assert card.count("held too many rows for the app to search them") == 1
        # **The NO-RULE wording, which must not appear on a line a rule DOES
        # reach.**  The needle is `_verdict._look_first`'s opening, and it
        # changed at plan step ``bank_import:X-gf-3b`` because the old one
        # named a POSITION ("check the match form below") that the form's move
        # made false.  It is re-pointed rather than dropped: a negative
        # assertion whose needle no longer exists anywhere passes for the wrong
        # reason and discriminates nothing.
        # Re-pointed AGAIN at plan step ``bank_import:X-gj-2b-3``, when
        # ruling **bank_import:R-II** made *as new spending* false of a refund
        # this same pipeline files.  The needle is still the NO-RULE opening
        # and still absent from a rule-reached line, whose sentence ends with
        # ``_LOOK_FIRST`` instead; this case stages one outflow and no inflow,
        # so ``_queue._notes_for`` -- which composes the identical sentence for
        # a parked line or a deposit through the same function since that step
        # -- puts nothing in this card either.
        assert "Before recording this, match it" not in card
        # ONE render on this page -- the create card's -- and the second is on
        # the hand-build surface, which is where the same fact is rendered
        # beside the act it prompts.  Still one derivation and two renders;
        # what changed is that the second render is a page away.
        assert body.count("held too many rows for the app to search them") == 1
        workbench = " ".join(auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements/match",
        ).data.decode().split())
        assert workbench.count(
            "held too many rows for the app to search them"
        ) == 1


class TestWhereAParkedLineSendsTheOwner:
    """Plan step ``bank_import:X-gf-3a``: the register, named or not named.

    Since ruling **bank_import:R-GX** an answered merchant leaves this screen's
    own control, so the only place a parked line's answer can be changed is the
    register -- and the line did not name it.  What the fix had to get right is
    that it must NOT name it where changing the answer would change nothing:
    on the developer's own data 2026-08-27 that is 9 of 9 parked lines, so a
    link rendered unconditionally would have been wrong every time it appeared.
    """

    @staticmethod
    def _register_url(seed_user):
        """Return the register's URL, built the way the template builds it."""
        return f"/accounts/{seed_user['account'].id}/statements/register"

    def test_an_answer_the_owner_could_change_is_LINKED_to_the_register(
        self, auth_client, db, seed_user,
    ):
        """An ordinary swipe merchant answered *never a purchase*."""
        an_envelope(seed_user)
        an_unexplained_outflow(seed_user, merchant="Walmart", amount="-57.96")
        a_rule(seed_user, "Walmart")
        db.session.commit()

        page = auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode()

        assert "Your records may already hold these" in page
        assert (
            f'<a href="{self._register_url(seed_user)}">Change what you have '
            f"said about Walmart</a>" in " ".join(page.split())
        )

    def test_an_answer_that_would_change_NOTHING_offers_no_link(
        self, auth_client, db, seed_user,
    ):
        """THE FIRING CONTROL, and the case that is 9 of the developer's 9.

        A card merchant answered *never a purchase* carries BOTH bars, and the
        second is lifted by no answer at all -- so the register would show the
        row and refuse every change made on it.
        """
        an_envelope(seed_user)
        an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category=_CARD_PAYMENT,
        )
        a_rule(seed_user, "Capital One Credit Card")
        db.session.commit()

        page = auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode()
        body = " ".join(page.split())

        assert "Your records may already hold these" in body
        assert "Change what you have said about" not in body
        # ...and the reason says why no answer would help, rather than leaving
        # the owner to discover it at a refusal.
        assert "which no answer lifts" in body


class TestWhatTheRENDEREDQueueOwesEachLine:
    """The half that broke last time: what a browser actually receives.

    Plan step ``bank_import:X-gf-3b-2``.  Three claims this step makes are
    RENDERING claims, and each was graded only in the service until an
    adversarial review measured the gap 2026-08-28 -- one of them by deleting
    the markup outright and watching 705 route tests stay green.
    """

    def test_the_group_card_carries_the_hook_the_SWEEP_S_REACH_depends_on(
        self, auth_client, db, seed_user,
    ):
        """``data-queue-group`` is the whole structural guard of ruling R-HD.

        ``statement_review.js`` roots the sweep at
        ``target.closest("[data-queue-group]")`` and returns when there is
        none.  Delete the attribute and every sweep silently stops working --
        or, before the fallback was closed, reverted to the whole money form
        and could tick a placed row in another group.  An adversarial review
        deleted it and **705 tests passed**, because the only test naming the
        string treated its absence as a valid outcome.

        The PAIR is what states the invariant: the card carries the hook, and
        the swept row carries its placement class INSIDE that card.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", amount="500.00", is_envelope=True,
        )
        an_unexplained_outflow(seed_user, merchant="Amazon", amount="-57.96")
        a_rule(seed_user, "Amazon", template_id=envelope.template_id)
        db.session.commit()

        body = " ".join(auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode().split())
        card = TestWhyARuleDidNotFileALineItReaches._group_card(
            body, "nothing_found",
        )

        assert 'data-queue-group="nothing_found"' in card
        assert 'data-placement-class="into_open"' in card
        assert "data-tick-placed=" in card

    def test_a_PARKED_line_prints_its_search_gap_ON_THE_PAGE(
        self, auth_client, db, seed_user,
    ):
        """The asymmetry ruling **bank_import:R-HB** is named for.

        A parked line was the one kind that never printed its gap, and that is
        a RENDERING fact -- the service case alone would have stayed green if
        the template dropped the sentence, which is the half that broke when
        the hand-build form moved.
        """
        day = seed_user["bootstrap_period"].start_date
        an_envelope(seed_user)
        for index in range(33):
            a_transaction(
                seed_user, name=f"Bill {index}", amount=f"{index + 11}.00",
                status=StatusEnum.DONE, settled_on=day,
            )
        an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category=_CARD_PAYMENT,
        )
        db.session.commit()

        body = " ".join(auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode().split())
        card = TestWhyARuleDidNotFileALineItReaches._group_card(
            body, "already_held",
        )

        assert "payment to an account you hold" in card
        assert "held too many rows for the app to search them" in card

    def test_an_INFLOW_S_gap_names_the_act_and_not_only_the_fact(
        self, auth_client, db, seed_user,
    ):
        """An outflow's gap is framed by ``_look_first``; an inflow's owed it.

        The deleted deposit card wrapped the same sentence in *before
        recording this ... match it against rows you already hold*, and
        printing the bare gap would state a FACT where the outflow path states
        an ACT -- the per-mechanism asymmetry this step exists to end,
        reintroduced in the other direction.
        """
        day = seed_user["bootstrap_period"].start_date
        an_envelope(seed_user)
        for index in range(33):
            a_transaction(
                seed_user, name=f"Bill {index}", amount=f"{index + 11}.00",
                status=StatusEnum.DONE, settled_on=day,
            )
        a_bank_line(
            seed_user, an_import(seed_user), amount="41.10", posted_on=day,
            description="DIVIDEND EARNED", sequence_in_group=7,
        )
        db.session.commit()

        body = " ".join(auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode().split())

        assert (
            "Before recording this, match it against rows you already hold:"
            in body
        )
        assert "held too many rows for the app to search them" in body


class TestWhatTheStatementNeverShowed:
    """Finding **bank_import:N-380**, on the rendered page.

    The panel prints two counts and two money figures and is the whole
    user-visible half of the finding.  An adversarial review replaced its
    guard with ``{% if False %}`` and **4,153 tests passed**: it could have
    been deleted and CI would have been green.
    """

    def test_both_directions_are_stated_and_never_summed(
        self, auth_client, db, seed_user,
    ):
        """One unshown payment and one unshown deposit, counted APART.

        Their signed net would be `$2,385.38` and their absolute total
        `$2,561.38`; the panel must state neither, because a net cancels
        income the bank never credited against payments it never made.
        """
        day = seed_user["bootstrap_period"].start_date
        an_unexplained_outflow(seed_user, merchant="Amazon", amount="-57.96")
        a_transaction(
            seed_user, name="Water Bill", amount="88.00",
            status=StatusEnum.DONE, settled_on=day,
        )
        a_transaction(
            seed_user, name="Data Manager", amount="2473.38", income=True,
            status=StatusEnum.DONE, settled_on=day,
        )
        db.session.commit()

        body = " ".join(auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode().split())

        assert "What your records hold and this statement did not show" in body
        assert "1 payment(s) totalling $88.00" in body
        assert "1 deposit(s) totalling $2,473.38" in body
        # Neither aggregate is offered: the net is the misleading one.
        assert "$2,385.38" not in body
        assert "$2,561.38" not in body

    def test_a_pass_that_explains_every_row_states_NOTHING(
        self, auth_client, db, seed_user,
    ):
        """Silence is the empty state, not a zero.

        Without this the panel could render "0 payment(s)" for ever and the
        case above would still pass.
        """
        an_unexplained_outflow(seed_user, merchant="Amazon", amount="-57.96")
        db.session.commit()

        body = " ".join(auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode().split())

        assert (
            "What your records hold and this statement did not show"
            not in body
        )


class TestEveryExceptionLinksToTheTool:
    """Ruling **bank_import:R-HC**: the queue's rows reach the workbench.

    The hand-build match form moved to a surface of its own at plan step
    ``bank_import:X-gf-3b`` because it is the TOOL three exceptions send the
    owner to and not an exception itself.  **Moving it is only half the
    ruling**: the other half is that each of those exceptions links to it
    "with its own line already ticked", and without that half the queue names
    a remedy the owner has to go and find.

    **The link is rendered UNCONDITIONALLY on every row**, and that is a fact
    about the act rather than a shortcut: every line the three cards show is in
    ``review.unmatched``, which is exactly the set the workbench renders a
    checkbox for, so there is no line here the tool cannot take.  Compare
    ``ParkedLine.answer_door`` beside it, which the service withholds on 9 of 9
    of the developer's parked lines because restating that answer would change
    nothing.
    """

    @staticmethod
    def _linked_lines(page):
        """Return the line ids the page links to the workbench with."""
        return sorted(
            int(found) for found in re.findall(
                r"/statements/match\?line=(\d+)", page,
            )
        )

    def test_a_CREATABLE_line_links_to_the_tool_carrying_itself(
        self, auth_client, db, seed_user,
    ):
        """The card where the WRONG act is cheapest gets the right one beside it.

        Recording a line the app already holds in another shape is the
        duplicate finding **N-335** measures at `$356.61` for one `$178.29`
        movement, and matching is what that owner should have done instead.
        """
        an_envelope(seed_user)
        line = an_unexplained_outflow(seed_user, merchant="Lowe's")
        db.session.commit()

        page = auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode()

        assert "Nothing of yours accounts for these" in page
        assert self._linked_lines(page) == [line.id]

    def test_a_PARKED_line_links_to_the_tool_carrying_itself(
        self, auth_client, db, seed_user,
    ):
        """Ruling **R-GJ**'s only remaining arm, reachable from the row.

        A card payment meets the payback rows it repays by being ticked beside
        them.  The reason beside such a line USED to end "tick them together
        below and match them" -- a sentence composed in the SERVICE
        (``ParkedLine.reason``) naming a position on a page the service cannot
        see.  It states the bar now, and this link states the act.
        """
        an_envelope(seed_user)
        line = an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card",
        )
        db.session.commit()
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=rule_item(
                0, a_merchant(seed_user, "Capital One Credit Card").id,
                answer="never",
            ),
        )

        page = auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode()
        body = " ".join(page.split())

        assert "Your records may already hold these" in body
        assert self._linked_lines(page) == [line.id]
        # **NO SENTENCE ON THIS PAGE NAMES A POSITION.**  Five did until this
        # step and all five went false in the commit that moved the form; two
        # of them were composed in the service, which cannot see a layout at
        # all.  The needles are the exact clauses that broke.
        assert "tick them together below" not in body.lower()
        assert "match form below" not in body
        assert "match it below" not in body

    def test_a_RECORDABLE_INFLOW_links_to_the_tool_carrying_itself(
        self, auth_client, db, seed_user,
    ):
        """Ruling **bank_import:R-GW**'s card, where the duplicate runs the
        other way.

        Seven of the sixteen inflows on the developer's own statement are
        payroll deposits his app already holds as two or three rows each, and
        recording one here would book `$2,573.42` of income the books already
        carry.  Matching it against those rows is the act, and it is one click
        from the line.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="2573.42", posted_on=bank_day,
            description="ACH DEPOSIT PAYROLL",
        )
        db.session.commit()

        page = auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode()

        assert "Nothing of yours accounts for these" in page
        assert self._linked_lines(page) == [line.id]


class TestTheQueueDoesNotRenderTheTool:
    """N-374's own regression, and until 2026-08-28 nothing graded it.

    **This is the step's HEADLINE claim and it had no control.** An adversarial
    test-quality review put the two pick lists back into
    ``_statement_review_body.html`` and ran both statement route files: **102
    passed, 0 failed.** Every case here dies to that edit.

    What the claim is worth: those two lists were **89,247 bytes, 59% of the
    review body**, on the developer's own data -- 22,830 for 27 bank lines and
    66,417 for 67 rows, against 1 unanswered merchant, 2 creatable lines, 16
    deposits and 9 parked payments of actual work. Both are UNBOUNDED and the
    right one grows with the whole span the statements cover, for ever, which
    is why ruling **bank_import:R-HC** bounds the queue by the tool LEAVING it
    rather than by capping either list.
    """

    def test_the_queue_renders_NEITHER_pick_list(
        self, auth_client, db, seed_user,
    ):
        """The absence itself, by the captions and the field names.

        **Both**, because the two lists are separate cards and an edit could
        restore one: the byte cost is 22,830 for the left and 66,417 for the
        right, so either alone is most of what this step removed.
        """
        an_envelope(seed_user)
        an_unexplained_outflow(seed_user, merchant="Geico")
        a_transaction(
            seed_user, name="Ghost Payment", amount="22.22",
            status=StatusEnum.DONE,
            settled_on=seed_user["bootstrap_period"].start_date,
        )
        db.session.commit()

        page = auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode()

        assert "Lines your bank shows and nothing explains" not in page
        assert "Rows you recorded that no line explains" not in page
        assert 'name="line_ids"' not in page
        assert 'name="rows"' not in page
        assert 'id="hand-build-form"' not in page
        assert 'id="hand-totals"' not in page

    def test_the_queue_renders_no_control_that_posts_to_the_tool(
        self, auth_client, db, seed_user,
    ):
        """The FORM, not just its lists.

        A restored form with its captions reworded would pass the case above.
        This asks the one question a browser asks: is there anything here that
        POSTS to the workbench's write door?
        """
        an_envelope(seed_user)
        an_unexplained_outflow(seed_user, merchant="Geico")
        db.session.commit()
        account_id = seed_user["account"].id

        page = auth_client.get(_review_url(account_id)).data.decode()

        match_url = f"/accounts/{account_id}/statements/match"
        # It LINKS there -- that is ruling R-HC's other half -- so the
        # assertion has to be about a form ACTION rather than about the URL
        # appearing at all, which is what an earlier draft of this case got
        # wrong and what would have made it pass on an empty page.
        assert match_url in page, (
            "the queue no longer links to the tool at all, so this case would "
            "pass for the wrong reason"
        )
        assert f'action="{match_url}"' not in page
        assert f'hx-post="{match_url}"' not in page

    def test_the_TOOL_still_renders_both_lists(
        self, auth_client, db, seed_user,
    ):
        """THE PAIR: the lists moved rather than being deleted.

        Without this, every assertion above is satisfied by a step that simply
        removed the hand-build form from the app -- which would take ruling
        **R-GJ**'s only remaining arm with it, since a parked card payment
        meets its payback rows nowhere else.
        """
        an_envelope(seed_user)
        line = an_unexplained_outflow(seed_user, merchant="Geico")
        a_transaction(
            seed_user, name="Ghost Payment", amount="22.22",
            status=StatusEnum.DONE,
            settled_on=seed_user["bootstrap_period"].start_date,
        )
        db.session.commit()

        page = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements/match",
        ).data.decode()

        assert "Lines your bank shows and nothing explains" in page
        assert "Rows you recorded that no line explains" in page
        assert f'name="line_ids" value="{line.id}"' in page
        assert "Ghost Payment" in page




class TestTheBooksBoundIsRENDERED:
    """The owner-visible half of ``balance:X-f3c-2b-2b``, on this surface.

    **This class exists because the whole of it was invisible to the suite.**
    An adversarial test-quality review disabled the three ``{% if %}`` blocks
    that render the bound across the three surfaces and measured the FULL
    suite still green at 12,206 -- every assertion was against the service
    value, and none against a page.  The anchor especially exists ONLY in
    Jinja: a URL is the one fact the service may not build, so nothing below
    the template can grade it.
    """

    def test_the_page_states_the_bound_and_links_the_restatement_door(
        self, app, auth_client, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """FIRING CONTROL for the rendering, the figure and the href."""
        account, day = an_account_whose_books_hide_a_line(
            db, seed_user, seed_periods,
        )

        body = auth_client.get(_review_url(account.id)).data.decode()

        # **No apostrophes in any of these.**  Jinja escapes ``'`` to
        # ``&#39;``, so asserting the service's own sentence verbatim can
        # never match a rendered page -- a control that fails for the wrong
        # reason is not a control.  The existing pay-calendar precedent in
        # this suite avoids them for the same reason.
        assert "already inside this account" in body
        assert "opening balance of" in body
        assert "$689.16" in body
        assert day.strftime("%b %-d, %Y") in body
        assert f"/accounts/{account.id}/edit#books-opening" in body
        assert "Restate this account" in body


class TestThePageAndTheReceiptSayWhichDIRECTIONApplyRecorded:
    """Two owner-visible money sentences that no case graded.

    Plan step ``bank_import:X-gj-2b-3``.  Ruling **bank_import:R-II** routes a
    merchant credit into the PURCHASE pipeline, and both of the sentences that
    describe what that pipeline does were written when only spending could
    reach it:

      * the press-level *what Apply will create* paragraph -- *a line you name
        a destination for becomes a purchase your records did not have, dated
        the day your bank took it* -- over money the bank GAVE BACK; and
      * the receipt line ``recorded_count`` prints, whose caption says the
        same thing about a count that had come to hold both directions.

    Neither had a test at all, which is why both went false in silence.  Every
    case here asserts the sentence that must appear AND the one that must not,
    because a template rendering both unconditionally satisfies half of it.
    """

    @staticmethod
    def _a_refund_line(seed_user, db, amount="42.00"):
        """Stage a merchant credit a standing SPENDING answer files.

        The rule names the envelope's own template, which is what makes the
        credit that rule's inverse (ruling **R-HT(a)**) rather than a deposit.
        """
        envelope = an_envelope(seed_user)
        a_rule(seed_user, "Amazon", template_id=envelope.template_id)
        line = an_unexplained_outflow(
            seed_user, merchant="Amazon", amount=amount,
        )
        db.session.commit()
        return envelope, line

    def test_the_page_promises_a_REFUND_for_a_merchant_credit(
        self, auth_client, db, seed_user,
    ):
        """The paragraph names the act this press would actually perform."""
        self._a_refund_line(seed_user, db)

        page = " ".join(auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode().split())

        assert "becomes <strong>a refund against the budget line you name" in page
        assert "dated the day your bank paid it in" in page
        assert "a purchase your records did not have" not in page, (
            "the charge sentence describes an act this press cannot perform"
        )

    def test_the_page_promises_a_PURCHASE_for_an_ordinary_swipe(
        self, auth_client, db, seed_user,
    ):
        """The control, and the half that must keep working.

        Without it the case above passes on a page that had simply swapped one
        unconditional sentence for another.
        """
        an_envelope(seed_user)
        an_unexplained_outflow(seed_user, merchant="Amazon", amount="-57.96")
        db.session.commit()

        page = " ".join(auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode().split())

        assert "becomes <strong>a purchase your records did not have" in page
        assert "a refund against the budget line you name" not in page

    def test_the_RECEIPT_says_a_refund_was_recorded_as_a_refund(
        self, auth_client, db, seed_user,
    ):
        """End to end, through the form the page itself rendered.

        The payload is scraped from the page and the destination set on it, so
        this posts what a browser would submit rather than what a reader thinks
        the template emits.  Asserts the ENTRY as well as the sentence: a
        receipt is only worth grading over an act that happened.
        """
        envelope, line = self._a_refund_line(seed_user, db)
        payload = _apply_form_controls(auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode())
        payload[f"destination-{line.id}"] = str(envelope.id)

        body = " ".join(auth_client.post(
            _review_url(seed_user["account"].id), data=payload,
        ).data.decode().split())

        entries = db.session.query(TransactionEntry).all()
        assert [entry.amount for entry in entries] == [Decimal("-42.00")], (
            "the act itself must have landed, or the receipt grades nothing"
        )
        assert (
            "1 bank line(s) recorded as a refund against a budget line, "
            "lowering what it has cost." in body
        )
        assert "recorded as a purchase your records did not have" not in body, (
            "one count carried both directions and its caption named the act "
            "the bank did NOT perform on this line"
        )

    def test_the_RECEIPT_says_a_swipe_was_recorded_as_a_purchase(
        self, auth_client, db, seed_user,
    ):
        """The receipt's control, on the same door and the same form."""
        envelope = an_envelope(seed_user)
        line = an_unexplained_outflow(
            seed_user, merchant="Amazon", amount="-57.96",
        )
        db.session.commit()
        payload = _apply_form_controls(auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode())
        payload[f"destination-{line.id}"] = str(envelope.id)

        body = " ".join(auth_client.post(
            _review_url(seed_user["account"].id), data=payload,
        ).data.decode().split())

        entries = db.session.query(TransactionEntry).all()
        assert [entry.amount for entry in entries] == [Decimal("57.96")]
        assert (
            "1 bank line(s) recorded as a purchase your records did not have."
            in body
        )
        assert "recorded as a refund against a budget line" not in body
