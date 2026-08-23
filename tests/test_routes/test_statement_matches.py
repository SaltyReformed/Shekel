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

from datetime import date, timedelta
from html.parser import HTMLParser
from decimal import Decimal

import pytest

from app.enums import StatusEnum
from app.models.account import Account
from app.models.statement_import import BankStatementLine
from app.models.statement_match import StatementMatch, StatementMatchMember
from app.models.transaction import Transaction
from app.models.user import User, UserSettings
from sqlalchemy.exc import OperationalError

from app.exceptions import ValidationError
from app.routes.accounts import statement_matches as statement_matches_route
from app.services import auth_service
from app.services.statement_match import _batch as statement_match_batch
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_purchase,
    a_transaction,
    an_import,
)


def _review_url(account_id):
    """Return the review page's URL for *account_id*."""
    return f"/accounts/{account_id}/statements/review"


def _match(index=0, lines=(), transactions=(), entries=()):
    """Return the form fields a TICKED match item submits.

    Exactly what ``_statement_review_body.html`` emits: the tick names the
    item's rendered position and the hidden inputs beside it carry that item's
    ids, so what commits is what was reviewed (ruling **R-FP**).

    Args:
        index: The item's rendered position, or ``"hand"`` for the
            hand-build form -- whose index is deliberately not a number, so it
            can never collide with a proposal's.
        lines: Bank line rows it explains.
        transactions: Transaction rows that explain them.
        entries: Purchase rows that explain them.

    Returns:
        The form fields, as a plain ``dict`` for the test client.
    """
    return {
        "apply": [str(index)],
        f"match-{index}-line_ids": [str(line.id) for line in lines],
        f"match-{index}-transaction_ids": [str(txn.id) for txn in transactions],
        f"match-{index}-entry_ids": [str(entry.id) for entry in entries],
    }


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


def _policy(index, merchant, *, answer, name="", category_id=""):
    """Return the form fields ONE merchant row of the policy section submits.

    **Every control the row renders**, whichever answer was picked, because a
    browser submits every control it renders -- the fact a hand-picked payload
    hid at plan step X-f6a-3b, applied to the section this leaf adds.

    Args:
        index: The row's rendered position, which is what keys its fields.
        merchant: The merchant string the hidden input carries.
        answer: ``"unset"`` (I have not said), ``"never"``, ``"new"``, or
            ``"t:<template_id>"``.
        name: What the envelope-name box carries.
        category_id: What the category select carries; ``""`` is its default.

    Returns:
        The form fields, as a plain ``dict`` for the test client.
    """
    return {
        f"policy-{index}": str(answer),
        f"policy_merchant-{index}": merchant,
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

        assert b"1 line(s) have a row of yours" in body
        assert b"not one this page would pick for you" in body
        assert b'data-proposal-class=' not in body


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
        """``RowId``, not ``fields.Integer``: '007' names no row (N-141)."""
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement)
        db.session.commit()

        response = auth_client.post(
            _review_url(seed_user["account"].id),
            data={
                "apply": ["0"],
                "match-0-line_ids": [str(line.id)],
                "match-0-transaction_ids": ["007"],
            },
        )

        # **The status and the sentence, not just the absence of success.**  A
        # route answering 200 with an empty body, or 500 behind an error page,
        # passed the "not in response.data" arm alone.  Named by adversarial
        # test-quality review 2026-08-19.
        assert response.status_code == 400
        assert response.headers.get("Shekel-Designed-Fragment") == "1"
        assert b"Not a valid id" in response.data
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
            data[f"match-{index}-transaction_ids"] = ["007"]

        response = auth_client.post(
            _review_url(seed_user["account"].id), data=data,
        )

        assert response.status_code == 400
        body = response.data.decode()
        assert body.count("Not a valid id.") == 1, (
            "the same sentence was repeated once per bad value"
        )
        # ...and it says WHERE, so the owner can find the ticked item.
        assert "matches" in body
        assert "transaction_ids" in body

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
        assert b"the bank never showed" in response.data
        assert b'name="match-hand-line_ids"' in response.data
        assert b'name="match-hand-transaction_ids"' in response.data
        # **Its index cannot collide with a rendered proposal's.**  Both forms
        # post to one door; only their separateness as <form> elements keeps
        # proposal 0's hidden ids out of this group today, and that is a
        # property of the document rather than of the form.
        assert b'name="apply" value="hand"' in response.data

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
        db.session.flush()
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


