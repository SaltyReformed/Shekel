"""The statement page shows what the bank said, and its POST records one.

Plan step **bank_import:X-f6a-1**.  The route's own subjects, none of which the
service tests can see: OWNERSHIP (the security response rule's 404 for both "not
found" and "not yours"), the UPLOAD itself, the unit of work (a refusal must
leave nothing behind after a real request), and the FLASH that tells the user
what happened.

**The ownership tests are firing controls against an IDOR.**  A statement is a
record of a real person's bank activity, so a route that answered for another
user's account would disclose their spending -- and it would do so on a GET, the
cheapest thing to probe.  Both the read and the write are tested against a
second user's account, because the two decorators are applied independently and
one of them being right proves nothing about the other.
"""

from datetime import date
from io import BytesIO

import pytest

from app.models.statement_import import BankStatementLine, StatementImport
from app.models.user import User, UserSettings
from app.services import auth_service
from tests.test_services.test_statement_import import _csv_builder as build

_ENTRIES = [
    (date(2026, 3, 2), "-25.00", "POINT OF SALE DEBIT L340 COFFEE"),
    (date(2026, 3, 3), "1500.00", "ACH DEPOSIT TOWN OF CLAYTON  PAYROLL"),
]


def _payload(entries=None, start="100.00", **kwargs):
    """Return a well-formed SECU CSV payload."""
    return build.build(build.chained(start, entries or _ENTRIES, **kwargs))


def _upload(client, account_id, payload, source="secu_checking_csv",
            filename="statement.csv", **extra):
    """POST a statement file the way the form does."""
    data = {"source": source, **extra}
    if payload is not None:
        data["statement_file"] = (BytesIO(payload), filename)
    return client.post(
        f"/accounts/{account_id}/statements",
        data=data,
        content_type="multipart/form-data",
        follow_redirects=True,
    )


class TestThePageReadsForItsOwner:
    """The GET."""

    def test_it_renders_for_the_owner(self, auth_client, seed_user):
        """The ordinary case."""
        response = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        )

        assert response.status_code == 200
        assert b"Import a statement" in response.data

    def test_it_says_the_import_changes_no_balance(
        self, auth_client, seed_user,
    ):
        """The page's own claim about itself, asserted rather than assumed.

        An import screen that looked like it reconciled would be READ as having
        reconciled -- and at this leaf it does not, so the sentence saying so
        is part of the deliverable rather than decoration.
        """
        response = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        )

        assert b"changes no balance" in response.data

    def test_an_account_with_nothing_recorded_says_so(
        self, auth_client, seed_user,
    ):
        """The empty state is an answer, not a blank."""
        response = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        )

        assert b"Nothing recorded yet" in response.data

    def test_it_offers_the_adapters_that_have_a_parser(
        self, auth_client, seed_user,
    ):
        """The form's options come from the registry, so an unusable source
        cannot be chosen."""
        response = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        )

        assert b"secu_checking_csv" in response.data

    def test_recorded_lines_are_rendered(self, auth_client, seed_user):
        """What the bank said, back on the page that recorded it."""
        _upload(auth_client, seed_user["account"].id, _payload())

        response = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        )

        assert b"POINT OF SALE DEBIT L340 COFFEE" in response.data
        assert b"lines recorded" in response.data


class TestItRefusesAnotherUsersAccount:
    """Firing controls against an IDOR on a record of real bank activity."""

    @pytest.fixture()
    def other_users_account(self, db, seed_user):
        """Return an account id belonging to a DIFFERENT user."""
        stranger = User(
            email="stranger@shekel.local",
            password_hash=auth_service.hash_password("otherpass"),
            display_name="Stranger",
        )
        db.session.add(stranger)
        db.session.flush()
        db.session.add(UserSettings(user_id=stranger.id))
        db.session.flush()
        from app.models.account import Account

        account = Account(
            user_id=stranger.id,
            account_type_id=seed_user["account"].account_type_id,
            name="Stranger Checking",
        )
        db.session.add(account)
        db.session.flush()
        return account.id

    def test_the_page_answers_404(self, auth_client, other_users_account):
        """404 for both "not found" and "not yours" -- the security rule.

        A 403 would confirm the account exists, which is the disclosure the
        rule exists to prevent.
        """
        response = auth_client.get(
            f"/accounts/{other_users_account}/statements"
        )

        assert response.status_code == 404

    def test_the_import_answers_404(
        self, auth_client, db, other_users_account,
    ):
        """The write door is decorated independently of the read."""
        response = auth_client.post(
            f"/accounts/{other_users_account}/statements",
            data={
                "source": "secu_checking_csv",
                "statement_file": (BytesIO(_payload()), "s.csv"),
            },
            content_type="multipart/form-data",
        )

        assert response.status_code == 404
        assert db.session.query(BankStatementLine).count() == 0


