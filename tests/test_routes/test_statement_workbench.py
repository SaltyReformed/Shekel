"""
Shekel Budget App -- The hand-build match WORKBENCH, through HTTP

Plan step **bank_import:X-gf-3b**, ruling **bank_import:R-HC**.  These cases
moved here from ``test_statement_matches.py`` with the form they are about: the
hand-build match form is the TOOL three exceptions on the review queue send the
owner to, not an exception itself, so it has a surface, a write door and a
live-totals endpoint of its own.

**What travelled unchanged is every assertion.**  What changed is the URL each
case posts to and the field names it names -- the form carries no ordering
index any more, so ``match-hand-line_ids`` is ``line_ids``, ``match-hand-rows``
is ``rows`` and ``match-hand-residual`` is ``residual``.
"""

from decimal import Decimal
from html.parser import HTMLParser
import re
from datetime import timedelta

import pytest
from werkzeug.datastructures import MultiDict

from app.enums import StatusEnum
from app.models.account import Account
from app.models.statement_match import StatementMatch
from app.models.transaction import Transaction
from app.models.user import User, UserSettings
from app.services import auth_service
from tests.test_routes._statement_forms import hand_match, rule_item
from tests.test_routes.test_statement_matches import _visible_text
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    an_account_whose_books_hide_a_line,
    a_merchant,
    a_purchase,
    a_transaction,
    an_envelope,
    an_import,
    an_unexplained_outflow,
)


def _workbench_url(account_id):
    """Return the hand-build surface's URL for *account_id*."""
    return f"/accounts/{account_id}/statements/match"


def _merchants_url(account_id):
    """Return the merchant-rule door's URL for *account_id*.

    On the REVIEW screen, because that is where an answer is stated -- this
    file needs it only to arrange the state a parked line requires.
    """
    return f"/accounts/{account_id}/statements/review/merchants"


def _totals_url(seed_user):
    """Return the endpoint the hand-build panel re-renders through."""
    return f"/accounts/{seed_user['account'].id}/statements/match/totals"


def _never_showed_panel(body):
    """Return just the "rows no line explains" card's markup.

    **An assertion about ONE panel has to read that panel.**  The page this
    card sits on renders other cards and many badges, so "the words are somewhere in the body"
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

    The card carries an ``id`` for exactly this reason, and the totals panel
    below it carries the one that bounds the far end.

    Args:
        body: The rendered workbench page, as text.

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
        body: The rendered workbench page, as text.

    Returns:
        The card's ``<tbody>`` markup, or ``""`` when it lists nothing.
    """
    panel = _never_showed_panel(body)
    if "<tbody>" not in panel:
        return ""
    return panel[panel.index("<tbody>"):]


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


class _WorkbenchFieldReader(HTMLParser):
    """Collect the hand-build form's controls, keeping REPEATED names.

    ``test_statement_matches._TickedMatchReader``'s twin for the surface that
    no longer emits an index: it filters on ``match-``, which this form stopped
    submitting at plan step ``bank_import:X-gf-3b``, so a scraper reading that
    prefix here would come back EMPTY and every assertion built on it would
    grade nothing.  That is the one failure this file most has to avoid --
    fabricating the row token in the template left 418 tests green once
    already.

    A ``dict`` cannot hold a GROUP: ``rows`` is rendered once per member row,
    and a group is exactly where a multi-value defect hides.
    """

    def __init__(self):
        super().__init__()
        self.fields = []
        self.names = []
        #: When set, collect EVERY named control rather than the three the
        #: door reads -- which is how :func:`_submitted_names` can notice a
        #: field the template grew and the door does not know about.
        self.all_names = False

    def handle_starttag(self, tag, attrs):
        """Record every control the hand-build form would submit."""
        attributes = dict(attrs)
        name = attributes.get("name", "")
        if tag in {"input", "select", "textarea"} and name:
            self.names.append(name)
        if tag == "input" and name in {"line_ids", "rows", "residual"}:
            self.fields.append((name, attributes.get("value", "")))


def _rendered_match_fields(page):
    """Return the form's own fields a browser would post, verbatim."""
    reader = _WorkbenchFieldReader()
    reader.feed(page)
    return reader.fields


def _no_refusal(page):
    """Return whether the totals panel is free of a door refusal.

    The panel renders ``HandTotals.refused``'s sentence inside an
    ``alert-warning`` (``accounts/_statement_hand_totals.html``), and every one
    of those sentences ends the way every designed refusal in this package
    does.  Both needles are asserted so a wording change cannot quietly turn
    this into a check of nothing.

    Args:
        page: The rendered workbench page, as text.

    Returns:
        Whether the panel carries no refusal.
    """
    at = page.find('id="hand-totals"')
    assert at != -1, "no totals panel on the page, so this graded nothing"
    panel = " ".join(page[at:].split())
    return "alert-warning" not in panel and "Nothing was changed" not in panel


