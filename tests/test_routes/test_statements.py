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

from datetime import date, timedelta
from decimal import Decimal
import re
from io import BytesIO

import pytest

from app.models.account import Account
from app.models.ref import AccountType
from app.models.statement_import import BankStatementLine, StatementImport
from app.models.statement_match import StatementMatch as statement_match_model
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.user import User, UserSettings
from app.services import auth_service, entry_service, statement_match
from tests._test_helpers import create_settled_cash_transaction
from tests.test_services.test_statement_import import _csv_builder as build
from tests.test_services.test_statement_match._builders import (
    a_rule,
    a_submission,
    a_transaction,
)

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

    def test_it_says_which_of_the_two_things_it_does(
        self, auth_client, seed_user,
    ):
        """The page's own claim about itself, asserted rather than assumed.

        An import screen that looked like it reconciled would be READ as having
        reconciled, so the sentence saying so has always been part of the
        deliverable.  **Plan step ``bank_import:X-ge`` made HALF of it false**:
        recording still moves no figure, and a standing rule now files a new
        swipe as a purchase in the same request (ruling **R-GH**), so a page
        claiming only *it changes no balance* would be claiming the opposite of
        what happened to the owner's money.

        **Both halves are asserted, and the second is why this case was
        rewritten rather than left alone.**  The old assertion was
        ``b"changes no balance" in response.data``, and the replacement copy
        still contains that phrase -- about the RECORDING half -- so the case
        went on passing while the sentence it was written to grade had gone.
        A test that survives the change it exists to catch is not a test.
        """
        response = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        )
        body = response.data

        # The recording half, unchanged.
        assert b"That part changes no balance" in body
        # ...and the half X-ge added, which is what MOVES money.
        assert b"files what you have already\n    decided" in body
        assert b"standing" in body and b"filed as a purchase" in body
        # ...and the protective half R-GH keeps: nothing touches a row the
        # owner made without their tick.
        assert b"is never\n    done for you" in body

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
        # COMMITTED, not flushed (plan step balance:X-i3): a query request
        # opens a transaction of its OWN, so a row this fixture only flushed is
        # one the request cannot see.  The 404 these tests assert must be the
        # OWNERSHIP gate refusing a real account of someone else's rather than
        # a missing row.
        db.session.commit()
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

    def test_a_rule_covered_swipe_is_FILED_by_the_import_itself(
        self, auth_client, db, seed_user,
    ):
        """Ruling **R-GH** through the real door, and it MOVES MONEY.

        The whole point of the step: the owner stated once where Food Lion
        goes, uploads an export, and the swipe is a Groceries purchase before
        they have pressed anything else.  The receipt says so, names the
        figure, and the page lists the act with its undo.

        **The second line is a payroll DEPOSIT the same file carries**, so this
        cannot pass on a door that filed everything it saw: money coming in can
        never be a purchase.
        """
        with auth_client.application.app_context():
            envelope = a_transaction(
                seed_user, name="Groceries", amount="500.00",
                is_envelope=True,
            )
            a_rule(seed_user, "Coffee", template_id=envelope.template_id)
            envelope_id = envelope.id
            db.session.commit()

        response = _upload(
            auth_client, seed_user["account"].id,
            _payload(entries=[
                (date(2024, 1, 8), "-25.00",
                 "POINT OF SALE DEBIT L340 COFFEE HOUSE (Coffee)"),
                (date(2024, 1, 9), "1500.00",
                 "ACH DEPOSIT TOWN OF CLAYTON  PAYROLL"),
            ]),
        )

        assert response.status_code == 200
        toasts = _flash_toasts(response.get_data(as_text=True))
        assert any(
            "filed 1 of them as purchases worth -25.00" in message
            for _, message in toasts
        ), toasts
        assert db.session.query(TransactionEntry).filter(
            TransactionEntry.transaction_id == envelope_id,
        ).count() == 1
        # ...and the act is on the page, with the control that takes it back.
        assert b"Filed by your rules" in response.data
        assert b"match_id" in response.data

    def test_an_import_whose_rules_file_NOTHING_says_nothing_about_them(
        self, auth_client, db, seed_user,
    ):
        """The control that stops the receipt sentence appearing always.

        An ordinary import for an owner who has stated no rule files nothing,
        and a receipt claiming a filing pass on every upload would be noise on
        the one screen whose sentences have to be read.
        """
        response = _upload(auth_client, seed_user["account"].id, _payload())

        toasts = _flash_toasts(response.get_data(as_text=True))
        assert toasts, "the import receipt itself is missing"
        assert not any(
            "your standing rules" in message.lower()
            for _, message in toasts
        ), toasts
        # The receipt CARD, which is a different surface from the banner's
        # explanation of what a rule would do.
        assert b'<i class="bi bi-signpost-split"></i> Filed by your rules' \
            not in response.data
        assert db.session.query(TransactionEntry).count() == 0

    def test_the_filing_is_COMMITTED_with_the_import(
        self, auth_client, db, seed_user,
    ):
        """ONE unit of work: the lines and the purchases land together.

        The route commits once, so an import whose filing died outside a
        designed refusal would leave neither -- which is what makes the receipt
        an account of what happened rather than a hope.
        """
        with auth_client.application.app_context():
            envelope = a_transaction(
                seed_user, name="Groceries", amount="500.00",
                is_envelope=True,
            )
            a_rule(seed_user, "Coffee", template_id=envelope.template_id)
            db.session.commit()

        _upload(
            auth_client, seed_user["account"].id,
            _payload(entries=[
                (date(2024, 1, 8), "-25.00",
                 "POINT OF SALE DEBIT L340 COFFEE HOUSE (Coffee)"),
            ]),
        )

        db.session.expire_all()
        assert db.session.query(StatementImport).count() == 1
        assert db.session.query(TransactionEntry).count() == 1
        assert db.session.query(statement_match_model).filter(
            statement_match_model.applied_by_rule.is_(True),
        ).count() == 1

    def test_a_BUDGET_the_app_cannot_derive_still_records_the_bank_s_lines(
        self, auth_client, db, seed_user,
    ):
        """Found by adversarial security review 2026-08-26.

        `ReviewScope.build` raises for two setup states -- `PayCalendarError`
        when the owner's paydays cannot define a calendar, and
        `BaselineMissingError` when no scenario can price a row.  **Nothing
        registers a handler for the first**, so before this arm existed a
        broken calendar reached the browser as a bare 500 AND rolled the whole
        unit of work back, losing the import.

        Recording what the bank said has no dependency on either.  The page's
        own GET proves it: `statements()` builds no scope at all and renders
        perfectly well.  So the lines land, the rules report that they could
        not run, and the owner is told what to fix.

        **The baseline arm is the one this drives**, because it is the one
        ordinary data can reach: `uq_pay_periods_user_start` makes two paydays
        on one day UNWRITABLE, so the calendar's own tie arm cannot be built
        through the schema.  Both are caught by the same `except`, on the same
        line, so the case that reaches one grades the arm for both.
        """
        with auth_client.application.app_context():
            envelope = a_transaction(
                seed_user, name="Groceries", amount="500.00",
                is_envelope=True,
            )
            a_rule(seed_user, "Coffee", template_id=envelope.template_id)
            # What `BaselineMissingError`'s own message calls "the data was
            # changed outside the app": the owner has scenarios and none of
            # them is the baseline.
            seed_user["scenario"].is_baseline = False
            db.session.commit()

        response = _upload(
            auth_client, seed_user["account"].id,
            _payload(entries=[
                (date(2024, 1, 8), "-25.00",
                 "POINT OF SALE DEBIT L340 COFFEE HOUSE (Coffee)"),
            ]),
        )

        assert response.status_code == 200
        toasts = _flash_toasts(response.get_data(as_text=True))
        assert any(
            "did not run" in message for _, message in toasts
        ), toasts
        db.session.expire_all()
        # The BANK's lines are recorded...
        assert db.session.query(BankStatementLine).count() == 1
        assert db.session.query(StatementImport).count() == 1
        # ...and nothing was filed into the budget.
        assert db.session.query(TransactionEntry).count() == 0

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
        # COMMITTED, not flushed (plan step balance:X-i3): a query request
        # opens a transaction of its OWN, so a row this test only flushed is
        # one the request cannot see.  The 404 below must come from the KIND
        # gate refusing a real mortgage rather than from a missing row.
        db.session.commit()

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

        **This is the ASSUMED-day arm, and the warning it keeps is the one
        plan step ``bank_import:X-gc`` deliberately did not remove.**  A first
        import has nothing to solve its day against, so
        :func:`~app.services.statement_import.resolve_anchor` takes the file's
        last line -- ``day_is_solved`` is ``False`` -- and the developer's
        ruling **R-GN** (2026-08-25) is that a GUESSED placement earns the
        warning
        where a merely uncorroborated LEVEL no longer does.

        **A draft of this step flipped the assertion to ``success`` and it was
        wrong**, because the level cannot tell the two arms apart: an import
        that PROVES its day against an unconfirmed opening mints the same
        ``uncorroborated``.  Two adversarial reviews measured the green tick
        landing on the developer's 2026-01-02..2026-03-31 export, whose header
        names a day 145 days past its last line.
        ``test_a_SOLVED_day_on_an_UNCORROBORATED_chain_is_not_a_warning`` below
        is the other side of the pair, and it is what makes this one
        discriminate.
        """
        payload = build.build(build.chained(
            "100.00", _ENTRIES, with_running=False,
        ))

        response = _upload(auth_client, seed_user["account"].id, payload)
        toasts = _flash_toasts(response.get_data(as_text=True))

        assert len(toasts) == 1
        category, message = toasts[0]
        assert "Nothing has confirmed its stated balance" in message
        assert category == "warning"
        # ...and the receipt says WHY in words, not only in colour: the level
        # alone cannot distinguish this from a proven day.
        assert "taken from its last line rather than worked out" in message
        # **And it prescribes nothing.**  The sentence told the owner to
        # "Export once with your bank's running-balance option ticked", an
        # option no SECU export he can take today offers.
        assert "option ticked" not in message

    def test_a_SOLVED_day_on_an_UNCORROBORATED_chain_is_not_a_warning(
        self, auth_client, db, seed_user,
    ):
        """The half of ruling **R-GN** that REMOVED a warning.

        The level and the placement are two facts, and only one of them earns a
        warning now.  Here the chain behind the figure is unconfirmed -- the
        first file was chainless, so everything solved against it inherits
        ``uncorroborated`` by the weakest-link rule -- while the DAY is worked
        out from what the account already holds rather than guessed.  That is
        the developer's steady state, measured on his real books 2026-08-25:
        one contiguous recorded run from 2026-01-02 to 2026-08-21, so his next
        import's day is solved.

        **This is what makes the pair discriminate.**  Without it the predicate
        could warn on every ``uncorroborated`` import -- which is what it did
        before this step -- and
        ``test_a_first_import_says_its_balance_is_UNCORROBORATED`` above would
        still be green.

        The arithmetic: the first file opens at `$100.00` and its two lines
        move `-25.00` then `+1500.00`, so it closes at `$1,575.00` on 03-03.
        The second covers 03-03..03-05 with `+1500.00` and `-30.00`, so the
        balance before its first line is `1575.00 - 1500.00 = 75.00` and its
        own closing is `75.00 + 1500.00 - 30.00 = 1545.00`.
        """
        # The first file carries NO chain, so its own day is assumed (arm 3)
        # and its figure is uncorroborated -- which is what makes everything
        # solved against it inherit that level.  Its header is stated
        # explicitly because a chainless file has no implied closing for the
        # builder to take.
        _upload(
            auth_client, seed_user["account"].id,
            build.build(
                build.chained("100.00", _ENTRIES, with_running=False),
                balance_as_of="03/03/2026", stated_balance="1575.00",
            ),
        )

        response = _upload(
            auth_client, seed_user["account"].id,
            build.build(
                build.chained(
                    "0.00",
                    [(date(2026, 3, 3), "1500.00",
                      "ACH DEPOSIT TOWN OF CLAYTON  PAYROLL"),
                     (date(2026, 3, 5), "-30.00",
                      "POINT OF SALE DEBIT L340 FUEL")],
                    with_running=False,
                ),
                balance_as_of="03/05/2026", stated_balance="1545.00",
            ),
        )
        toasts = _flash_toasts(response.get_data(as_text=True))

        assert len(toasts) == 1
        category, message = toasts[0]
        # The LEVEL is still the weakest rung...
        assert "Nothing has confirmed its stated balance" in message
        # ...and the DAY was worked out, so nothing here was guessed.
        assert "taken from its last line rather than worked out" not in message
        assert category == "success"

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
        # **The COLOUR is no longer the tell** (plan step ``bank_import:X-gc``,
        # ruling **R-GN**): an uncorroborated import reports as an
        # ordinary success now, so what this control has to prove is that the
        # second upload did not become CORROBORATED -- which is the promotion
        # the review reproduced, and which the assertion above pins directly.
        # ``info`` and not ``success`` because this upload adds no LINES: the
        # re-import path reports "Nothing new", and its non-warning colour is
        # the neutral one.
        assert category == "info"

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

    def test_NO_surface_prescribes_an_export_option_the_bank_dropped(
        self, auth_client, db, seed_user,
    ):
        """Plan step X-gc: the screens stop telling the owner to do the impossible.

        Every surface that spoke about an unconfirmed balance told the owner to
        "export once with your bank's running-balance option ticked".  SECU
        stopped publishing that column between the developer's 2026-07-19 and
        2026-08-16 pulls: every pull from 2026-08-16 onward carries NO balance
        column, its header being ``Date, Account, Account Number, Account
        Type, Description, Check #, Category, Memo, Credit, Debit``.  So the
        instruction names an act he cannot perform with any export he can take
        today.

        **The receipt AND the imports table are both read**, because they are
        two renders of one map and a first version of this file's assertions
        passed against a receipt that said nothing at all (see
        :func:`_flash_toasts`).  The whole page body covers the table's
        ``title`` attribute; the toast covers the receipt.
        """
        payload = build.build(build.chained(
            "100.00", _ENTRIES, with_running=False,
        ))
        response = _upload(auth_client, seed_user["account"].id, payload)
        _, receipt = _flash_toasts(response.get_data(as_text=True))[0]

        page = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        ).get_data(as_text=True)

        for surface in (receipt, page):
            assert "running-balance option" not in surface
            assert "option ticked" not in surface
        # It still says the figure is unconfirmed, and now says what the level
        # MEANS rather than naming an act that cannot be performed.
        assert "Nothing has confirmed its stated balance" in receipt
        # Asserted on a clause carrying NO apostrophe: the flash is rendered
        # into HTML, so `file's` reaches the page as `file&#39;s` and a raw
        # assertion naming it would fail for a reason that is not the subject.
        assert (
            "It is checked against what this account has already recorded"
            in receipt
        )

    def test_the_UNCORROBORATED_badge_is_not_coloured_as_a_warning(
        self, auth_client, db, seed_user,
    ):
        """Plan step X-gc: the permanent state stops being dressed as an alarm.

        The badge is looked up by enum member in ``EVIDENCE_COPY``, so this
        reads the rendered markup rather than the map -- the map's own
        ``badge`` value could be changed with the template still hard-coding a
        class, which is exactly the drift the map exists to prevent.

        **``text-bg-warning`` is asserted absent NEXT TO THE LABEL, not
        page-wide**: this page has other legitimate amber (a "not placed"
        badge, the delete control), so a body-wide assertion would be graded by
        whichever of them happened to render.
        """
        _upload(
            auth_client, seed_user["account"].id,
            build.build(build.chained(
                "100.00", _ENTRIES, with_running=False,
            )),
        )

        page = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        ).get_data(as_text=True)

        badge = re.search(
            r'<span class="badge ([\w-]+)"[^>]*>uncorroborated</span>', page,
        )
        assert badge is not None, "the imports table renders no evidence badge"
        assert badge.group(1) == "text-bg-secondary"

    def test_the_source_select_names_the_FORMAT_not_a_column_it_lacks(
        self, auth_client, seed_user,
    ):
        """Plan step X-gc: the label stops contradicting the help text under it.

        ``ref.statement_sources.display_name`` is the text of the one control
        that chooses a parser, and it read "SECU checking -- CSV with running
        balance" while the form text directly beneath it has said the column is
        optional since plan step ``bank_import:X-f6e-1``.  Migration
        ``a1f4c7e0b839`` re-labels the row an existing database carries;
        ``app.ref_seeds`` is what a new one is born with, and the test database
        is built by the migration chain, so this grades the migration.
        """
        page = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        ).get_data(as_text=True)

        select = page[page.index('id="source"'):page.index("</select>")]
        assert "SECU checking -- CSV export" in select
        assert "with running balance" not in select

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

    def test_it_DESTROYS_what_was_created_from_those_lines_and_says_so(
        self, auth_client, db, seed_user,
    ):
        """Plan step **bank_import:X-f6f**, ruling **R-GG**, amending R-GB.

        A row created from one of these lines exists only because that line
        did, so destroying the line while keeping it leaves a movement in
        the books that nothing accounts for.  It goes with the line -- which
        means this act MOVES MONEY where R-GB said it could not, and both
        the confirmation and the receipt have to say so.

        **The wording stopped saying *the review* created it at plan step
        ``bank_import:X-ge``**, and that is a correction rather than a
        rewording: a row destroyed here may have been filed by a standing
        RULE at import, which nobody reviewed.  What the sentence must be
        right about is the count and the figure, and both are unchanged.

        Driven through the two real POSTs: import, record the `-$25.00` coffee
        line as a purchase in a new envelope, then delete the import.
        """
        # **Dated INSIDE the owner's own calendar**, unlike the module's
        # default fixture: recording a line as a purchase resolves the pay
        # period it is BUDGETED in, so a line outside every saved period is
        # never offered as creatable and this case would silently test nothing.
        inside = seed_user["bootstrap_period"].start_date + timedelta(days=2)
        _upload(auth_client, seed_user["account"].id, _payload(entries=[
            (inside, "-25.00", "POINT OF SALE DEBIT L340 COFFEE"),
            (inside + timedelta(days=1), "1500.00",
             "ACH DEPOSIT TOWN OF CLAYTON  PAYROLL"),
        ]))
        line = db.session.query(BankStatementLine).filter(
            BankStatementLine.amount < 0,
        ).one()
        auth_client.post(
            f"/accounts/{seed_user['account'].id}/statements/review",
            data={
                f"destination-{line.id}": "new",
                f"envelope_name-{line.id}": "Coffee",
                f"category_id-{line.id}": str(
                    seed_user["categories"]["Groceries"].id,
                ),
            },
        )
        db.session.expire_all()
        assert db.session.query(TransactionEntry).count() == 1, (
            "the recording must really have happened, or the delete below "
            "proves nothing"
        )

        # The CONFIRMATION says it first, on the page that offers the button.
        listing = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        ).get_data(as_text=True)
        assert "DESTROYS 2 row(s) created from those lines" in listing
        assert "-$25.00" in listing

        recorded = db.session.query(StatementImport).one()
        response = self._delete(
            auth_client, seed_user["account"].id, recorded.id,
        )

        body = response.get_data(as_text=True)
        assert "2 row(s) created from those " in body
        assert "-25.00" in body
        db.session.expire_all()
        assert db.session.query(TransactionEntry).count() == 0
        assert db.session.query(Transaction).filter(
            Transaction.name == "Coffee",
        ).count() == 0

    def test_a_BLOCKED_delete_says_so_instead_of_naming_a_destruction(
        self, auth_client, db, seed_user,
    ):
        """Found by adversarial security review 2026-08-24, in one edit.

        One refusing release takes the WHOLE import delete down
        (``_release_matches``), so an import holding a created row the owner
        has since edited cannot be deleted at all.  A first version of this
        page went on printing *"DESTROYS 2 row(s) ... worth -$25.00"* over a
        press that destroys nothing and cannot succeed -- a money figure
        attached to a no-op, and an import the owner cannot delete until they
        find the row by hand, which is finding **N-302**'s shape with a
        confirmation that lies on top of it.
        """
        inside = seed_user["bootstrap_period"].start_date + timedelta(days=2)
        _upload(auth_client, seed_user["account"].id, _payload(entries=[
            (inside, "-25.00", "POINT OF SALE DEBIT L340 COFFEE"),
        ]))
        line = db.session.query(BankStatementLine).one()
        auth_client.post(
            f"/accounts/{seed_user['account'].id}/statements/review",
            data={
                f"destination-{line.id}": "new",
                f"envelope_name-{line.id}": "Coffee",
                f"category_id-{line.id}": str(
                    seed_user["categories"]["Groceries"].id,
                ),
            },
        )
        db.session.expire_all()
        entry_service.update_entry(
            db.session.query(TransactionEntry).one().id,
            seed_user["user"].id, description="Coffee -- the good beans",
        )
        db.session.commit()

        listing = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        ).get_data(as_text=True)

        assert "This delete is REFUSED right now" in listing
        assert "you have edited that row since" in listing
        assert "DESTROYS" not in listing
        # ...and the press really is refused, so the page was not being
        # pessimistic about an act that would have worked.
        recorded = db.session.query(StatementImport).one()
        body = self._delete(
            auth_client, seed_user["account"].id, recorded.id,
        ).get_data(as_text=True)
        assert "you have edited that row since" in body
        db.session.expire_all()
        assert db.session.query(StatementImport).count() == 1

    def test_an_import_whose_review_created_NOTHING_says_nothing(
        self, auth_client, db, seed_user,
    ):
        """The control: no warning where the delete destroys no record.

        A dialog that always threatened destruction would train the owner to
        click through the one that means it.
        """
        _upload(auth_client, seed_user["account"].id, _payload())

        listing = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        ).get_data(as_text=True)
        recorded = db.session.query(StatementImport).one()
        body = self._delete(
            auth_client, seed_user["account"].id, recorded.id,
        ).get_data(as_text=True)

        assert "DESTROYS" not in listing
        assert "the review had created" not in body

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