class TestTheImportPost:
    """The write door, end to end through HTTP."""

    def test_it_records_the_file_and_says_what_it_did(
        self, auth_client, db, seed_user,
    ):
        """The happy path, with the count in the flash."""
        response = _upload(auth_client, seed_user["account"].id, _payload())

        assert response.status_code == 200
        assert b"Recorded 2 new line" in response.data
        assert db.session.query(BankStatementLine).count() == 2

    def test_a_second_identical_upload_reports_nothing_new(
        self, auth_client, db, seed_user,
    ):
        """Idempotency, as the user experiences it."""
        _upload(auth_client, seed_user["account"].id, _payload())

        response = _upload(auth_client, seed_user["account"].id, _payload())

        assert b"Nothing new" in response.data
        assert db.session.query(BankStatementLine).count() == 2

    def test_the_import_is_COMMITTED_not_merely_staged(
        self, auth_client, db, seed_user,
    ):
        """The route owns the unit of work, so the rows survive the request."""
        _upload(auth_client, seed_user["account"].id, _payload())

        db.session.expire_all()
        assert db.session.query(StatementImport).count() == 1

    def test_a_missing_file_is_refused_without_a_500(
        self, auth_client, db, seed_user,
    ):
        """Submitting the form with no file chosen."""
        response = _upload(auth_client, seed_user["account"].id, None)

        assert response.status_code == 200
        assert b"Choose a file" in response.data
        assert db.session.query(StatementImport).count() == 0

    def test_an_unknown_source_is_refused(self, auth_client, db, seed_user):
        """A submitted source with no adapter, e.g. a tampered form."""
        response = _upload(
            auth_client, seed_user["account"].id, _payload(),
            source="not_a_real_source",
        )

        assert response.status_code == 200
        assert b"Must be one of" in response.data
        assert db.session.query(StatementImport).count() == 0

    def test_a_file_that_is_not_a_statement_shows_the_refusal(
        self, auth_client, db, seed_user,
    ):
        """The service's own sentence reaches the user, not a generic error."""
        response = _upload(
            auth_client, seed_user["account"].id, b"not,a,statement\r\n1,2,3\r\n",
        )

        assert response.status_code == 200
        assert b"not a SECU transaction export" in response.data
        assert db.session.query(StatementImport).count() == 0

    def test_a_file_that_contradicts_itself_shows_the_refusal(
        self, auth_client, db, seed_user,
    ):
        """The running-balance chain, refused through the real request path.

        Written by breaking the chain rather than by mocking the checker, so
        this test would fail if the route stopped calling it.
        """
        rows = build.chained("100.00", _ENTRIES)
        rows[0][10] = "9999.00"
        payload = build.build(rows)

        response = _upload(auth_client, seed_user["account"].id, payload)

        assert b"does not add up" in response.data
        assert db.session.query(BankStatementLine).count() == 0

    def test_a_refusal_after_a_good_import_leaves_the_good_one_intact(
        self, auth_client, db, seed_user,
    ):
        """The rollback must not take the previous request's work with it."""
        _upload(auth_client, seed_user["account"].id, _payload())

        _upload(
            auth_client, seed_user["account"].id,
            _payload(account_number="******9999"), filename="wrong.csv",
        )

        db.session.expire_all()
        assert db.session.query(BankStatementLine).count() == 2
        assert db.session.query(StatementImport).count() == 1

    def test_a_wrong_account_file_shows_the_refusal(
        self, auth_client, db, seed_user,
    ):
        """R-FP's mapping check, through HTTP."""
        _upload(auth_client, seed_user["account"].id, _payload())

        response = _upload(
            auth_client, seed_user["account"].id,
            _payload(account_number="******9999"), filename="wrong.csv",
        )

        assert b"has been imported from" in response.data


class TestTheAccountPageLinksHere:
    """A feature nothing reaches is a feature nobody has."""

    def test_a_NON_CASH_account_has_no_statements_page(
        self, auth_client, db, seed_user,
    ):
        """A loan, a property or a 401(k) has no bank statement to import.

        Ownership alone is not the gate: without the account-KIND check this
        page rendered for those types with a back link that 404s, and would
        record bank lines against an account with no cash ledger.  The project
        has already paid twice for a sibling route skipping this helper.
        """
        from app.models.ref import AccountType
        from app.services import account_service
        from decimal import Decimal

        mortgage_type = (
            db.session.query(AccountType).filter_by(name="Mortgage").one()
        )
        loan = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=mortgage_type.id,
                name="House Loan",
                anchor_balance=Decimal("100000.00"),
                observed_on=seed_user["bootstrap_period"].start_date,
            )
        )
        db.session.flush()

        assert auth_client.get(
            f"/accounts/{loan.id}/statements"
        ).status_code == 404
        assert _upload(
            auth_client, loan.id, _payload(),
        ).status_code == 404

    def test_an_unverifiable_file_is_flagged_rather_than_ticked(
        self, auth_client, db, seed_user,
    ):
        """A 10-column export carries no chain, and the user must be told.

        The page asks for the running-balance column precisely because
        "without it a missing line cannot be detected" -- so reporting success
        in green over a statement nothing could check tells the user the
        opposite of what happened.
        """
        payload = build.build(build.chained(
            "100.00", _ENTRIES, with_running=False,
        ))

        response = _upload(auth_client, seed_user["account"].id, payload)

        assert b"could not be checked against itself" in response.data

    def test_a_verified_file_is_NOT_flagged(
        self, auth_client, db, seed_user,
    ):
        """The warning must not fire on the ordinary case."""
        response = _upload(auth_client, seed_user["account"].id, _payload())

        assert b"could not be checked against itself" not in response.data

    def test_the_cash_detail_page_offers_the_statements_link(
        self, auth_client, seed_user,
    ):
        """The only entry point, so its absence would strand the whole leaf."""
        response = auth_client.get(
            f"/accounts/{seed_user['account'].id}/details"
        )

        assert response.status_code == 200
        assert (
            f"/accounts/{seed_user['account'].id}/statements".encode()
            in response.data
        )
