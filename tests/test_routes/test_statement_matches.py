"""The review screen, and the two POSTs that accept and release a match.

Plan step **bank_import:X-f6a-2**.  The route's own subjects, none of which the
service tests can see: OWNERSHIP (the security response rule's 404 for both
"not found" and "not yours"), the FORM PAYLOAD, the unit of work, and the FLASH
that tells the user what moved.

**The multi-value form test is the one that would otherwise ship broken.**  A
GROUP match posts the same field name several times, and ``request.form["k"]``
returns only the FIRST of them -- so a schema handed the raw ``MultiDict``
refuses a two-row group as "not a valid list".  No service test can see that:
they pass real lists.  It was found by mutating a real browser payload through
this route, and it is pinned here because the failure is total in a browser and
invisible everywhere else.

**The ownership tests are firing controls against an IDOR** on a door that
MOVES MONEY: a route answering for another user's account would let one user
re-date another's records.  All three doors are tested, because the decorators
are applied independently and one being right proves nothing about the others.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.enums import StatusEnum
from app.models.account import Account
from app.models.statement_match import StatementMatch, StatementMatchMember
from app.models.user import User, UserSettings
from app.services import auth_service
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_transaction,
    an_import,
)


def _review_url(account_id):
    """Return the review page's URL for *account_id*."""
    return f"/accounts/{account_id}/statements/review"


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

    def test_it_says_the_page_changes_records(self, auth_client, seed_user):
        """This screen MOVES MONEY and, unlike its sibling, says so."""
        response = auth_client.get(_review_url(seed_user["account"].id))

        assert b"Accepting a match changes your records" in response.data

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
            data={"line_ids": [line.id], "transaction_ids": [txn.id]},
            follow_redirects=True,
        )

        assert response.status_code == 200
        assert b"onto the bank&#39;s day" in response.data
        db.session.expire_all()
        assert txn.settled_on == bank_day

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
            data={
                "line_ids": [line.id],
                "transaction_ids": [salary.id, allowance.id],
            },
            follow_redirects=True,
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
            data={
                "line_ids": [line.id],
                "transaction_ids": [salary.id, allowance.id],
            },
            follow_redirects=True,
        )

        assert b"do not add up" in response.data
        db.session.expire_all()
        assert salary.settled_on is None
        assert allowance.settled_on is None
        assert db.session.query(StatementMatch).count() == 0

    def test_a_lax_id_spelling_is_refused(self, auth_client, db, seed_user):
        """``RowId``, not ``fields.Integer``: '007' names no row (N-141)."""
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement)
        db.session.commit()

        response = auth_client.post(
            _review_url(seed_user["account"].id),
            data={"line_ids": [str(line.id)], "transaction_ids": ["007"]},
            follow_redirects=True,
        )

        assert db.session.query(StatementMatch).count() == 0
        assert b"onto the bank" not in response.data


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
        assert b'name="line_ids"' in response.data
        assert b'name="transaction_ids"' in response.data

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
            data={
                "line_ids": [line.id],
                "transaction_ids": [salary.id, allowance.id],
            },
            follow_redirects=True,
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
            data={
                "line_ids": [line.id],
                "transaction_ids": [salary.id, allowance.id],
            },
            follow_redirects=True,
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
            data={"line_ids": [line.id], "transaction_ids": [txn.id]},
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
            data={"line_ids": [1], "transaction_ids": [1]},
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
