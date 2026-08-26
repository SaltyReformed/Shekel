"""The books-vs-bank page reports, and refuses everything that is not its own.

Plan step **bank_import:X-f6e-2**, ruling **R-GF**.  The route's own subjects,
none of which the service tests can see: OWNERSHIP (the security response
rule's 404 for both "not found" and "not yours"), the account KIND gate, what
the page actually renders, and the drill-down fragment's own refusals.

**The ownership tests are firing controls against an IDOR**, and both endpoints
are tested: the page and the fragment carry their decorators independently, and
one of them being right proves nothing about the other.  What leaks here is not
only a balance -- the fragment names a real person's merchants and the rows they
entered against them.

**The kind gate is tested too**, because this page was written after plan step
X-f2-b's adversarial review found a fragment shipped without one, rendering cash
copy for an amortizing account whose own detail page 404s.
"""

from datetime import date, timedelta
from decimal import Decimal
import re

import pytest

from app import ref_cache
from app.enums import (
    StatementBalanceEvidenceEnum,
    StatusEnum,
    TxnTypeEnum,
)
from app.models.account import Account
from app.models.ref import AccountType
from app.models.transaction import Transaction
from app.models.user import User, UserSettings
from app.services import auth_service, cash_ledger
from app.services.scenario_resolver import get_baseline_scenario
from app.services.scenario_resolver import get_baseline_scenario
from tests._test_helpers import (
    append_balance_assertion,
    settle_day_columns,
    settlement_columns,
)
from tests.test_services.test_cash_fold import _instant
from tests.test_services.test_statement_import.test_anchor import _seed_import

_FILE_CHAIN = StatementBalanceEvidenceEnum.FILE_CHAIN
_UNCORROBORATED = StatementBalanceEvidenceEnum.UNCORROBORATED


def _url(account_id):
    """Return the page's URL."""
    return f"/accounts/{account_id}/statements/agreement"


def _day_url(account_id, day):
    """Return the drill-down fragment's URL."""
    return f"/accounts/{account_id}/statements/agreement/day?day={day}"


def _an_account_with_no_anchor(db, seed_user):
    """Return a second cash account holding a statement that states no balance.

    The books-vs-bank page renders its two balance paragraphs in mutually
    exclusive states, so a test that has to read BOTH needs two accounts.  This
    is the state both of the developer's first two real imports are in.

    Args:
        db: The session fixture.
        seed_user: The seeded user bundle.

    Returns:
        The staged :class:`~app.models.account.Account`.
    """
    account = Account(
        user_id=seed_user["user"].id,
        account_type_id=seed_user["account"].account_type_id,
        name="Second Checking",
    )
    db.session.add(account)
    db.session.flush()
    _seed_import(
        db, account, stated=None, lines=[(date(2026, 3, 2), "-60.00")],
    )
    return account


def _row_for(body, day):
    """Return the rendered ``<tr>`` for one day of the table.

    **The row, not the page.**  Asserting a figure appears "somewhere in the
    response" is how this project has already shipped a green test that passed
    on a ``title`` attribute rather than on the thing it named, so every
    assertion below is scoped to the row it is about.

    Args:
        body: The decoded response body.
        day: The civil day whose row to return.

    Returns:
        The row's markup.
    """
    rows = re.findall(r"<tr\b.*?</tr>", body, re.S)
    for row in rows:
        if str(day) in row:
            return row
    raise AssertionError(f"no table row rendered for {day}")


def _money_cells(row):
    """Return the money strings a rendered row carries, in column order."""
    return re.findall(r"-?\$[\d,]+\.\d{2}", row)


def _settled(db, seed_user, period, name, amount, day):
    """Insert one SETTLED expense whose cash moved on *day*."""
    status_id = ref_cache.status_id(StatusEnum.DONE)
    txn = Transaction(
        account_id=seed_user["account"].id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        status_id=status_id,
        name=name,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        estimated_amount=Decimal(str(amount)),
        **settlement_columns(day, amount, amount),
        **settle_day_columns(day),
    )
    db.session.add(txn)
    db.session.flush()
    return txn


@pytest.fixture()
def a_disagreement(db, seed_user, seed_periods):
    """Seed the shape the whole design turns on, and return its day.

    A ``$100.00`` row the bank never posted, and a same-day balance assertion
    that cancels it exactly -- so the two running balances agree to the cent on
    the day the two records do not.
    """
    day = date(2026, 3, 3)
    _seed_import(
        db, seed_user["account"], stated="1000.00",
        effective_on=date(2026, 3, 4), evidence=_FILE_CHAIN,
        lines=[(date(2026, 3, 2), "50.00"), (date(2026, 3, 4), "-25.00")],
    )
    _settled(db, seed_user, seed_periods[4], "Card payback", "100.00", day)
    append_balance_assertion(
        db.session, seed_user["account"], seed_periods[4],
        Decimal("1025.00"), _instant(2026, 3, 3),
    )
    db.session.commit()
    return day


