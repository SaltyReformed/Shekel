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
from decimal import Decimal
import re
from io import BytesIO

import pytest

from app.models.account import Account
from app.models.ref import AccountType
from app.models.statement_import import BankStatementLine, StatementImport
from app.models.user import User, UserSettings
from app.services import auth_service, statement_match
from tests._test_helpers import create_settled_cash_transaction
from tests.test_services.test_statement_import import _csv_builder as build
from tests.test_services.test_statement_match._builders import a_submission

_ENTRIES = [
    (date(2026, 3, 2), "-25.00", "POINT OF SALE DEBIT L340 COFFEE"),
    (date(2026, 3, 3), "1500.00", "ACH DEPOSIT TOWN OF CLAYTON  PAYROLL"),
]


def _payload(entries=None, start="100.00", **kwargs):
    """Return a well-formed SECU CSV payload."""
    return build.build(build.chained(start, entries or _ENTRIES, **kwargs))


def _flash_toasts(body):
    """Return ``(category, message)`` for every flash toast the page rendered.

    **An assertion about the RECEIPT has to read the receipt**, which
    :func:`_delete_form` next door already says about forms and which this file
    then went on to get wrong anyway: the imports table renders each evidence
    sentence in a ``title`` attribute, so ``sentence in response.data`` passed
    against a receipt that said nothing at all.  Proven by isolation --
    dropping the receipt sentence alone left 3 of 4 balance tests green, and
    dropping the table attribute alone left all 4 green.  Found by adversarial
    review of this step's own tests, 2026-08-23.

    **It returns the CATEGORY as well, because the colour is half the
    message.**  ``_import_flash`` exists to say that a green tick over an
    unconfirmed balance tells the owner the opposite of what happened, and the
    same review measured that flipping every category to ``success`` -- or to
    ``warning`` -- left all 32 tests in this file passing.

    Args:
        body: The rendered page.

    Returns:
        One ``(category, message)`` pair per toast, in render order.
    """
    return re.findall(
        r'class="toast text-bg-(\w+)".*?<div class="toast-body">(.*?)</div>',
        body, re.S,
    )


def _delete_form(body):
    """Return just the delete form's markup from a rendered statements page.

    An assertion about a state-changing form has to read THAT form: this page
    renders two, and "the token is somewhere in the body" is satisfied by the
    other one.

    Args:
        body: The rendered page.

    Returns:
        The delete form's markup, from its ``<form`` to its ``</form>``.
    """
    marker = body.index("/statements/delete")
    start = body.rindex("<form", 0, marker)
    return body[start:body.index("</form>", start)]


