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

**The ownership tests are firing controls against an IDOR** on doors that MOVE
MONEY: a route answering for another user's account would let one user re-date
another's records, and -- since plan step **bank_import:X-f6a-3b** -- mint
budget rows in their periods.  All FOUR doors are tested, because the
decorators are applied independently and three being right proves nothing about
the fourth.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.enums import StatusEnum
from app.models.account import Account
from app.models.statement_match import StatementMatch, StatementMatchMember
from app.models.transaction import Transaction
from app.models.user import User, UserSettings
from app.services import auth_service
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_purchase,
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

    def test_the_purchase_door_answers_404(
        self, auth_client, db, other_users_account,
    ):
        """The FOURTH decorator, and the one that CREATES budget rows.

        Plan step **bank_import:X-f6a-3b**.  Three doors being right proves
        nothing about a fourth, and this one can mint a transaction and a
        purchase -- so an un-decorated route would let one user grow another's
        budget from their own statement.
        """
        response = auth_client.post(
            f"{_review_url(other_users_account)}/purchase",
            data={"line_id": 1, "transaction_id": 1},
        )

        assert response.status_code == 404
        assert db.session.query(StatementMatch).count() == 0


class TestThePurchasePost:
    """The create door end to end -- plan step **bank_import:X-f6a-3b**.

    The route's own subjects, none of which the service test can see: the
    destination ``<select>``'s EMPTY value meaning "a new envelope", the
    ownership decorator on a third door, and the flash that says what was
    recorded and where.
    """

    @staticmethod
    def _purchase_url(account_id):
        """Return the create door's URL for *account_id*."""
        return f"{_review_url(account_id)}/purchase"

    @staticmethod
    def _an_open_envelope(seed_user):
        """Return a Projected envelope a purchase may join."""
        return a_transaction(
            seed_user, name="Groceries", amount="500.00", is_envelope=True,
        )

    def test_the_page_offers_the_line_and_a_destination(
        self, auth_client, db, seed_user,
    ):
        """The card is what makes the door reachable at all.

        Without it the create door fires only on a crafted POST, and the 74
        card swipes the step exists for are never put in front of the person
        who can record them -- which is the same defect the hand-build form was
        added to fix one leaf earlier.
        """
        statement = an_import(seed_user)
        a_bank_line(
            seed_user, statement, amount="-57.96",
            posted_on=seed_user["bootstrap_period"].start_date,
            description="POINT OF SALE DEBIT L340 WAL-MART (Walmart)",
        )
        self._an_open_envelope(seed_user)
        db.session.commit()

        response = auth_client.get(_review_url(seed_user["account"].id))

        assert response.status_code == 200
        assert b"a purchase you never recorded" in response.data
        assert b"-- a new envelope --" in response.data
        # The name box is prefilled with the MERCHANT, not the whole line.
        assert b'value="Walmart"' in response.data

    def test_it_records_into_an_existing_envelope_and_says_where(
        self, auth_client, db, seed_user,
    ):
        """The RENDERED payload, not a hand-picked subset of it.

        **This posted only ``line_id`` and ``transaction_id`` and shipped a
        dead arm.**  Every control in the form is submitted on every POST: the
        name box is always rendered and always prefilled from the merchant, and
        the category select has no empty option.  Keying the arm on
        ``envelope_name is not None`` therefore named BOTH destinations on every
        real submission and the door refused all of them -- 66 of the
        developer's 91 creatable lines, on the first click, with no sequence of
        interactions that reached the arm at all.  Three independent adversarial
        reviews found it on 2026-08-19; this file's own module docstring
        describes the identical class one leaf earlier.

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
            self._purchase_url(seed_user["account"].id),
            data={
                "line_id": line.id,
                "transaction_id": envelope.id,
                # The two the browser sends whatever the owner picked.
                "envelope_name": "Walmart",
                "category_id": seed_user["categories"]["Groceries"].id,
            },
            follow_redirects=True,
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

    def test_an_EMPTY_destination_select_means_a_new_envelope(
        self, auth_client, db, seed_user,
    ):
        """The browser submits ``""`` for the "a new envelope" option.

        ``RowId`` reads an empty string as a validation error rather than as
        "absent", so without the schema's ``@pre_load`` the whole arm 400s on
        the ordinary path -- the failure is total in a browser and invisible to
        a service test, which passes ``None``.
        """
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(seed_user, statement, amount="-31.41",
                           posted_on=day, description="LOWES #00907 (Lowe's)")
        db.session.commit()

        response = auth_client.post(
            self._purchase_url(seed_user["account"].id),
            data={
                "line_id": line.id,
                "transaction_id": "",
                "envelope_name": "Lowe's",
                "category_id": seed_user["categories"]["Groceries"].id,
            },
            follow_redirects=True,
        )

        assert response.status_code == 200
        assert b"as a purchase in a new envelope, Lowe" in response.data
        db.session.expire_all()
        created = db.session.query(Transaction).filter(
            Transaction.name == "Lowe's",
        ).one()
        assert created.estimated_amount == Decimal("0.00")

    def test_a_new_envelope_missing_its_CATEGORY_is_refused(
        self, auth_client, db, seed_user,
    ):
        """A budget line with no category is invisible to every spending report.

        Both-or-neither is a fact about this FORM, so the schema owns it -- and
        without it the service would refuse with "that category is not one of
        yours", which is a true sentence about the wrong problem.
        """
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="-31.41",
            posted_on=seed_user["bootstrap_period"].start_date,
        )
        db.session.commit()

        response = auth_client.post(
            self._purchase_url(seed_user["account"].id),
            data={
                "line_id": line.id,
                "transaction_id": "",
                "envelope_name": "Lowe's",
                "category_id": "",
            },
            follow_redirects=True,
        )

        assert response.status_code == 200
        assert b"needs both a name and a category" in response.data
        assert db.session.query(Transaction).filter(
            Transaction.name == "Lowe's",
        ).count() == 0

    def test_a_refusal_leaves_nothing_behind(
        self, auth_client, db, seed_user,
    ):
        """The unit of work is the REQUEST, which is what makes the message true.

        Every refusal here ends "Nothing was changed", and the new-envelope arm
        stages a budget line before the purchase -- so a refusal arriving after
        that has to take the row with it.
        """
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="2573.42",
            posted_on=seed_user["bootstrap_period"].start_date,
        )
        db.session.commit()

        response = auth_client.post(
            self._purchase_url(seed_user["account"].id),
            data={
                "line_id": line.id,
                "transaction_id": "",
                "envelope_name": "Payroll",
                "category_id": seed_user["categories"]["Groceries"].id,
            },
            follow_redirects=True,
        )

        assert response.status_code == 200
        assert b"money LEAVING" in response.data
        db.session.expire_all()
        assert db.session.query(Transaction).filter(
            Transaction.name == "Payroll",
        ).count() == 0
        assert db.session.query(StatementMatch).count() == 0