class TestThePageReportsForItsOwner:
    """What it renders, and that it renders nothing alarming when empty."""

    def test_an_account_with_no_recorded_line_says_there_is_nothing_to_compare(
        self, auth_client, seed_user,
    ):
        """An absence, stated -- not an empty table implying agreement."""
        response = auth_client.get(_url(seed_user["account"].id))

        assert response.status_code == 200
        assert b"Nothing to compare yet" in response.data

    def test_it_says_it_changes_nothing(
        self, auth_client, seed_user, a_disagreement,
    ):
        """Ruling R-GF: an instrument, never a gate.

        A page of red figures about money must say on its face that looking at
        it moved nothing, or it reads as an action having been taken.
        """
        response = auth_client.get(_url(seed_user["account"].id))

        assert b"This compares. It changes nothing." in response.data

    def test_a_disagreement_the_BALANCES_hide_is_still_reported(
        self, auth_client, seed_user, a_disagreement,
    ):
        """The measurement the design turns on, rendered.

        On the developer's own account eleven real disagreements read as exact
        agreement in the balance columns, one of them the ``$943.41`` finding
        **N-337** names.  Here the seeded day's two balances are equal and the
        row must still be flagged.
        """
        response = auth_client.get(_url(seed_user["account"].id))
        row = _row_for(response.data.decode(), a_disagreement)

        # Flagged, and showing the residue...
        assert "table-warning" in row
        assert "-$100.00" in row
        # ...on a row whose two BALANCE cells are the same figure.  Those are
        # the last two money cells in column order, and their equality is the
        # whole point: a report built on their difference prints nothing here.
        bank_balance, app_balance = _money_cells(row)[-2:]
        assert bank_balance == app_balance

    def test_it_names_the_day_the_records_begin(
        self, auth_client, db, seed_user, seed_periods,
    ):
        """Finding N-314 labelled rather than counted 83 times."""
        walk = cash_ledger.walk_cash_ledger(
            seed_user["account"].id,
            get_baseline_scenario(seed_user["user"].id).id,
        )
        before = walk.anchor_corrections[0].observed_on - timedelta(days=3)
        _seed_import(
            db, seed_user["account"], stated="1000.00",
            effective_on=before, evidence=_FILE_CHAIN,
            lines=[(before, "-77.00")],
        )
        db.session.commit()

        response = auth_client.get(_url(seed_user["account"].id))

        assert b"before your records begin" in response.data
        # The bank's own line is SHOWN, never hidden.
        assert b"-$77.00" in response.data

    def test_an_account_whose_files_state_no_balance_still_reports_movement(
        self, auth_client, db, seed_user, seed_periods,
    ):
        """The state BOTH of the developer's real imports are in.

        A level-only report renders nothing at all here, which is why the page
        compares movement as well -- and it must SAY the balance columns are
        empty rather than leave two blank columns unexplained.
        """
        _seed_import(
            db, seed_user["account"], stated=None,
            lines=[(date(2026, 3, 2), "-60.00")],
        )
        db.session.commit()

        response = auth_client.get(_url(seed_user["account"].id))

        assert response.status_code == 200
        assert b"No statement here places a balance on a day" in response.data


    def test_a_statement_entirely_in_the_future_says_so_plainly(
        self, auth_client, db, seed_user, seed_periods,
    ):
        """Not "no bank lines" -- the lines are there and none is comparable.

        The page must not tell the owner their account holds no statement while
        it holds a file full of one.
        """
        from app.services.balance_at import BalanceContext

        beyond = BalanceContext.build(seed_user["user"].id).as_of + timedelta(
            days=5,
        )
        _seed_import(
            db, seed_user["account"], stated="1000.00",
            effective_on=beyond, evidence=_FILE_CHAIN,
            lines=[(beyond, "-99.00")],
        )
        db.session.commit()

        response = auth_client.get(_url(seed_user["account"].id))

        assert response.status_code == 200
        assert b"not because there is nothing recorded" in response.data
        assert b"Nothing to compare yet. " not in response.data