def _a_loan_account(db, seed_user):
    """Return a loan account, which the statement pages do not serve.

    Args:
        db: The session fixture.
        seed_user: The seeded user bundle.

    Returns:
        The staged :class:`~app.models.account.Account`.
    """
    loan_type = (
        db.session.query(AccountType).filter_by(name="Auto Loan").one()
    )
    account = Account(
        user_id=seed_user["user"].id,
        account_type_id=loan_type.id,
        name="Van Loan",
    )
    db.session.add(account)
    db.session.flush()
    return account


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

    def test_a_first_import_says_its_balance_is_UNCORROBORATED(
        self, auth_client, db, seed_user,
    ):
        """Nothing can check the first file's stated balance, and it says so.

        **The signal used to be the running-balance chain, and that was
        measured WRONG** (plan step ``bank_import:X-f6e-1``): SECU stopped
        exporting that column, so the old warning fired on every modern import
        while the file's ``Totals:`` row had already checked the line list.
        What is genuinely unconfirmed on a first import is the BALANCE.
        """
        payload = build.build(build.chained(
            "100.00", _ENTRIES, with_running=False,
        ))

        response = _upload(auth_client, seed_user["account"].id, payload)
        toasts = _flash_toasts(response.get_data(as_text=True))

        assert len(toasts) == 1
        category, message = toasts[0]
        assert "Nothing has confirmed its stated balance" in message
        # The COLOUR is half the message: a green tick over an unconfirmed
        # balance tells the owner the opposite of what happened.
        assert category == "warning"

    def test_a_CHAINED_file_is_PROVED_by_itself_and_ticked_green(
        self, auth_client, db, seed_user,
    ):
        """A per-line running balance states the opening, so nothing is assumed.

        The firing half of the pair above: this asserts the warning's ABSENCE
        on a file that carries its own proof, so a receipt that warned
        unconditionally would fail here and one that never warned would fail
        there -- and the same for the colour.
        """
        response = _upload(auth_client, seed_user["account"].id, _payload())
        toasts = _flash_toasts(response.get_data(as_text=True))

        assert len(toasts) == 1
        category, message = toasts[0]
        assert "proved by the file" in message
        assert "per-line running balance" in message
        assert "Nothing has confirmed" not in message
        assert category == "success"

    def test_a_re_import_that_adds_NO_LINES_still_reports_its_balance(
        self, auth_client, db, seed_user,
    ):
        """A line is not the only thing an import can learn.

        **Found by driving the real app rather than by a green test**: on the
        developer's dev database, re-importing his 2026-08-16 export added 0 of
        its 361 lines and yet RECORDED an anchor -- its stated `$4,747.63`
        placed at 2026-08-13 -- while the receipt said "Nothing new" and
        nothing else.  The idempotent no-op path had never carried the balance
        sentence, because until this step there was no balance fact for it to
        carry.
        """
        _upload(auth_client, seed_user["account"].id, _payload())

        response = _upload(auth_client, seed_user["account"].id, _payload())
        toasts = _flash_toasts(response.get_data(as_text=True))

        assert len(toasts) == 1
        _, message = toasts[0]
        assert "Nothing new" in message
        assert "proved by the file" in message
        assert "per-line running balance" in message

    def test_a_RE_UPLOAD_cannot_promote_an_UNCORROBORATED_balance(
        self, auth_client, db, seed_user,
    ):
        """The assumption may not check itself.

        **Reproduced in two clicks by adversarial review, 2026-08-23**: the
        first import of a chainless file was recorded as unconfirmed, and
        re-uploading the IDENTICAL BYTES made the app walk back to its own
        assumption, find that the file agreed with it, and record the result as
        corroborated -- with the receipt turning from a warning to a green
        tick.  Nothing had reconciled.  The weakest-link rule makes that
        unreachable: the chain behind the second answer still contains the
        first one's assumption.
        """
        payload = build.build(build.chained(
            "100.00", _ENTRIES, with_running=False,
        ))
        _upload(auth_client, seed_user["account"].id, payload)

        response = _upload(auth_client, seed_user["account"].id, payload)
        toasts = _flash_toasts(response.get_data(as_text=True))

        assert len(toasts) == 1
        category, message = toasts[0]
        assert "Nothing has confirmed its stated balance" in message
        assert "agrees with the statements already recorded" not in message
        assert category == "warning"

    def test_a_SECOND_import_is_CORROBORATED_against_a_proved_anchor(
        self, auth_client, db, seed_user,
    ):
        """Two statements agreeing, rooted in one that proved itself.

        The first file carries a chain, so it is ``file_chain``; the second
        does not, and is checked against what the first left behind.  That is
        the only route to ``corroborated``, which is the point of the
        weakest-link rule and is why this fixture pays for a chained first
        file rather than reusing the chainless one above.

        The arithmetic: the first file opens at `$100.00` and its two lines
        move `-25.00` then `+1500.00`, so it closes at `$1,575.00` on 03-03.
        The second covers 03-03..03-05 with `+1500.00` and `-30.00`, so the
        balance before its first line is `1575.00 - 1500.00 = 75.00` and its
        own closing is `75.00 + 1500.00 - 30.00 = 1545.00`.
        """
        _upload(auth_client, seed_user["account"].id, _payload())

        later = build.build(
            build.chained(
                "0.00",
                [(date(2026, 3, 3), "1500.00",
                  "ACH DEPOSIT TOWN OF CLAYTON  PAYROLL"),
                 (date(2026, 3, 5), "-30.00",
                  "POINT OF SALE DEBIT L340 FUEL")],
                with_running=False,
            ),
            stated_balance="1545.00",
        )
        response = _upload(auth_client, seed_user["account"].id, later)
        toasts = _flash_toasts(response.get_data(as_text=True))

        assert len(toasts) == 1
        category, message = toasts[0]
        assert "agrees with the statements already recorded" in message
        assert category == "success"

    def test_a_DATE_RANGE_export_records_its_claim_and_no_anchor(
        self, auth_client, db, seed_user,
    ):
        """The measured shape a refusal would have rejected.

        The developer exported 2026-01-02..2026-03-31 on 2026-08-23 and its
        header states TODAY's balance -- 145 days past its last line and
        `$255.41` from what its own 139 lines imply.  Its claim is real and its
        placement is undeterminable, and the receipt says exactly that rather
        than refusing an honest file or inventing a day for it.

        **A prior anchor has to exist for this arm to be reachable at all**,
        and that is the design rather than fixture convenience: "cannot be
        placed" is a statement about a KNOWN opening the figure fails to
        reconcile with.  A first import has no opening to fail against, so the
        same file would be taken at face value -- which is exactly what
        finding **N-342** records, and it is why the first upload here carries
        a chain.
        """
        _upload(auth_client, seed_user["account"].id, _payload())

        response = _upload(
            auth_client, seed_user["account"].id,
            build.build(build.chained("100.00", _ENTRIES, with_running=False),
                        balance_as_of="08/23/2026", stated_balance="2459.60"),
        )
        toasts = _flash_toasts(response.get_data(as_text=True))

        assert len(toasts) == 1
        category, message = toasts[0]
        assert "which its own lines do not reach" in message
        assert "no balance was recorded from it" in message
        assert category == "warning"

    def test_a_file_stating_NO_balance_says_there_was_none_to_check(
        self, auth_client, db, seed_user,
    ):
        """The fourth receipt arm, which had no test at all."""
        payload = build.build(build.chained("100.00", _ENTRIES))
        without = b"\n".join(
            line for line in payload.split(b"\n")
            if not line.startswith(b"Balance as of")
        )

        response = _upload(auth_client, seed_user["account"].id, without)
        toasts = _flash_toasts(response.get_data(as_text=True))

        assert len(toasts) == 1
        category, message = toasts[0]
        assert "It states no balance, so there was none to check." in message
        assert category == "warning"

    def test_the_receipt_names_BOTH_days_when_they_differ(
        self, auth_client, db, seed_user,
    ):
        """The gap is the number the owner has to judge.

        An ordinary export's header sits a day past its last line; the
        developer's date-range one sat **145 days** past it and `$255.41` out.
        The days are asserted rather than the sentence's prefix, because
        swapping them left the old assertion green.
        """
        response = _upload(
            auth_client, seed_user["account"].id,
            build.build(build.chained("100.00", _ENTRIES, with_running=False),
                        balance_as_of="03/09/2026"),
        )
        _, message = _flash_toasts(response.get_data(as_text=True))[0]

        # Placed at its last line (03-03); the file states it as of 03-09.
        assert "placed at 2026-03-03" in message
        assert "states it as of 2026-03-09" in message

    def test_the_imports_table_shows_what_the_bank_said(
        self, auth_client, db, seed_user,
    ):
        """The RECORD, where the receipt is transient.

        ``_reads.ImportedBalance``'s own docstring argues this table matters
        because an anchor the import only assumed has to stay readable after
        the flash is gone -- and the whole column had no test, so forcing
        ``_imported_balance`` to return ``None`` left 233 tests passing.
        """
        _upload(
            auth_client, seed_user["account"].id,
            build.build(build.chained("100.00", _ENTRIES, with_running=False)),
        )

        body = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        ).get_data(as_text=True)

        assert "Bank said" in body
        assert "uncorroborated" in body
        assert "as of 2026-03-03" in body

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