def _submitted_names(page):
    """Return every control NAME the hand-build form would submit.

    Reads the form's own markup rather than a list written beside it, so a
    field added to the template without the door being taught to read it shows
    up here as a set that no longer matches.
    """
    reader = _WorkbenchFieldReader()
    reader.all_names = True
    reader.feed(page)
    return set(reader.names)


class _ConsentReader(HTMLParser):
    """Read the hand-build panel's consent box, exactly as a browser would.

    Plan step ``bank_import:X-f6d-4``.  The box's VALUE is the figure the
    server computed and the door will compare, so scraping it is the only way
    a test can post what the screen actually offered rather than a figure of
    its own.

    **It records the input's TYPE, and that is not decoration** (plan step
    ``bank_import:X-gj-1b``).  ``residual`` is rendered by three different
    controls now -- an enabled checkbox where there is a difference to permit,
    a HIDDEN input carrying ``"0.00"`` where the two sides agree, and a
    disabled checkbox where nothing is ticked -- so "is it disabled" stopped
    being the same question as "did the panel offer the owner a consent".  A
    hidden input is not disabled, so every caller asserting
    ``disabled is False`` to prove the panel offered something would pass on
    the agrees arm, where it offered nothing to press.  Found by adversarial
    review 2026-08-30.
    """

    def __init__(self):
        super().__init__()
        self.value = None
        self.disabled = None
        self.kind = None

    def handle_starttag(self, tag, attrs):
        """Record the consent control the panel rendered."""
        attributes = dict(attrs)
        if tag == "input" and attributes.get("name") == "residual":
            self.value = attributes.get("value", "")
            self.disabled = "disabled" in attributes
            self.kind = attributes.get("type")


def _rendered_consent(page):
    """Return ``(value, disabled)`` for the panel's consent box.

    Args:
        page: The rendered workbench body, or the panel fragment alone.

    Returns:
        The box's submitted value and whether a browser would submit it.
    """
    reader = _ConsentReader()
    reader.feed(page)
    return reader.value, reader.disabled