class TestThePageSaysWhatTheStepIsOBLIGEDToSay:
    """Every one of these survived a mutation until it was written.

    Ruling **R-GF** and the step's own specification put three obligations on
    this screen -- say which side rests on an assumption, name a constant
    offset, and never truncate silently -- and a fourth follows from the design
    pivot: the true-up must be its own number.  Each was rendered and none was
    asserted, so deleting the markup left the suite green.  Found by
    adversarial review 2026-08-24.
    """

    def test_it_says_when_the_bank_side_rests_on_an_ASSUMPTION(
        self, auth_client, db, seed_user, seed_periods,
    ):
        """The step's explicit "which side rests on an assumption".

        A first import seats its anchor by assumption (**N-342**), which is the
        LIVE state on the developer's own account -- not an edge case.
        """
        _seed_import(
            db, seed_user["account"], stated="1000.00",
            effective_on=date(2026, 3, 3), evidence=_UNCORROBORATED,
            lines=[(date(2026, 3, 3), "-40.00")],
        )
        db.session.commit()

        response = auth_client.get(_url(seed_user["account"].id))

        assert b"Nothing has confirmed that figure" in response.data
        assert b"uncorroborated" in response.data

    def test_NEITHER_paragraph_prescribes_the_export_option_SECU_dropped(
        self, auth_client, db, seed_user, seed_periods,
    ):
        """Plan step X-gc: this page told the owner to tick an option twice.

        Both of its balance paragraphs ended in "export once with your bank's
        running-balance option ticked" -- the no-anchor one, to explain how to
        fill the two empty columns, and the assumed-anchor one, to explain how
        the figure could be checked.  SECU removed that option; all four of the
        developer's exports on disk 2026-08-25 carry no balance column at all.

        **Both states are driven in one test because they are ONE defect** --
        the same dead instruction, twice -- and each arm renders only in its
        own state, so a single-state test would grade half of it.
        """
        _seed_import(
            db, seed_user["account"], stated="1000.00",
            effective_on=date(2026, 3, 3), evidence=_UNCORROBORATED,
            lines=[(date(2026, 3, 3), "-40.00")],
        )
        db.session.commit()
        with_anchor = auth_client.get(
            _url(seed_user["account"].id)
        ).get_data(as_text=True)

        # ...and the other arm, on an account whose file states no balance.
        other = _an_account_with_no_anchor(db, seed_user)
        db.session.commit()
        without_anchor = auth_client.get(
            _url(other.id)
        ).get_data(as_text=True)

        assert "Nothing has confirmed that figure" in with_anchor
        assert "No statement here places a balance on a day" in without_anchor
        for body in (with_anchor, without_anchor):
            assert "running-balance option" not in body
            assert "option ticked" not in body

    def test_a_PROVED_anchor_is_not_captioned_as_an_assumption(
        self, auth_client, db, seed_user, seed_periods,
    ):
        """The other side of the same control, so it discriminates."""
        _seed_import(
            db, seed_user["account"], stated="1000.00",
            effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 3), "-40.00")],
        )
        db.session.commit()

        response = auth_client.get(_url(seed_user["account"].id))

        assert b"Nothing has confirmed that figure" not in response.data
        assert b"proved by the file" in response.data

    def test_a_CONSTANT_offset_is_named_on_the_page(
        self, auth_client, db, seed_user, seed_periods,
    ):
        """R-GF / N-342's stated signature, rendered.

        Two compared days whose movements agree and whose balances stand apart
        by the same figure throughout.
        """
        walk = cash_ledger.walk_cash_ledger(
            seed_user["account"].id,
            get_baseline_scenario(seed_user["user"].id).id,
        )
        opening = walk.anchor_corrections[0].observed_on
        first, second = (
            opening + timedelta(days=1), opening + timedelta(days=2),
        )
        _seed_import(
            db, seed_user["account"], stated="500.00",
            effective_on=second, evidence=_UNCORROBORATED,
            lines=[(first, "-40.00"), (second, "-10.00")],
        )
        _settled(db, seed_user, seed_periods[0], "Groceries", "40.00", first)
        _settled(db, seed_user, seed_periods[0], "Coffee", "10.00", second)
        db.session.commit()

        response = auth_client.get(_url(seed_user["account"].id))

        assert b"the offset is constant" in response.data

    def test_a_TRUNCATED_span_says_so(
        self, auth_client, db, seed_user, seed_periods,
    ):
        """A report of part of the record must not read as the whole record."""
        from app.services.balance_at import BalanceContext

        beyond = BalanceContext.build(seed_user["user"].id).as_of + timedelta(
            days=9,
        )
        _seed_import(
            db, seed_user["account"], stated="1000.00",
            effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 3), "-40.00"), (beyond, "-1.00")],
        )
        db.session.commit()

        response = auth_client.get(_url(seed_user["account"].id))

        assert b"lines run to" in response.data
        assert str(beyond).encode() in response.data

    def test_the_TRUE_UP_is_its_own_column(
        self, auth_client, seed_user, a_disagreement,
    ):
        """One of the THREE numbers the whole design pivot rests on.

        Without it the page shows a residue and two balances that agree, and
        nothing on screen says why -- which is the reading the pivot exists to
        prevent.
        """
        response = auth_client.get(_url(seed_user["account"].id))
        row = _row_for(response.data.decode(), a_disagreement)

        assert b"You corrected" in response.data
        # The seeded assertion moved +100.00 on that day.
        assert "$100.00" in row

    def test_the_two_balances_DIFFERENCE_is_given_not_left_to_the_eye(
        self, auth_client, seed_user, a_disagreement,
    ):
        """The spec's own "difference ... REPORTED per day".

        It was computed and never rendered: a reader had to subtract two
        columns by eye on every row, and days whose balances differ while their
        movements agree were flagged nowhere at all.
        """
        response = auth_client.get(_url(seed_user["account"].id))

        assert b"Apart" in response.data
        assert b"your books against your bank" in response.data


