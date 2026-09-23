"""An anchor save answers with the display of the screen that opened it.

Finding **CC-365**; rulings **R-CC74** and **R-CC77** (developer, 2026-09-23).
The anchor editor opens from five screens -- the grid cell, the dashboard hero,
the Net Worth Cockpit's per-card cell, the investment / retirement hero and the
cash detail hero -- and its Cancel has always drawn each screen's own display.
Its SAVE did not: it answered with the grid's cell whichever screen opened it,
until ``balanceChanged`` re-rendered the screen's region one round trip later.
Since plan step credit_card:CC-5-5c the cockpit tile shows what a debt OWES,
so a card owing $1,200.00 saved from /savings showed ``-$1,200.00`` -- the
grid's held figure -- for that round trip.

R-CC77's shape is ONE table (``accounts.anchor._SURFACES``) naming each
screen's Cancel page and its draw function, a draw each screen's route module
owns and both its Cancel GET and the save call.  So the property graded here is
the ONE-function property itself: for every screen, the save's primary fragment
is byte-identical to that screen's Cancel GET for the same account after the
save.  Two draws that merely agreed today would pass it too, which is why the
table, not this test, is what makes them one; what this test catches is a save
that answers with anything else.

**Every save posts what the rendered editor emits** -- its ``hx-patch`` URL
(carrying the ``revert`` token), and every named control: the balance, the
statement day, and on a liability the hidden ``asks_owed`` -- and reads its
Cancel URL off the rendered Cancel button, so the screen-to-display map is
exercised end to end rather than restated here.  Each save MOVES the governing
balance and leaves nothing outstanding, so neither the acknowledgement nor the
reconcile prompt rides along, and the whole body is the primary fragment plus,
on the grid alone, the out-of-band "as of" caption.
"""

import re
from decimal import Decimal
from html.parser import HTMLParser

import pytest

from app import ref_cache
from app.enums import EmployerContributionTypeEnum
from app.extensions import db
from app.models.account import Account
from app.models.investment_params import InvestmentParams
from app.services import cash_ledger
from tests._test_helpers import create_account_of_type

#: What an htmx request carries; the cell GETs redirect without it.
_HX = {"HX-Request": "true"}

#: The grid's out-of-band caption, the one fragment a grid save adds after its
#: primary one (``grid/_anchor_as_of_oob.html``).
_AS_OF_OOB = '<small class="text-muted" id="anchor-as-of" hx-swap-oob="true">'


class _EditorForm(HTMLParser):
    """What the rendered anchor editor would submit, where to, and its Cancel.

    A form submits every control it renders, so a save here posts exactly
    :attr:`fields` to :attr:`patch_url`; Cancel is the Cancel button's
    ``hx-get``, which is how the browser reaches the screen's display.
    """

    def __init__(self):
        """Start with nothing collected."""
        super().__init__()
        self.patch_url = None
        self.cancel_url = None
        self.fields = {}

    def handle_starttag(self, tag, attrs):
        """Record the form's PATCH target, each named input, and Cancel's GET."""
        attributes = dict(attrs)
        if tag == "form":
            self.patch_url = attributes.get("hx-patch")
        elif tag == "input" and attributes.get("name"):
            self.fields[attributes["name"]] = attributes.get("value") or ""
        elif tag == "button" and attributes.get("type") == "button":
            self.cancel_url = attributes.get("hx-get")


def _open_editor(client, account_id, revert):
    """Open the editor as the screen named by *revert* opens it, and parse it."""
    query = "" if revert is None else f"?revert={revert}"
    resp = client.get(
        f"/accounts/{account_id}/anchor-form{query}", headers=_HX,
    )
    assert resp.status_code == 200, resp.data[:300]
    form = _EditorForm()
    form.feed(resp.data.decode())
    assert form.patch_url is not None, "the editor rendered no hx-patch"
    assert form.cancel_url is not None, "the editor rendered no Cancel"
    return form


def _save(client, form, typed):
    """Press Save having typed *typed*: every emitted control, posted back."""
    return client.patch(
        form.patch_url, data={**form.fields, "anchor_balance": typed},
        headers=_HX,
    )


def _governing(account_id):
    """The governing assertion's HELD balance, read fresh from the table."""
    db.session.expire_all()
    return cash_ledger.resolve_anchor(db.session.get(Account, account_id)).balance


def _checking(seed_user):
    """The seed user's Checking account (``$1,000.00``, nothing transacted)."""
    return seed_user["account"].id