class TestTheDeletePost:
    """The UNDO door, end to end through HTTP (plan step X-f6a-4).

    The route's own subjects, none of which the service test can see: OWNERSHIP
    on a DESTRUCTIVE act, the validated id, the unit of work, and the flash
    that reports what was actually removed.
    """

    def _delete(self, client, account_id, import_id, **extra):
        """POST the delete the way the form does."""
        return client.post(
            f"/accounts/{account_id}/statements/delete",
            data={"import_id": str(import_id), **extra},
            follow_redirects=True,
        )

    def test_it_deletes_the_import_and_reports_what_went(
        self, auth_client, db, seed_user,
    ):
        """The happy path, with the counts in the flash."""
        _upload(auth_client, seed_user["account"].id, _payload())
        recorded = db.session.query(StatementImport).one()

        response = self._delete(
            auth_client, seed_user["account"].id, recorded.id,
        )

        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert "Deleted the import of" in body
        assert "2 bank line(s)" in body
        assert db.session.query(StatementImport).count() == 0
        assert db.session.query(BankStatementLine).count() == 0

    def test_the_page_offers_the_control_with_what_it_would_remove(
        self, auth_client, db, seed_user,
    ):
        """The confirmation names a LIVE count, not the historical one.

        ``recorded_count`` is what the act wrote on the day it ran and
        ``lines_held`` is what it still owns; putting the first on a
        destructive control would be a stored value standing in for a live one
        -- on the one sentence the owner reads before pressing delete.
        """
        _upload(auth_client, seed_user["account"].id, _payload())

        response = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        )

        body = response.get_data(as_text=True)
        assert "/statements/delete" in body
        assert "This removes 2 bank line(s)" in body
        # **The CSRF token is asserted INSIDE THE DELETE FORM**, not merely
        # somewhere on the page.  TestConfig sets `WTF_CSRF_ENABLED = False`, so
        # a form that lost its token passes every functional test here while
        # 400ing in production -- which is why the project asserts the markup
        # (`test_loan.py`, `test_security_event_banner.py`).  A first version of
        # this assertion looked for the token anywhere in the body and PASSED
        # with the delete form's token deleted, because the upload form on the
        # same page carries one; measured by planting it 2026-08-20.
        assert 'name="csrf_token"' in _delete_form(body)

    def test_it_says_how_many_matches_it_would_UNDO(
        self, auth_client, db, seed_user,
    ):
        """The one LIVE count on the confirmation, and its arm.

        A match can be released independently of its import, so this number is
        genuinely current where the line count is the act's own history.  The
        `{% if row.matches_affected %}` arm had no test at all until adversarial
        review said so -- it is the sentence that warns an owner they are about
        to undo work they accepted.
        """
        _upload(auth_client, seed_user["account"].id, _payload())
        line = db.session.query(BankStatementLine).first()
        txn = create_settled_cash_transaction(
            seed_user, db.session, seed_user["bootstrap_period"],
            Decimal("25.00"), settled_on=line.posted_on, name="Coffee",
        )
        db.session.flush()
        scope = statement_match.ReviewScope.build(
            seed_user["user"].id, seed_user["account"].id,
        )
        statement_match.accept_match(
            a_submission(scope, lines=[line], transactions=[txn]),
            scope,
        )
        db.session.commit()

        body = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        ).get_data(as_text=True)

        assert "undoes 1 accepted match(es)" in body

    def test_the_flash_names_the_matches_and_the_pairing_it_removed(
        self, auth_client, db, seed_user,
    ):
        """Both conditional arms of the report, which had no test.

        A destructive act whose report is one word leaves the owner unable to
        tell a no-op from a much larger removal -- and this one reaches past
        the import itself, releasing matches and clearing the bank pairing.
        """
        _upload(auth_client, seed_user["account"].id, _payload())
        recorded = db.session.query(StatementImport).one()

        response = self._delete(
            auth_client, seed_user["account"].id, recorded.id,
        )

        body = response.get_data(as_text=True)
        assert "This was the last import for this account from that source" in body
        assert "the app no longer records which bank account it is" in body

    def test_the_KIND_gate_answers_404_for_a_loan(
        self, auth_client, db, seed_user,
    ):
        """A loan has no bank statement, so it has no import to delete.

        The gate is `load_cash_account_or_404`, and this project has already
        paid twice for a sibling route being written without it -- the GET and
        the import POST both assert it, and the door that DESTROYS was added
        without joining them.  Adversarial security review 2026-08-20.
        """
        loan = _a_loan_account(db, seed_user)

        response = auth_client.post(
            f"/accounts/{loan.id}/statements/delete",
            data={"import_id": "1"},
        )

        assert response.status_code == 404

    def test_ANOTHER_users_import_answers_404(
        self, auth_client, db, seed_user,
    ):
        """FIRING CONTROL against an IDOR on a destructive act.

        The delete is decorated independently of the read and the import, so
        neither of those being right proves anything about this one -- and
        this is the door that DESTROYS.
        """
        stranger = User(
            email="stranger2@shekel.local",
            password_hash=auth_service.hash_password("otherpass"),
            display_name="Stranger Two",
        )
        db.session.add(stranger)
        db.session.flush()
        db.session.add(UserSettings(user_id=stranger.id))
        db.session.flush()
        from app.models.account import Account

        theirs = Account(
            user_id=stranger.id,
            account_type_id=seed_user["account"].account_type_id,
            name="Stranger Checking Two",
        )
        db.session.add(theirs)
        db.session.flush()
        _upload(auth_client, seed_user["account"].id, _payload())
        mine = db.session.query(StatementImport).one()

        response = auth_client.post(
            f"/accounts/{theirs.id}/statements/delete",
            data={"import_id": str(mine.id)},
        )

        assert response.status_code == 404
        assert db.session.query(BankStatementLine).count() == 2

    def test_an_import_on_ANOTHER_of_the_owners_accounts_is_refused(
        self, auth_client, db, seed_user,
    ):
        """Ownership of the ACCOUNT is not ownership of the IMPORT.

        The decorator proves the caller owns the account in the URL; only the
        service can say the import belongs to THAT account.  Without that arm a
        single owner could delete one account's statements through another
        account's door.
        """
        from app.models.ref import AccountType
        from app.services import account_service
        from decimal import Decimal

        checking_type = (
            db.session.query(AccountType).filter_by(name="Checking").one()
        )
        second = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=checking_type.id,
                name="Second Checking",
                anchor_balance=Decimal("0.00"),
                observed_on=seed_user["bootstrap_period"].start_date,
            )
        )
        db.session.flush()
        _upload(auth_client, seed_user["account"].id, _payload())
        mine = db.session.query(StatementImport).one()

        response = self._delete(auth_client, second.id, mine.id)

        assert "no longer there" in response.get_data(as_text=True)
        assert db.session.query(BankStatementLine).count() == 2

    def test_a_missing_id_is_refused_by_the_schema(
        self, auth_client, db, seed_user,
    ):
        """The validated-input discipline, not a bare ``request.form.get``."""
        _upload(auth_client, seed_user["account"].id, _payload())

        response = auth_client.post(
            f"/accounts/{seed_user['account'].id}/statements/delete",
            data={},
            follow_redirects=True,
        )

        assert "Which import do you want to delete?" in response.get_data(
            as_text=True,
        )
        assert db.session.query(BankStatementLine).count() == 2

    def test_a_NON_ROW_id_is_refused_rather_than_coerced(
        self, auth_client, db, seed_user,
    ):
        """``RowId``, not ``fields.Integer`` (finding N-141).

        ``Integer`` reads ``' 12 '``, ``'+12'``, ``'007'`` and ``'0'`` as ids,
        two of which name no row at all -- on a door that destroys.  The
        padded id is the REAL one, so this case cannot pass by naming a row
        that does not exist.
        """
        _upload(auth_client, seed_user["account"].id, _payload())

        recorded = db.session.query(StatementImport).one()
        response = self._delete(
            auth_client, seed_user["account"].id, f" {recorded.id} ",
        )

        assert db.session.query(BankStatementLine).count() == 2
        assert "Deleted the import of" not in response.get_data(as_text=True)

    def test_a_refused_delete_leaves_the_pairing_alone(
        self, auth_client, db, seed_user,
    ):
        """The unit of work is the request, asserted through a real one."""
        from app.models.statement_import import AccountExternalIdentity

        _upload(auth_client, seed_user["account"].id, _payload())

        self._delete(auth_client, seed_user["account"].id, 999999)

        assert db.session.query(AccountExternalIdentity).count() == 1
        assert db.session.query(BankStatementLine).count() == 2