def _a_line(seed_user, merchant="Amazon", amount="-57.96", sequence=0):
    """Record one unexplained outflow from *merchant*.

    Args:
        seed_user: The seeded user bundle.
        merchant: What the bank names the merchant, which is the policy key.
        amount: Signed, negative OUT of the account.
        sequence: The ordinal completing the line's identity.

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
            data=_policy(0, "Amazon", answer=f"t:{envelope.template_id}"),
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
            data=_policy(0, "Amazon", answer=f"t:{envelope.template_id}"),
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
            data=_policy(0, "Amazon", answer=f"t:{envelope.template_id}"),
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
            data=_policy(0, "Amazon", answer=f"t:{envelope.template_id}"),
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

    def test_a_NEVER_answer_says_so_on_every_line_and_offers_nothing(
        self, auth_client, db, seed_user,
    ):
        """The answer that is worth the most money on the developer's own data.

        Capital One Credit Card is 9 of their 91 unexplained outflows and
        `-$7,412.94` of the `-$11,336.36` in that list, all of which the app
        already holds as CC Payback rows.  Saying it once has to stop the
        screen asking again -- and must place nothing, so the sweep passes over
        it.
        """
        _an_envelope(seed_user)
        _a_line(seed_user, merchant="Capital One Credit Card")
        db.session.commit()

        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=_policy(0, "Capital One Credit Card", answer="never"),
        )
        response = auth_client.get(_review_url(seed_user["account"].id))
        # WHITESPACE-NORMALISED, because a rendered sentence wraps: asserting
        # on the contiguous string grades the template's line breaks rather
        # than what the page says.
        body = " ".join(response.data.decode().split())

        assert "Capital One Credit Card is never a purchase" in body
        # It places nothing, so the sweep has nothing to offer and passes over
        # the line entirely.
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
            data=_policy(0, "Alpha", answer=f"t:{envelope.template_id}"),
        )

        response = auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=_pass(
                _policy(0, "Alpha", answer=f"t:{envelope.template_id}"),
                _policy(1, "Beta", answer="never"),
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
            data=_policy(0, "Lowe's", answer="new", name="Yard & Garden",
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
            data=_policy(0, "Amazon", answer=f"t:{foreign.template_id}"),
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

        response = auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=_policy(0, "Nowhere Ltd", answer="never"),
        )

        assert response.status_code == 400
        assert b"never shown" in response.data

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
            data=_policy(0, "Amazon", answer="t:007"),
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
            data=_policy(0, "Amazon", answer="never"),
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
                0, "Lowe's", answer="new", name="Yard & Garden",
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
            data=_policy(0, "Amazon", answer=f"t:{envelope.template_id}"),
        )
        assert db.session.query(MerchantDestination).count() == 1

        response = auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=_policy(0, "Amazon", answer="unset"),
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
            data=_policy(0, "Amazon", answer=f"t:{envelope.template_id}"),
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
            data=_policy(0, "Amazon", answer=f"t:{closed.template_id}"),
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
                _policy(0, "Alpha", answer=f"t:{envelope.template_id}"),
                _policy(1, "Beta", answer="new", name="Beta Fund",
                        category_id=category.id),
                _policy(2, "Gamma", answer="never"),
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
            data=_policy(0, "Amazon", answer=f"t:{envelope.template_id}"),
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
                _policy(0, "Alpha", answer=f"t:{template_id}"),
                _policy(1, "Beta", answer="new", name="Beta Fund",
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
            data=_policy(0, "Lowe's", answer="new", name="Yard & Garden",
                         category_id=category.id),
        )

        body = " ".join(auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode().split())

        assert "One is created per pay period" in body
        assert "an earlier line here already creates it" in body