def _card(seed_user):
    """A committed Credit Card owing ``$1,000.00`` (held ``-1,000.00``)."""
    card = create_account_of_type(
        seed_user, db.session, "Credit Card", "Visa",
        anchor_balance=Decimal("-1000.00"),
    )
    db.session.commit()
    return card.id


def _retirement(seed_user):
    """A committed 401(k) holding ``$50,000.00``, with its growth params."""
    account = create_account_of_type(
        seed_user, db.session, "401(k)", "My 401k",
        anchor_balance=Decimal("50000.00"),
    )
    db.session.add(InvestmentParams(
        account_id=account.id,
        assumed_annual_return=Decimal("0.07000"),
        annual_contribution_limit=Decimal("23500.00"),
        contribution_limit_year=2026,
        employer_contribution_type_id=ref_cache.employer_contribution_type_id(
            EmployerContributionTypeEnum.NONE,
        ),
    ))
    db.session.commit()
    return account.id


def _cockpit_cell(html, account_id):
    """Cut one account's cockpit balance cell out of *html*, or fail."""
    match = re.search(
        rf'<div id="acct-balance-{account_id}"(.*?)</div>', html, re.S,
    )
    assert match is not None, f"no cockpit cell for account {account_id}"
    return match.group(0)


class TestTheSaveAnswersWithTheOpenersDisplay:
    """R-CC74: the save's primary fragment IS the opener's Cancel display."""

    @pytest.mark.parametrize(
        ("revert", "build", "typed", "held", "marker", "shows"),
        [
            pytest.param(
                None, _checking, "1234.56", Decimal("1234.56"),
                'id="anchor-display"', "$1,235", id="grid",
            ),
            pytest.param(
                "dashboard", _checking, "1234.56", Decimal("1234.56"),
                'id="balance-display"', "$1,234.56", id="dashboard",
            ),
            pytest.param(
                "accounts", _card, "1200.00", Decimal("-1200.00"),
                'id="acct-balance-', "$1,200.00", id="cockpit-card",
            ),
            pytest.param(
                "accounts", _checking, "1234.56", Decimal("1234.56"),
                'id="acct-balance-', "$1,234.56", id="cockpit-checking",
            ),
            # The hero reads the MODELLED balance, which accrues growth past
            # the assertion to the period's end (ruling R-Y), so it shows no
            # figure this case can state by hand; the byte identity with the
            # GET is the whole claim.
            pytest.param(
                "investment", _retirement, "51234.56", Decimal("51234.56"),
                'id="investment-balance-hero"', None, id="investment",
            ),
            pytest.param(
                "cash", _checking, "1234.56", Decimal("1234.56"),
                'id="cash-balance-hero"', "$1,234.56", id="cash",
            ),
            pytest.param(
                "cash", _card, "1200.00", Decimal("-1200.00"),
                'id="cash-balance-hero"', "-$1,200.00", id="cash-card",
            ),
        ],
    )
    def test_the_save_is_byte_identical_to_cancel_after_it(
        self, app, auth_client, seed_user, seed_periods_today,
        revert, build, typed, held, marker, shows,
    ):  # pylint: disable=too-many-arguments,unused-argument
        """Save from a screen, then Cancel from the same editor: one fragment.

        The Cancel GET runs AFTER the save, so both draw the post-write state;
        a save answering with any other screen's display -- the grid's cell was
        the defect -- fails the prefix check.  The cash detail page shows a
        card's HELD balance (its rows are summed in it), so ``-$1,200.00`` is
        that screen's own figure, and the cockpit's is what the card owes.
        """
        with app.app_context():
            account_id = build(seed_user)
            form = _open_editor(auth_client, account_id, revert)

            resp = _save(auth_client, form, typed)

            assert resp.status_code == 200, resp.data[:300]
            assert resp.headers.get("HX-Trigger") == "balanceChanged"
            assert _governing(account_id) == held

            cancel = auth_client.get(form.cancel_url, headers=_HX)
            assert cancel.status_code == 200
            drawn = cancel.data.decode()
            assert marker in drawn, "the Cancel GET drew another screen"
            if shows is not None:
                assert shows in drawn

            body = resp.data.decode()
            assert body.startswith(drawn), (
                "the save's primary fragment is not its screen's display"
            )
            tail = body[len(drawn):]
            if revert is None:
                # The caption template opens on a newline after its comment.
                assert tail.lstrip().startswith(_AS_OF_OOB)
            else:
                assert tail == ""

    def test_a_card_saved_from_savings_shows_what_it_owes(
        self, app, auth_client, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """CC-365 itself: 1,200.00 owed saved from /savings reads ``$1,200.00``.

        A Visa owing ``$1,000.00`` (held ``-1,000.00``) is saved from its
        cockpit tile at ``1,200.00`` owed, which the door stores held as
        ``-1,200.00``.  The tile shows what a debt OWES (ruling R-CC47), so the
        answer's cell reads ``$1,200.00``; the grid's held cell, which the save
        answered with before, read ``-$1,200`` (whole dollars).  The absence is
        checked on the PREFIX ``-$1,200`` because that grid cell prints no
        cents: asserting ``-$1,200.00`` absent would pass on the defect.
        """
        with app.app_context():
            card_id = _card(seed_user)
            form = _open_editor(auth_client, card_id, "accounts")
            # The form asks what is owed, and says so in what it submits.
            assert form.fields.get("asks_owed") == "true"
            assert form.fields.get("anchor_balance") == "1000.00"

            resp = _save(auth_client, form, "1200.00")

            assert resp.status_code == 200
            assert _governing(card_id) == Decimal("-1200.00")
            body = resp.data.decode()
            cell = _cockpit_cell(body, card_id)
            assert "$1,200.00" in cell
            assert "-$1,200" not in cell
            # Nothing else on the response shows the held figure either.
            assert "-$1,200" not in body


class TestAScreenThatNoLongerShowsTheAccount:
    """R-CC77: a save after a mid-edit archive commits and draws no cell."""

    @pytest.mark.parametrize(
        ("revert", "build", "typed", "held"),
        [
            pytest.param(
                "accounts", _card, "1200.00", Decimal("-1200.00"),
                id="cockpit",
            ),
            pytest.param(
                "investment", _retirement, "51234.56", Decimal("51234.56"),
                id="investment",
            ),
        ],
    )
    def test_the_save_commits_and_answers_an_empty_cell(
        self, app, auth_client, seed_user, seed_periods_today,
        revert, build, typed, held,
    ):  # pylint: disable=too-many-arguments,unused-argument
        """Open the editor, archive the account in another tab, press Save.

        The /savings and investment draws read the owner's ACTIVE accounts, so
        they find none and answer ``None``.  The write has already committed,
        so the save answers 200 with an empty body -- never a 404 over a
        committed save -- and the Cancel from the same editor, reading the
        same draw, is that screen's 404.
        """
        with app.app_context():
            account_id = build(seed_user)
            form = _open_editor(auth_client, account_id, revert)
            archived = auth_client.post(f"/accounts/{account_id}/archive")
            assert archived.status_code == 302
            db.session.expire_all()
            assert db.session.get(Account, account_id).is_active is False

            resp = _save(auth_client, form, typed)

            assert resp.status_code == 200, resp.data[:300]
            assert resp.headers.get("HX-Trigger") == "balanceChanged"
            assert resp.data == b""
            assert _governing(account_id) == held
            cancel = auth_client.get(form.cancel_url, headers=_HX)
            assert cancel.status_code == 404


class TestTheCellGetsKeepTheirGates:
    """Each Cancel GET routes for its owner and 404s for anyone else.

    Paired, because a 404 from the URL map and a 404 from the ownership gate
    look identical: the owner's 200 is what proves the URL still routes, so
    the other user's 404 is the gate's.
    """

    @pytest.mark.parametrize(
        ("path", "build", "marker"),
        [
            pytest.param(
                "/accounts/{id}/anchor-display", _checking,
                'id="anchor-display"', id="grid",
            ),
            pytest.param(
                "/savings/cockpit/{id}/balance", _card,
                'id="acct-balance-{id}"', id="cockpit",
            ),
            pytest.param(
                "/accounts/{id}/investment/balance-hero", _retirement,
                'id="investment-balance-hero"', id="investment",
            ),
            pytest.param(
                "/accounts/{id}/details/balance-hero", _checking,
                'id="cash-balance-hero"', id="cash",
            ),
        ],
    )
    def test_the_owner_is_served_and_another_user_is_not(
        self, app, auth_client, seed_user, seed_second_user,
        seed_periods_today, path, build, marker,
    ):  # pylint: disable=too-many-arguments,unused-argument
        """The owner's account draws its cell; the second user's is a 404."""
        with app.app_context():
            own = build(seed_user)
            resp = auth_client.get(path.format(id=own), headers=_HX)
            assert resp.status_code == 200
            assert marker.format(id=own) in resp.data.decode()

            other = seed_second_user["account"]
            resp = auth_client.get(path.format(id=other.id), headers=_HX)
            assert resp.status_code == 404
            assert other.name.encode() not in resp.data