class TestTheDrillDown:
    """The fragment, and the two ways it is asked for wrongly."""

    def test_it_names_both_sides_of_the_day(
        self, auth_client, seed_user, a_disagreement,
    ):
        """A number a reader can act on rather than one they must chase."""
        response = auth_client.get(
            _day_url(seed_user["account"].id, a_disagreement)
        )

        assert response.status_code == 200
        assert b"Card payback" in response.data
        assert b"Your bank posted no line this day" in response.data

    def test_a_malformed_day_answers_404(self, auth_client, seed_user):
        """Nothing composes this URL by hand, so it is tampering or staleness.

        A FIRING control: without the schema's own date rules the value reaches
        the service and dies in a comparison, which is a 500 on a GET anyone
        can probe.
        """
        response = auth_client.get(
            f"/accounts/{seed_user['account'].id}"
            f"/statements/agreement/day?day=2026-13-40"
        )

        assert response.status_code == 404

    def test_a_missing_day_answers_404(self, auth_client, seed_user):
        """The argument is required, and its absence is not a default."""
        response = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements/agreement/day"
        )

        assert response.status_code == 404


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
        """404 for both "not found" and "not yours" -- the security rule."""
        response = auth_client.get(_url(other_users_account))

        assert response.status_code == 404

    def test_the_fragment_answers_404(self, auth_client, other_users_account):
        """Decorated independently of the page, and it leaks more.

        The fragment names merchants and the rows entered against them, so a
        gate on the page alone would disclose a stranger's spending in detail.
        """
        response = auth_client.get(
            _day_url(other_users_account, date(2026, 3, 3))
        )

        assert response.status_code == 404


class TestItRefusesAnAccountWithNoBankStatement:
    """The kind gate, on both endpoints."""

    @pytest.fixture()
    def a_loan_account(self, db, seed_user):
        """Return the id of an AMORTIZING account of this same user."""
        loan_type = (
            db.session.query(AccountType)
            .filter(AccountType.has_amortization.is_(True))
            .first()
        )
        account = Account(
            user_id=seed_user["user"].id,
            account_type_id=loan_type.id,
            name="Van Loan",
        )
        db.session.add(account)
        # COMMITTED, not flushed (plan step balance:X-i3): a query request
        # opens a transaction of its OWN, so a row this fixture only flushed is
        # one the request cannot see.  The 404 below must come from the KIND
        # gate refusing a real loan rather than from a missing row.
        db.session.commit()
        return account.id

    def test_the_page_answers_404_for_a_loan(self, auth_client, a_loan_account):
        """A loan's balance is not a transaction sum and has no statement.

        Ruling D4 / plan step A1, finding B-15.  Without the gate this renders
        a books-vs-bank page whose back link dead-ends on a 404.
        """
        response = auth_client.get(_url(a_loan_account))

        assert response.status_code == 404

    def test_the_fragment_answers_404_for_a_loan(
        self, auth_client, a_loan_account,
    ):
        """The gate a fragment is written without -- measured once already."""
        response = auth_client.get(
            _day_url(a_loan_account, date(2026, 3, 3))
        )

        assert response.status_code == 404


class TestTheStatementsPageLinksHere:
    """A page nothing links to is a page nobody finds."""

    def test_the_statements_page_offers_the_comparison(
        self, auth_client, seed_user,
    ):
        """The one entry point, from the page that records what is compared."""
        response = auth_client.get(
            f"/accounts/{seed_user['account'].id}/statements"
        )

        assert _url(seed_user["account"].id).encode() in response.data
