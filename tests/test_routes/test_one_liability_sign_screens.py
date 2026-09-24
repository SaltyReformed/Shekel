"""Route tests for plan step credit_card:CC-5-5c -- what /savings renders.

The service figures are pinned in
``tests/test_services/test_one_liability_sign.py``; this module reads the
RENDERED page, because three of this step's rulings are words on the screen:
a liability tile shows what it owes (ruling **R-CC47**), an archived debt is
captioned "Owed" (ruling **R-CC67**), and the footer names the debt the payoff
date leaves out "with no payoff date" (ruling **R-CC68**, ledger row CC-361).
A configured loan's own page is here too: R-CC47 names "every loan screen".
"""

import re
from datetime import date
from decimal import Decimal

from tests._test_helpers import create_account_of_type, create_loan_account


def _tile(html, account_id):
    """Return the rendered cockpit balance cell of one account."""
    match = re.search(
        rf'<div id="acct-balance-{account_id}"(.*?)</div>', html, re.S,
    )
    assert match is not None, f"no balance cell for account {account_id}"
    return match.group(0)


class TestTheSavingsPageSpeaksOwedForADebt:
    """The cockpit tile, the archived drawer and the footer, as rendered."""

    def test_a_card_tile_shows_what_it_owes(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """A Visa holding ``-1,000.00`` renders ``$1,000.00``, labelled owed.

        Its figure carries no negative ink (it is not negative in the words it
        is shown in) and still carries the liability ink, keyed on category.
        """
        with app.app_context():
            visa = create_account_of_type(
                seed_user, db.session, "Credit Card", "Visa",
                anchor_balance=Decimal("-1000.00"),
            )
            db.session.commit()

            html = auth_client.get("/savings").data.decode()

            cell = _tile(html, visa.id)
            assert "$1,000.00" in cell
            assert "-$1,000.00" not in cell
            assert "Visa owed: $1,000." in cell
            assert "acct-card__num--neg" not in cell
            assert "acct-card__num--liability" in cell

    def test_the_footer_names_debt_with_no_payoff_date(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """R-CC68: the footer reads ``excludes $6,000.00 with no payoff date``.

        A Visa owing ``$1,000``, an Amex holding a ``$50`` credit (floored to
        nothing, R-CC49) and an auto loan with no terms owing ``$5,000``,
        beside a configured loan whose payoff date the caption qualifies:
        ``1,000.00 + 0.00 + 5,000.00 = 6,000.00``.
        """
        with app.app_context():
            create_loan_account(
                seed_user, db.session, name="Car Loan",
                principal=Decimal("12000.00"), rate=Decimal("0.05000"),
                term=24, origination_date=date(2026, 1, 1),
            )
            create_account_of_type(
                seed_user, db.session, "Credit Card", "Visa",
                anchor_balance=Decimal("-1000.00"),
            )
            create_account_of_type(
                seed_user, db.session, "Credit Card", "Amex",
                anchor_balance=Decimal("50.00"),
            )
            create_account_of_type(
                seed_user, db.session, "Auto Loan", "Auto loan, no terms",
                anchor_balance=Decimal("-5000.00"),
            )
            db.session.commit()

            html = auth_client.get("/savings").data.decode()

            text = re.sub(r"<[^>]+>", "", html)
            assert re.search(
                r"excludes\s+\$6,000\.00\s+with no payoff date", text,
            ), "the footer does not name the debt with no payoff date"
            assert "revolving" not in text

    def test_an_archived_debt_is_captioned_owed(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """An archived Visa owing ``$1,000.00`` reads ``Owed $1,000.00``."""
        with app.app_context():
            visa = create_account_of_type(
                seed_user, db.session, "Credit Card", "Visa",
                anchor_balance=Decimal("-1000.00"),
            )
            visa.is_active = False
            db.session.commit()

            html = auth_client.get("/savings").data.decode()

            drawer = html[html.index('id="archivedAccounts"'):]
            text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", drawer))
            assert "Owed $1,000.00" in text
            assert "Last Balance" not in text


def _car_loan(seed_user, session):
    """Create the never-paid configured loan both pins below read.

    $12,000.00 at 5% over 24 months, originated 2026-01-01, and no payment is
    ever recorded against it.  Its balance today is the ledger's: the opening
    it was originated at, untouched, because an installment nobody paid pays
    nothing down (finding B-9).  So on whatever day this runs it HOLDS
    ``-12,000.00`` (ruling R-CC47, plan step credit_card:CC-5-5c) and owes
    ``owed(-12,000.00) = 12,000.00``.
    """
    return create_loan_account(
        seed_user, session, name="Car Loan",
        principal=Decimal("12000.00"), rate=Decimal("0.05000"),
        term=24, origination_date=date(2026, 1, 1),
    )


class TestAConfiguredLoanShowsWhatItOwes:
    """R-CC47: "every loan screen and liability tile shows what is owed".

    The seam reports a configured loan HELD since plan step
    credit_card:CC-5-5c, so each loan surface crosses the figure once --
    ``_RouteLoanContext.current_owed`` for the loan's own page, and
    ``AccountProjection.shown_balance`` for its /savings tile.  A figure
    matcher that strips the sign reads ``$12,000.00`` out of a regressed
    ``-$12,000.00`` too, so each pin below asserts the leading minus is ABSENT
    as its own line, on the one element that shows the figure.
    """

    def test_the_loan_page_hero_shows_what_the_loan_owes(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """``/accounts/<id>/loan`` renders ``$12,000.00`` owed, no minus.

        Hand-computed from :func:`_car_loan`: held ``-12,000.00``, owed
        ``12,000.00``.  The hero is ``#loan-balance-hero``; its figure and its
        ``aria-label`` (the same figure without cents) both render through the
        ``money`` macro, which prints a negative as ``-$``.
        """
        with app.app_context():
            loan = _car_loan(seed_user, db.session)
            db.session.commit()

            resp = auth_client.get(f"/accounts/{loan.id}/loan")
            assert resp.status_code == 200
            html = resp.data.decode()

            match = re.search(
                r'<div id="loan-balance-hero"(.*?)</div>', html, re.S,
            )
            assert match is not None, "no balance hero on the loan page"
            hero = match.group(0)
            # The figure, then the minus a regressed page would print before
            # it -- the first line alone passes on ``-$12,000.00`` too.
            assert "$12,000.00" in hero
            assert "-$" not in hero
            assert "Car Loan balance owed: $12,000." in hero

    def test_the_savings_tile_shows_what_the_loan_owes(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """The loan's /savings tile renders ``$12,000.00``, no minus, no neg ink.

        A configured loan's tile is the read-only branch of
        ``savings/_cockpit_balance.html``, which carries no id, so it is found
        by the cell's own name link (``/accounts/<id>/loan``) and cut at the
        cell's ``acct-cell__balance`` wrapper.  Hand-computed from
        :func:`_car_loan`: held ``-12,000.00``, shown ``12,000.00``.  The
        liability ink is keyed on the category and stays; the negative ink is
        keyed on the shown figure's sign and must not appear.
        """
        with app.app_context():
            loan = _car_loan(seed_user, db.session)
            db.session.commit()

            html = auth_client.get("/savings").data.decode()

            # The CELL's own name link, not any other link to the loan page:
            # the lazy span below must not run on into another account's cell.
            match = re.search(
                rf'href="/accounts/{loan.id}/loan"\s+'
                r'class="stretched-link acct-cell__link">[\s\S]*?'
                r'<div class="acct-cell__balance">([\s\S]*?)</div>',
                html,
            )
            assert match is not None, "no balance cell for the loan's tile"
            tile = match.group(1)
            # The figure, then the minus a regressed tile would print before
            # it -- the first line alone passes on ``-$12,000.00`` too.
            assert "$12,000.00" in tile
            assert "-$" not in tile
            assert "acct-card__num--neg" not in tile
            assert "acct-card__num--liability" in tile