def _offered_consent(page):
    """Return the figure the panel asked the owner to AGREE to.

    The question ``_rendered_consent`` used to be asked for, separated from it
    because the two diverged at plan step ``bank_import:X-gj-1b``: a panel can
    now carry a ``residual`` the owner cannot press (the hidden ``"0.00"`` of
    an agreeing group), and a case that means *the panel offered a consent*
    must say so rather than infer it from the field not being disabled.

    Args:
        page: The rendered workbench body, or the panel fragment alone.

    Returns:
        The tick box's value.

    Raises:
        AssertionError: When the panel rendered no consent CHECKBOX, or
            rendered one a browser would not submit -- either of which means
            the case that called this graded nothing.
    """
    reader = _ConsentReader()
    reader.feed(page)
    assert reader.kind == "checkbox", (
        f"the panel offered no consent checkbox (rendered {reader.kind!r}), "
        f"so this graded nothing"
    )
    assert reader.disabled is False, (
        "the panel's consent box was disabled, so this graded nothing"
    )
    return reader.value


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

        response = auth_client.get(_workbench_url(seed_user["account"].id))

        assert b"ACH DEBIT NOTHING EXPLAINS THIS" in response.data
        assert b"Ghost Payment" in response.data
        # The card's HEADER names what it LISTS since plan step X-gc -- rows no
        # line explains -- because its badge counts all of them while the
        # caption now says the bank-failed-to-pay reading holds for only some.
        assert b"Rows you recorded that no line explains" in response.data
        assert b'name="line_ids"' in response.data
        # The ROW side posts one token per row rather than an id list (plan
        # step bank_import:X-f6d-3), and the assertion follows the field it is
        # about: it is here to prove the hand-build form renders BOTH sides to
        # pick from, which is what makes the accept door's refusals reachable
        # from a browser at all.
        assert b'name="rows"' in response.data
        # **THE WHOLE SUBMITTED NAME SET, not the absence of one name.**  This
        # form submitted ``apply=hand`` while it shared a page with the
        # reviewed pass, and that reserved token was the only thing keeping its
        # ticks out of proposal 0's submission -- the two being separate
        # <form> elements, a property of the DOCUMENT.  It posts to a door of
        # its own now (ruling bank_import:R-HC), so no shared namespace exists
        # for that collision to be expressed in.
        #
        # **A first version asserted ``b'name="apply"' not in response.data``,
        # and that could never fail**: ``name="apply"`` is emitted only by
        # ``_statement_review_body.html``, a different template on a different
        # URL, so the needle was never on this page to begin with.  Found by
        # adversarial test-quality review 2026-08-28 -- the same shape as *a
        # negative assertion whose needle no longer exists anywhere passes for
        # the wrong reason*.  An EQUALITY over every name the form emits is
        # what discriminates: it fails if an index comes back, and it fails
        # equally if a field is renamed or added without the door being taught
        # to read it.
        assert _submitted_names(response.data.decode()) == {
            "csrf_token", "line_ids", "rows", "residual",
        }

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
            _workbench_url(seed_user["account"].id)
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
                _workbench_url(seed_user["account"].id)
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

        **And it states no DIRECTION** since finding **bank_import:N-380**,
        plan step ``bank_import:X-gf-3b-2``.  It said *a payment ... your bank
        did not make*, which is a claim about an OUTFLOW, over a list that is
        17 deposits in 49 of the developer's own rows -- his `Data Manager`
        salary at `$2,473.38`, whose payroll credit sits unexplained in the
        queue at `$2,573.42`.  A caption naming one direction is wrong about
        the other whichever way it is written, so each ROW states its own by
        its figure.
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
                _workbench_url(seed_user["account"].id)
            ).get_data(as_text=True)
        )

        assert "A row here that you expected the bank to show is" not in panel
        # Whitespace-normalised: the sentence wraps across template lines, and
        # an assertion carrying the indentation would be graded by the editor.
        flat = " ".join(panel.split())
        assert (
            "One you expected the bank to show is money your records say "
            "moved that this statement never showed moving" in flat
        )
        # The OUTFLOW-only claim is gone, which is N-380's other half.
        assert "your bank did not make" not in flat
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
            _workbench_url(seed_user["account"].id),
            data=hand_match(lines=[line],
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
            _workbench_url(seed_user["account"].id),
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
            _workbench_url(seed_user["account"].id),
        ).get_data(as_text=True)

        trigger = _element_carrying(page, "hx-trigger")
        assert trigger is not None, "nothing re-prices the panel at all"
        assert "line_ids" in trigger, (
            "the trigger does not contain the tick lists, so nothing fires"
        )
        assert "residual" not in trigger, (
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
            _totals_url(seed_user), data=hand_match(lines=[line]),
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
            data=hand_match(lines=[line],
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
        ticked = hand_match(lines=[line],
                        transactions=[salary, allowance])

        panel = auth_client.post(
            _totals_url(seed_user), data=ticked,
        ).get_data(as_text=True)
        offered = _offered_consent(panel)

        response = auth_client.post(
            _workbench_url(seed_user["account"].id),
            data={**ticked, "residual": [offered]},
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
            data=hand_match(lines=[line],
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
            data=hand_match(lines=[first, second],
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
            _workbench_url(seed_user["account"].id),
            data=hand_match(lines=[line],
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
            _workbench_url(seed_user["account"].id),
            data=hand_match(lines=[line], transactions=[salary, allowance]),
        )

        db.session.expire_all()
        assert salary.settled_on == bank_day
        assert allowance.settled_on == bank_day

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
            _workbench_url(seed_user["account"].id)
        ).get_data(as_text=True)
        hand = [
            (name, value) for name, value in _rendered_match_fields(page)
            if name.startswith("line_ids") or name.startswith("rows")
        ]

        assert sum(1 for name, _ in hand if name == "rows") == 1, (
            "the hand form rendered no row token, so this graded nothing"
        )
        payload = MultiDict(
            [("csrf_token", "x")]
            + [(name, value) for name, value in hand if name == "rows"]
            + [("line_ids", str(line.id))]
        )

        # **The panel is priced first, exactly as a browser does it**, and the
        # figure it offers is what the press carries.  Ticking a row fires the
        # totals fragment, and since plan step ``bank_import:X-gj-1b`` the door
        # exempts no shape -- so this -5.00 gap needs the owner's agreement
        # like any other, and a payload assembled without it would be grading
        # the consent gate rather than this form's row token.
        payload.add("residual", _offered_consent(auth_client.post(
            _totals_url(seed_user), data=payload,
        ).get_data(as_text=True)))

        response = auth_client.post(
            _workbench_url(seed_user["account"].id), data=payload,
        )

        assert response.status_code == 200
        assert db.session.query(StatementMatch).count() == 1, (
            "the page's own hand-form token was refused by the door"
        )
        db.session.refresh(row)
        # ...and the bank's figure was written to it (R-GD(a)).
        assert row.settled_amount == Decimal("180.00")

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
        an_envelope(seed_user)
        line = an_unexplained_outflow(seed_user, merchant="Capital One Credit Card")
        db.session.commit()
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=rule_item(
                0, a_merchant(seed_user, "Capital One Credit Card").id,
                answer="never",
            ),
        )

        page = auth_client.get(
            _workbench_url(seed_user["account"].id),
        ).data.decode()

        assert f'name="line_ids" value="{line.id}"' in page


class TestTheLineAnExceptionSentYouHereAbout:
    """Ruling **bank_import:R-HC**: each exception links "with its own line
    already ticked".

    **The preselection is a SET INTERSECTION and never a parse-then-authorise**
    (:func:`~app.routes.accounts.statement_workbench._preselected`), and these
    cases are what make that claim checkable.  A value is honoured only if it
    names a line THIS pass left unexplained on THIS account -- which is exactly
    the set the form renders a checkbox for -- so every way of getting it wrong
    fails through one predicate rather than through a branch per way.

    **A bad link is never an ERROR**, either.  The owner ticks a line, matches
    it, presses Back: the link now names a line that is explained, and
    answering an ordinary browser gesture with a 400 would be worse than
    answering it with a correct page that ticks nothing.
    """

    @staticmethod
    def _ticked(page):
        """Return the line ids whose checkbox rendered ``checked``.

        Read off the RENDERED control rather than off the route's own value,
        because what this class is about is what a browser would submit.

        Args:
            page: The rendered workbench page, as text.

        Returns:
            The ticked line ids, ascending.
        """
        return sorted(
            int(found) for found in re.findall(
                r'name="line_ids" value="(\d+)"[^>]*?checked', page,
            )
        )

    def test_the_line_the_link_named_arrives_ticked(
        self, auth_client, db, seed_user,
    ):
        """The whole point of the link: land on the tool with it selected."""
        line = an_unexplained_outflow(seed_user, merchant="Geico")
        db.session.commit()

        page = auth_client.get(
            f"{_workbench_url(seed_user['account'].id)}?line={line.id}"
        ).data.decode()

        assert self._ticked(page) == [line.id]

    def test_the_panel_PRICES_the_line_that_arrived_ticked(
        self, auth_client, db, seed_user,
    ):
        """A ticked box over an empty panel is a screen contradicting itself.

        The panel is drawn from the SAME reader the door uses
        (``preview_hand_build``), so the figure beside the tick is the one the
        act would compute.  A first version of plan step
        ``bank_import:X-f6d-4`` reported `$0.00` for a `$2,573.43` line the
        owner had just ticked, which is this shape one surface earlier.
        """
        line = an_unexplained_outflow(
            seed_user, merchant="Geico", amount="-178.29",
        )
        db.session.commit()

        page = auth_client.get(
            f"{_workbench_url(seed_user['account'].id)}?line={line.id}"
        ).data.decode()

        said = " ".join(page.split())
        # The BANK side is the line's own figure, signed as the bank showed it.
        assert "Your bank shows <span class=\"fw-semibold text-body\">-$178.29" in said
        # ...and no consent is offered, because a match needs both halves and
        # only one is ticked (``_accept._reject_empty_side``).
        assert _rendered_consent(page) == ("", True)

    def test_a_line_ANOTHER_ACCOUNT_holds_ticks_nothing(
        self, auth_client, second_auth_client, db, seed_user, seed_second_user,
    ):
        """The ownership control, and it is the one that has to hold.

        A crafted ``?line=`` naming someone else's line must neither tick a
        control nor confirm the line exists.  It is refused by MEMBERSHIP: the
        set is derived from this account's own pass, so a foreign id is absent
        for the same reason a claimed one is, through one predicate rather than
        through an ownership branch that could be forgotten.
        """
        theirs = an_unexplained_outflow(seed_second_user, merchant="Geico")
        mine = an_unexplained_outflow(seed_user, merchant="Amazon")
        db.session.commit()

        page = auth_client.get(
            f"{_workbench_url(seed_user['account'].id)}?line={theirs.id}"
        ).data.decode()

        assert self._ticked(page) == []
        # ...and the page still rendered MY pass, so this is a preselection
        # that found nothing rather than a request that failed.
        assert f'name="line_ids" value="{mine.id}"' in page
        # **THE PANEL IS THE OBSERVABLE, and the tick above is not.**  The form
        # loops over ``review.unmatched``, so a foreign id has no control to
        # tick whether or not the route narrowed it -- an assertion about the
        # checkbox alone passes with the whole intersection deleted, which is
        # what a mutation run measured 2026-08-28.  The route hands that set to
        # ``preview_hand_build``, and ``_resolve.load_lines`` refuses an id
        # that names no line on this account -- so an unnarrowed preselection
        # answers with a refusal that DISTINGUISHES a line that exists from one
        # that does not.  This is the assertion that can see it.
        assert _no_refusal(page), (
            "a foreign ?line= reached the pricer, which answers a refusal "
            "that tells the caller whether the line exists"
        )

    def test_a_line_ALREADY_MATCHED_ticks_nothing(
        self, auth_client, db, seed_user,
    ):
        """The stale-link case, which is the ordinary one rather than an attack.

        The owner ticks a line, matches it, and presses Back.  That line is
        no longer in ``review.unmatched``, so it renders no checkbox at all --
        and a preselection that ticked it would be pointing at a control that
        is not on the page.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="-100.00", posted_on=bank_day,
            description="ACH DEBIT NOTHING EXPLAINS THIS",
        )
        row = a_transaction(
            seed_user, name="Ghost Payment", amount="100.00",
            status=StatusEnum.DONE, settled_on=bank_day,
        )
        db.session.commit()
        applied = auth_client.post(
            _workbench_url(seed_user["account"].id),
            data=hand_match(lines=[line], transactions=[row]),
        )
        assert applied.status_code == 200
        assert db.session.query(StatementMatch).count() == 1

        page = auth_client.get(
            f"{_workbench_url(seed_user['account'].id)}?line={line.id}"
        ).data.decode()

        assert self._ticked(page) == []
        assert f'name="line_ids" value="{line.id}"' not in page
        # **THE POSITIVE TAIL**, because everything above is an absence: this
        # fixture matches its only line, so the pick list is EMPTY and nothing
        # here would notice `_ticked` going permanently vacuous.  A second
        # unexplained line, still offered, is what proves the scraper is
        # looking at a real control while the assertions above are made.
        # Named by adversarial test-quality review 2026-08-28.
        still_open = an_unexplained_outflow(seed_user, merchant="Geico")
        db.session.commit()
        page = auth_client.get(
            f"{_workbench_url(seed_user['account'].id)}?line={still_open.id}"
        ).data.decode()
        assert self._ticked(page) == [still_open.id]
        # **And the panel says nothing about it** -- see the sibling case for
        # why this, rather than the tick, is what the narrowing is for.
        # Unnarrowed, this ordinary gesture (tick, match, press Back) renders
        # the workbench under "A statement line you picked is already matched
        # to something else.  Nothing was changed." for an act nobody
        # attempted.  Measured on a clone of the developer's data 2026-08-28.
        assert _no_refusal(page), (
            "a stale ?line= reached the pricer and the page now reports a "
            "refusal the owner did not earn"
        )

    def test_a_value_that_is_not_a_NUMBER_is_answered_rather_than_refused(
        self, auth_client, db, seed_user,
    ):
        """``str.isdigit`` is the wrong predicate and this project owns why.

        It is true for 888 characters, 128 of which make ``int()`` raise
        (:mod:`app.utils.digit_strings`, finding **N-136**) -- and the same
        family put a 500 on the reviewed pass once through ``apply=%C2%B2``
        (:func:`~app.schemas.validation._helpers.order_token_key`).  Here the
        value is never authorised by parsing at all, so what must hold is only
        that it does not raise.
        """
        line = an_unexplained_outflow(seed_user, merchant="Geico")
        db.session.commit()
        base = _workbench_url(seed_user["account"].id)

        for value in ("not-a-number", "\N{SUPERSCRIPT TWO}", "", "-1", "1e3"):
            response = auth_client.get(f"{base}?line={value}")

            assert response.status_code == 200, value
            assert self._ticked(response.data.decode()) == [], value
        # ...and the control renders the real one, so the loop above was run
        # against a page that had something to tick.
        assert self._ticked(
            auth_client.get(f"{base}?line={line.id}").data.decode()
        ) == [line.id]

    def test_TWO_lines_both_arrive_ticked_and_the_panel_sums_them(
        self, auth_client, db, seed_user,
    ):
        """A repeated key is a group, and the panel prices the group.

        Nothing on the queue links two lines at once today, but the argument
        for reading ``getlist`` rather than ``get`` is that a repeated key
        must not silently keep the first: the panel would then price a
        selection different from the one the boxes show.
        """
        first = an_unexplained_outflow(
            seed_user, merchant="Geico", amount="-100.00",
        )
        second = an_unexplained_outflow(
            seed_user, merchant="Amazon", amount="-25.50",
        )
        db.session.commit()

        page = auth_client.get(
            f"{_workbench_url(seed_user['account'].id)}"
            f"?line={first.id}&line={second.id}"
        ).data.decode()

        assert self._ticked(page) == sorted([first.id, second.id])
        # -100.00 + -25.50 = -125.50, summed by the service and not the page.
        assert "-$125.50" in " ".join(page.split())


class TestItRefusesAnotherUsersAccount:
    """Firing controls against an IDOR on a door that MOVES MONEY.

    **THE CONTROL DID NOT TRAVEL WITH THE DOOR, and that is the whole reason
    this class exists.** `TestItRefusesAnotherUsersAccount` in
    ``test_statement_matches.py`` grades the reviewed pass; when plan step
    ``bank_import:X-gf-3b`` gave the hand-built match a write door of its own,
    the new door shipped with **zero** ownership assertions and the full suite
    stayed green. Found by adversarial money review 2026-08-28.

    **A 404 from the URL MAP and a 404 from the OWNERSHIP GATE are
    indistinguishable**, so an ownership case whose route does not exist passes
    for the wrong reason and guards nothing. Every case below is therefore
    PAIRED with a case proving the same URL routes for its own owner --
    ``test_the_routes_exist_for_their_own_owner`` -- because that pairing is
    the only thing that tells the two 404s apart.

    **Nothing here relies on the service.** ``ReviewScope.build`` takes
    ``owner_id`` and ``account_id`` as independent parameters and its own
    docstring says the caller must have PROVED the pairing; ``_resolve
    .load_lines`` filters on ``account_id`` alone. So the route's
    ``load_cash_account_or_404`` is the entire proof, and deleting it is a
    one-line edit that moves another owner's money.
    """

    @pytest.fixture()
    def other_users_account(self, db, seed_user):
        """Return an account id belonging to a DIFFERENT user."""
        stranger = User(
            email="workbenchstranger@shekel.local",
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
        # one the request cannot see. The 404 these tests assert must be the
        # OWNERSHIP gate refusing a real account of someone else's rather than
        # a missing row.
        db.session.commit()
        return account.id

    def test_the_routes_exist_for_their_own_owner(
        self, auth_client, db, seed_user,
    ):
        """THE PAIR for every 404 below, and it is not a formality.

        A typo in a URL, a blueprint that stopped registering, a route deleted
        by a later step: each answers 404 from the URL MAP, which is the same
        404 the ownership gate returns. Without this case the three below would
        all pass against routes that do not exist.
        """
        an_envelope(seed_user)
        an_unexplained_outflow(seed_user, merchant="Geico")
        db.session.commit()
        account_id = seed_user["account"].id

        assert auth_client.get(
            _workbench_url(account_id),
        ).status_code == 200
        assert auth_client.post(
            _workbench_url(account_id), data=hand_match(),
        ).status_code == 200
        assert auth_client.post(
            _totals_url(seed_user), data=hand_match(),
        ).status_code == 200

    def test_the_page_answers_404(self, auth_client, other_users_account):
        """A 403 would confirm the account exists."""
        assert auth_client.get(
            _workbench_url(other_users_account),
        ).status_code == 404

    def test_the_APPLY_door_answers_404_and_writes_NOTHING(
        self, auth_client, db, seed_user, other_users_account,
    ):
        """The write door is decorated independently of the read.

        **The payload names rows this caller really does own**, which is the
        shape the refusal has to survive: without the route's proof, the pass
        would be built from THIS owner's candidates against THAT owner's
        account, and `load_lines` filters on `account_id` alone -- so the
        stranger's bank line would be written onto this owner's rows.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="-100.00", posted_on=bank_day,
            description="ACH DEBIT NOTHING EXPLAINS THIS",
        )
        mine = a_transaction(
            seed_user, name="Ghost Payment", amount="100.00",
            status=StatusEnum.DONE, settled_on=bank_day,
        )
        db.session.commit()
        settled_before = mine.settled_on

        response = auth_client.post(
            _workbench_url(other_users_account),
            data=hand_match(lines=[line], transactions=[mine]),
        )

        assert response.status_code == 404
        assert db.session.query(StatementMatch).count() == 0
        db.session.expire_all()
        assert mine.settled_on == settled_before

    def test_the_TOTALS_endpoint_answers_404(
        self, auth_client, other_users_account,
    ):
        """It writes nothing and still may not be asked.

        It PRICES what it is given, so answering for another owner's account
        would report their figures -- and answering differently for an account
        that exists than for one that does not is an oracle either way.
        """
        assert auth_client.post(
            f"/accounts/{other_users_account}/statements/match/totals",
            data=hand_match(),
        ).status_code == 404


class TestWhatTheProposalsTakeOutOfBothLists:
    """The FIFTH bound, and the only one that is not a `ReviewBounds` field.

    A line this pass PROPOSES a match for is dropped by `_reads._unexplained`
    before ``unmatched`` exists, and a row one names is dropped by
    `_rows_the_bank_never_showed` -- so both pick lists are shorter than their
    captions, in a way the *what is not in these lists* panel could not see
    because it was reading `ReviewBounds` alone. Found by adversarial design
    review 2026-08-28; the developer's own statement carries **124** proposals,
    so this is a large absence rather than a corner.

    **It matters most when the proposal is WRONG.** A proposal is a suggestion
    nobody has accepted, so a badly paired line is absent from the very tool for
    pairing it correctly -- and while this form stood on the review screen the
    proposal card was beside it, which is what makes this the split's own debt.
    """

    @staticmethod
    def _proposed_pair(seed_user, db):
        """Stage TWO lines the proposer will pair with two rows.

        Equal figures on the same day is the EXACT tier's own shape, so this
        arranges proposals rather than asserting some exist.

        **TWO of each, and not one, because one cannot tell the counts
        apart.** With a single proposal, ``len(self.proposals)`` and the count
        of distinct LINES are both 1 -- so a mutation replacing the subject
        count with the proposal count SURVIVED the first version of these
        cases. Measured 2026-08-28. Two proposals over two lines still would
        not discriminate; what does is asserting the pair (2, 2) against a
        derivation that could return either, so both counts are read.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        lines, rows = [], []
        for index, amount in enumerate(("77.77", "88.88")):
            lines.append(a_bank_line(
                seed_user, statement, amount=f"-{amount}", posted_on=bank_day,
                description=f"ACH DEBIT THE PROPOSER WILL PAIR THIS {index}",
            ))
            rows.append(a_transaction(
                seed_user, name=f"Paired Bill {index}", amount=amount,
                status=StatusEnum.DONE, settled_on=bank_day,
            ))
        db.session.commit()
        return lines, rows

    def test_a_PROPOSED_line_and_row_are_absent_from_both_pick_lists(
        self, auth_client, db, seed_user,
    ):
        """The absence itself, asserted before the panel that reports it.

        A panel naming an absence that is not real would be worse than one
        naming none, so this grades the fact and the sentence separately.
        """
        lines, _rows = self._proposed_pair(seed_user, db)

        page = auth_client.get(
            _workbench_url(seed_user["account"].id),
        ).data.decode()

        for line in lines:
            assert f'name="line_ids" value="{line.id}"' not in page
        assert "Paired Bill" not in page

    def test_the_panel_SAYS_SO_and_names_both_counts(
        self, auth_client, db, seed_user,
    ):
        """The bullet, with a link to the screen holding the decision.

        **The counts come from the SERVICE** (`ReviewSet
        .explained_by_a_proposal`), because a caption may not promise a number
        a template computed.
        """
        self._proposed_pair(seed_user, db)

        body = " ".join(auth_client.get(
            _workbench_url(seed_user["account"].id),
        ).data.decode().split())

        assert "What is not in these lists" in body
        assert (
            "2 line(s) and 2 row(s) are not here because the review screen "
            "is already"
        ) in body
        assert f"/accounts/{seed_user['account'].id}/statements/review" in body

    def test_with_NO_proposal_the_bullet_is_absent(
        self, auth_client, db, seed_user,
    ):
        """THE FIRING CONTROL for both cases above.

        A bullet rendered unconditionally would satisfy them while saying
        `0 line(s) and 0 row(s)`, which is a panel reporting an absence that is
        not one -- the exact shape this whole panel exists to avoid.
        """
        an_unexplained_outflow(seed_user, merchant="Geico")
        db.session.commit()

        body = " ".join(auth_client.get(
            _workbench_url(seed_user["account"].id),
        ).data.decode().split())

        assert "are not here because the review screen is already" not in body
        assert "0 line(s) and 0 row(s)" not in body


class TestAnEmptyConsentIsUntouchedRatherThanMalformed:
    """`hand_match_payload`'s own founding principle, which nothing graded.

    A browser submits every control it renders, so an untouched one must be
    recognisable as untouched. The panel renders the consent box ``value=""``
    and ``disabled`` in lockstep, so a browser cannot send an empty one -- but a
    body that does must not 400 the whole act over a field nobody filled in.

    **The control did not travel with the reader.** The equivalent arm on
    ``batch_payload`` is covered (``tests/test_schemas/test_statement_batch.py``
    's own consent cases); when plan step ``bank_import:X-gf-3b`` gave the
    workbench ``hand_match_payload``, the `if residual:` line shipped with
    nothing behind it -- mutating it to `if residual is not None:` survived
    **1258** tests. Found by adversarial test-quality review 2026-08-28.
    """

    @staticmethod
    def _a_group(seed_user, db):
        """Stage one line and one row that agree, so no consent is wanted."""
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="-64.00", posted_on=bank_day,
            description="ACH DEBIT NOTHING EXPLAINS THIS",
        )
        row = a_transaction(
            seed_user, name="Ghost Payment", amount="64.00",
            status=StatusEnum.DONE, settled_on=bank_day,
        )
        db.session.commit()
        return line, row

    def test_the_APPLY_door_takes_an_empty_consent_and_records(
        self, auth_client, db, seed_user,
    ):
        """`residual=""` is an untouched box, not a malformed figure."""
        line, row = self._a_group(seed_user, db)
        payload = hand_match(lines=[line], transactions=[row])
        payload["residual"] = [""]

        response = auth_client.post(
            _workbench_url(seed_user["account"].id), data=payload,
        )

        assert response.status_code == 200
        assert db.session.query(StatementMatch).count() == 1

    def test_the_TOTALS_endpoint_takes_one_too(
        self, auth_client, db, seed_user,
    ):
        """The same body reaches both doors, so both must read it alike.

        A panel that 400s on the body its own form submits is the shape the
        review screen already paid for: an owner presses a control and the
        page reports a failure nobody caused.
        """
        line, row = self._a_group(seed_user, db)
        payload = hand_match(lines=[line], transactions=[row])
        payload["residual"] = [""]

        response = auth_client.post(_totals_url(seed_user), data=payload)

        assert response.status_code == 200

    def test_a_MALFORMED_consent_is_still_refused(
        self, auth_client, db, seed_user,
    ):
        """THE FIRING CONTROL: dropping the field entirely is not the fix.

        `hand_match_payload` could satisfy both cases above by never passing
        `residual` on at all, which would take `ReviewedFigureField`'s whole
        strictness with it -- and that field's own docstring records what a lax
        reader cost: `"0.054"` silently REPAIRED into agreement by
        ROUND_HALF_EVEN, on the one figure the design says must be exact.
        """
        line, row = self._a_group(seed_user, db)
        payload = hand_match(lines=[line], transactions=[row])
        payload["residual"] = ["NaN"]

        response = auth_client.post(
            _workbench_url(seed_user["account"].id), data=payload,
        )

        assert response.status_code == 400
        assert db.session.query(StatementMatch).count() == 0


class TestAGroupProposalTakesSEVERALRowsOutOfTheList:
    """The `rows` half of `explained_by_a_proposal`, which counts SUBJECTS.

    **The two halves are not equally falsifiable, and saying so is the point.**
    Every proposal this app builds names exactly ONE line -- `_propose` at both
    its sites and `_near` at its own all construct `lines=(line,)` -- so
    counting distinct line ids and counting proposals give the same number for
    every input that exists, and a mutation swapping one for the other is an
    EQUIVALENT mutant rather than a blind control. It survives, and it should:
    no case can kill it without a multi-line tier to arrange.

    The ROWS half is different. `_propose._groups` builds `rows=combo`, so one
    proposal can name several rows -- and this case is what makes the subject
    count checkable rather than merely correct-looking.
    """

    def test_one_proposal_over_two_rows_reports_TWO_rows(
        self, auth_client, db, seed_user,
    ):
        """A group match: one bank line, two rows that sum to it.

        `1 line(s) and 2 row(s)` is a sentence the proposal COUNT cannot
        produce, which is the whole reason this case exists beside its sibling.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        a_bank_line(
            seed_user, statement, amount="-100.00", posted_on=bank_day,
            description="ACH DEBIT ONE LINE TWO ROWS",
        )
        for name, amount in (("Half A", "60.00"), ("Half B", "40.00")):
            a_transaction(
                seed_user, name=name, amount=amount,
                status=StatusEnum.DONE, settled_on=bank_day,
            )
        db.session.commit()

        body = " ".join(auth_client.get(
            _workbench_url(seed_user["account"].id),
        ).data.decode().split())

        assert (
            "1 line(s) and 2 row(s) are not here because the review screen "
            "is already"
        ) in body




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

        body = auth_client.get(_workbench_url(account.id)).data.decode()

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
