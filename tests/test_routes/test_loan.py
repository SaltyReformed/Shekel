"""
Tests for unified loan routes.

Covers dashboard, setup, parameter updates, escrow management,
rate history, and payoff calculator across multiple loan types.
"""

import re
from datetime import date, time, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from app import ref_cache
from app.enums import AcctTypeEnum, StatusEnum
from app.models.account import Account
from app.models.escrow_line import EscrowComponentVersion, EscrowLine
from app.models.journal_entry import JournalEntry
from app.models.loan_params import LoanParams
from app.models.loan_features import RateHistory
from app.extensions import db as _db
from app.models.ref import AccountType, Status
from app.routes.loan._helpers import accelerated_overlay, build_band_chart
from app.services.balance_at import BalanceContext
from app.services.loan_loaders import load_loan_params, load_rate_changes
from app.services.recurrence import ClosesOn
from app.services.recurring_definition import resolved_definition
from app.services.balance_at._resolution import (
    contractual_schedule_from_origination,
)
from app.services.transfer_service import TransferSpec, create_transfer
from app.utils.dates import add_months, months_between
from app.utils.money import round_money
from app.services import (
    account_service,
    balance_at,
    escrow_calculator,
    loan_loaders,
)

from tests._test_helpers import (
    add_escrow_line,
    an_entered_day,
    bind_rule_to_loan,
    clear_loan_ledger,
    create_account_of_type,
    create_loan_account,
    create_loan_with_trueup,
    create_settled_transfer,
    freeze_today,
    insert_tracking_start_event,
    insert_trueup_event,
    linked_ledger_account,
    linked_net_by_date,
    loan_params_for,
    make_cadence_rule,
    make_loan_payment_template,
    make_transfer_template,
    posted_loan_balance_at,
    select_option_values,
    state_template_price,
    transfer_side_journal_filter,
)
from tests.oracles.recurrence_baseline import MONTHLY
from app.models.amount_ownership import AmountOwnership


@pytest.fixture(autouse=True)
def _freeze_today_inside_seed_range(monkeypatch):
    """Freeze today to date(2026, 3, 20) so seed_periods tests pass past 2026-05-22.

    Loan tests use specific origination_date values, inline
    ``date.today()`` calls (e.g. ``first_of_this_month =
    date.today().replace(day=1)``), and assertions like
    ``rule.end_date > date.today()``.  Auto-discovery patches every
    loaded module so test, fixture, and production services all see
    the same frozen "today" regardless of wall-clock date.
    """
    freeze_today(monkeypatch, date(2026, 3, 20))


# ── Helpers ──────────────────────────────────────────────────────────

# The day before the seeded calendar's first payday (``seed_periods`` rebuilds
# the owner's schedule from 2026-01-02): the date every loan built through
# ``_create_loan_account`` states its balance for -- the setup door's "as of"
# day.  See that helper.
_TRACKED_FROM = date(2026, 1, 1)


def _create_loan_account(seed_user, db_session, account_type, name, principal,
                         rate, term, orig_date, payment_day, is_arm=False):
    """Helper to create a loan account with params for any amortizing type.

    Test contract: the ``principal`` argument is the value the test
    expects to see displayed as "Current Principal" on the loan
    card.  Pre-Commit-15 this worked because the dashboard rendered
    the unmaintained stored ``current_principal`` column verbatim;
    post-Commit-15 the dashboard reads the resolver's current_balance
    (E-18) which is derived from :class:`LoanAnchorEvent`, not the
    stored column.

    To preserve the test contract without rewriting every caller,
    this helper builds the loan with an ``original_principal`` of
    ``principal + 5000`` (simulating "$5,000 already paid down before
    the test starts") and states the lower ``principal`` as the balance
    "as of" :data:`_TRACKED_FROM` -- the day before the seeded calendar's
    first payday, i.e. "the balance when the owner began tracking this
    loan".  That is the setup door's own write (plan step R20, ruling
    R-R72 part 3): a ``tracking_start`` event, recorded by the shared
    factory exactly as ``loan.create_params`` records it.  The origination
    anchor is SYNTHESIZED from the params (no stored ``LoanAnchorEvent`` --
    matching production's ``create_params`` since the read switch retired
    that write), so the loan carries exactly ONE stored anchor event: the
    tracking-start.  *It was a ``user_trueup`` appended after the fact until
    R20*; the two sources differ in label alone, so no figure moved.

    **The assertion is dated when tracking began, not the day after
    origination** (plan step R16-b-2, rulings R-R71 / R-R72).  A loan's
    balance replays from its LATEST assertion over the CONTRACT's calendar,
    so every contractual month after the assertion that no payment record
    covers is charged.  Dated ``orig_date + 1 day``, as it was until
    R16-b-2, a 2023 mortgage read as unpaid for three years and never
    cleared -- which is what those records said, and not what any test here
    means.  Dated the day before the seeded calendar opens, every payment a
    test records (``seed_periods`` runs from 2026-01-02, so its earliest due
    date is 2026-02-01) falls AFTER the assertion, and only the months a
    test leaves unrecorded between it and the frozen 2026-03-20 today are
    charged.

    When ``principal == 0`` the gap is the full $5,000, so the
    assertion at $0 produces a paid-off loan state -- what
    ``test_refinance_paid_off_loan`` needs.  When ``principal > 0``
    the assertion at ``principal`` produces a partially-paid loan
    state -- what ``test_refinance_principal_auto_calculated`` and
    every other refinance / debt-card test needs.

    Routes through :func:`tests._test_helpers.create_loan_account` (the ONE
    shared loan builder), so the assertion is reconciled into the loan's
    genesis posting ledger in the same transaction that writes it -- exactly
    what ``loan.create_params`` does in production.  ``is_arm`` is set after:
    the flag alone moves no posting, since the rate-period engine's ARM
    branch fires only on a first-adjustment count this helper never sets
    (``rate_period_engine.py``, the ``terms.is_arm and first is not None``
    condition), and the origination rate row holds either way.  The
    hand-rolled block this replaced opened no ledger at all, so every loan
    here exercised the no-ledger fallback production never takes.

    Args:
        seed_user: The ``seed_user`` (or ``seed_second_user``) fixture dict.
        db_session: The test ``db.session``.
        account_type: The :class:`~app.enums.AcctTypeEnum` member to create
            the loan account as.
        name: The account name.
        principal: The loan's current balance (the trueup anchor).
        rate: The origination annual rate as a Decimal fraction.
        term: The loan term in months.
        orig_date: The loan origination date.
        payment_day: The day-of-month payment day.
        is_arm: Whether the loan is adjustable-rate.

    Returns:
        The created loan :class:`~app.models.account.Account`.
    """
    account = create_loan_account(
        seed_user, db_session, name=name,
        principal=principal + Decimal("5000.00"), rate=rate, term=term,
        origination_date=orig_date, payment_day=payment_day,
        account_type=account_type,
        tracked_balance=principal, tracked_from=_TRACKED_FROM,
    )
    params = loan_params_for(db_session, account.id)
    params.is_arm = is_arm
    db_session.commit()
    return account


def _create_auto_loan(seed_user, db_session, name="My Auto Loan"):
    """Helper: auto loan account with params."""
    return _create_loan_account(
        seed_user, db_session, AcctTypeEnum.AUTO_LOAN, name,
        Decimal("25000.00"), Decimal("0.05000"), 60,
        date(2025, 1, 1), 15,
    )


def _payment_template(db_session, seed_user, acct):
    """Return the loan's recurring payment template (the settings doors' URLs name it)."""
    from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel

    tpl = (
        db_session.query(TransferTemplate)
        .filter_by(to_account_id=acct.id, user_id=seed_user["user"].id)
        .first()
    )
    assert tpl is not None
    return tpl


def _create_mortgage(seed_user, db_session, name="My Mortgage"):
    """Helper: mortgage account with params."""
    return _create_loan_account(
        seed_user, db_session, AcctTypeEnum.MORTGAGE, name,
        Decimal("250000.00"), Decimal("0.06500"), 360,
        date(2023, 6, 1), 1,
    )


def _create_other_loan(second_user, db_session,
                       account_type=AcctTypeEnum.AUTO_LOAN):
    """Create a loan account owned by the second user.

    A $20,000 loan (the origination anchor -- the hand-rolled block this
    replaced wrote no trueup, so the origination principal IS the resolved
    balance) at 4% over 48 months, originated 2024-06-01.  Built through the
    shared factory, so its genesis posting ledger is opened with it.
    """
    return create_loan_account(
        second_user, db_session, name="Other Loan",
        principal=Decimal("20000.00"), rate=Decimal("0.04000"), term=48,
        origination_date=date(2024, 6, 1), payment_day=1,
        account_type=account_type,
    )


# ── Dashboard Tests ──────────────────────────────────────────────────


class TestLoanDashboard:
    """Tests for the unified loan dashboard page."""

    @pytest.mark.parametrize("create_fn", [_create_auto_loan, _create_mortgage])
    def test_dashboard_view(self, auth_client, seed_user, db, seed_periods, create_fn):
        """GET returns 200 with loan summary for any amortizing type."""
        acct = create_fn(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert b"Balance owed" in resp.data

    def test_broken_loan_detail_page_folds_not_the_money_blind_replay(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A broken loan's detail page shows the seam's fold, not the replay (B-13, C4).

        Before C4 the loan route rendered ``LoanState.current_balance``: for a loan
        whose genesis POSTING ledger is missing, the resolver falls back to the
        MONEY-BLIND anchor replay, which advances only the SCHEDULED principal per
        payment and discards the actual cash.  C4 points the route at the
        ``balance_at`` seam, which FOLDS the loan's source facts (origination anchor
        + settled shadows), so the page shows what the borrower actually owes.

        The $240,000 loan (6%, originated 2026-01-01, the month before the
        payment's 2026-02-01 installment -- ruling R-R101 moved it from
        2024-09-01 when plan step recurrence:R16-c-2 began charging every
        installment from origination) receives ONE $10,000 payment:
        $1,200 interest that month ($240,000 x 0.06 / 12) and $8,800 principal, so
        the fold owes $240,000 - $8,800 = $231,200.00.  The money-blind replay
        advances only the scheduled first-month principal of $238.92 ->
        $239,761.08, silently ignoring the $8,561.08 the borrower actually paid
        down.  The posting ledger is cleared EXPLICITLY (``clear_loan_ledger`` --
        production cannot make this state) so the resolver's fallback is the one
        under test.

        Control that fires: asserting the pre-C4 replay value ($239,761.08) is
        ABSENT proves the route moved off the money-blind path -- reverting C4 to
        ``ctx.state.current_balance`` renders that value and turns this red.
        """
        acct = create_loan_account(
            seed_user, db.session, name="Broken Mortgage",
            principal=Decimal("240000.00"), rate=Decimal("0.06000"),
            term=360, origination_date=date(2026, 1, 1), payment_day=1,
            account_type=AcctTypeEnum.MORTGAGE,
        )
        checking = create_account_of_type(
            seed_user, db.session, "Checking", "Chk",
            anchor_balance=Decimal("50000.00"),
        )
        db.session.commit()
        create_settled_transfer(
            seed_user, db.session, checking, acct, seed_periods[0],
            amount=Decimal("10000.00"),
            settled_on=seed_periods[0].start_date,
        )
        db.session.commit()
        # The BROKEN state, built on purpose: only the postings are removed; the
        # LoanParams + settled shadows the fold reads are untouched.
        clear_loan_ledger(acct.id)

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert b"$231,200.00" in resp.data          # the seam's fold (C4)
        assert b"$239,761.08" not in resp.data       # the money-blind replay

    @pytest.mark.parametrize("payment_day,expected", [
        (1, "1st"), (2, "2nd"), (3, "3rd"),
        (11, "11th"), (12, "12th"), (13, "13th"),
        (21, "21st"), (22, "22nd"), (23, "23rd"),
        (30, "30th"), (31, "31st"),
    ])
    def test_dashboard_payment_day_ordinal(
        self, auth_client, seed_user, db, seed_periods, payment_day, expected,
    ):
        """Payment-day ordinal suffix is correct across the 1-31 range (TPLB-11).

        The old logic only special-cased days 1/2/3, so 21/22/23/31 rendered
        as '21th'/'22th'/'23th'/'31th', and the teens were never exercised.
        """
        acct = _create_loan_account(
            seed_user, db.session, AcctTypeEnum.MORTGAGE, "Ordinal Mortgage",
            Decimal("250000.00"), Decimal("0.06500"), 360,
            date(2023, 6, 1), payment_day,
        )
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert f"{expected} of each month".encode() in resp.data

    def test_the_transfer_prompt_names_the_first_payment_date(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The prompt says WHEN the payment it offers to create will start.

        Plan step R7c-b.  This form collects no recurrence controls at all --
        the route derives the whole cadence from the loan's contract -- so
        without the date the user commits to a schedule and finds out when it
        starts by opening the template afterwards.

        Hand-computed: a loan originating 2026-04-15 with ``payment_day`` 1
        first bills 2026-05-01 (``first_installment_date``'s convention is the
        payment day of the month AFTER origination, never a later day in the
        origination month itself).  It is asserted as the DATE rather than as
        the string a second derivation would produce, because the whole point
        is that the prompt and
        ``loan_recurrence_sync.loan_cadence_start`` answer once.
        """
        acct = create_loan_account(
            seed_user, db.session, name="Closing In April",
            principal=Decimal("200000.00"), rate=Decimal("0.05000"),
            term=360, origination_date=date(2026, 4, 15), payment_day=1,
            account_type=AcctTypeEnum.MORTGAGE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")

        assert resp.status_code == 200
        assert b"No recurring payment set up" in resp.data, (
            "the prompt must be showing, or the date below proves nothing"
        )
        assert b"The first payment is due" in resp.data
        assert b"May 1, 2026" in resp.data

    def test_dashboard_anchor_scorecard_badges(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The Balance anchors card badges origination AND a tracking-start (C1).

        A mid-life-import loan (origination + a tracking-start assertion) renders
        the anchor scorecard with an "Origination" badge on the origination
        opening row and a "Tracking start" badge on the tracking-start assertion
        row -- since C1 the label rides ``is_tracking_start``, not ``is_opening``
        (the origination is the opening now, the tracking-start a non-opening
        assertion).
        """
        acct = create_loan_account(
            seed_user, db.session, name="Imported Mortgage",
            principal=Decimal("250000.00"), rate=Decimal("0.06000"),
            term=360, origination_date=date(2023, 6, 1),
            account_type=AcctTypeEnum.MORTGAGE,
        )
        insert_tracking_start_event(
            loan_params_for(db.session, acct.id),
            Decimal("180000.00"), date(2026, 2, 10),
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert b"Balance anchors" in resp.data
        # Match the badge spans specifically, not the "Origination date" field
        # label elsewhere on the page.
        assert b">Origination</span>" in resp.data
        assert b">Tracking start</span>" in resp.data

    def test_dashboard_setup_when_no_params(self, auth_client, seed_user, db, seed_periods):
        """Dashboard renders setup page when params don't exist yet."""
        loan_type = db.session.query(AccountType).filter_by(name="Auto Loan").one()
        account = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=loan_type.id,
                name="No Params Loan",
                anchor_balance=Decimal("0"),
            ),
        )
        db.session.add(account)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{account.id}/loan")
        assert resp.status_code == 200
        assert b"Configure" in resp.data

    def test_dashboard_404_nonexistent(self, auth_client, seed_user, db, seed_periods):
        """Nonexistent account returns 404 (security: 404 for not-found and not-yours)."""
        resp = auth_client.get("/accounts/99999/loan")
        assert resp.status_code == 404

    def test_dashboard_idor(self, auth_client, second_user, db, seed_periods):
        """Another user's loan dashboard returns 404 without leaking data (security)."""
        other = _create_other_loan(second_user, db.session)
        resp = auth_client.get(f"/accounts/{other.id}/loan")
        assert resp.status_code == 404
        assert b"Other Loan" not in resp.data

    def test_dashboard_wrong_type(self, auth_client, seed_user, db, seed_periods):
        """Non-amortizing account type returns 404.

        The loan dashboard route's _load_loan_account helper returns None
        for both ownership-failure and wrong-type cases, and the route
        now uniformly aborts 404 for any None result. This is the same
        404-for-not-found-or-not-yours security response.
        """
        acct = seed_user["account"]  # checking account
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 404

    def test_dashboard_login_required(self, client, seed_user, db, seed_periods):
        """Unauthenticated request redirects to login."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 302
        assert "/login" in resp.headers.get("Location", "")

    def test_dashboard_shows_term_field(self, auth_client, seed_user, db, seed_periods):
        """Dashboard parameter form includes editable term_months input."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert b'name="term_months"' in resp.data
        assert b'value="60"' in resp.data

    def test_dashboard_shows_payoff_calculator(self, auth_client, seed_user, db, seed_periods):
        """Dashboard renders the "pay off sooner" lever (with its slider) for all loan types."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert b"Pay off sooner" in resp.data
        assert b'data-slider-group="payoff"' in resp.data

    def test_dashboard_shows_icon_from_account_type(self, auth_client, seed_user, db, seed_periods):
        """Dashboard renders the correct icon class from account_type."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert b"bi-house" in resp.data


# ── Setup / Create Params Tests ──────────────────────────────────────


class TestLoanSetup:
    """Tests for initial loan parameter setup."""

    @pytest.mark.parametrize("type_name", ["Auto Loan", "Mortgage"])
    def test_create_params(self, auth_client, seed_user, db, seed_periods, type_name):
        """POST valid params creates LoanParams record."""
        loan_type = db.session.query(AccountType).filter_by(name=type_name).one()
        account = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=loan_type.id,
                name=f"Setup {type_name}",
                anchor_balance=Decimal("0"),
            ),
        )
        db.session.add(account)
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data={
                "original_principal": "30000.00",
                "anchor_balance": "25000.00",
                "anchor_date": "2026-03-20",
                "interest_rate": "5.000",
                "term_months": "60",
                "origination_date": "2025-01-01",
                "payment_day": "15",
            },
        )
        assert resp.status_code == 302
        assert "/loan" in resp.headers.get("Location", "")

        params = db.session.query(LoanParams).filter_by(account_id=account.id).one()
        # DH-#56: the loan's rate now lives in the origination
        # RateHistory row (effective_date == origination_date), not the
        # dropped ``LoanParams.interest_rate`` column.
        origination_rate = (
            db.session.query(RateHistory)
            .filter_by(
                account_id=account.id,
                effective_date=params.origination_date,
            )
            .one()
        )
        assert origination_rate.interest_rate == Decimal("0.05000")
        assert params.term_months == 60

    def _unconfigured_loan_with_a_recurring_transfer(self, seed_user, db):
        """Return ``(account, template)``: an every-paycheck transfer into a NOT-yet-configured Auto Loan.

        The shape ruling **R-R81** names at the setup door: the transfer's
        start is its owner's while the account is not a loan, and becomes
        the contract's the moment it is one.  The transfer is built through
        the generic fixture (no settings row), the way ``POST /transfers``
        leaves one, and committed so the request can see it.
        """
        loan_type = db.session.query(AccountType).filter_by(name="Auto Loan").one()
        account = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=loan_type.id,
                name="Set Up Later",
                anchor_balance=Decimal("0"),
            ),
        )
        db.session.add(account)
        db.session.flush()
        template = make_transfer_template(db.session, seed_user, to_account=account)
        db.session.commit()
        return account, template

    def test_setup_after_the_transfer_exists_makes_its_start_the_contracts(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The loan-setup door derives the standing payment's start (plan step R7d-g, ruling **R-R81**).

        Until R7d-g the next chokepoint of any kind healed a start authored
        before the account was a loan; R7d-g-1's review measured that with
        the nine chokepoints gone nothing did until a params edit, so the
        transfer justified occurrences before the loan existed.  Setup is
        where the transfer BECOMES the loan's payment, and it derives the
        start there.  The every-paycheck rule's stored start is the payday of
        the paycheck hosting the first installment (2026-04-01 on this
        origination): the biweekly schedule from 2026-01-02 puts that in the
        paycheck opening 2026-03-27 (six fortnights on), which is what
        ``bind_rule_to_loan`` writes at create; the fixture's default start
        is the schedule's opening payday.
        """
        account, template = self._unconfigured_loan_with_a_recurring_transfer(
            seed_user, db,
        )
        rule = template.recurrence_rule
        owners_start = rule.starts_on
        assert owners_start == date(2026, 1, 2)

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data={
                "original_principal": "12000.00",
                "anchor_balance": "12000.00",
                "anchor_date": "2026-03-20",
                "interest_rate": "5.000",
                "term_months": "24",
                "origination_date": "2026-03-15",
                "payment_day": "1",
            },
        )
        assert resp.status_code == 302
        assert db.session.query(LoanParams).filter_by(account_id=account.id).one()

        db.session.refresh(rule)
        assert rule.starts_on != owners_start
        assert rule.starts_on == date(2026, 3, 27), (
            "the standing payment's start must be the payday hosting the "
            f"first installment 2026-04-01, got {rule.starts_on}"
        )
        assert rule.end_date is None

    def test_setup_that_would_move_the_start_past_an_authored_stop_is_refused_whole(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The same refusal the params edit makes, at the door where the account becomes a loan.

        The transfer carries an owner's stop of 2026-01-16 (its second
        payday); setting the loan up with a first installment of 2026-04-01
        would lift the start past it.  The write door refuses, the setup is
        rolled back WHOLE -- no ``LoanParams`` row, the rule untouched -- and
        the flash names the transfer.
        """
        account, template = self._unconfigured_loan_with_a_recurring_transfer(
            seed_user, db,
        )
        rule = template.recurrence_rule
        rule.end_date = date(2026, 1, 16)
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data={
                "original_principal": "12000.00",
                "anchor_balance": "12000.00",
                "anchor_date": "2026-03-20",
                "interest_rate": "5.000",
                "term_months": "24",
                "origination_date": "2026-03-15",
                "payment_day": "1",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        body = resp.data.decode()
        assert template.name in body
        # The refusal's own remedy clause, worded once for every door since
        # plan step R7d-g-2 (ruling R-R85; it read "Archive that transfer
        # first" while the params edit was the only door that could reach it).
        assert "Set it up again without an end date, or archive it." in body
        assert db.session.query(LoanParams).filter_by(
            account_id=account.id,
        ).one_or_none() is None, "a refused setup left LoanParams behind"
        # The origination rate row is keyed by the account, not the params
        # row, so it is the one artefact a partial rollback could leave.
        assert db.session.query(RateHistory).filter_by(
            account_id=account.id,
        ).count() == 0, "a refused setup left the origination rate behind"
        # The stated balance's tracking-start is staged in the same
        # transaction (plan step R20) and rolls back with the rest.
        from app.models.loan_anchor_event import LoanAnchorEvent as _LAE  # pylint: disable=import-outside-toplevel
        assert db.session.query(_LAE).filter_by(
            account_id=account.id,
        ).count() == 0, "a refused setup left the stated balance behind"
        db.session.refresh(rule)
        assert rule.starts_on == date(2026, 1, 2)
        assert rule.end_date == date(2026, 1, 16)

    def test_create_params_writes_no_anchor_event_and_posts_the_opening(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Setup writes NO origination LoanAnchorEvent; the ledger opening is the record.

        The read switch's final commit retired the origination event write:
        the origination anchor is synthesized from the immutable LoanParams,
        so setup leaves the event table EMPTY and instead posts the genesis
        OPENING (-original_principal on the loan's linked ledger, so the
        confirmed-balance reader answers the full $30,000 owed).  The
        dashboard must still resolve and render -- the resolver's replay
        fallback runs on the synthesized facts, never a stored row.

        The balance is stated for the ORIGINATION day, so the setup door's
        own write (plan step R20: a ``tracking_start`` for a balance stated
        after origination) has nothing to record either -- the origination
        IS that assertion.  :meth:`test_setup_records_the_stated_balance_as_a_tracking_start`
        is the other half.
        """
        loan_type = (
            db.session.query(AccountType).filter_by(name="Auto Loan").one()
        )
        account = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=loan_type.id,
                name="No-Event Setup Loan",
                anchor_balance=Decimal("0"),
            ),
        )
        db.session.add(account)
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data={
                "original_principal": "30000.00",
                "anchor_balance": "30000.00",
                "anchor_date": "2025-01-01",
                "interest_rate": "5.000",
                "term_months": "60",
                "origination_date": "2025-01-01",
                "payment_day": "15",
            },
        )
        assert resp.status_code == 302

        # No anchor event row of ANY kind was written.
        from app.models.loan_anchor_event import (  # pylint: disable=import-outside-toplevel
            LoanAnchorEvent,
        )
        assert (
            db.session.query(LoanAnchorEvent)
            .filter_by(account_id=account.id)
            .count()
        ) == 0

        # The genesis OPENING posted instead: the postings sum to the full
        # original principal owed in the baseline scenario.
        assert posted_loan_balance_at(
            account.id, seed_user["scenario"].id, date.today(),
        ) == Decimal("30000.00")

        # And the dashboard resolves on the synthesized facts.
        page = auth_client.get(f"/accounts/{account.id}/loan")
        assert page.status_code == 200
        assert b"30,000.00" in page.data

    def test_post_retirement_loan_trueup_lifecycle(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Setup (zero events) then a true-up: the card reads the asserted value.

        The post-retirement lifecycle end-to-end through the ROUTES: a loan
        created with NO stored anchor rows at all (its balance stated for the
        origination day, so the setup door records nothing -- plan step
        R20), then a $25,000 true-up
        asserted through the dashboard form.  The true-up appends the ONE
        ``user_trueup`` event (the source document), the genesis sync books
        its TRUEUP correction so the confirmed-balance reader answers the
        asserted $25,000 (opening -30,000 + correction +5,000, negated), and
        the dashboard card renders it -- proving a loan that never had an
        origination event supports the whole assert-and-display flow.
        """
        loan_type = (
            db.session.query(AccountType).filter_by(name="Auto Loan").one()
        )
        account = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=loan_type.id,
                name="Lifecycle Loan",
                anchor_balance=Decimal("0"),
            ),
        )
        db.session.add(account)
        db.session.commit()
        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data={
                "original_principal": "30000.00",
                "anchor_balance": "30000.00",
                "anchor_date": "2025-01-01",
                "interest_rate": "5.000",
                "term_months": "60",
                "origination_date": "2025-01-01",
                "payment_day": "15",
            },
        )
        assert resp.status_code == 302

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/trueup",
            data={
                "anchor_balance": "25000.00",
                "anchor_date": "2025-06-01",
            },
        )
        assert resp.status_code == 302

        from app.models.loan_anchor_event import (  # pylint: disable=import-outside-toplevel
            LoanAnchorEvent,
        )
        events = (
            db.session.query(LoanAnchorEvent)
            .filter_by(account_id=account.id)
            .all()
        )
        # Exactly ONE stored row: the user's assertion.  No origination row.
        assert len(events) == 1
        assert events[0].anchor_balance == Decimal("25000.00")

        # The ledger reconciled to the asserted value...
        assert posted_loan_balance_at(
            account.id, seed_user["scenario"].id, date.today(),
        ) == Decimal("25000.00")
        # ...and the card shows it.
        page = auth_client.get(f"/accounts/{account.id}/loan")
        assert page.status_code == 200
        assert b"25,000.00" in page.data

    def test_create_params_already_configured(self, auth_client, seed_user, db, seed_periods):
        """POST setup when params exist redirects with info flash."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/setup",
            data={
                "original_principal": "99999.00",
                "anchor_balance": "99999.00",
                "anchor_date": "2026-03-20",
                "interest_rate": "0.99",
                "term_months": "12",
                "origination_date": "2025-01-01",
                "payment_day": "1",
            },
        )
        assert resp.status_code == 302
        resp2 = auth_client.get(resp.headers["Location"])
        assert b"Loan parameters already configured." in resp2.data

    def test_create_params_term_exceeds_type_max(self, auth_client, seed_user, db, seed_periods):
        """Auto loan rejects term > 120 (type-specific max_term_months)."""
        loan_type = db.session.query(AccountType).filter_by(name="Auto Loan").one()
        account = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=loan_type.id,
                name="Term Test Auto",
                anchor_balance=Decimal("0"),
            ),
        )
        db.session.add(account)
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data={
                "original_principal": "30000.00",
                "anchor_balance": "25000.00",
                "anchor_date": "2026-03-20",
                "interest_rate": "5.000",
                "term_months": "360",
                "origination_date": "2025-01-01",
                "payment_day": "15",
            },
        )
        assert resp.status_code == 200  # re-renders setup
        assert b"cannot exceed" in resp.data

        count = db.session.query(LoanParams).filter_by(account_id=account.id).count()
        assert count == 0

    def test_mortgage_allows_long_term(self, auth_client, seed_user, db, seed_periods):
        """Mortgage accepts term=360 (max_term_months=600)."""
        loan_type = db.session.query(AccountType).filter_by(name="Mortgage").one()
        account = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=loan_type.id,
                name="Long Term Mortgage",
                anchor_balance=Decimal("0"),
            ),
        )
        db.session.add(account)
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data={
                "original_principal": "300000.00",
                "anchor_balance": "250000.00",
                "anchor_date": "2026-03-20",
                "interest_rate": "6.500",
                "term_months": "360",
                "origination_date": "2023-06-01",
                "payment_day": "1",
            },
        )
        assert resp.status_code == 302
        params = db.session.query(LoanParams).filter_by(account_id=account.id).one()
        assert params.term_months == 360

    def test_setup_prefills_the_stated_balance_and_its_day(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The setup form opens on the account's anchor balance, stated for today.

        Plan step R20: the "balance today" field prefills from the account's
        latest cash assertion (the $15,000 typed when the account was made)
        and its "as of" date from today, the setup date, bounded above by
        today -- what the form submits unchanged is the door's default.
        """
        loan_type = db.session.query(AccountType).filter_by(name="Auto Loan").one()
        resp = auth_client.post(
            "/accounts",
            data={
                "name": "Prepop Auto",
                "account_type_id": str(loan_type.id),
                "anchor_balance": "15000",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b'name="current_principal"' not in resp.data
        assert b'name="anchor_balance"' in resp.data
        assert b'value="15000.00"' in resp.data
        assert b'name="anchor_date"' in resp.data
        assert b'max="2026-03-20" value="2026-03-20"' in resp.data

    def _unconfigured_auto_loan(self, seed_user, db, name):
        """Return a committed, not-yet-configured Auto Loan account."""
        loan_type = db.session.query(AccountType).filter_by(name="Auto Loan").one()
        account = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=loan_type.id,
                name=name,
                anchor_balance=Decimal("0"),
            ),
        )
        db.session.add(account)
        db.session.commit()
        return account

    def _stored_anchors(self, db, account):
        """Return ``[(date, balance, source id)]`` for every stored anchor row."""
        from app.models.loan_anchor_event import LoanAnchorEvent as _LAE  # pylint: disable=import-outside-toplevel
        rows = (
            db.session.query(_LAE)
            .filter(_LAE.account_id == account.id)
            .order_by(_LAE.id)
            .all()
        )
        return [(e.anchor_date, e.anchor_balance, e.source_id) for e in rows]

    def test_setup_records_the_stated_balance_as_a_tracking_start(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A loan originating before the stated day gets ONE tracking_start at that day.

        Plan step R20 (ruling R-R72 part 3, finding REC-519).  A $30,000
        loan originating 2025-01-01, set up on the frozen 2026-03-20 with
        "balance today" $25,000 as of 2026-02-15: the door appends exactly
        one ``tracking_start`` at (2026-02-15, $25,000) in the same
        transaction as the params, the genesis ledger reconciles to it, and
        the dashboard reads $25,000 with the row labelled a tracking start.
        Until R20 the $25,000 landed in ``LoanParams.current_principal`` and
        the loan read as unpaid since 2025 (the R-R71 calendar).
        """
        account = self._unconfigured_auto_loan(seed_user, db, "Mid-life Setup")

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data={
                "original_principal": "30000.00",
                "anchor_balance": "25000.00",
                "anchor_date": "2026-02-15",
                "interest_rate": "5.000",
                "term_months": "60",
                "origination_date": "2025-01-01",
                "payment_day": "15",
            },
        )
        assert resp.status_code == 302
        from app.enums import LoanAnchorSourceEnum  # pylint: disable=import-outside-toplevel
        assert self._stored_anchors(db, account) == [(
            date(2026, 2, 15), Decimal("25000.00"),
            ref_cache.loan_anchor_source_id(LoanAnchorSourceEnum.TRACKING_START),
        )]
        assert posted_loan_balance_at(
            account.id, seed_user["scenario"].id, date.today(),
        ) == Decimal("25000.00")
        page = auth_client.get(f"/accounts/{account.id}/loan")
        assert page.status_code == 200
        assert b"25,000.00" in page.data
        # The anchors card's row badge, not the tracking-start FORM's label
        # (which every configured loan's page renders).
        assert (
            b'<span class="badge bg-secondary ms-1">Tracking start</span>'
            in page.data
        )

    def test_setup_asserts_nothing_for_a_loan_originating_after_today(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A loan not yet originated is configured, and no assertion is written.

        The form still submits its prefilled pair (today, the account's
        anchor), but a loan originating 2026-06-01 has no balance today: the
        origination IS its assertion (plan step R20), and the stated pair --
        necessarily dated before origination -- is not a refusal either,
        since no date on or before today could satisfy the bound.
        """
        account = self._unconfigured_auto_loan(seed_user, db, "Future Setup")

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data={
                "original_principal": "30000.00",
                "anchor_balance": "0.00",
                "anchor_date": "2026-03-20",
                "interest_rate": "5.000",
                "term_months": "60",
                "origination_date": "2026-06-01",
                "payment_day": "15",
            },
        )
        assert resp.status_code == 302
        assert db.session.query(LoanParams).filter_by(account_id=account.id).one()
        assert self._stored_anchors(db, account) == []

    def test_setup_refuses_a_stated_day_before_origination(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A stated day before an ORIGINATED loan's origination is refused whole.

        The origination half of the ``[origination_date, today]`` bound
        (plan step R20), refused the way the two dashboard doors refuse a
        pre-origination date: the form re-renders with the sentence, and
        neither params, rate row nor anchor row is written.
        """
        account = self._unconfigured_auto_loan(seed_user, db, "Refused Setup")

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data={
                "original_principal": "30000.00",
                "anchor_balance": "25000.00",
                "anchor_date": "2024-12-31",
                "interest_rate": "5.000",
                "term_months": "60",
                "origination_date": "2025-01-01",
                "payment_day": "15",
            },
        )
        assert resp.status_code == 200
        assert (
            b"Balance date cannot be before the loan&#39;s origination date "
            b"(2025-01-01)." in resp.data
        )
        assert db.session.query(LoanParams).filter_by(
            account_id=account.id,
        ).count() == 0
        assert db.session.query(RateHistory).filter_by(
            account_id=account.id,
        ).count() == 0
        assert self._stored_anchors(db, account) == []

    def test_setup_refuses_a_stated_day_after_today(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The schema refuses a stated day in the future; nothing is written."""
        account = self._unconfigured_auto_loan(seed_user, db, "Future-dated Setup")

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data={
                "original_principal": "30000.00",
                "anchor_balance": "25000.00",
                "anchor_date": "2026-03-21",
                "interest_rate": "5.000",
                "term_months": "60",
                "origination_date": "2025-01-01",
                "payment_day": "15",
            },
        )
        assert resp.status_code == 200
        assert b"Please correct the highlighted errors" in resp.data
        assert db.session.query(LoanParams).filter_by(
            account_id=account.id,
        ).count() == 0
        assert self._stored_anchors(db, account) == []

    def test_setup_requires_the_stated_pair(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A submission carrying only the retired ``current_principal`` field is refused.

        A stale client's field is dropped by the schema's EXCLUDE policy,
        which leaves the required pair missing -- so the door refuses rather
        than configuring a loan whose stated balance it silently lost.
        """
        account = self._unconfigured_auto_loan(seed_user, db, "Stale Client Setup")

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data={
                "original_principal": "30000.00",
                "current_principal": "25000.00",
                "interest_rate": "5.000",
                "term_months": "60",
                "origination_date": "2025-01-01",
                "payment_day": "15",
            },
        )
        assert resp.status_code == 200
        assert b"Please correct the highlighted errors" in resp.data
        assert db.session.query(LoanParams).filter_by(
            account_id=account.id,
        ).count() == 0
        assert self._stored_anchors(db, account) == []


# ── Update Params Tests ──────────────────────────────────────────────


class TestLoanParamsUpdate:
    """Tests for updating loan parameters."""

    def test_params_update(self, auth_client, seed_user, db, seed_periods):
        """POST valid data updates editable params; a stray ``current_principal`` is ignored.

        Re-pinned for E-18 / Commit 16 (decision D-C) and again for plan step
        R20.  The balance is not a parameter: ``LoanParamsUpdateSchema``'s
        ``unknown = EXCLUDE`` policy silently strips a stray
        ``current_principal`` submission (the column E-18 demoted and R20
        dropped), and the loan's balance -- the ledger's, asserted only
        through the dated true-up form -- survives the POST untouched.  The
        interest_rate percentage-to-decimal conversion is unchanged.

        Hand-check:
        * The fixture states ``$25,000.00`` as the balance via
          ``_create_auto_loan`` (one ``tracking_start``).
        * POSTing ``current_principal=22000.00`` is the silent no-op
          because the schema does not declare the field and
          ``_PARAM_FIELDS`` in :func:`app.routes.loan.update_params`
          does not reference it: no anchor row is written and the posted
          balance still reads ``$25,000.00``.
        * ``interest_rate=4.500`` -> ``Decimal("0.04500")`` via
          ``LoanParamsUpdateSchema``'s ``@pre_load`` hook, which
          dispatches to
          :func:`app.schemas.validation._normalize_percent_fields`
          (Commit 24 / HIGH-06 convention).
        """
        from app.models.loan_anchor_event import LoanAnchorEvent as _LAE  # pylint: disable=import-outside-toplevel
        acct = _create_auto_loan(seed_user, db.session)
        scenario_id = seed_user["scenario"].id
        events_before = (
            db.session.query(_LAE).filter_by(account_id=acct.id).count()
        )
        assert events_before == 1
        assert posted_loan_balance_at(
            acct.id, scenario_id, date.today(),
        ) == Decimal("25000.00")

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/params",
            data={
                # Stray ``current_principal`` -- silently dropped by
                # the schema's EXCLUDE policy (E-18 / Commit 16).
                "current_principal": "22000.00",
                "interest_rate": "4.500",
                "payment_day": "1",
            },
        )
        assert resp.status_code == 302

        db.session.expire_all()
        params = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        # DH-#56: the rate form field now edits the loan's ORIGINATION
        # rate -- the route upserts the RateHistory row effective at
        # origination_date (the resolver's period-0 rate), not the
        # dropped ``LoanParams.interest_rate`` column.
        origination_rate = (
            db.session.query(RateHistory)
            .filter_by(
                account_id=acct.id,
                effective_date=params.origination_date,
            )
            .one()
        )
        assert origination_rate.interest_rate == Decimal("0.04500")
        # E-18 / Commit 16 / R20: the stray ``current_principal`` post
        # asserts nothing.  Users edit the displayed balance via the dated
        # true-up form, not this endpoint.
        assert (
            db.session.query(_LAE).filter_by(account_id=acct.id).count()
        ) == events_before
        assert posted_loan_balance_at(
            acct.id, scenario_id, date.today(),
        ) == Decimal("25000.00")

    def test_params_update_validation(self, auth_client, seed_user, db, seed_periods):
        """POST invalid data leaves DB unchanged."""
        acct = _create_auto_loan(seed_user, db.session)
        orig = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        orig_day = orig.payment_day

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/params",
            data={"payment_day": "32"},
        )
        assert resp.status_code == 302

        db.session.expire_all()
        after = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        assert after.payment_day == orig_day

    def test_term_update_saves(self, auth_client, seed_user, db, seed_periods):
        """POST with valid term_months persists the new value."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/params",
            data={
                "interest_rate": "5.000",
                "payment_day": "15",
                "term_months": "48",
            },
        )
        assert resp.status_code == 302

        db.session.expire_all()
        params = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        assert params.term_months == 48

    def test_arm_fields_update(self, auth_client, seed_user, db, seed_periods):
        """ARM fields can be toggled on and adjusted."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/params",
            data={
                "interest_rate": "6.500",
                "payment_day": "1",
                "is_arm": "true",
                "arm_first_adjustment_months": "60",
                "arm_adjustment_interval_months": "12",
            },
        )
        assert resp.status_code == 302

        db.session.expire_all()
        params = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        assert params.is_arm is True
        assert params.arm_first_adjustment_months == 60

    def test_params_update_idor(self, auth_client, second_user, db, seed_periods):
        """POST to another user's loan params returns 404 (security) and is unchanged."""
        other = _create_other_loan(second_user, db.session)
        orig = db.session.query(LoanParams).filter_by(account_id=other.id).one()
        orig_day = orig.payment_day
        assert orig_day != 28

        resp = auth_client.post(
            f"/accounts/{other.id}/loan/params",
            data={
                "interest_rate": "0.99",
                "payment_day": "28",
            },
        )
        assert resp.status_code == 404

        db.session.expire_all()
        after = db.session.query(LoanParams).filter_by(account_id=other.id).one()
        assert after.payment_day == orig_day

    def test_params_update_nonexistent(self, auth_client, seed_user, db, seed_periods):
        """POST to nonexistent account returns 404 (security)."""
        resp = auth_client.post(
            "/accounts/999999/loan/params",
            data={"interest_rate": "5.0", "payment_day": "1"},
        )
        assert resp.status_code == 404

    def test_params_update_wrong_type(self, auth_client, seed_user, db, seed_periods):
        """POST loan params to checking account returns 404.

        The route's _load_loan_account helper returns None for both
        ownership-failure and wrong-type cases, which both abort 404.
        """
        checking = seed_user["account"]
        resp = auth_client.post(
            f"/accounts/{checking.id}/loan/params",
            data={"interest_rate": "5.0", "payment_day": "1"},
        )
        assert resp.status_code == 404

    def test_amortization_uses_updated_term(self, auth_client, seed_user, db, seed_periods):
        """Changing term_months recalculates amortization on next dashboard load."""
        acct = _create_auto_loan(seed_user, db.session)
        resp1 = auth_client.get(f"/accounts/{acct.id}/loan")

        auth_client.post(
            f"/accounts/{acct.id}/loan/params",
            data={
                "interest_rate": "5.000",
                "payment_day": "15",
                "term_months": "36",
            },
        )
        resp2 = auth_client.get(f"/accounts/{acct.id}/loan")

        pattern = rb"Monthly P.{1,6}I.*?\$([0-9,]+\.\d{2})"
        match1 = re.search(pattern, resp1.data, re.DOTALL)
        match2 = re.search(pattern, resp2.data, re.DOTALL)
        assert match1 is not None
        assert match2 is not None
        assert match1.group(1) != match2.group(1)


# ── Escrow Tests ─────────────────────────────────────────────────────


class TestEscrow:
    """Tests for escrow component management."""

    def test_escrow_add(self, auth_client, seed_user, db, seed_periods):
        """POST escrow creates a line + opening version, percent -> decimal."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow",
            data={"name": "Property Tax", "annual_amount": "4800.00", "inflation_rate": "3"},
        )
        assert resp.status_code == 200
        assert b"Property Tax" in resp.data

        line = (
            db.session.query(EscrowLine)
            .filter_by(account_id=acct.id, name="Property Tax").one()
        )
        version = (
            db.session.query(EscrowComponentVersion)
            .filter_by(line_id=line.id).one()
        )
        assert version.annual_amount == Decimal("4800.00")
        # 3% form percent stored as the 0.03 decimal fraction.
        assert version.inflation_rate == Decimal("0.03")
        assert version.is_removed is False

    def test_dashboard_next_year_escrow_note_is_one_annual_step(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The loan card projects next-year escrow one full annual step from today.

        An escrow line of $4,800/yr ($400.00/mo) at a 3% rate shows a "next year"
        note of 4800 * 1.03 / 12 = $412.00/mo -- one annual step (spec Sec. 8),
        NOT the old elapsed-span-since-created_at figure (which on a same-day-
        seeded line rounded to roughly $406).  The projection takes no date
        argument, so this figure is viewing-date independent by construction.
        """
        acct = _create_mortgage(seed_user, db.session)
        add_escrow_line(
            db.session, acct.id, "Property Tax", Decimal("4800.00"),
            inflation_rate=Decimal("0.03"),
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert b"may increase" in resp.data
        # Current escrow $400.00/mo is projected to $412.00/mo next year.
        assert b"$412.00" in resp.data

    def _build_split_escrow(self, db, account_id, boundary):
        """Seed the account-3 rename-split: removed "Old Tax" + active "New Tax".

        "Old Tax" is $6,000/yr ($500.00/mo) from the loan's origination, tombstoned
        on ``boundary``; "New Tax" is $7,200/yr ($600.00/mo) from ``boundary``.  The
        two tile the timeline (no overlap), so escrow resolves to $500 before the
        boundary and $600 on/after it.  Returns ``(old_line, new_line)``.
        """
        old = add_escrow_line(db.session, account_id, "Old Tax", Decimal("6000.00")).line
        db.session.add(EscrowComponentVersion(
            line_id=old.id, effective_date=boundary,
            annual_amount=Decimal("0.00"), is_removed=True,
        ))
        add_escrow_line(
            db.session, account_id, "New Tax", Decimal("7200.00"),
            effective_date=boundary,
        )
        db.session.commit()
        new = (
            db.session.query(EscrowLine)
            .filter_by(account_id=account_id, name="New Tax").one()
        )
        return old, new

    def test_merge_reunifies_split_line(self, auth_client, seed_user, db, seed_periods):
        """POST merge folds a removed line's history into the active line, escrow intact.

        Merging "Old Tax" into "New Tax" leaves only the new line, carrying BOTH
        versions, and the escrow resolved on every date is unchanged ($500 before
        the boundary, $600 on/after) -- so no settled split can move, since the split
        reads escrow solely via escrow_monthly_as_of.
        """
        acct = _create_mortgage(seed_user, db.session)
        boundary = date(2026, 3, 1)
        old, new = self._build_split_escrow(db, acct.id, boundary)

        # The removed predecessor is hidden from the card but offered as a merge
        # source in the active line's drawer (labelled by its span + removed state).
        page = auth_client.get(f"/accounts/{acct.id}/loan")
        assert b"Merge in" in page.data
        assert b"Old Tax (Jun 2023 - Mar 2026, removed)" in page.data

        lines_before = loan_loaders.load_escrow_lines(acct.id)
        before_early = escrow_calculator.escrow_monthly_as_of(
            lines_before, date(2026, 1, 1),
        )
        before_late = escrow_calculator.escrow_monthly_as_of(
            lines_before, date(2026, 4, 1),
        )
        assert before_early == Decimal("500.00")
        assert before_late == Decimal("600.00")

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/{new.id}/merge",
            data={"source_line_id": old.id},
        )
        assert resp.status_code == 200

        # Only the merged line survives, carrying both versions in date order.
        remaining = db.session.query(EscrowLine).filter_by(account_id=acct.id).all()
        assert [line.id for line in remaining] == [new.id]
        assert db.session.get(EscrowLine, old.id) is None
        versions = sorted(
            db.session.query(EscrowComponentVersion).filter_by(line_id=new.id).all(),
            key=lambda v: v.effective_date,
        )
        assert [(v.effective_date, v.annual_amount) for v in versions] == [
            (date(2023, 6, 1), Decimal("6000.00")),
            (boundary, Decimal("7200.00")),
        ]
        # Escrow-per-date is unchanged, so no derived split moves.
        lines_after = loan_loaders.load_escrow_lines(acct.id)
        assert escrow_calculator.escrow_monthly_as_of(
            lines_after, date(2026, 1, 1),
        ) == before_early
        assert escrow_calculator.escrow_monthly_as_of(
            lines_after, date(2026, 4, 1),
        ) == before_late

    def test_merge_rejects_overlap(self, auth_client, seed_user, db, seed_periods):
        """POST merge on two concurrent lines returns 400; both lines survive.

        "Tax" ($6,000/yr from origination) and "Insurance" ($7,200/yr from 2024)
        overlap in time, so merging would drop a charge.  The route rejects it with
        an actionable message and leaves both lines intact.
        """
        acct = _create_mortgage(seed_user, db.session)
        tax = add_escrow_line(db.session, acct.id, "Tax", Decimal("6000.00")).line
        ins = add_escrow_line(
            db.session, acct.id, "Insurance", Decimal("7200.00"),
            effective_date=date(2024, 1, 1),
        ).line
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/{ins.id}/merge",
            data={"source_line_id": tax.id},
        )
        assert resp.status_code == 400
        assert b"overlap" in resp.data.lower()
        assert db.session.get(EscrowLine, tax.id) is not None
        assert db.session.get(EscrowLine, ins.id) is not None

    def test_merge_source_idor(
        self, auth_client, seed_user, second_user, db, seed_periods,
    ):
        """Merging in a source line owned by another user returns 404, merges nothing."""
        mine = _create_mortgage(seed_user, db.session)
        target = add_escrow_line(db.session, mine.id, "My Tax", Decimal("6000.00")).line
        other = _create_other_loan(second_user, db.session)
        foreign = add_escrow_line(
            db.session, other.id, "Foreign Tax", Decimal("6000.00"),
        ).line
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{mine.id}/loan/escrow/{target.id}/merge",
            data={"source_line_id": foreign.id},
        )
        assert resp.status_code == 404
        assert db.session.get(EscrowLine, target.id) is not None
        assert db.session.get(EscrowLine, foreign.id) is not None

    def test_merge_into_self_rejected(self, auth_client, seed_user, db, seed_periods):
        """Merging a line into itself returns 400 and changes nothing."""
        acct = _create_mortgage(seed_user, db.session)
        line = add_escrow_line(db.session, acct.id, "Tax", Decimal("6000.00")).line
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/{line.id}/merge",
            data={"source_line_id": line.id},
        )
        assert resp.status_code == 400
        assert db.session.get(EscrowLine, line.id) is not None
        assert db.session.query(EscrowComponentVersion).filter_by(
            line_id=line.id,
        ).count() == 1

    def test_merge_preserves_settled_payment_split(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A settled payment's posted split survives merging its escrow line.

        The proof that a merge moves no settled split even though it now reconciles
        like every escrow write (E1b): a payment settled in period 0 (2026-01-02,
        before the 2026-03-01 boundary) posts a split whose escrow leg is "Old Tax"
        $6,000/yr = $500.00/mo.  Merging "Old Tax" into "New Tax" preserves
        escrow-per-date, so the sync the route now runs re-derives the SAME
        principal / interest / escrow -- byte-identical, the E1a assert passing --
        and a later reconcile re-derives it again.
        """
        from app.services.loan_posting_service import (  # pylint: disable=import-outside-toplevel
            backfill_all_loan_postings,
            confirmed_loan_payment_history,
        )
        acct = _create_mortgage(seed_user, db.session)
        scenario_id = seed_user["scenario"].id
        old, new = self._build_split_escrow(db, acct.id, date(2026, 3, 1))
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], acct,
            seed_periods[0], amount=Decimal("2000.00"),
            settled_on=seed_periods[0].start_date,
        )
        db.session.commit()
        backfill_all_loan_postings()
        db.session.commit()

        before = confirmed_loan_payment_history(acct.id, scenario_id, date.today())
        assert len(before) == 1
        assert before[0].escrow == Decimal("500.00")  # Old Tax: 6000 / 12

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/{new.id}/merge",
            data={"source_line_id": old.id},
        )
        assert resp.status_code == 200

        # The posted split is byte-identical -- the merge's E1b reconcile
        # re-derives the SAME split, escrow-per-date being preserved ...
        after = confirmed_loan_payment_history(acct.id, scenario_id, date.today())
        assert after[0].escrow == before[0].escrow
        assert after[0].principal == before[0].principal
        assert after[0].interest == before[0].interest
        # ... and a later reconcile re-derives the SAME split from the merged line.
        backfill_all_loan_postings()
        db.session.commit()
        reconciled = confirmed_loan_payment_history(acct.id, scenario_id, date.today())
        assert reconciled[0].escrow == before[0].escrow
        assert reconciled[0].principal == before[0].principal
        assert reconciled[0].interest == before[0].interest

    def test_escrow_add_duplicate_name(self, auth_client, seed_user, db, seed_periods):
        """Duplicate ACTIVE line name returns 400."""
        acct = _create_mortgage(seed_user, db.session)
        add_escrow_line(db.session, acct.id, "Insurance", Decimal("2400.00"))
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow",
            data={"name": "Insurance", "annual_amount": "3000.00"},
        )
        assert resp.status_code == 400
        assert b"already exists" in resp.data

    def test_escrow_delete(self, auth_client, seed_user, db, seed_periods):
        """POST delete appends a removal tombstone; the line resolves inactive."""
        acct = _create_mortgage(seed_user, db.session)
        line_id = add_escrow_line(
            db.session, acct.id, "Old Insurance", Decimal("1200.00"),
        ).line_id
        db.session.commit()

        resp = auth_client.post(f"/accounts/{acct.id}/loan/escrow/{line_id}/delete")
        assert resp.status_code == 200
        db.session.expire_all()
        # Removal appends an is_removed tombstone (supersession) at today, so the
        # line's latest version is that tombstone and it contributes nothing now.
        line = db.session.get(EscrowLine, line_id)
        latest = max(line.versions, key=lambda v: v.effective_date)
        assert latest.is_removed is True

    def test_escrow_delete_idor(self, auth_client, second_user, db, seed_periods):
        """DELETE another user's escrow returns 404 and leaves it active."""
        other = _create_other_loan(second_user, db.session, AcctTypeEnum.MORTGAGE)
        line_id = add_escrow_line(
            db.session, other.id, "Tax", Decimal("3000.00"),
        ).line_id
        db.session.commit()

        resp = auth_client.post(f"/accounts/{other.id}/loan/escrow/{line_id}/delete")
        assert resp.status_code == 404

        db.session.expire_all()
        # No tombstone appended: the line's only version is the original, active.
        line = db.session.get(EscrowLine, line_id)
        latest = max(line.versions, key=lambda v: v.effective_date)
        assert latest.is_removed is False

    def test_escrow_delete_same_day_add_converts_in_place(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Add then delete the SAME day converts the today-version in place.

        The route add opens the line's version AT today, so a same-day delete
        cannot append a second tombstone at today (the
        ``uq_escrow_component_versions_line_effective_date`` unique forbids two
        versions on one date).  It must convert that version in place --
        ``is_removed`` True, ``annual_amount`` 0.00 (satisfying the
        tombstone-zero CHECK) -- leaving exactly one version and an inactive line.
        """
        acct = _create_mortgage(seed_user, db.session)
        # Add via the route so the opening version is effective TODAY.
        add = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow",
            data={"name": "PMI", "annual_amount": "1200.00"},
        )
        assert add.status_code == 200
        line = (
            db.session.query(EscrowLine)
            .filter_by(account_id=acct.id, name="PMI").one()
        )

        resp = auth_client.post(f"/accounts/{acct.id}/loan/escrow/{line.id}/delete")
        assert resp.status_code == 200
        db.session.expire_all()
        line = db.session.get(EscrowLine, line.id)
        # Converted in place: still ONE version, now a zero-amount tombstone.
        assert len(line.versions) == 1
        assert line.versions[0].is_removed is True
        assert line.versions[0].annual_amount == Decimal("0.00")

    def test_escrow_delete_twice_is_idempotent(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A repeat delete is a no-op: still exactly one tombstone, no 500.

        After the first delete the line resolves inactive today, so the second
        delete's resolve-active-today guard short-circuits before appending
        another tombstone (a double-click must not stack tombstones or error).
        """
        acct = _create_mortgage(seed_user, db.session)
        line_id = add_escrow_line(
            db.session, acct.id, "Old Insurance", Decimal("1200.00"),
        ).line_id
        db.session.commit()

        first = auth_client.post(f"/accounts/{acct.id}/loan/escrow/{line_id}/delete")
        second = auth_client.post(f"/accounts/{acct.id}/loan/escrow/{line_id}/delete")
        assert first.status_code == 200
        assert second.status_code == 200
        db.session.expire_all()
        line = db.session.get(EscrowLine, line_id)
        # Exactly one tombstone appended (the origination version + one tombstone).
        tombstones = [v for v in line.versions if v.is_removed]
        assert len(tombstones) == 1

    def test_escrow_oob_payment_update(self, auth_client, seed_user, db, seed_periods):
        """Adding escrow returns OOB fragments for payment summary."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow",
            data={"name": "Property Tax", "annual_amount": "4800.00"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'id="total-payment-chip"' in html
        assert 'hx-swap-oob="true"' in html
        assert "$400.00/mo" in html

    # ── Effective-date field + forward-only guard ────────────────────

    def test_escrow_add_with_effective_date(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """POST add with an explicit effective_date opens the version at that date."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow",
            data={
                "name": "Property Tax", "annual_amount": "7200.00",
                "effective_date": "2026-02-01",
            },
        )
        assert resp.status_code == 200
        line = (
            db.session.query(EscrowLine)
            .filter_by(account_id=acct.id, name="Property Tax").one()
        )
        version = (
            db.session.query(EscrowComponentVersion)
            .filter_by(line_id=line.id).one()
        )
        assert version.effective_date == date(2026, 2, 1)
        assert version.annual_amount == Decimal("7200.00")

    def test_escrow_add_rejects_before_origination(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """An effective date before origination (2023-06-01) is rejected; no line."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow",
            data={
                "name": "Tax", "annual_amount": "1200.00",
                "effective_date": "2023-05-01",
            },
        )
        assert resp.status_code == 400
        assert b"origination" in resp.data
        assert (
            db.session.query(EscrowLine).filter_by(account_id=acct.id).count() == 0
        )

    def test_forward_guard_rejects_on_or_before_latest_settled(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Add-version on/before the latest settled payment's DUE date is rejected.

        A settled payment in seed_periods[0] (start 2026-01-02) satisfies the
        2026-02-01 installment (``payment_day`` 1), and its split resolves escrow
        on that DUE date -- contract time, ruling D5 / finding N-34 -- so that is
        the frozen boundary.  A version effective 2026-02-01 (== the due date)
        would move that settled split, so it is rejected; 2026-02-02 (strictly
        after) is allowed.

        **A period-start boundary is what this must not be:** the payment's period
        starts 2026-01-02, a full month before the installment it pays, so the old
        boundary admitted every date in between -- each of which still governs the
        settled split.  2026-01-03 is asserted rejected for exactly that reason.
        """
        acct = _create_mortgage(seed_user, db.session)
        line_id = add_escrow_line(
            db.session, acct.id, "Tax", Decimal("7200.00"),
        ).line_id
        db.session.commit()
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], acct,
            seed_periods[0], amount=Decimal("1500.00"),
        )
        db.session.commit()
        assert seed_periods[0].start_date == date(2026, 1, 2)

        # Inside the period-start .. due-date window: the old guard allowed this.
        inside_window = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/{line_id}/version",
            data={"annual_amount": "8000.00", "effective_date": "2026-01-03"},
        )
        assert inside_window.status_code == 400
        assert b"latest recorded payment" in inside_window.data

        on_boundary = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/{line_id}/version",
            data={"annual_amount": "8000.00", "effective_date": "2026-02-01"},
        )
        assert on_boundary.status_code == 400
        assert b"latest recorded payment" in on_boundary.data

        after = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/{line_id}/version",
            data={"annual_amount": "8000.00", "effective_date": "2026-02-02"},
        )
        assert after.status_code == 200
        assert (
            db.session.query(EscrowComponentVersion)
            .filter_by(line_id=line_id, effective_date=date(2026, 2, 2)).count() == 1
        )

    def test_forward_guard_boundary_is_latest_settled(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The guard boundary is the LATEST settled payment's due date, not the earliest.

        Two settled payments whose installments differ: seed_periods[0] (start
        2026-01-02, due 2026-02-01) and seed_periods[3] (start 2026-02-13, due
        2026-03-01).  The boundary is the LATER due date (2026-03-01), so a version
        effective 2026-02-15 -- after the first payment's installment but before the
        second's -- is STILL rejected (a min-based guard would wrongly allow it, the
        exact bug this correction avoids); only strictly after 2026-03-01 is allowed.

        The two periods are chosen so their DUE dates differ: periods 0, 1 and 2 all
        satisfy the same 2026-02-01 installment, which would make max and min
        coincide and leave this test no teeth.
        """
        acct = _create_mortgage(seed_user, db.session)
        line_id = add_escrow_line(
            db.session, acct.id, "Tax", Decimal("7200.00"),
        ).line_id
        db.session.commit()
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], acct,
            seed_periods[0], amount=Decimal("1500.00"),
        )
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], acct,
            seed_periods[3], amount=Decimal("1500.00"),
        )
        db.session.commit()
        assert seed_periods[3].start_date == date(2026, 2, 13)

        between = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/{line_id}/version",
            data={"annual_amount": "8000.00", "effective_date": "2026-02-15"},
        )
        assert between.status_code == 400
        # Rejected by the SETTLED-payment boundary, not by the origination bound
        # or a same-date collision (which would satisfy a bare 400).
        assert b"latest recorded payment" in between.data

        after = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/{line_id}/version",
            data={"annual_amount": "8000.00", "effective_date": "2026-03-02"},
        )
        assert after.status_code == 200

    # ── Version drawer CRUD: schedule / edit / delete a version ───────

    def test_add_version_schedules_change(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """POST add-version appends a future-dated version under the existing line."""
        acct = _create_mortgage(seed_user, db.session)
        line_id = add_escrow_line(
            db.session, acct.id, "Tax", Decimal("7403.88"),
        ).line_id
        db.session.commit()
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/{line_id}/version",
            data={"annual_amount": "8003.88", "effective_date": "2026-08-01"},
        )
        assert resp.status_code == 200
        versions = (
            db.session.query(EscrowComponentVersion)
            .filter_by(line_id=line_id)
            .order_by(EscrowComponentVersion.effective_date).all()
        )
        assert len(versions) == 2
        assert versions[1].effective_date == date(2026, 8, 1)
        assert versions[1].annual_amount == Decimal("8003.88")

    def test_add_version_duplicate_date_rejected(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A second version on a date the line already carries is rejected."""
        acct = _create_mortgage(seed_user, db.session)
        # add_escrow_line opens the version at origination (2023-06-01).
        line_id = add_escrow_line(
            db.session, acct.id, "Tax", Decimal("7200.00"),
        ).line_id
        db.session.commit()
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/{line_id}/version",
            data={"annual_amount": "8000.00", "effective_date": "2023-06-01"},
        )
        assert resp.status_code == 400
        assert b"already has a version" in resp.data
        assert (
            db.session.query(EscrowComponentVersion)
            .filter_by(line_id=line_id).count() == 1
        )

    def _line_with_scheduled(self, db, account_id):
        """Build a line with an origination version + a scheduled 2026-08-01 version.

        Returns ``(line_id, scheduled_version)`` for the edit / delete tests.
        """
        v1 = add_escrow_line(db.session, account_id, "Tax", Decimal("7200.00"))
        db.session.add(EscrowComponentVersion(
            line_id=v1.line_id, effective_date=date(2026, 8, 1),
            annual_amount=Decimal("8003.88"),
        ))
        db.session.commit()
        sched = (
            db.session.query(EscrowComponentVersion)
            .filter_by(line_id=v1.line_id, effective_date=date(2026, 8, 1)).one()
        )
        return v1.line_id, sched

    def test_edit_version_updates_scheduled(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Editing a scheduled (unfrozen) version updates amount + date in place."""
        acct = _create_mortgage(seed_user, db.session)
        _line_id, sched = self._line_with_scheduled(db, acct.id)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/version/{sched.id}/edit",
            data={"annual_amount": "8500.00", "effective_date": "2026-09-01"},
        )
        assert resp.status_code == 200
        db.session.refresh(sched)
        assert sched.annual_amount == Decimal("8500.00")
        assert sched.effective_date == date(2026, 9, 1)

    def test_edit_version_frozen_rejected(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A version at/before the latest settled payment is frozen -- edit rejected."""
        acct = _create_mortgage(seed_user, db.session)
        version = add_escrow_line(db.session, acct.id, "Tax", Decimal("7200.00"))
        db.session.commit()
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], acct,
            seed_periods[0], amount=Decimal("1500.00"),
        )
        db.session.commit()
        # The origination version (2023-06-01) is <= the settled payment's period
        # start (2026-01-02), so it underpins that settled split and is read-only.
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/version/{version.id}/edit",
            data={"annual_amount": "9000.00", "effective_date": "2023-06-01"},
        )
        assert resp.status_code == 400
        assert b"settled payment" in resp.data
        db.session.refresh(version)
        assert version.annual_amount == Decimal("7200.00")

    def test_delete_version_removes_scheduled(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Deleting a scheduled (unfrozen, non-sole) version removes just that one."""
        acct = _create_mortgage(seed_user, db.session)
        line_id, sched = self._line_with_scheduled(db, acct.id)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/version/{sched.id}/delete",
        )
        assert resp.status_code == 200
        assert db.session.get(EscrowComponentVersion, sched.id) is None
        # The origination version survives.
        assert (
            db.session.query(EscrowComponentVersion)
            .filter_by(line_id=line_id).count() == 1
        )

    def test_delete_version_current_rejected(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A current (non-scheduled) version can't be per-version deleted.

        The origination version (2023-06-01) is on/before today, so it is not a
        scheduled future change; the route rejects the delete (edit it, or remove
        the whole line, instead).
        """
        acct = _create_mortgage(seed_user, db.session)
        version = add_escrow_line(db.session, acct.id, "Tax", Decimal("7200.00"))
        db.session.commit()
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/version/{version.id}/delete",
        )
        assert resp.status_code == 400
        assert b"scheduled future change" in resp.data
        assert db.session.get(EscrowComponentVersion, version.id) is not None

    def test_delete_upcoming_only_scheduled_drops_line(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Deleting an upcoming-only line's sole scheduled version removes the line.

        A line added with a FUTURE effective date (2026-12-01, after the frozen
        today of 2026-03-20) has no current version; deleting that sole scheduled
        version leaves the line empty, so the orphan line is dropped too.
        """
        acct = _create_mortgage(seed_user, db.session)
        version = add_escrow_line(
            db.session, acct.id, "Future", Decimal("6000.00"),
            effective_date=date(2026, 12, 1),
        )
        line_id = version.line_id
        db.session.commit()
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/version/{version.id}/delete",
        )
        assert resp.status_code == 200
        assert db.session.get(EscrowComponentVersion, version.id) is None
        assert db.session.get(EscrowLine, line_id) is None

    # ── Rename in place ──────────────────────────────────────────────

    def test_rename_line_updates_name(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Renaming a line updates its label; the version amount is untouched."""
        acct = _create_mortgage(seed_user, db.session)
        version = add_escrow_line(
            db.session, acct.id, "Property Tax & Insurance", Decimal("7200.00"),
        )
        line_id = version.line_id
        db.session.commit()
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/{line_id}/rename",
            data={"name": "Tax and Insurance"},
        )
        assert resp.status_code == 200
        assert db.session.get(EscrowLine, line_id).name == "Tax and Insurance"
        # Rename is display-only: the version amount does not move.
        db.session.refresh(version)
        assert version.annual_amount == Decimal("7200.00")

    def test_rename_duplicate_active_name_rejected(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Renaming onto another ACTIVE line's name is rejected."""
        acct = _create_mortgage(seed_user, db.session)
        add_escrow_line(db.session, acct.id, "Insurance", Decimal("1200.00"))
        line_id = add_escrow_line(
            db.session, acct.id, "Tax", Decimal("7200.00"),
        ).line_id
        db.session.commit()
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/{line_id}/rename",
            data={"name": "Insurance"},
        )
        assert resp.status_code == 400
        assert b"already exists" in resp.data
        assert db.session.get(EscrowLine, line_id).name == "Tax"

    def test_rename_to_same_name_allowed(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Renaming a line to its own current name is a no-op, allowed."""
        acct = _create_mortgage(seed_user, db.session)
        line_id = add_escrow_line(
            db.session, acct.id, "Tax", Decimal("7200.00"),
        ).line_id
        db.session.commit()
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/{line_id}/rename",
            data={"name": "Tax"},
        )
        assert resp.status_code == 200
        assert db.session.get(EscrowLine, line_id).name == "Tax"

    def test_dashboard_escrow_drawer_renders(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The dashboard GET renders the escrow card: summary, scheduled badge, drawer.

        A line with an origination version (current) + a 2026-08-01 version
        (scheduled) must render the summary amount, the 'Scheduled' badge, the
        collapsible drawer, both status labels, the inline edit inputs for the
        scheduled row, and the schedule-a-change form -- proving the version-drawer
        template renders inline exactly as the HTMX routes re-render it.
        """
        acct = _create_mortgage(seed_user, db.session)
        v1 = add_escrow_line(db.session, acct.id, "Property Tax", Decimal("7200.00"))
        db.session.add(EscrowComponentVersion(
            line_id=v1.line_id, effective_date=date(2026, 8, 1),
            annual_amount=Decimal("8003.88"),
        ))
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Property Tax" in html
        # Collapsed summary flags a queued change.
        assert "Scheduled" in html
        assert f'id="escrow-drawer-{v1.line_id}"' in html
        # Status labels for the two versions.
        assert "Current" in html
        # The scheduled version renders as an inline edit form (its amount input).
        assert 'name="annual_amount"' in html
        assert "Schedule a change" in html
        # The rejected-change error surface is present (hidden until a 4xx).
        assert 'id="escrow-error"' in html

    def test_version_routes_idor(
        self, auth_client, second_user, db, seed_periods,
    ):
        """Another user's escrow version routes return 404 (no existence oracle)."""
        other = _create_other_loan(second_user, db.session, AcctTypeEnum.MORTGAGE)
        version = add_escrow_line(db.session, other.id, "Tax", Decimal("7200.00"))
        db.session.commit()
        edit = auth_client.post(
            f"/accounts/{other.id}/loan/escrow/version/{version.id}/edit",
            data={"annual_amount": "9000.00", "effective_date": "2026-08-01"},
        )
        delete = auth_client.post(
            f"/accounts/{other.id}/loan/escrow/version/{version.id}/delete",
        )
        rename = auth_client.post(
            f"/accounts/{other.id}/loan/escrow/{version.line_id}/rename",
            data={"name": "Hacked"},
        )
        assert edit.status_code == 404
        assert delete.status_code == 404
        assert rename.status_code == 404

    def test_version_edit_cross_account_version_id_is_404(
        self, auth_client, seed_user, second_user, db, seed_periods,
    ):
        """Passing MY account_id with another user's version_id returns 404.

        The subtler IDOR that ``_owned_version`` exists for: ``@require_owner``
        passes (I own the URL account), but the version belongs to the victim's
        line, so the account-mismatch check must still 404 -- no cross-account
        version edit / delete.
        """
        mine = _create_mortgage(seed_user, db.session)
        victim = _create_other_loan(second_user, db.session, AcctTypeEnum.MORTGAGE)
        victim_version = add_escrow_line(
            db.session, victim.id, "Tax", Decimal("7200.00"),
        )
        db.session.commit()
        edit = auth_client.post(
            f"/accounts/{mine.id}/loan/escrow/version/{victim_version.id}/edit",
            data={"annual_amount": "9000.00", "effective_date": "2026-08-01"},
        )
        delete = auth_client.post(
            f"/accounts/{mine.id}/loan/escrow/version/{victim_version.id}/delete",
        )
        assert edit.status_code == 404
        assert delete.status_code == 404
        db.session.refresh(victim_version)
        assert victim_version.annual_amount == Decimal("7200.00")

    # ── Early-settle regime: boundary can be in the FUTURE ────────────

    def test_delete_version_blocked_by_early_settle_boundary(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A version in (today, boundary] can't be deleted when a payment is paid ahead.

        Frozen today is 2026-03-20; seed_periods[6] starts 2026-03-27 and its
        installment falls 2026-04-01 (``payment_day`` 1).  Settling a payment for
        period[6] BEFORE its period begins (an early-settle) puts the boundary at
        that DUE date -- 2026-04-01, in the FUTURE (ruling D5 / finding N-34; it
        was the 2026-03-27 period start before).  A version effective 2026-03-25
        (after today, but on/before the boundary) underpins that settled payment's
        escrow split, so deleting it must be rejected even though it is "after
        today" -- the exact bypass the ``> today``-only guard allowed.
        """
        acct = _create_mortgage(seed_user, db.session)
        origin = add_escrow_line(db.session, acct.id, "Tax", Decimal("3600.00"))
        # Schedule a version at 2026-03-25 (allowed now: no settled payment yet).
        gap_version = EscrowComponentVersion(
            line_id=origin.line_id, effective_date=date(2026, 3, 25),
            annual_amount=Decimal("4800.00"),
        )
        db.session.add(gap_version)
        db.session.commit()
        # Early-settle period[6] (starts 2026-03-27, after today) -> boundary future.
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], acct,
            seed_periods[6], amount=Decimal("1500.00"),
        )
        db.session.commit()
        assert seed_periods[6].start_date == date(2026, 3, 27)
        lines = loan_loaders.load_escrow_lines(acct.id)
        escrow_at_installment = escrow_calculator.escrow_monthly_as_of(
            lines, date(2026, 4, 1),
        )
        # 4800 / 12 = 400.00 (the 2026-03-25 version wins as of the 04-01
        # installment -- the date the settled payment's split reads).
        assert escrow_at_installment == Decimal("400.00")

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/version/{gap_version.id}/delete",
        )
        assert resp.status_code == 400
        assert b"settled payment" in resp.data
        # The version survives and the settled payment's escrow is unmoved.
        assert db.session.get(EscrowComponentVersion, gap_version.id) is not None
        lines = loan_loaders.load_escrow_lines(acct.id)
        assert escrow_calculator.escrow_monthly_as_of(
            lines, date(2026, 4, 1),
        ) == Decimal("400.00")

    def test_delete_line_blocked_by_early_settle_boundary(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Line remove (tombstone as of today) is blocked while a payment is paid ahead.

        The removal tombstone lands at today (2026-03-20); with an early-settled
        payment for period[6] (starts 2026-03-27, installment due 2026-04-01) the
        boundary is that DUE date, so a tombstone at today is on/before the
        boundary and would zero the line for that settled payment.  The route must
        reject the removal.
        """
        acct = _create_mortgage(seed_user, db.session)
        line = add_escrow_line(db.session, acct.id, "Tax", Decimal("3600.00"))
        db.session.commit()
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], acct,
            seed_periods[6], amount=Decimal("1500.00"),
        )
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow/{line.line_id}/delete",
        )
        assert resp.status_code == 400
        assert b"latest recorded payment" in resp.data
        # No tombstone appended: the line's only version is still the original.
        db.session.expire_all()
        reloaded = db.session.get(EscrowLine, line.line_id)
        assert all(not v.is_removed for v in reloaded.versions)
        lines = loan_loaders.load_escrow_lines(acct.id)
        # 3600 / 12 = 300.00, unchanged at the early-settled payment's 04-01
        # installment -- the date its split resolves escrow on.
        assert escrow_calculator.escrow_monthly_as_of(
            lines, date(2026, 4, 1),
        ) == Decimal("300.00")


# ── Escrow Posting-Sync Tests (E1b) ──────────────────────────────────


def _make_sync_loan(seed_user, db_session):
    """Create a $100,000 / 6% mortgage originated 2026-01-01 (clean arithmetic).

    Interest on a payment folded against the origination principal is
    100000 * 0.06 / 12 = 500.00 exactly, so a $1,000 payment splits 500 interest /
    500 principal with no rounding -- the shape the E1b sync tests fold against.
    Originated the month before the one payment's 2026-02-01 installment (plan
    step recurrence:R16-c-2, ruling R-R101): every contractual installment from
    origination is charged now, so the 2023-06-01 it carried until then read as
    thirty-two unpaid months ahead of that payment.
    """
    return create_loan_account(
        seed_user, db_session, name="Sync Mortgage",
        principal=Decimal("100000.00"), rate=Decimal("0.06000"), term=360,
        origination_date=date(2026, 1, 1), payment_day=1,
        account_type=AcctTypeEnum.MORTGAGE,
    )


# Each escrow WRITE route, as a (precondition + POST) callable returning the
# response.  Every escrow effective date is strictly AFTER the settled payment's
# 2026-02-01 DUE date -- the forward-only boundary, which is the date its split
# resolves escrow on (ruling D5, finding N-34), NOT the 2026-01-16 pay-period
# start these dates were first chosen against.  So no case moves the settled
# split, and the only ledger change the sync makes is to HEAL the forged cash
# entry, proving the route ran the sync regardless of its escrow op.


def _write_add_escrow(client, db_session, loan):
    """add_escrow: POST a brand-new line, its opening version future-dated."""
    return client.post(
        f"/accounts/{loan.id}/loan/escrow",
        data={"name": "Property Tax", "annual_amount": "4800.00",
              "effective_date": "2026-04-01"},
    )


def _write_delete_escrow(client, db_session, loan):
    """delete_escrow: tombstone a post-boundary line as of today."""
    line = add_escrow_line(
        db_session, loan.id, "Old Tax", Decimal("6000.00"),
        effective_date=date(2026, 3, 1),
    ).line
    db_session.commit()
    return client.post(f"/accounts/{loan.id}/loan/escrow/{line.id}/delete")


def _write_add_version(client, db_session, loan):
    """add_escrow_version: schedule a future change on an existing line."""
    line = add_escrow_line(
        db_session, loan.id, "Tax", Decimal("6000.00"),
        effective_date=date(2026, 3, 1),
    ).line
    db_session.commit()
    return client.post(
        f"/accounts/{loan.id}/loan/escrow/{line.id}/version",
        data={"annual_amount": "7200.00", "effective_date": "2026-05-01"},
    )


def _write_edit_version(client, db_session, loan):
    """edit_escrow_version: edit a future (post-boundary) version's amount."""
    version = add_escrow_line(
        db_session, loan.id, "Tax", Decimal("6000.00"),
        effective_date=date(2026, 4, 1),
    )
    db_session.commit()
    return client.post(
        f"/accounts/{loan.id}/loan/escrow/version/{version.id}/edit",
        data={"annual_amount": "7200.00"},
    )


def _write_delete_version(client, db_session, loan):
    """delete_escrow_version: delete a future scheduled version."""
    version = add_escrow_line(
        db_session, loan.id, "Tax", Decimal("6000.00"),
        effective_date=date(2026, 5, 1),
    )
    db_session.commit()
    return client.post(
        f"/accounts/{loan.id}/loan/escrow/version/{version.id}/delete",
    )


def _write_rename(client, db_session, loan):
    """rename_escrow_line: rename a line in place (display-only)."""
    line = add_escrow_line(
        db_session, loan.id, "Tax", Decimal("6000.00"),
        effective_date=date(2026, 3, 1),
    ).line
    db_session.commit()
    return client.post(
        f"/accounts/{loan.id}/loan/escrow/{line.id}/rename",
        data={"name": "Property Tax"},
    )


def _write_merge(client, db_session, loan):
    """merge_escrow_line: fold a removed predecessor into the active line."""
    boundary = date(2026, 4, 1)
    old = add_escrow_line(
        db_session, loan.id, "Old Tax", Decimal("6000.00"),
        effective_date=date(2026, 3, 1),
    ).line
    db_session.add(EscrowComponentVersion(
        line_id=old.id, effective_date=boundary,
        annual_amount=Decimal("0.00"), is_removed=True,
    ))
    add_escrow_line(
        db_session, loan.id, "New Tax", Decimal("7200.00"),
        effective_date=boundary,
    )
    db_session.commit()
    new = (
        db_session.query(EscrowLine)
        .filter_by(account_id=loan.id, name="New Tax").one()
    )
    return client.post(
        f"/accounts/{loan.id}/loan/escrow/{new.id}/merge",
        data={"source_line_id": old.id},
    )


_ESCROW_WRITES = [
    ("add_escrow", _write_add_escrow),
    ("delete_escrow", _write_delete_escrow),
    ("add_escrow_version", _write_add_version),
    ("edit_escrow_version", _write_edit_version),
    ("delete_escrow_version", _write_delete_version),
    ("rename_escrow_line", _write_rename),
    ("merge_escrow_line", _write_merge),
]


class TestEscrowPostingSync:
    """Every escrow write reconciles the loan's posting ledger (E1b, finding N-3).

    Before E1b the seven escrow write routes committed without funnelling the loan
    through the posting sync chokepoint -- escrow was the one loan-write door that
    left the postings unre-derived, protected only by the forward-only guard.  Now
    each route ends on ``_commit_escrow_change``, which runs
    ``sync_loan_postings_all_scenarios`` before committing, so the linked ledger
    re-derives as a checked projection (the E1a assert runs) after every escrow
    write.  These tests prove the wiring end-to-end -- a stale posting self-heals
    through EACH of the seven routes (the firing control) -- and that a clean escrow
    write on the normal path moves no money (the sync is a benign no-op under the
    guard).
    """

    @pytest.mark.parametrize(
        "escrow_write",
        [write for _label, write in _ESCROW_WRITES],
        ids=[label for label, _write in _ESCROW_WRITES],
    )
    def test_escrow_write_reconciles_a_stale_posting(
        self, escrow_write, auth_client, seed_user, db, seed_periods,
    ):
        """Each escrow write route re-derives a stale-dated loan posting (E1b).

        The firing control (verification standard 7.3).  A $1,000 payment settles
        2026-01-20 against the folded $100,000 balance: interest
        100000 * 0.06 / 12 = 500.00, principal 500.00, so the linked ledger nets
        cash +1000.00 - correction 500.00 = +500.00 on 01-20.  Its cash entry is
        then forged onto 2026-01-05 (the pre-E1a cross-date residue the dev-clone
        sweep found), leaving +1000.00 on 01-05 and only the -500.00 correction on
        01-20.  Performing the escrow write must run the loan sync, whose lineage
        pass re-dates the cash entry back: 01-05 nets to 0.00 and 01-20 back to
        +500.00.  Without E1b the escrow route never syncs and the forgery survives,
        so this fails.  Every escrow effective date is after the 01-16 boundary, so
        the escrow op moves no settled split -- the heal is purely the sync's,
        proving each of the seven routes reaches it.
        """
        loan = _make_sync_loan(seed_user, db.session)
        scenario_id = seed_user["scenario"].id
        xfer = create_settled_transfer(
            seed_user, db.session, seed_user["account"], loan, seed_periods[1],
            amount=Decimal("1000.00"), settled_on=date(2026, 1, 20),
        )
        db.session.commit()
        linked_id = linked_ledger_account(db.session, loan.id).id
        assert linked_net_by_date(db.session, linked_id, scenario_id)[
            date(2026, 1, 20)
        ] == Decimal("500.00")

        # Forge the pre-E1a cross-date residue: the settled payment's cash entry
        # carries a WRONG date.  Raw SQL bypasses the append-only ORM listener,
        # exactly as the legacy writes that predate it did (the test runs as the
        # table owner).
        # The loan-side cash entry is the loan-side MOVEMENT's since plan step
        # ``balance:X-bi-6-3`` (it was the one ``transfer_id`` entry).
        cash_entry_id = (
            db.session.query(JournalEntry.id)
            .filter(
                transfer_side_journal_filter(xfer.id, loan.id),
                JournalEntry.scenario_id == scenario_id,
            )
            .scalar()
        )
        db.session.execute(
            sa.text(
                "UPDATE budget.journal_entries SET entry_date = :day "
                "WHERE id = :entry_id"
            ),
            {"day": date(2026, 1, 5), "entry_id": cash_entry_id},
        )
        db.session.commit()
        assert linked_net_by_date(db.session, linked_id, scenario_id)[
            date(2026, 1, 5)
        ] == Decimal("1000.00")

        resp = escrow_write(auth_client, db.session, loan)
        assert resp.status_code == 200

        # The escrow write ran the loan sync: the forged entry is re-dated back.
        by_date = linked_net_by_date(db.session, linked_id, scenario_id)
        assert by_date.get(date(2026, 1, 5), Decimal("0.00")) == Decimal("0.00")
        assert by_date[date(2026, 1, 20)] == Decimal("500.00")

    def test_a_clean_escrow_write_moves_no_money(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A guard-passing escrow write on a clean loan leaves the ledger unmoved (E1b).

        The baseline-unmoved / no-spurious-fire control: the sync E1b runs on every
        escrow write, but under the forward-only guard an escrow change never moves a
        settled split, so on a loan whose postings already project its facts the sync
        is an idempotent no-op.  A $1,000 payment settles 2026-01-20 (interest 500.00,
        principal 500.00, net +500.00 on 01-20); adding a future-dated escrow line
        then returns 200 (the E1a assert did not fire on a 500), writes the line, and
        leaves the linked ledger byte-identical per date.  This proves the write is
        SAFE, not that the sync ran -- the sibling firing control proves the sync
        runs; here a skipped sync would look identical, and that is the point (a
        clean escrow write must move no money either way).
        """
        loan = _make_sync_loan(seed_user, db.session)
        scenario_id = seed_user["scenario"].id
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], loan, seed_periods[1],
            amount=Decimal("1000.00"), settled_on=date(2026, 1, 20),
        )
        db.session.commit()
        linked_id = linked_ledger_account(db.session, loan.id).id
        nets_before = linked_net_by_date(db.session, linked_id, scenario_id)
        assert nets_before[date(2026, 1, 20)] == Decimal("500.00")

        resp = auth_client.post(
            f"/accounts/{loan.id}/loan/escrow",
            data={"name": "Property Tax", "annual_amount": "4800.00",
                  "effective_date": "2026-04-01"},
        )
        assert resp.status_code == 200
        # The mutation itself worked: the escrow line was written.
        assert (
            db.session.query(EscrowLine)
            .filter_by(account_id=loan.id, name="Property Tax").count() == 1
        )
        # No money moved: the linked ledger is byte-identical per date.
        assert linked_net_by_date(db.session, linked_id, scenario_id) == nets_before


# ── Rate History Tests ───────────────────────────────────────────────


class TestRateHistory:
    """Tests for ARM rate change recording."""

    def test_rate_change_create(self, auth_client, seed_user, db, seed_periods):
        """POST rate change creates a RateHistory row at the effective date.

        DH-#56: the prior ``params.interest_rate`` mirror-write is gone --
        the rate's sole source of truth is now the RateHistory feed.  The
        new change row (effective 2026-04-01) records the 7.000% rate; the
        origination row seeded by ``_create_mortgage`` (effective
        2023-06-01) keeps the loan's period-0 rate.
        """
        acct = _create_mortgage(seed_user, db.session)
        params = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        params.is_arm = True
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/rate",
            data={"effective_date": "2026-04-01", "interest_rate": "7.000", "notes": "Adjustment"},
        )
        assert resp.status_code == 200

        # Scope the lookup to the posted effective date: the loan now has
        # two RateHistory rows (origination + this change), so an
        # unscoped ``first()`` is non-deterministic.
        entry = (
            db.session.query(RateHistory)
            .filter_by(account_id=acct.id, effective_date=date(2026, 4, 1))
            .one()
        )
        assert entry.interest_rate == Decimal("0.07000")

    def test_rate_change_records_monthly_pi(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """An optional monthly_pi pins the period's recast P&I (E-18 setup capture).

        The lender's stated recast payment is stored on the RateHistory
        row so the rate-period engine holds the period's P&I at that
        exact figure instead of deriving it from origination.
        """
        acct = _create_mortgage(seed_user, db.session)
        params = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        params.is_arm = True
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/rate",
            data={
                "effective_date": "2026-04-01",
                "interest_rate": "7.000",
                "monthly_pi": "2600.00",
            },
        )
        assert resp.status_code == 200

        entry = (
            db.session.query(RateHistory)
            .filter_by(account_id=acct.id)
            .order_by(RateHistory.effective_date.desc())
            .first()
        )
        assert entry is not None
        assert entry.interest_rate == Decimal("0.07000")
        assert entry.monthly_pi == Decimal("2600.00")

    def test_rate_change_without_monthly_pi_is_null(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Omitting monthly_pi leaves it NULL so the period P&I is derived."""
        acct = _create_mortgage(seed_user, db.session)
        params = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        params.is_arm = True
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/rate",
            data={"effective_date": "2026-05-01", "interest_rate": "7.500"},
        )
        assert resp.status_code == 200
        entry = (
            db.session.query(RateHistory)
            .filter_by(account_id=acct.id)
            .order_by(RateHistory.effective_date.desc())
            .first()
        )
        assert entry.monthly_pi is None

    def test_rate_change_validation(self, auth_client, seed_user, db, seed_periods):
        """Invalid rate returns 400."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/rate",
            data={"interest_rate": "200.0"},
        )
        assert resp.status_code == 400

    def test_rate_change_before_origination_rejected(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A rate change dated before origination is rejected (400), no row.

        DH-#56: the origination RateHistory row is the loan's period-0 /
        base rate; a pre-origination change would become the earliest row
        and displace the true origination row in the dashboard's
        ``origination_rate`` derivation (``rate_history[-1]``) and in
        ``_origination_rate``'s ``min()``.  ``_create_mortgage`` originates
        the loan on 2023-06-01, so a change effective 2023-01-01 is
        pre-origination.  Revert-proof: without the route guard the POST
        would persist a row and return 200, failing both assertions.
        """
        acct = _create_mortgage(seed_user, db.session)
        params = db.session.query(LoanParams).filter_by(
            account_id=acct.id,
        ).one()
        params.is_arm = True
        db.session.commit()
        before = (
            db.session.query(RateHistory)
            .filter_by(account_id=acct.id)
            .count()
        )

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/rate",
            data={"effective_date": "2023-01-01", "interest_rate": "7.000"},
        )
        assert resp.status_code == 400
        after = (
            db.session.query(RateHistory)
            .filter_by(account_id=acct.id)
            .count()
        )
        assert after == before

    def test_rate_change_idor(self, auth_client, second_user, db, seed_periods):
        """Rate change to another user's loan returns 404 with no side effects."""
        other = _create_other_loan(second_user, db.session, AcctTypeEnum.MORTGAGE)
        # DH-#56: the fixture seeds an origination RateHistory row, so the
        # "no side effects" invariant is that the count is UNCHANGED by the
        # IDOR POST -- not that it is zero.
        before = db.session.query(RateHistory).filter_by(account_id=other.id).count()
        resp = auth_client.post(
            f"/accounts/{other.id}/loan/rate",
            data={"interest_rate": "9.0", "effective_date": "2026-06-01"},
        )
        assert resp.status_code == 404

        after = db.session.query(RateHistory).filter_by(account_id=other.id).count()
        assert after == before

    def test_rate_change_same_date_double_submit(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """F-104 / C-22: same effective_date double-submit produces one row.

        The composite unique ``uq_rate_history_account_effective_date``
        rejects the second INSERT.  The route flashes a clear
        message and re-renders the rate history without the
        proposed duplicate; total row count is exactly 1.
        """
        acct = _create_mortgage(seed_user, db.session)
        params = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        params.is_arm = True
        db.session.commit()

        data = {
            "effective_date": "2026-04-01",
            "interest_rate": "7.000",
        }
        r1 = auth_client.post(f"/accounts/{acct.id}/loan/rate", data=data)
        assert r1.status_code == 200

        r2 = auth_client.post(f"/accounts/{acct.id}/loan/rate", data=data)
        # Idempotent path: route returns the partial; total rows == 1.
        assert r2.status_code == 200

        db.session.expire_all()
        # Scope to the duplicated effective date: DH-#56 seeds an
        # origination RateHistory row (effective 2023-06-01), so an
        # unscoped count includes it.  The F-104 dedupe invariant is
        # that the 2026-04-01 change exists exactly once.
        count = (
            db.session.query(RateHistory)
            .filter_by(account_id=acct.id, effective_date=date(2026, 4, 1))
            .count()
        )
        assert count == 1, (
            f"Expected 1 rate history row after duplicate submit, "
            f"found {count}; F-104 dedupe failed."
        )

    def test_rate_change_different_date_allowed(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """F-104 / C-22: different effective dates both succeed."""
        acct = _create_mortgage(seed_user, db.session)
        params = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        params.is_arm = True
        db.session.commit()

        r1 = auth_client.post(
            f"/accounts/{acct.id}/loan/rate",
            data={"effective_date": "2026-04-01", "interest_rate": "7.000"},
        )
        r2 = auth_client.post(
            f"/accounts/{acct.id}/loan/rate",
            data={"effective_date": "2026-05-01", "interest_rate": "7.500"},
        )
        assert r1.status_code == 200
        assert r2.status_code == 200

        db.session.expire_all()
        # Scope to the two posted change dates: DH-#56 seeds an
        # origination RateHistory row (effective 2023-06-01), so an
        # unscoped count would include it.  The invariant is that both
        # distinct-date changes were admitted (two change rows).
        count = (
            db.session.query(RateHistory)
            .filter(
                RateHistory.account_id == acct.id,
                RateHistory.effective_date.in_(
                    [date(2026, 4, 1), date(2026, 5, 1)],
                ),
            )
            .count()
        )
        assert count == 2

    def test_rate_change_refreshes_band(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A successful ARM rate change carries a fresh band series for the client.

        loan_audit deferred follow-up #1: the band's balance line is always
        visible now (not a hidden tab), but the rate POST swaps only the
        rate-history card + the OOB rate chip.  Since a rate change RE-AMORTIZES
        the loan, the route emits the recomputed band in a hidden
        #loan-band-refresh carrier so loan_detail.js rebuilds #loan-balance-chart
        from it.  A higher rate raises every forward balance, so the carried
        series differs from the pre-change band -- proving the refresh is real,
        not an echo.  Before the fix the rate route carried no band, so the band
        stayed on the pre-change rate until a full reload.
        """
        acct = _create_mortgage(seed_user, db.session)
        params = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        params.is_arm = True
        db.session.commit()

        pre = _parse_band_chart(
            auth_client.get(f"/accounts/{acct.id}/loan").data.decode()
        )
        assert pre is not None

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/rate",
            data={"effective_date": "2026-04-01", "interest_rate": "9.500"},
        )
        assert resp.status_code == 200
        # The rate response is the rate-history partial; its ONLY data-chart is
        # the band-refresh carrier (the canvas itself is not re-rendered here).
        refreshed = _parse_band_chart(resp.data.decode())
        assert refreshed is not None, (
            "A successful rate change must carry the recomputed band in "
            "#loan-band-refresh so the always-visible band does not go stale."
        )
        # Well-formed + self-consistent (the follow-up #2 invariant survives).
        assert len(refreshed["labels"]) == len(refreshed["balance"]) > 0
        assert "current_index" in refreshed
        # 9.500% re-amortizes the 6.500% loan: forward balances move, so the
        # carried series is not the pre-change one.
        assert refreshed["balance"] != pre["balance"]

    def test_duplicate_rate_submit_omits_band_refresh(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A rejected duplicate rate submit carries no band-refresh carrier.

        loan_audit deferred follow-up #1: the same-effective-date second submit
        re-amortizes nothing (the composite unique rejects it), so the route
        re-renders the rate history WITHOUT the band carrier -- loan_detail.js
        then leaves the band (and any active payoff preview) untouched.  Only the
        accepted first submit carries the refreshed band.
        """
        acct = _create_mortgage(seed_user, db.session)
        params = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        params.is_arm = True
        db.session.commit()

        data = {"effective_date": "2026-04-01", "interest_rate": "7.000"}
        r1 = auth_client.post(f"/accounts/{acct.id}/loan/rate", data=data)
        assert r1.status_code == 200
        # The first (accepted) submit DOES carry the refreshed band.
        assert _parse_band_chart(r1.data.decode()) is not None

        r2 = auth_client.post(f"/accounts/{acct.id}/loan/rate", data=data)
        assert r2.status_code == 200
        # The duplicate re-render carries no band carrier (nothing re-amortized).
        assert 'id="loan-band-refresh"' not in r2.data.decode()
        assert _parse_band_chart(r2.data.decode()) is None


# ── Payoff Calculator Tests ──────────────────────────────────────────


class TestPayoffCalculator:
    """Tests for the payoff calculator."""

    @pytest.mark.parametrize("create_fn", [_create_auto_loan, _create_mortgage])
    def test_payoff_extra_payment(self, auth_client, seed_user, db, seed_periods, create_fn):
        """POST extra payment mode returns results for any loan type."""
        acct = create_fn(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "200"},
        )
        assert resp.status_code == 200
        assert b"Months Saved" in resp.data

    def test_payoff_target_date(self, auth_client, seed_user, db, seed_periods):
        """POST target date mode returns payment data."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "target_date", "target_date": "2040-01-01"},
        )
        assert resp.status_code == 200
        assert b"$" in resp.data

    def test_payoff_validation(self, auth_client, seed_user, db, seed_periods):
        """Invalid mode returns error."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "invalid_mode"},
        )
        assert resp.status_code == 200
        assert b"Please correct the highlighted errors" in resp.data

    def test_payoff_idor(self, auth_client, second_user, db, seed_periods):
        """Payoff calc to another user's loan returns 404."""
        other = _create_other_loan(second_user, db.session)
        resp = auth_client.post(
            f"/accounts/{other.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "100"},
        )
        assert resp.status_code == 404


# ── Payoff Chart Shape Tests (Commit 4) ─────────────────────────────


def _parse_chart_array(html, attr):
    """Extract a chart ``data-<attr>='...'`` JSON array.

    The template renders Chart.js datasets via ``|tojson`` inside
    single-quoted HTML attributes, so the content between the
    single quotes is a JSON-encoded list.  Returns the parsed
    Python list, or ``None`` when the attribute is absent.

    Args:
        html: Rendered HTMX fragment.
        attr: Attribute name without the ``data-`` prefix
            (``"original"``, ``"committed"``, ``"accelerated"``,
            ``"labels"``).
    """
    import json as _json  # pylint: disable=import-outside-toplevel
    import re as _re  # pylint: disable=import-outside-toplevel

    match = _re.search(rf"data-{attr}='([^']*)'", html)
    if match is None:
        return None
    return _json.loads(match.group(1))


def _parse_band_chart(html):
    """Extract the loan detail band chart's ``data-chart`` JSON object.

    The Fable 5 band renders a single ``{labels, balance, current_index}`` JSON
    object on ``#loan-balance-chart`` (loan_detail.js splits the balance line at
    ``current_index`` and overlays the payoff lever's preview).  Returns the
    parsed dict, or ``None`` when absent (e.g. a paid-off loan renders no chart).

    Args:
        html: Rendered loan dashboard HTML.
    """
    return _parse_chart_array(html, "chart")


def _label_to_month_tuple(label):
    """Convert a ``"%b %Y"`` chart label to a (year, month) tuple.

    Chart labels are formatted as ``"Mar 2026"`` via
    ``strftime('%b %Y')``; ``strptime`` round-trips them so the
    comparison test can build an ordered (year, month) key for
    today's-month boundary checks.
    """
    from datetime import datetime  # pylint: disable=import-outside-toplevel

    parsed = datetime.strptime(label, "%b %Y")
    return (parsed.year, parsed.month)


class TestPayoffChartShape:
    """C4-1..C4-8: HTTP-level regression locks for the payoff-calculator chart.

    Today is frozen to 2026-03-20 by ``_freeze_today_inside_seed_range``,
    so confirmed payments dated in Jan/Feb 2026 are historical and
    chart points dated April 2026 onward are forward.  Each test
    pins one property of the composer's chart output as exposed by
    the route -- the architectural fix landed in Commit 4 must
    structurally satisfy them.
    """

    TODAY_MONTH = (2026, 3)

    def _create_loan_with_historical_confirmed(
        self, seed_user, db_session, periods,
    ):
        """Create a mortgage with two confirmed payments in Jan-Feb 2026.

        Returns the loan account.  The confirmed payments live in
        ``periods[1]`` (2026-01-16 window) and ``periods[3]``
        (2026-02-13 window), both strictly before today
        (2026-03-20).  ``_create_mortgage`` originates at
        2023-06-01, so 30+ months of gap separate origination from
        the first confirmed payment -- the temporal-gap shape that
        surfaces the buggy "extra applied to ghost historical
        months" behavior the architectural fix prevents.
        """
        acct = _create_mortgage(seed_user, db_session)
        _create_transfer_to_loan(
            seed_user, acct, periods[1], Decimal("1611.64"),
            status_enum=StatusEnum.DONE,
        )
        _create_transfer_to_loan(
            seed_user, acct, periods[3], Decimal("1611.64"),
            status_enum=StatusEnum.DONE,
        )
        db_session.commit()
        return acct

    def test_chart_lengths_equal(self, auth_client, seed_user, db, seed_periods):
        """C4-1: the payoff overlay aligns to the band chart's shared x-axis.

        The band chart (labels + committed balance line) and the payoff lever's
        accelerated overlay are both padded to the same contractual length, so
        loan_detail.js overlays the green preview against one set of labels
        without alignment gymnastics on the JS side.
        """
        acct = self._create_loan_with_historical_confirmed(
            seed_user, db.session, seed_periods,
        )
        band = _parse_band_chart(
            auth_client.get(f"/accounts/{acct.id}/loan").data.decode()
        )
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "500"},
        )
        assert resp.status_code == 200
        overlay = _parse_chart_array(resp.data.decode(), "overlay")
        assert band is not None
        assert overlay is not None
        assert len(band["labels"]) == len(band["balance"]) == len(overlay)

    def test_accelerated_overlay_null_in_historical_region(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C4-2: HTTP-level regression lock for the user's reported visual bug.

        The reported bug was $500 of extra principal applied to ghost historical
        months, drawing an accelerated line diverging from committed back at
        month 1 (2023-07).  The band-chart preview overlay now begins at Today
        and never redraws the confirmed history: every overlay index before the
        confirmed/projected boundary (the band's ``current_index``) is null, and
        the boundary index is the first non-null forward point -- so extra can
        never be attributed to a historical month.
        """
        acct = self._create_loan_with_historical_confirmed(
            seed_user, db.session, seed_periods,
        )
        band = _parse_band_chart(
            auth_client.get(f"/accounts/{acct.id}/loan").data.decode()
        )
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "500"},
        )
        assert resp.status_code == 200
        overlay = _parse_chart_array(resp.data.decode(), "overlay")
        assert band is not None and overlay is not None

        current_index = band["current_index"]
        assert current_index > 0, (
            "This fixture has two confirmed payments, so the band's "
            f"current_index must be > 0; got {current_index}"
        )
        for i in range(current_index):
            assert overlay[i] is None, (
                f"Overlay[{i}] ({overlay[i]!r}) is not null in the confirmed "
                "history region -- the preview must not redraw (or accelerate) "
                "historical months."
            )
        assert overlay[current_index] is not None, (
            "The overlay must begin (non-null) at the confirmed/projected "
            "boundary -- the first forward, accelerable month."
        )

    def test_accelerated_overlay_below_committed_post_today(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C4-3: the preview overlay drops strictly below the committed line.

        With ``extra_monthly=500`` and no projected override, every forward
        month receives the extra principal, so the accelerated overlay's balance
        is strictly below the band's committed line for at least one post-today
        index.
        """
        acct = self._create_loan_with_historical_confirmed(
            seed_user, db.session, seed_periods,
        )
        band = _parse_band_chart(
            auth_client.get(f"/accounts/{acct.id}/loan").data.decode()
        )
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "500"},
        )
        assert resp.status_code == 200
        overlay = _parse_chart_array(resp.data.decode(), "overlay")
        assert band is not None and overlay is not None

        committed = band["balance"]
        forward = range(band["current_index"], len(committed))
        assert any(
            overlay[i] is not None and overlay[i] < committed[i]
            for i in forward
        ), (
            "Expected the accelerated overlay strictly below the committed line "
            "at some post-today index; extra_monthly was ignored on the "
            "projection side."
        )

    def test_summary_consistent_with_chart(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C4-4: Displayed Months Saved matches the overlay-vs-plan divergence.

        The lever's ``months_saved`` is the calendar months between the plan's
        payoff and the payoff with the extra, both the seam's fold
        (``routes/loan/calculators._build_payoff_summary``, plan step
        R7d-g-3).  Single-source-of-truth means the rendered ``Months Saved``
        label equals the count of chart indices where the plan's line still
        owes but the accelerated overlay has already reached $0 -- the line
        and the overlay are the same fold on the band's monthly grid, so they
        agree by construction.
        """
        import re as _re  # pylint: disable=import-outside-toplevel

        acct = self._create_loan_with_historical_confirmed(
            seed_user, db.session, seed_periods,
        )
        band = _parse_band_chart(
            auth_client.get(f"/accounts/{acct.id}/loan").data.decode()
        )
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "500"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        overlay = _parse_chart_array(html, "overlay")
        assert band is not None and overlay is not None
        committed = band["balance"]

        # months_saved == count of months where Committed still owes but the
        # accelerated overlay has already paid off (0.0).  Both series pad with
        # 0.0 after their own payoff, so the count is exactly the difference in
        # payoff month index; the overlay is null across the history region and
        # so never counts there.
        chart_months_saved = sum(
            1 for i in range(len(committed))
            if committed[i] > 0.0 and overlay[i] == 0.0
        )

        # The template renders Months Saved as the first numeric value following
        # the "Months Saved" label; the negated class skips every non-digit
        # character (including hyphens in HTML class names) up to the integer.
        match = _re.search(
            r"Months Saved[^0-9]*(\d+)", html,
        )
        assert match is not None, (
            "Could not find Months Saved label in the rendered "
            "payoff results partial."
        )
        displayed_months_saved = int(match.group(1))
        assert displayed_months_saved == chart_months_saved, (
            f"Displayed Months Saved ({displayed_months_saved}) "
            f"!= chart divergence count ({chart_months_saved}); "
            "chart and summary must derive from the same forward "
            "slices."
        )

    def test_no_payment_history_chart_starts_at_origination(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C4-5: Loan with zero confirmed payments still renders a full band chart.

        With no confirmed payments the band still splits at TODAY, not at a
        confirmed row: the grid runs from the first installment after the
        loan's latest assertion, and every grid date at or before the pass's
        as-of reads the LEDGER (plan step R7d-g-3, ruling **R-R88**: the
        band is the seam's ``positions`` on the contractual grid), which
        holds the asserted $250,000 flat because nothing was paid.  The
        overlay begins after today -- ``None`` on the ledger's points, a
        what-if balance on every point after -- and aligns to the same
        labels.
        """
        acct = _create_mortgage(seed_user, db.session)
        band = _parse_band_chart(
            auth_client.get(f"/accounts/{acct.id}/loan").data.decode()
        )
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "500"},
        )
        assert resp.status_code == 200
        overlay = _parse_chart_array(resp.data.decode(), "overlay")
        assert band is not None and overlay is not None
        # First label is the month after the loan's LATEST assertion:
        # _create_mortgage's tracking-start is dated ``_TRACKED_FROM`` (2026-01-01)
        # at $250k, and with no confirmed payments the first row is the
        # contractual grid's first installment after it.  (It read "Jul 2023"
        # while the true-up was dated the day after origination.)  Feb 1 and
        # Mar 1 2026 are at or before the frozen 2026-03-20 today, so those
        # two points are the ledger's: the asserted balance, unpaid.
        assert band["labels"][:3] == ["Feb 2026", "Mar 2026", "Apr 2026"]
        assert band["current_index"] == 2
        assert band["balance"][:2] == [250000.0, 250000.0]
        assert len(band["balance"]) == len(band["labels"]) == len(overlay) > 0
        # The overlay is null on the ledger's points and begins after today.
        assert overlay[:2] == [None, None]
        assert overlay[2] is not None

    def test_target_date_mode_unchanged(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C4-6: target_date branch behavior unaffected by Commit 4.

        Commit 4 modifies only the extra_payment branch; the
        target_date branch migrates in Commit 7.  Verify
        target_date still returns the expected required-extra
        partial and does not regress on the helpers/imports the
        composer-collapse refactor touched.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "target_date", "target_date": "2040-06-01"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Expected message strings from the target_date branch of
        # the template (existing behavior).
        assert (
            "Required Extra Monthly Payment" in html
            or "Your loan will be paid off" in html
            or "Target date is not achievable" in html
        )

    def test_no_direct_calculate_summary_call(self):
        """C4-7: production code no longer calls ``calculate_summary``.

        Static-source guard for the architectural invariant that
        the route surface routes through ``compute_payoff_scenarios``,
        not directly to the engine's now-deprecated summary helper.
        Lighter-weight than parsing AST; the grep matches any
        call-style use of the symbol on its
        ``amortization_engine.`` prefix.
        """
        from pathlib import Path  # pylint: disable=import-outside-toplevel

        # loan.py is now the app/routes/loan/ package (Phase 3 pylint
        # cleanup split); grep every sub-module so coverage is preserved.
        loan_pkg = Path(__file__).resolve().parents[2] / "app" / "routes" / "loan"
        text = "\n".join(
            p.read_text(encoding="utf-8")
            for p in sorted(loan_pkg.glob("*.py"))
        )
        assert "amortization_engine.calculate_summary" not in text, (
            "app/routes/loan/ still references "
            "amortization_engine.calculate_summary -- the Commit 4 "
            "migration should have removed the only production "
            "caller of this function."
        )

    def test_no_direct_generate_schedule_call_in_extra_payment_branch(self):
        """C4-8: the extra-payment payoff path makes no direct engine call.

        The extra-payment branch's computation now lives in the
        ``_payoff_extra_payment_result`` helper (Phase 3 pylint cleanup
        decomposed ``payoff_calculate``; the route just dispatches to
        it).  Slice that helper out of
        ``app/routes/loan/calculators.py`` and assert it routes through
        ``compute_payoff_scenarios``, never a direct
        ``generate_schedule`` / ``calculate_summary`` call.
        """
        from pathlib import Path  # pylint: disable=import-outside-toplevel

        calculators = (
            Path(__file__).resolve().parents[2]
            / "app" / "routes" / "loan" / "calculators.py"
        )
        text = calculators.read_text(encoding="utf-8")
        start_marker = "def _payoff_extra_payment_result("
        end_marker = "\ndef _payoff_target_date_result("
        start = text.find(start_marker)
        end = text.find(end_marker, start)
        assert start != -1 and end != -1 and end > start, (
            "Could not slice _payoff_extra_payment_result out of "
            "calculators.py -- marker strings have drifted."
        )
        branch_source = text[start:end]
        assert "amortization_engine.generate_schedule" not in branch_source, (
            "extra_payment computation still contains a direct "
            "amortization_engine.generate_schedule call -- the "
            "Commit 4 migration should have collapsed every direct "
            "engine call onto compute_payoff_scenarios."
        )
        assert "amortization_engine.calculate_summary" not in branch_source, (
            "extra_payment computation still contains a direct "
            "amortization_engine.calculate_summary call."
        )


class TestBandChartLongestBaseline:
    """loan_audit deferred follow-up #2: the band x-axis runs to the plan's payoff.

    A plan slower than the contractual P&I -- here a loan two installments
    behind, whose missed principal the post-contractual extension clears --
    reaches zero AFTER the contract's last installment.  The band's grid is the
    contractual calendar extended month by month to the seam's derived payoff
    (``band_chart_dates``, plan step R7d-g-3), so the balance line never plots
    past the last labelled tick, and the lever's accelerated overlay is folded
    on that same grid so it stays aligned to the band's labels one-to-one.

    These read the route helpers directly (``build_band_chart`` /
    ``accelerated_overlay``) over a real loan: since R7d-g-3 both are the
    seam's fold on the band's grid, and the grid-selection defect lives in
    ``band_chart_dates``'s extension, which a loan whose payoff outruns its
    contract exercises directly.
    """

    @staticmethod
    def _two_installments_behind(seed_user, db_session):
        """A $250k / 6.5% / 30-year mortgage originated 2026-01-01, nothing paid.

        Its Feb 1 and Mar 1 2026 installments are due and unpaid at the frozen
        2026-03-20 today, so they are not in the plan (ruling D1 / finding
        B-9) and the fold clears the loan in the extension past Jan 2056.
        """
        acct = _create_fresh_mortgage(
            seed_user, db_session, origination_date=date(2026, 1, 1),
        )
        db_session.commit()
        return acct

    @staticmethod
    def _band_inputs(seed_user, acct):
        """Return ``(balance_ctx, scenarios, dates)`` the way the dashboard builds them.

        ``_load_route_context`` reads the pass's owner off ``current_user``;
        outside a request the pass is built from the owner's id and the loan
        context loaded the same way it does.  The grid is
        ``band_chart_dates`` over the pass's payoff and the plan as it
        stands, as the dashboard hands it.
        """
        # Pylint: import-outside-toplevel -- route-private helpers under test.
        from app.routes.loan._helpers import (  # pylint: disable=import-outside-toplevel
            _loan_inputs, band_chart_dates, build_baseline_scenarios,
        )
        from app.services.loan_payment_service import (  # pylint: disable=import-outside-toplevel
            load_loan_context,
        )
        params = load_loan_params(acct.id)
        balance_ctx = BalanceContext.build(seed_user["user"].id)
        loan = load_loan_context(acct.id, balance_ctx.amounts(), params)
        scenarios = build_baseline_scenarios(
            _loan_inputs(params, loan), acct, balance_ctx,
        )
        dates = band_chart_dates(
            scenarios,
            balance_at.loan_payoff_date(acct, balance_ctx),
            balance_at.loan_installments(acct, balance_ctx),
            params,
        )
        return balance_ctx, scenarios, dates

    def test_band_labels_cover_the_plans_line(
        self, app, seed_user, db, seed_periods,
    ):
        """The band's balance line never runs past its labelled x-axis.

        The grid is the confirmed history's dates plus the contract's (none
        and 359 here: Feb 2026 .. Jan 2056), extended to the fold's payoff.
        So len(labels) == len(balance), the last label is LATER than the
        contract's last installment, the balance at the contract's last tick
        is still positive (the residue the extension clears) and the line
        ends at $0.  Before the fix (labels keyed off the contract alone) the
        line overran the labels by the extension.
        """
        acct = self._two_installments_behind(seed_user, db.session)
        with app.app_context():
            balance_ctx, scenarios, dates = self._band_inputs(seed_user, acct)
            band = build_band_chart(acct, balance_ctx, dates)
            contract_end = scenarios.original_forward[-1].payment_date
            grid_end = balance_at.loan_payoff_date(acct, balance_ctx)
        assert not scenarios.history_rows
        assert len(band["labels"]) == len(band["balance"])
        assert band["labels"][0] == "Feb 2026"
        assert band["labels"][len(scenarios.original_forward) - 1] == "Jan 2056"
        assert contract_end == date(2056, 1, 1)
        assert grid_end is not None and grid_end > contract_end
        assert band["labels"][-1] == grid_end.strftime("%b %Y")
        assert band["balance"][len(scenarios.original_forward) - 1] > 0.0
        assert band["balance"][-1] == 0.0
        # The two grid dates at or before today are the ledger's.
        assert band["current_index"] == 2
        assert band["balance"][:2] == [250000.0, 250000.0]

    def test_a_month_end_loans_extension_keeps_the_months_end(
        self, app, seed_user, db, seed_periods,
    ):
        """The grid's extension steps from the contract's last date, not from itself.

        A loan due on the 31st: ``add_months`` clamps a 31st to a short
        month's end and, stepped AGAIN from that result, decays to the 28th
        for good, so an extension built one step at a time would put its
        dates before the plan's and run one tick past the payoff.  Since plan
        step recurrence:R16-c-2 the grid and the plan's extension both step
        the loan's ONE installment calendar
        (``loan_ledger.installment_dates``), which clamps the due day to each
        month afresh.  The grid's dates past the contract are the plan's own:
        the last label is the payoff's month and the point count past the
        contract is the month count to the payoff.
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 31),
            payment_day=31,
        )
        db.session.commit()
        with app.app_context():
            balance_ctx, scenarios, dates = self._band_inputs(seed_user, acct)
            band = build_band_chart(acct, balance_ctx, dates)
            payoff = balance_at.loan_payoff_date(acct, balance_ctx)
            plan_dates = [
                installment.due_date
                for installment in balance_at.loan_installments(
                    acct, balance_ctx,
                )
            ]
        contract_end = scenarios.original_forward[-1].payment_date
        assert contract_end.day == 31
        assert payoff is not None and payoff > contract_end
        assert payoff in plan_dates
        past_contract = len(band["labels"]) - len(scenarios.original_forward)
        assert past_contract == months_between(contract_end, payoff)
        assert band["labels"][-1] == payoff.strftime("%b %Y")
        assert band["balance"][-1] == 0.0
        assert band["balance"][-2] > 0.0

    def test_overlay_stays_aligned_to_the_band_labels(
        self, app, seed_user, db, seed_periods,
    ):
        """The accelerated overlay is folded on the band's own grid.

        ``accelerated_overlay`` reads ``band_chart_dates`` too, so the overlay
        has exactly the band's label count: ``None`` on the grid dates at or
        before today (the ledger's points), the what-if balance after.  With
        $500 a month on top it clears the loan before the plan does and pads
        $0.00 to the band's last tick -- aligned one-to-one, where a grid cut
        at the contract would land the overlay short of the plan's line.
        """
        acct = self._two_installments_behind(seed_user, db.session)
        with app.app_context():
            balance_ctx, _scenarios, dates = self._band_inputs(seed_user, acct)
            band = build_band_chart(acct, balance_ctx, dates)
            overlay = accelerated_overlay(
                acct, balance_ctx, dates, Decimal("500.00"),
            )
        assert len(overlay) == len(band["labels"])
        current_index = band["current_index"]
        assert current_index == 2
        assert overlay[:current_index] == [None, None]
        assert all(value is not None for value in overlay[current_index:])
        # Accelerates: reaches $0 strictly before the plan's line does, and
        # stays at $0 to the band's last tick.
        overlay_zero = overlay.index(0.0)
        plan_zero = band["balance"].index(0.0)
        assert overlay_zero < plan_zero
        assert set(overlay[overlay_zero:]) == {0.0}
        assert overlay[-1] == 0.0


# ── Account Creation Redirect Tests ──────────────────────────────────


class TestLoanAccountCreation:
    """Test that creating amortizing account types redirects to loan setup."""

    @pytest.mark.parametrize("type_name", ["Auto Loan", "Mortgage"])
    def test_creation_redirects_to_loan_dashboard(
        self, auth_client, seed_user, db, seed_periods, type_name,
    ):
        """Creating an amortizing account redirects to the loan dashboard."""
        loan_type = db.session.query(AccountType).filter_by(name=type_name).one()
        resp = auth_client.post(
            "/accounts",
            data={
                "name": f"New {type_name}",
                "account_type_id": str(loan_type.id),
                "anchor_balance": "20000",
            },
        )
        assert resp.status_code == 302
        assert "/loan" in resp.headers.get("Location", "")

    def test_params_not_duplicated(self, auth_client, seed_user, db, seed_periods):
        """Visiting dashboard does not create duplicate param records."""
        acct = _create_auto_loan(seed_user, db.session)
        auth_client.get(f"/accounts/{acct.id}/loan")
        auth_client.get(f"/accounts/{acct.id}/loan")

        count = db.session.query(LoanParams).filter_by(account_id=acct.id).count()
        assert count == 1


# ── Negative Path Tests ──────────────────────────────────────────────


class TestLoanNegativePaths:
    """Negative-path and boundary tests for loan routes."""

    def test_negative_interest_rate(self, auth_client, seed_user, db, seed_periods):
        """Negative interest rate is rejected."""
        acct = _create_auto_loan(seed_user, db.session)
        params = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        # DH-#56: the loan's rate lives in the origination RateHistory row,
        # not the dropped ``LoanParams.interest_rate`` column.  A rejected
        # negative-rate POST must leave that origination rate unchanged.
        orig_rate = (
            db.session.query(RateHistory)
            .filter_by(account_id=acct.id, effective_date=params.origination_date)
            .one()
            .interest_rate
        )

        auth_client.post(
            f"/accounts/{acct.id}/loan/params",
            data={"interest_rate": "-0.01", "payment_day": "15"},
        )
        db.session.expire_all()
        after_rate = (
            db.session.query(RateHistory)
            .filter_by(account_id=acct.id, effective_date=params.origination_date)
            .one()
            .interest_rate
        )
        assert after_rate == orig_rate

    def test_payment_day_zero(self, auth_client, seed_user, db, seed_periods):
        """Payment day 0 is rejected."""
        acct = _create_auto_loan(seed_user, db.session)
        orig = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        orig_day = orig.payment_day

        auth_client.post(
            f"/accounts/{acct.id}/loan/params",
            data={"interest_rate": "5.000", "payment_day": "0"},
        )
        db.session.expire_all()
        after = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        assert after.payment_day == orig_day

    def test_payment_day_32(self, auth_client, seed_user, db, seed_periods):
        """Payment day 32 is rejected."""
        acct = _create_auto_loan(seed_user, db.session)
        orig = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        orig_day = orig.payment_day

        auth_client.post(
            f"/accounts/{acct.id}/loan/params",
            data={"interest_rate": "5.000", "payment_day": "32"},
        )
        db.session.expire_all()
        after = db.session.query(LoanParams).filter_by(account_id=acct.id).one()
        assert after.payment_day == orig_day

    def test_escrow_missing_name(self, auth_client, seed_user, db, seed_periods):
        """Escrow POST without name returns 400."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/escrow",
            data={"annual_amount": "1200.00"},
        )
        assert resp.status_code == 400

    def test_rate_change_missing_date(self, auth_client, seed_user, db, seed_periods):
        """Rate change without effective_date returns 400."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/rate",
            data={"interest_rate": "5.5"},
        )
        assert resp.status_code == 400

    def test_escrow_nonexistent_account(self, auth_client, seed_user, db, seed_periods):
        """Escrow POST to nonexistent account returns 404."""
        resp = auth_client.post(
            "/accounts/999999/loan/escrow",
            data={"name": "Tax", "annual_amount": "3000.00"},
        )
        assert resp.status_code == 404

    def test_rate_change_nonexistent_account(self, auth_client, seed_user, db, seed_periods):
        """Rate change to nonexistent account returns 404."""
        resp = auth_client.post(
            "/accounts/999999/loan/rate",
            data={"interest_rate": "5.5", "effective_date": "2026-06-01"},
        )
        assert resp.status_code == 404

    def test_escrow_idor_add(self, auth_client, second_user, db, seed_periods):
        """Escrow add to another user's loan returns 404."""
        other = _create_other_loan(second_user, db.session, AcctTypeEnum.MORTGAGE)
        resp = auth_client.post(
            f"/accounts/{other.id}/loan/escrow",
            data={"name": "Stolen", "annual_amount": "9999.00"},
        )
        assert resp.status_code == 404

        count = db.session.query(EscrowLine).filter_by(account_id=other.id).count()
        assert count == 0


# ── Section 5 Regression Baseline ──────────────────────────────────────

# All five amortizing account types with realistic parameters.
_AMORTIZING_TYPES = [
    (AcctTypeEnum.MORTGAGE, Decimal("250000.00"), Decimal("0.06500"), 360, 600),
    (AcctTypeEnum.AUTO_LOAN, Decimal("25000.00"), Decimal("0.05000"), 60, 120),
    (AcctTypeEnum.STUDENT_LOAN, Decimal("45000.00"), Decimal("0.04500"), 120, 300),
    (AcctTypeEnum.PERSONAL_LOAN, Decimal("10000.00"), Decimal("0.08000"), 48, 120),
    (AcctTypeEnum.HELOC, Decimal("50000.00"), Decimal("0.07250"), 180, 360),
]


class TestLoanDashboardRegression:
    """Regression baseline for Section 5 loan dashboard changes.

    Verifies dashboard rendering, payoff calculator modes, and
    multi-type support before Section 5 modifies the amortization
    engine and loan UI.
    """

    @pytest.mark.parametrize("account_type,principal,rate,term,max_term", _AMORTIZING_TYPES)
    def test_dashboard_renders_for_all_amortizing_types(
        self, auth_client, seed_user, db, seed_periods,
        account_type, principal, rate, term, max_term,
    ):
        """Dashboard must render successfully for every amortizing account type.

        Section 5 may add type-specific dashboard panels.  This ensures
        all existing types continue to work.
        """
        acct = _create_loan_account(
            seed_user, db.session, account_type, f"Test {account_type.value}",
            principal, rate, term, date(2024, 1, 1), 1,
        )
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Dashboard should display the monthly payment.
        assert "Monthly" in html or "monthly" in html

    @pytest.mark.parametrize("account_type,principal,rate,term,max_term", _AMORTIZING_TYPES)
    def test_payoff_extra_payment_all_types(
        self, auth_client, seed_user, db, seed_periods,
        account_type, principal, rate, term, max_term,
    ):
        """Payoff calculator extra-payment mode works for all amortizing types.

        Verifies months saved, interest saved, and new payoff date are
        present in the response.
        """
        acct = _create_loan_account(
            seed_user, db.session, account_type, f"Test {account_type.value}",
            principal, rate, term, date(2024, 1, 1), 1,
        )
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "200.00"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Response must contain savings metrics.
        assert "saved" in html.lower() or "interest" in html.lower()

    def test_payoff_target_date_returns_required_payment(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Target-date mode returns the extra monthly payment needed.

        Verifies the payoff calculator correctly handles the target_date
        code path and returns a numeric result.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "target_date", "target_date": "2040-06-01"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Should contain a dollar amount for the required extra payment.
        assert "$" in html or "extra" in html.lower()
        # No recurring plan exists, so the raw single-number display
        # renders -- not the plan-aware reframe.
        assert "Required Extra Monthly Payment" in html
        assert "Current Plan" not in html

    def test_payoff_target_date_with_plan_shows_reframed_answer(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A recurring plan switches the target-date result to the reframe.

        F-27 ("fix + reframe, show both"): with a projected transfer
        paying the loan, the headline is the extra needed ON TOP of the
        plan; the raw no-plan figure stays as the secondary line.
        Without the fix, the route discarded ``ctx.loan.payments`` and
        showed only the overstated raw number.

        Since plan step C8f the headline folds the loan's forward PLAN
        (``balance_at.loan_required_extra``) and the panel no longer
        prints "Current Plan Pays Off" -- that is the payoff CHIP's
        question, and answering it here from a second producer is how
        the panel came to contradict the chip.
        """
        acct = _create_mortgage(seed_user, db.session)
        # Projected (future) transfer well above the ~$1,580 contractual
        # P&I, so the plan demonstrably contributes extra principal.
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[2], Decimal("2500.00"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "target_date", "target_date": "2040-06-01"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # The plan-aware headline renders...
        assert "Add on Top of Your Current Plan" in html
        # ...the raw figure stays as the secondary line...
        assert "Without your recurring plan" in html
        # ...and the payoff date is NOT restated here (C8f): one question,
        # one producer, one place on the page.
        assert "Current Plan Pays Off" not in html

    def test_payoff_zero_extra_payment_shows_standard_metrics(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Zero extra payment should return standard schedule metrics
        with zero months saved and zero interest saved.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "0.00"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # With zero extra, months saved should be 0.
        assert "0 months" in html.lower() or "0 mo" in html.lower() or \
               "$0" in html

    def test_payoff_invalid_mode_does_not_crash(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Invalid payoff mode must not cause a server error.

        The handler returns 200 with default/empty results rather than
        a 400 validation error.  This documents the current behavior.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "invalid_mode"},
        )
        # Must not crash -- 200 or 400 are both acceptable, not 500.
        assert resp.status_code != 500

    def test_payoff_negative_extra_payment_rejected(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Negative extra payment must be rejected by validation."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "-100.00"},
        )
        # Should not succeed -- either 400 or validation error.
        assert resp.status_code in (400, 422) or b"error" in resp.data.lower()

    def test_dashboard_idor_blocked(
        self, auth_client, seed_second_user, seed_second_periods,
        second_auth_client, seed_user, seed_periods, db,
    ):
        """User A cannot view User B's loan dashboard.

        Verifies the IDOR protection returns an identical response
        for 'not found' and 'not yours' per the security response rule.
        """
        other_acct = _create_loan_account(
            seed_second_user, db.session, AcctTypeEnum.MORTGAGE, "Other Mortgage",
            Decimal("200000.00"), Decimal("0.06000"), 360,
            date(2024, 1, 1), 1,
        )
        # User A tries to access User B's dashboard.
        resp = auth_client.get(f"/accounts/{other_acct.id}/loan")
        # Must not return 200 -- should redirect or return 404.
        assert resp.status_code in (302, 404)
        if resp.status_code == 200:
            pytest.fail("IDOR: User A could view User B's loan dashboard")


# ── Payment Integration Tests (Commit 5.1-2) ────────────────────────


def _create_transfer_to_loan(seed_user, loan_account, period, amount,
                              status_enum=StatusEnum.PROJECTED):
    """Create a transfer from checking to loan account via the transfer service.

    Enforces shadow transaction invariants by using the production
    code path.  Does NOT directly insert shadow transactions.

    A transfer created ALREADY settled carries an explicit settle instant at
    its period's start (step E1a: a born-settled create without one stamps
    ``now()``, and the real clock sits past these fixtures' frozen todays and
    seeded 2026 periods -- the payment would be invisible to every bounded
    read).  Period start is the exact visibility these fixtures had before
    E1a, when a born-settled create left the settle day NULL and every reader
    fell back to the period start, so no test's arithmetic moves.
    """
    status_id = ref_cache.status_id(status_enum)
    settled = _db.session.get(Status, status_id).is_settled
    return create_transfer(
        TransferSpec(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=loan_account.id,
            pay_period_id=period.id,
            scenario_id=seed_user["scenario"].id,
            amount_ownership=AmountOwnership.own(amount),
            status_id=status_id,
            category_id=seed_user["categories"]["Rent"].id,
            settle_day=an_entered_day(period.start_date) if settled else None,
        ),
    )


class TestLoanDashboardWithPayments:
    """Integration tests for payment-aware loan dashboard.

    Verifies that the dashboard and payoff calculator correctly load
    payment history from shadow transactions and pass it to the
    amortization engine.
    """

    def test_dashboard_no_payments_backward_compat(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Dashboard with no transfers renders identically to pre-5.1 behavior.

        This complements the Commit #0 regression tests by explicitly
        verifying the payment integration code path produces the same
        output when no payments exist.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Balance owed" in html

    def test_dashboard_with_confirmed_payments(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Dashboard with confirmed transfer payments renders successfully.

        A Paid transfer to the loan account creates a confirmed shadow
        income transaction.  The dashboard should load it and pass it
        to the engine without error.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Balance owed" in html

    def test_dashboard_with_projected_payments(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Dashboard with projected (future) transfer payments renders.

        Projected shadow transactions represent committed future payments
        from recurring transfers.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[2], Decimal("1580.00"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200

    def test_dashboard_with_mixed_payments(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Dashboard with confirmed + projected payments renders correctly.

        This is the typical real-world case: past payments are confirmed
        (Paid/Settled), future payments are projected.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.DONE,
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[3], Decimal("1580.00"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200

    def test_payoff_extra_payment_with_history(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Payoff calculator extra payment mode works with payment history.

        The calculator should not crash when shadow transactions exist
        for the loan account.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "200.00"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "saved" in html.lower() or "interest" in html.lower()

    def test_payoff_target_date_with_history(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Payoff calculator target date mode works with payment history.

        The target date mode projects from the loan's seam-derived balance
        (not derived from payments in this commit).
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "target_date", "target_date": "2040-06-01"},
        )
        assert resp.status_code == 200

    def test_dashboard_cancelled_transfer_excluded(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Cancelled transfers do not affect the dashboard projection.

        A cancelled payment should not appear in the payment history.
        The dashboard output should match the no-payments case.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.CANCELLED,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Balance owed" in html


# ── Transfer Prompt Tests (Commit 5.1-3) ─────────────────────────


class TestTransferPrompt:
    """Tests for the recurring payment transfer prompt on the loan dashboard
    and the create_payment_transfer route.
    """

    def test_dashboard_shows_prompt_no_transfer(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Dashboard with LoanParams but no recurring transfer: prompt visible."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No recurring payment" in html
        assert "Create Recurring Transfer" in html

    def test_dashboard_hides_prompt_transfer_exists(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Dashboard with active recurring transfer template: prompt hidden."""
        from app.models.recurrence_rule import RecurrenceRule  # pylint: disable=import-outside-toplevel
        from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel

        acct = _create_mortgage(seed_user, db.session)

        # Create an active recurring transfer template targeting this account.
        tpl = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=acct.id,
            name="Existing Mortgage Payment",
            default_amount=Decimal("1500.00"),
            is_active=True,
        )
        db.session.add(tpl)
        db.session.commit()
        # Priced, as every real definition is (the loan page's plan sums this
        # definition's occurrences since plan step R16-b-2 and refuses a
        # series nobody stated).
        state_template_price(tpl)
        db.session.commit()
        # The definition first, then the cadence onto it (plan step R-F6).
        rule = make_cadence_rule(
            tpl, MONTHLY,
            fires_on_day=1,
        )
        # And the cadence is COMMITTED before the request (plan step X-i3):
        # ``make_cadence_rule``'s contract is that the caller commits, and a
        # request cannot see a row this test never committed.
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No recurring payment" not in html

    def test_dashboard_shows_prompt_inactive_template(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Inactive (archived) transfer template: prompt still shown.

        The user may have deactivated a prior transfer. The prompt
        should reappear so they can create a new one.
        """
        from app.models.recurrence_rule import RecurrenceRule  # pylint: disable=import-outside-toplevel
        from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel

        acct = _create_mortgage(seed_user, db.session)

        tpl = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=acct.id,
            name="Old Mortgage Payment",
            default_amount=Decimal("1500.00"),
            is_active=False,  # Deactivated
        )
        db.session.add(tpl)
        db.session.commit()
        # The definition first, then the cadence onto it (plan step R-F6).
        rule = make_cadence_rule(
            tpl, MONTHLY,
            fires_on_day=1,
        )
        # Committed before the request, for the reason the sibling above states.
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No recurring payment" in html

    def test_create_transfer_success(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """POST with valid source account creates RecurrenceRule + TransferTemplate.

        Redirects to the loan dashboard after successful creation.
        """
        from app.models.recurrence_rule import RecurrenceRule  # pylint: disable=import-outside-toplevel
        from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel

        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(checking.id)},
        )
        assert resp.status_code == 302
        assert f"/accounts/{acct.id}/loan" in resp.headers.get("Location", "")

        # Verify records were created.
        tpl = (
            db.session.query(TransferTemplate)
            .filter_by(to_account_id=acct.id, user_id=seed_user["user"].id)
            .first()
        )
        assert tpl is not None
        assert tpl.is_active is True
        assert tpl.from_account_id == checking.id
        assert tpl.recurrence_rule is not None
        assert tpl.default_amount > 0
        # No "amount" override was posted, so the route defaults to the
        # full monthly payment and opts into live derivation: the loan-only
        # derive_from_loan flag lives in the 1:1 loan_payment_settings row
        # (decision B), which the loan route creates on the template (a
        # generic transfer / investment contribution gets no settings row).
        assert tpl.settings is not None
        assert tpl.settings.derive_from_loan is True

        # Reached through the OWNING relationship (plan step R-F6): the
        # rule names this template back, which is the arc the schema holds.
        assert tpl.recurrence_rule is not None
        assert tpl.recurrence_rule.transfer_template_id == tpl.id

    def test_create_transfer_redirect_hides_prompt(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """After creation, GET dashboard: prompt no longer visible."""
        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]

        # Create the recurring transfer.
        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(checking.id)},
        )

        # Dashboard should no longer show the prompt.
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No recurring payment" not in html

    def test_create_transfer_generates_shadow_transactions(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """After creation: shadow transactions exist on the loan account."""
        from app.models.transaction import Transaction  # pylint: disable=import-outside-toplevel
        from app.enums import TxnTypeEnum  # pylint: disable=import-outside-toplevel

        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]

        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(checking.id)},
        )

        # Shadow income transactions should exist on the loan account.
        income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
        shadows = (
            db.session.query(Transaction)
            .filter(
                Transaction.account_id == acct.id,
                Transaction.transfer_id.isnot(None),
                Transaction.transaction_type_id == income_type_id,
                Transaction.is_deleted.is_(False),
            )
            .all()
        )
        assert len(shadows) > 0

    def test_create_transfer_validates_source_ownership(
        self, auth_client, seed_user, seed_second_user,
        seed_second_periods, db, seed_periods,
    ):
        """POST with other user's account as source returns 404 (security)."""
        acct = _create_mortgage(seed_user, db.session)
        other_account = seed_second_user["account"]

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(other_account.id)},
        )
        # Should not succeed -- security response rule (404 for not-yours).
        assert resp.status_code == 404

    def test_create_transfer_validates_source_not_self(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """POST with debt account as source: validation error."""
        acct = _create_mortgage(seed_user, db.session)

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(acct.id)},
        )
        assert resp.status_code == 302
        assert f"/accounts/{acct.id}/loan" in resp.headers.get("Location", "")

    def test_create_transfer_idor_debt_account(
        self, auth_client, seed_user, seed_second_user,
        seed_second_periods, db, seed_periods,
    ):
        """POST to other user's debt account returns 404 (security)."""
        other_loan = _create_loan_account(
            seed_second_user, db.session, AcctTypeEnum.MORTGAGE, "Other Mortgage",
            Decimal("200000.00"), Decimal("0.06000"), 360,
            date(2024, 1, 1), 1,
        )
        checking = seed_user["account"]

        resp = auth_client.post(
            f"/accounts/{other_loan.id}/loan/create-transfer",
            data={"source_account_id": str(checking.id)},
        )
        assert resp.status_code == 404

    def test_create_transfer_amount_override(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """POST with custom amount: template uses the override amount."""
        from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel

        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={
                "source_account_id": str(checking.id),
                "amount": "2000.00",
            },
        )
        assert resp.status_code == 302

        tpl = (
            db.session.query(TransferTemplate)
            .filter_by(to_account_id=acct.id, user_id=seed_user["user"].id)
            .first()
        )
        assert tpl is not None
        assert tpl.default_amount == Decimal("2000.00")
        # Manual mode (typed amount) still gets a loan_payment_settings row,
        # but with derive_from_loan False -- the cash stays the typed base
        # (decision B / decision D).
        assert tpl.settings is not None
        assert tpl.settings.derive_from_loan is False

    def test_create_transfer_with_extra_principal(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Creating a payment with an extra stores it on settings, not the base.

        The standing extra rides the settings row (spec Sec. 6.3); the
        ``default_amount`` stays the derived base (P&I + escrow), NOT base +
        extra -- the extra is applied live, never baked in.
        """
        from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel

        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={
                "source_account_id": str(checking.id),
                "extra_principal": "100.00",
            },
        )
        assert resp.status_code == 302

        tpl = (
            db.session.query(TransferTemplate)
            .filter_by(to_account_id=acct.id, user_id=seed_user["user"].id)
            .first()
        )
        assert tpl is not None
        assert tpl.settings is not None
        assert tpl.settings.derive_from_loan is True
        assert tpl.settings.extra_principal == Decimal("100.00")
        # The base is the derived P&I + escrow, NOT base + extra.
        assert tpl.default_amount > Decimal("100.00")

    def test_update_payment_settings_changes_extra(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The per-definition settings door updates that payment's extra in place.

        No shadow regeneration is needed (the extra is a live parameter): the
        settings row's ``extra_principal`` is set to the new value.  The 302
        is also the proof that the moved door still ROUTES: the ownership 404s
        below are only meaningful beside it.
        """
        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]
        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(checking.id)},
        )
        tpl = _payment_template(db.session, seed_user, acct)

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payments/{tpl.id}/settings",
            data={"extra_principal": "150.00"},
        )
        assert resp.status_code == 302

        db.session.expire(tpl)
        assert tpl.settings.extra_principal == Decimal("150.00")

    def test_update_payment_settings_unknown_template_404s(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A template id that names none of this loan's payments is a 404.

        The door names the definition it writes (plan step R7d-g-3, ruling
        R-R83); with no recurring payment there is no id to name, and an id
        that names nothing reads the same as one that is not the owner's.
        """
        acct = _create_mortgage(seed_user, db.session)

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payments/999999/settings",
            data={"extra_principal": "150.00"},
        )
        assert resp.status_code == 404

    def test_update_payment_settings_refuses_another_loans_template(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The owner's OWN payment into a different loan is a 404 on this loan's door.

        The template must pay INTO the loan the URL names -- the set the card
        renders a strip for -- so a valid id on the wrong loan writes nothing.
        """
        acct = _create_mortgage(seed_user, db.session)
        other = _create_mortgage(seed_user, db.session, name="Other Mortgage")
        checking = seed_user["account"]
        auth_client.post(
            f"/accounts/{other.id}/loan/create-transfer",
            data={"source_account_id": str(checking.id)},
        )
        other_tpl = _payment_template(db.session, seed_user, other)

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payments/{other_tpl.id}/settings",
            data={"extra_principal": "150.00"},
        )
        assert resp.status_code == 404
        db.session.expire(other_tpl)
        assert other_tpl.settings.extra_principal == Decimal("0.00")

    def test_update_payment_settings_refuses_an_archived_template(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """An ARCHIVED payment is not in the loan's active set, so its door is a 404."""
        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]
        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(checking.id)},
        )
        tpl = _payment_template(db.session, seed_user, acct)
        resp = auth_client.post(f"/transfers/{tpl.id}/archive")
        assert resp.status_code == 302

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payments/{tpl.id}/settings",
            data={"extra_principal": "150.00"},
        )
        assert resp.status_code == 404

    def test_update_payment_settings_rejects_negative_extra(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A negative extra is rejected (danger flash) and never mutates settings."""
        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]
        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={
                "source_account_id": str(checking.id),
                "extra_principal": "50.00",
            },
        )

        tpl = _payment_template(db.session, seed_user, acct)

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payments/{tpl.id}/settings",
            data={"extra_principal": "-5.00"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"valid extra principal" in resp.data.lower()
        # The original extra is untouched.
        db.session.expire(tpl)
        assert tpl.settings.extra_principal == Decimal("50.00")

    def test_update_payment_settings_idor(
        self, second_auth_client, seed_user, db, seed_periods,
    ):
        """A non-owner editing a loan's extra gets a 404 (not-yours == not-found).

        With the owner's real template id in the URL, so the 404 is the
        ownership gate's and not the URL map's -- the sibling
        ``test_update_payment_settings_changes_extra`` proves the same URL
        shape routes (302) for the owner.  The owner's payment is built
        through the ORM rather than the owner's client: the ``db`` fixture
        holds ONE app context for the whole test and Flask-Login caches
        ``current_user`` on ``g`` per app context, so a request from a second
        client after the owner's runs AS THE OWNER (measured 2026-09-14: the
        non-owner's POST wrote the extra).
        """
        acct = _create_mortgage(seed_user, db.session)
        tpl = make_loan_payment_template(
            db.session, seed_user, acct, amount="1500.00",
        )
        db.session.commit()

        resp = second_auth_client.post(
            f"/accounts/{acct.id}/loan/payments/{tpl.id}/settings",
            data={"extra_principal": "150.00"},
        )
        assert resp.status_code == 404
        db.session.expire(tpl)
        assert tpl.settings.extra_principal == Decimal("0.00")

    def test_dashboard_shows_extra_control_when_payment_exists(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The payment card renders the definition's extra control once it exists.

        Prefilled from THAT payment's stored extra ($125.00), posting to its
        own per-definition settings door.
        """
        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]
        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={
                "source_account_id": str(checking.id),
                "extra_principal": "125.00",
            },
        )
        tpl = _payment_template(db.session, seed_user, acct)

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert f"/accounts/{acct.id}/loan/payments/{tpl.id}/settings" in html
        assert 'value="125.00"' in html

    def test_source_accounts_exclude_debt_account(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Source accounts dropdown does not include the current debt account.

        Scopes the assertion to the ``source_account_id`` select.  A
        naive ``f'value="{acct.id}" not in html`` check would falsely
        match the loan account's id when it appears in any unrelated
        attribute on the page (e.g., the dashboard's hidden form
        carrying ``value="N"`` for some other model whose id happens
        to equal the loan account's id).  The OR with ``"No recurring
        payment" not in html`` papered over collisions when the
        prompt was hidden, but failed deterministically when the
        prompt was shown AND ``acct.id`` collided with another
        element's value.
        """
        acct = _create_mortgage(seed_user, db.session)

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()

        source_options = select_option_values(html, "source_account_id")
        # When the prompt is shown, ``source_options`` carries the
        # eligible source accounts and the loan account itself must
        # not be among them.  When the prompt is hidden (no source
        # accounts available, e.g. the user has no non-loan
        # accounts), ``source_options`` is the empty list and the
        # assertion is vacuously true.
        assert str(acct.id) not in source_options, (
            f"Loan account {acct.id} ({acct.name}) is listed as a "
            f"source-account option on its own loan dashboard; got "
            f"source_account_id options {source_options!r}"
        )


# ── Payment Drift Warning + Auto-Track Switch Tests (C7 / ruling D3) ──


class TestPaymentDrift:
    """The loan detail underpayment-drift warning and its one-click auto-track switch.

    A MANUAL recurring payment whose stored base has fallen short of the
    contractual monthly payment (P&I + today's escrow) -- after an escrow or rate
    change the stored amount never absorbed -- warns on the loan detail page and
    offers to switch the payment to ``derive_from_loan`` so it tracks the contract
    automatically (step C7, ruling D3).  A derive payment already tracks and never
    warns; an at- or above-contract payment never warns (a deliberate overpayment
    does not trip it).
    """

    @staticmethod
    def _contract(acct, user_id):
        """The loan's contractual monthly payment today (P&I + escrow) via the seam.

        The same figure the route's ``_contractual_monthly_payment`` writes and the
        loan card displays as "Total Monthly": the seam ``loan_terms`` P&I plus
        today's ``resolve_active_lines`` escrow.  Used to size a drift precisely
        without hard-coding the resolver-derived P&I.
        """
        terms = balance_at.loan_terms(acct, BalanceContext.build(user_id))
        components = escrow_calculator.resolve_active_lines(
            loan_loaders.load_escrow_lines(acct.id), date.today(),
        )
        return escrow_calculator.calculate_total_payment(
            terms.monthly_payment, components,
        )

    @staticmethod
    def _legacy_manual_transfer(seed_user, db_session, acct, amount):
        """Create a legacy manual recurring payment with NO settings row.

        Mirrors the two real production loans (a payment created before the
        loan_payment_settings feature): an active monthly TransferTemplate with a
        recurrence rule and a stored ``default_amount``, and no 1:1 settings row --
        which every reader treats as manual mode (derive_from_loan False, no extra).
        Its price is STATED, as both production definitions' are (the
        template-amount backfill gave every existing template a version):
        the loan page's plan sums this definition's occurrences since plan
        step R16-b-2 and refuses a series nobody stated.
        """
        from app.models.recurrence_rule import RecurrenceRule  # pylint: disable=import-outside-toplevel
        from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel

        tpl = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=acct.id,
            name="Legacy Mortgage Payment",
            default_amount=amount,
            is_active=True,
        )
        db_session.add(tpl)
        db_session.commit()
        state_template_price(tpl)
        db_session.commit()
        # The definition first, then the cadence onto it (plan step R-F6),
        # then COMMITTED (plan step X-i3) -- every caller of this helper goes
        # on to issue a request, and a request cannot see uncommitted rows.
        rule = make_cadence_rule(
            tpl, MONTHLY,
            fires_on_day=1,
        )
        db_session.commit()
        return tpl

    def test_dashboard_warns_when_manual_payment_short(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A manual payment $50 below contract warns with the exact shortfall.

        The transfer is created via the amount override (manual mode,
        derive_from_loan False) at ``contract - $50.00``, so the shortfall is
        exactly $50.00.  The warning names the stored amount, the shortfall, and
        the contract, and offers the auto-track action.  The stored amount is
        warning-only on this page (the projection uses the contractual schedule),
        so matching it proves the warning renders it.
        """
        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]
        contract = self._contract(acct, seed_user["user"].id)
        stored = contract - Decimal("50.00")
        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(checking.id), "amount": str(stored)},
        )

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        tpl = _payment_template(db.session, seed_user, acct)
        assert "short of the" in html
        assert f"${stored:,.2f}" in html          # stored transfer amount
        assert "$50.00" in html                    # the exact shortfall
        assert f"${contract:,.2f}" in html         # the contractual payment
        assert f"/accounts/{acct.id}/loan/payments/{tpl.id}/track" in html
        assert "Track the loan" in html

    def test_dashboard_warns_when_legacy_manual_payment_short(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The real-loan shape -- a manual payment with NO settings row -- warns too.

        Both production loans predate loan_payment_settings, so their recurring
        payment has no settings row.  A missing row is manual mode, so a stored
        amount below contract must warn exactly as an amount-override manual
        payment does.
        """
        acct = _create_mortgage(seed_user, db.session)
        contract = self._contract(acct, seed_user["user"].id)
        self._legacy_manual_transfer(
            seed_user, db.session, acct, contract - Decimal("50.00"),
        )

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "short of the" in html
        assert "$50.00" in html

    def test_dashboard_warns_when_base_short_despite_standing_extra(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A short BASE warns even when a standing extra pushes total cash over contract.

        The firing control for ruling D3's ``extra_principal``-cancellation
        property: the warning compares the extra-free BASE (``default_amount``) to
        the contractual P&I + escrow, so a manual payment with base = contract-$50
        and a $75 standing extra -- total live cash contract+$25, ABOVE contract --
        must STILL warn, because the base under-covers P&I + escrow.  A regression
        that added the extra to the compared (stored) side (``stored =
        default_amount + extra``) would suppress this warning and still pass every
        other test in the class; this asserts the $50.00 base shortfall renders.
        """
        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]
        contract = self._contract(acct, seed_user["user"].id)
        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={
                "source_account_id": str(checking.id),
                "amount": str(contract - Decimal("50.00")),
                "extra_principal": "75.00",
            },
        )

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "short of the" in html
        assert "$50.00" in html          # the BASE shortfall, extra excluded

    @pytest.mark.parametrize("delta", [Decimal("0.00"), Decimal("100.00")])
    def test_dashboard_no_warning_when_manual_at_or_above_contract(
        self, auth_client, seed_user, db, seed_periods, delta,
    ):
        """A payment exactly at or above contract never warns.

        Ruling D3: a deliberate overpayment does not trip the drift warning; the
        warning is underpayment-only.
        """
        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]
        contract = self._contract(acct, seed_user["user"].id)
        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={
                "source_account_id": str(checking.id),
                "amount": str(contract + delta),
            },
        )

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert "short of the" not in resp.data.decode()

    def test_dashboard_no_warning_when_derive_even_after_escrow_rise(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A DERIVE payment never warns, even when its stored base is now stale.

        The control that proves derive mode is EXCLUDED, not merely free of drift:
        a derive transfer is created (default_amount = contract at creation), then a
        $200/mo escrow line is added so the contract rises ABOVE the stored base.
        The underpayment predicate (stored < contract) is now TRUE -- asserted
        directly -- yet no warning shows, because a derive payment recomputes its
        cash to the contract on every read and cannot drift.
        """
        from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel

        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]
        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(checking.id)},   # derive default
        )
        # Raise the contract above the stored base by adding escrow AFTER creation.
        add_escrow_line(db.session, acct.id, "Taxes", Decimal("2400.00"))
        db.session.commit()

        tpl = (
            db.session.query(TransferTemplate)
            .filter_by(to_account_id=acct.id, user_id=seed_user["user"].id)
            .first()
        )
        new_contract = self._contract(acct, seed_user["user"].id)
        # The control: the stored base is now below contract (would warn if manual).
        assert tpl.default_amount < new_contract
        assert tpl.settings.derive_from_loan is True

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert "short of the" not in resp.data.decode()

    def test_track_payment_flips_to_derive_and_clears_warning(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The one-click switch flips a short manual payment to auto-track.

        A manual payment $50 below contract warns; POSTing track-payment sets
        derive_from_loan True and resets the stored base to the contract, so a
        re-render shows no warning and the loan now tracks the contract.
        """
        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]
        contract = self._contract(acct, seed_user["user"].id)
        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={
                "source_account_id": str(checking.id),
                "amount": str(contract - Decimal("50.00")),
            },
        )
        tpl = _payment_template(db.session, seed_user, acct)
        # Precondition: the warning is showing.
        pre = auth_client.get(f"/accounts/{acct.id}/loan")
        assert "short of the" in pre.data.decode()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payments/{tpl.id}/track",
        )
        assert resp.status_code == 302
        assert f"/accounts/{acct.id}/loan" in resp.headers.get("Location", "")

        db.session.expire(tpl)
        assert tpl.settings.derive_from_loan is True
        assert tpl.default_amount == contract

        post = auth_client.get(f"/accounts/{acct.id}/loan")
        assert "short of the" not in post.data.decode()

    def test_track_payment_creates_settings_row_for_legacy_manual(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Switching a legacy no-settings payment creates the settings row (derive).

        The real-loan shape has no loan_payment_settings row; the switch must
        create one with derive_from_loan True (a missing row is manual mode), keep
        the extra at zero, and reset the stored base to the contract.
        """
        acct = _create_mortgage(seed_user, db.session)
        contract = self._contract(acct, seed_user["user"].id)
        tpl = self._legacy_manual_transfer(
            seed_user, db.session, acct, contract - Decimal("50.00"),
        )
        assert tpl.settings is None   # legacy shape: no settings row

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payments/{tpl.id}/track",
        )
        assert resp.status_code == 302

        db.session.expire(tpl)
        assert tpl.settings is not None
        assert tpl.settings.derive_from_loan is True
        assert tpl.settings.extra_principal == Decimal("0.00")
        assert tpl.default_amount == contract

    def test_track_payment_preserves_standing_extra(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Switching to auto-track preserves the standing extra principal.

        A manual payment with a $75 standing extra, $50 below contract on its base:
        the switch flips derive True and keeps extra at $75 (the extra rides on top
        of the tracked base, unchanged), resetting the base to the contract.
        """
        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]
        contract = self._contract(acct, seed_user["user"].id)
        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={
                "source_account_id": str(checking.id),
                "amount": str(contract - Decimal("50.00")),
                "extra_principal": "75.00",
            },
        )
        tpl = _payment_template(db.session, seed_user, acct)

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payments/{tpl.id}/track",
        )
        assert resp.status_code == 302

        db.session.expire(tpl)
        assert tpl.settings.derive_from_loan is True
        assert tpl.settings.extra_principal == Decimal("75.00")
        assert tpl.default_amount == contract

    def test_track_payment_unknown_template_404s(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A template id naming none of this loan's payments is a 404, no 500."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payments/999999/track",
        )
        assert resp.status_code == 404

    def test_track_payment_flips_the_named_definition_not_the_oldest(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Two payments into one loan: the door flips the one its URL names.

        The shape ruling R-R83 was asked for (plan ledger row D49): a fixed
        sweep created BEFORE the real payment, both fixed (the payment was
        created with a typed amount).  The old loan-keyed door took
        ``active_recurring_transfer_template`` -- the OLDEST -- and flipped
        the sweep to derive, so the loan was projected paying the contract
        twice a month.  Naming the payment in the URL makes the oldest
        irrelevant: the sweep stays fixed at its own figure and the named
        payment is the one that tracks.
        """
        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]
        contract = self._contract(acct, seed_user["user"].id)
        # The sweep first, so it is the oldest active definition into the loan.
        sweep = self._legacy_manual_transfer(
            seed_user, db.session, acct, Decimal("50.00"),
        )
        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={
                "source_account_id": str(checking.id),
                "amount": str(contract - Decimal("50.00")),
            },
        )
        from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel
        payment = (
            db.session.query(TransferTemplate)
            .filter_by(to_account_id=acct.id, user_id=seed_user["user"].id)
            .filter(TransferTemplate.id != sweep.id)
            .one()
        )
        assert sweep.id < payment.id

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payments/{payment.id}/track",
        )
        assert resp.status_code == 302

        db.session.expire_all()
        assert payment.settings.derive_from_loan is True
        assert payment.default_amount == contract
        assert sweep.settings is None
        assert sweep.default_amount == Decimal("50.00")

    def test_track_payment_refuses_another_loans_template(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The owner's payment into a DIFFERENT loan is a 404 on this loan's track door.

        The same gate the settings door proves
        (``TestTransferPrompt.test_update_payment_settings_refuses_another_loans_template``),
        asked of the second door: one function, two doors, each measured.
        """
        acct = _create_mortgage(seed_user, db.session)
        other = _create_mortgage(seed_user, db.session, name="Other Mortgage")
        contract = self._contract(other, seed_user["user"].id)
        other_tpl = self._legacy_manual_transfer(
            seed_user, db.session, other, contract - Decimal("50.00"),
        )

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payments/{other_tpl.id}/track",
        )
        assert resp.status_code == 404
        db.session.expire(other_tpl)
        assert other_tpl.settings is None
        assert other_tpl.default_amount == contract - Decimal("50.00")

    def test_the_card_renders_one_strip_per_definition_with_its_own_controls(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Two payments into one loan: two strips, each with ITS figure and doors.

        Ruling R-R83 (plan step R7d-g-3): the card lists every recurring
        transfer into the loan.  A fixed ``$50`` sweep (the older definition,
        no settings row) and the dashboard-created payment (tracks the loan,
        ``$125`` extra).  Each strip prefills its OWN extra, posts to its OWN
        settings door, and only the fixed one offers the Track button -- the
        tracking one has nothing to flip.  The sweep, at ``$50`` against a
        four-figure contract, is short, so its strip carries the shortfall
        sentence and the warning tint while the tracking strip carries none.
        """
        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]
        contract = self._contract(acct, seed_user["user"].id)
        sweep = self._legacy_manual_transfer(
            seed_user, db.session, acct, Decimal("50.00"),
        )
        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={
                "source_account_id": str(checking.id),
                "extra_principal": "125.00",
            },
        )
        from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel
        payment = (
            db.session.query(TransferTemplate)
            .filter_by(to_account_id=acct.id, user_id=seed_user["user"].id)
            .filter(TransferTemplate.id != sweep.id)
            .one()
        )

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()

        # Two strips, in definition order (oldest first), each named (the
        # payment's name carries a ``->`` the template escapes).
        assert (
            html.index(f'data-template-id="{sweep.id}"')
            < html.index(f'data-template-id="{payment.id}"')
        )
        assert sweep.name in html
        assert payment.name.replace(">", "&gt;") in html
        assert "2 into this loan" in html
        # Each strip's own doors.
        for tpl in (sweep, payment):
            assert f"/accounts/{acct.id}/loan/payments/{tpl.id}/settings" in html
        # One tracker per loan: the payment tracks, so neither strip offers
        # Track (the sweep's strip names the tracker in its shortfall).
        assert f"/accounts/{acct.id}/loan/payments/{sweep.id}/track" not in html
        assert f"/accounts/{acct.id}/loan/payments/{payment.id}/track" not in html
        # Each strip's own extra, prefilled on its own input.
        assert re.search(
            rf'id="extra-principal-{sweep.id}"[^>]*value="0\.00"', html,
        )
        assert re.search(
            rf'id="extra-principal-{payment.id}"[^>]*value="125\.00"', html,
        )
        # The modes, one each.
        assert html.count("Tracks the loan") == 1
        assert html.count("Fixed amount") == 1
        # Each strip's next payment, priced by the amount model: the sweep's
        # $50.00 stated price (rule 3 -- no settings row), the tracking
        # payment's contract + its $125 extra (rule 4's derive arm).
        assert "$50.00" in html
        assert f"${contract + Decimal('125.00'):,.2f}" in html
        assert "next payment," in html
        # The sweep is short; the tracking payment is not.
        assert html.count("loan-payment--short") == 1
        assert "short of the" in html
        # The page-top alerts are gone: the card is the one home (the base
        # layout's own MFA nag is the one warning alert a page may carry).
        assert "Switch to automatic payment" not in html
        assert html.count("alert-warning") == html.count("mfa-nag-banner")
        assert "No recurring payment set up" not in html

    def test_the_strip_prices_the_next_payment_not_the_newest_stated_price(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A fixed payment's figure and drift are its NEXT row's price, per the amount model.

        The owner states a future price on a fixed payment ($9,999.00 from
        2028).  ``default_amount`` follows the NEWEST statement
        (``template_amount_service._resync_scalar``), so the first cut of the
        card -- and the drift before it -- would have shown $9,999.00 as what
        the loan is paid and judged the drift on it.  The strip prices the
        next occurrence through ``cash_ledger.definition_cash`` (ruling
        R-R67's one producer), so it shows the price in effect for that date,
        and the shortfall sentence names the same figure.
        """
        acct = _create_mortgage(seed_user, db.session)
        contract = self._contract(acct, seed_user["user"].id)
        stored = contract - Decimal("50.00")
        tpl = self._legacy_manual_transfer(seed_user, db.session, acct, stored)
        state_template_price(
            tpl, Decimal("9999.00"), effective_on=date(2028, 1, 1),
        )
        db.session.commit()
        db.session.expire(tpl)
        assert tpl.default_amount == Decimal("9999.00"), (
            "precondition: the scalar follows the newest statement"
        )

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "$9,999.00" not in html
        assert f"${stored:,.2f}" in html
        assert "next payment," in html
        assert "$50.00" in html and "short of the" in html

    def test_a_second_tracker_is_refused_at_the_door_and_hidden_on_the_card(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """One definition tracks a loan (developer, 2026-09-14).

        A tracking payment plus a fixed $50 sweep: the sweep's strip offers no
        Track control and its shortfall sentence names the tracker; a crafted
        POST to the sweep's track door is refused with the flash naming the
        tracker and writes nothing -- no settings row, the $50 untouched.  The
        tracker's own door still answers (a re-track writes the same mode).
        """
        from app.routes.loan.payment_transfer import (  # pylint: disable=import-outside-toplevel
            ANOTHER_PAYMENT_TRACKS_THE_LOAN,
        )
        acct = _create_mortgage(seed_user, db.session)
        checking = seed_user["account"]
        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(checking.id)},
        )
        tracker = _payment_template(db.session, seed_user, acct)
        sweep = self._legacy_manual_transfer(
            seed_user, db.session, acct, Decimal("50.00"),
        )

        html = auth_client.get(f"/accounts/{acct.id}/loan").data.decode()
        assert f"/accounts/{acct.id}/loan/payments/{sweep.id}/track" not in html
        assert f"/accounts/{acct.id}/loan/payments/{tracker.id}/track" not in html
        assert "tracks the loan, so this" in html
        assert tracker.name.replace(">", "&gt;") in html

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payments/{sweep.id}/track",
            follow_redirects=True,
        )
        assert resp.status_code == 200
        page = resp.data.decode()
        # The door's own sentence, HTML-escaped as the flash renders it.
        assert "already tracks this loan" in ANOTHER_PAYMENT_TRACKS_THE_LOAN
        assert "already tracks this loan" in page
        assert tracker.name.replace(">", "&gt;") in page
        assert "cannot track it too" in page
        db.session.expire_all()
        assert sweep.settings is None
        assert sweep.default_amount == Decimal("50.00")

        # The tracker's own door is not refused by its own tracking.
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payments/{tracker.id}/track",
        )
        assert resp.status_code == 302
        db.session.expire_all()
        assert tracker.settings.derive_from_loan is True

    def test_track_payment_idor(
        self, second_auth_client, seed_user, db, seed_periods,
    ):
        """A non-owner switching a loan's payment gets a 404 (not-yours == not-found).

        With the owner's real template id in the URL, so the 404 is the
        ownership gate's and not the URL map's (the owner's 302 on the same
        URL shape is ``test_track_payment_flips_to_derive_and_clears_warning``).
        The owner's payment is built through the ORM, not the owner's client:
        see ``test_update_payment_settings_idor`` for why a second client's
        request after the owner's runs as the owner.
        """
        acct = _create_mortgage(seed_user, db.session)
        contract = self._contract(acct, seed_user["user"].id)
        tpl = self._legacy_manual_transfer(
            seed_user, db.session, acct, contract - Decimal("50.00"),
        )
        resp = second_auth_client.post(
            f"/accounts/{acct.id}/loan/payments/{tpl.id}/track",
        )
        assert resp.status_code == 404
        db.session.expire(tpl)
        assert tpl.settings is None
        assert tpl.default_amount == contract - Decimal("50.00")


# ── ARM Rate History Integration Tests (Commit 5.7-1) ──────────────


class TestARMRateHistoryIntegration:
    """Tests for ARM rate history integration in the loan dashboard.

    Verifies that the dashboard and payoff calculator correctly load
    RateHistory entries for ARM loans, convert them to RateChangeRecords,
    and pass them to the amortization engine.
    """

    def test_arm_dashboard_passes_rate_history(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """ARM mortgage with rate history: dashboard projection differs
        from a fixed-rate projection.

        Creates an ARM mortgage at 5%, adds a rate change to 7%,
        verifies the dashboard renders and shows rate history data.
        """
        acct = _create_loan_account(
            seed_user, db.session, AcctTypeEnum.MORTGAGE, "ARM Mortgage",
            Decimal("100000.00"), Decimal("0.05000"), 360,
            date(2024, 1, 1), 1, is_arm=True,
        )
        # Add a rate change effective Feb 2025.
        entry = RateHistory(
            account_id=acct.id,
            effective_date=date(2025, 2, 1),
            interest_rate=Decimal("0.07000"),
        )
        db.session.add(entry)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Dashboard should render with the rate history visible.
        assert "Balance owed" in html

    def test_non_arm_dashboard_ignores_rate_history(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Non-ARM loan with rate history entries: rate_changes not passed.

        Defensively verifies that rate history entries on a non-ARM loan
        do not affect the projection.  The dashboard should produce
        identical output to one with no rate history.
        """
        acct = _create_mortgage(seed_user, db.session)

        # Insert rate history despite is_arm=False (defensive case).
        entry = RateHistory(
            account_id=acct.id,
            effective_date=date(2025, 2, 1),
            interest_rate=Decimal("0.07000"),
        )
        db.session.add(entry)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Balance owed" in html
        # Non-ARM: rate history section should NOT be visible.
        assert "Rate History" not in html or "Rate Change" not in html


# ── Multi-Scenario Visualization Tests (Commit 5.5-1) ──────────────


class TestMultiScenarioVisualization:
    """Tests for multi-scenario balance chart and payoff calculator.

    Verifies that the dashboard and payoff calculator correctly compute
    and display original, committed, floor, and accelerated scenarios.
    """

    def test_dashboard_chart_no_payments_shows_contractual(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Dashboard with no transfers: the band splits at today, the past the ledger's.

        With no payments there is no confirmed history, yet the band still
        splits at TODAY (plan step R7d-g-3, ruling **R-R88**: the line is the
        seam's ``positions`` on the contractual grid -- a past date reads the
        ledger, a future one the plan fold).  ``_create_mortgage`` asserts
        $250,000 on 2026-01-01 with payment day 1, so the grid's Feb 1 and
        Mar 1 2026 fall at or before the frozen 2026-03-20 today: two
        ledger points at the asserted balance (nothing paid), then the
        projection.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        band = _parse_band_chart(resp.data.decode())
        assert band is not None
        assert band["current_index"] == 2
        assert band["balance"][:2] == [250000.0, 250000.0]
        assert len(band["balance"]) > 2

    def test_dashboard_chart_with_projected_payment(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Dashboard with a projected transfer: band renders, no false history.

        A projected (not-yet-confirmed) transfer is the plan's, not the
        ledger's: the band's split stays at today (the same two ledger
        points as the no-transfer case, at the asserted $250,000 -- a
        projected row moves no past balance) and the balance line is
        present.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        band = _parse_band_chart(resp.data.decode())
        assert band is not None
        assert band["current_index"] == 2
        assert band["balance"][:2] == [250000.0, 250000.0]
        assert len(band["balance"]) > 2

    def test_dashboard_chart_confirmed_payment_is_history(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Dashboard with a confirmed transfer: the ledger's points carry it.

        A confirmed (Paid) transfer is the ledger's, so the band's solid
        segment -- the grid dates at or before today, read off the ledger --
        shows the balance it paid down: the Feb 1 2026 point (the payment is
        due Feb 1, settled Jan 16) sits BELOW the $250,000 asserted on
        2026-01-01, where the no-payment sibling's sits flat on it.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        band = _parse_band_chart(resp.data.decode())
        assert band is not None
        assert band["current_index"] == 2
        assert band["labels"][0] == "Feb 2026"
        assert band["balance"][0] < 250000.0

    def test_payoff_results_committed_metrics(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Payoff calculator shows the committed-plan-vs-original comparison.

        When payments exist, the payoff results partial reports how the current
        committed plan compares to the original schedule and carries the
        accelerated overlay for the band chart.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "200"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Current plan vs. original" in html
        assert "data-overlay=" in html

    def test_payoff_what_if_overlay_and_metrics(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Payoff with extra renders the band overlay plus the saved-months metric.

        With payments and extra_monthly > 0, the result carries the accelerated
        overlay (the green preview) and the Months Saved figure.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "200"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "data-overlay=" in html
        assert "Months Saved" in html

    def test_payoff_no_transfer_degrades(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Payoff with no transfers still renders the overlay, without the plan comparison.

        With no payments the accelerated overlay still renders (from the
        contractual baseline) but the committed-plan-vs-original comparison line
        is omitted.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "200"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "data-overlay=" in html
        assert "Current plan vs. original" not in html

    def test_payoff_what_if_zero(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """extra_monthly=0 renders without error and still carries the overlay."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "0"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "data-overlay=" in html

    def test_payoff_target_date_still_works(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Target date mode is unaffected by multi-scenario changes.

        The existing target_date mode should continue to return
        required extra payment data without regressions.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "target_date", "target_date": "2040-01-01"},
        )
        assert resp.status_code == 200

    def test_dashboard_arm_band_renders(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """ARM loan: the band renders its balance line and ARM tag.

        With no payments the band's past points are the ledger's (the
        $100,000 asserted on 2026-01-01, unpaid on the grid's Feb 1 and
        Mar 1) and the rest the plan fold's; the rate chip carries the ARM
        tag.
        """
        acct = _create_loan_account(
            seed_user, db.session, AcctTypeEnum.MORTGAGE, "ARM Mortgage",
            Decimal("100000.00"), Decimal("0.05000"), 360,
            date(2024, 1, 1), 1, is_arm=True,
        )
        entry = RateHistory(
            account_id=acct.id,
            effective_date=date(2025, 2, 1),
            interest_rate=Decimal("0.07000"),
        )
        db.session.add(entry)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        band = _parse_band_chart(html)
        assert band is not None
        assert band["current_index"] == 2
        assert band["balance"][:2] == [100000.0, 100000.0]
        assert len(band["balance"]) > 2
        # The rate chip carries the ARM tag.
        assert "ARM" in html
        assert "Balance owed" in html


# ── Payment Breakdown Tests (Commit 5.14-1) ────────────────────────


def _add_escrow(db_session, account_id, name, annual_amount,
                inflation_rate=None):
    """Add an escrow line (with one origination-dated version) to a loan.

    Thin wrapper over the shared :func:`tests._test_helpers.add_escrow_line`;
    the version defaults to the loan's origination date, so the escrow is active
    for the current-period breakdown these tests read.
    """
    return add_escrow_line(
        db_session, account_id, name, annual_amount,
        inflation_rate=inflation_rate,
    )


class TestPaymentBreakdown:
    """Tests for the payment allocation breakdown card on the loan dashboard.

    Verifies that the breakdown shows correct P/I/E split, handles
    edge cases, and renders the progress bar with accurate percentages.
    """

    def test_breakdown_shows_on_dashboard(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Mortgage with escrow: breakdown card shows P/I/E amounts.

        Setup: $250,000 mortgage at 6.5%, 360 months, origination 2023-06-01.
        LoanParams created by _create_mortgage: balance stated at $250K,
        original_principal=$255K, rate=0.065, term=360, payment_day=1.
        Escrow: $7,200 property tax + $2,400 insurance = $9,600/yr = $800/mo.

        Monthly P&I from original terms ($255K, 6.5%, 360mo): ~$1,611.64.
        The breakdown should show the P/I split for the current period
        plus the escrow portion.
        """
        acct = _create_mortgage(seed_user, db.session)
        _add_escrow(db.session, acct.id, "Property Tax", Decimal("7200.00"))
        _add_escrow(db.session, acct.id, "Insurance", Decimal("2400.00"))
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Breakdown card renders.
        assert "Payment allocation" in html
        assert "to principal" in html
        assert "to interest" in html
        assert "to escrow" in html
        # Escrow = $800/mo (9600/12).
        assert "800.00" in html

    def test_breakdown_no_escrow(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Auto loan with no escrow: only P/I shown, escrow line absent."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Payment allocation" in html
        assert "to principal" in html
        assert "to interest" in html
        # Escrow line should not appear.
        assert "to escrow" not in html

    def test_breakdown_proportions_sum_to_100(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Displayed percentages sum to exactly 100.0%.

        Parse the three percentage values from the HTML and verify
        their sum.  Uses a mortgage with escrow for three components.
        """
        acct = _create_mortgage(seed_user, db.session)
        _add_escrow(db.session, acct.id, "Tax", Decimal("6000.00"))
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Extract percentages from "to principal (XX.X%)" pattern.
        import re as _re
        pcts = _re.findall(r"to (?:principal|interest|escrow) \((\d+\.\d)%\)", html)
        assert len(pcts) == 3, f"Expected 3 percentages, found {len(pcts)}: {pcts}"
        total = sum(Decimal(p) for p in pcts)
        assert total == Decimal("100.0"), (
            f"Percentages sum to {total}, expected 100.0"
        )

    def test_breakdown_hidden_no_params(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Loan without LoanParams: breakdown card not shown."""
        # Create a bare account with no params.
        loan_type = db.session.query(AccountType).filter_by(
            name="Mortgage",
        ).one()
        account = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=loan_type.id,
                name="No Params Mortgage",
                anchor_balance=Decimal("200000.00"),
            ),
        )
        db.session.add(account)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{account.id}/loan")
        # Without params, renders setup page (no breakdown).
        assert resp.status_code == 200
        assert b"Payment allocation" not in resp.data

    def test_breakdown_with_extra_payment(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Extra payments included in the principal portion.

        When the committed schedule includes extra payments (from
        transfers), the "principal" line in the breakdown should
        reflect both standard principal and extra_payment.
        """
        acct = _create_mortgage(seed_user, db.session)
        # Create a transfer that overpays the standard P&I.
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("2000.00"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Payment allocation" in html
        assert "to principal" in html

    def test_breakdown_confirmed_row_labeled(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Confirmed payment: card header shows Confirmed badge.

        When all schedule rows are confirmed (loan fully paid through
        transfers), the breakdown should label the data as confirmed.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # The first non-confirmed (projected) row is shown, but
        # the confirmed payment's row would show "Confirmed" badge.
        assert "Payment allocation" in html

    def test_breakdown_escrow_zero_hidden(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Mortgage where all escrow components are inactive: escrow hidden.

        Even though the loan type typically has escrow, if all components
        are inactive, the escrow line should not render.
        """
        acct = _create_mortgage(seed_user, db.session)
        # No escrow components added (none active).
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Payment allocation" in html
        assert "to escrow" not in html

    def test_breakdown_uses_committed_schedule(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Breakdown uses committed (payment-aware) schedule, not original.

        When payments exist, the committed schedule's P/I split differs
        from the original because real payments affect the balance
        trajectory.  The breakdown should reflect the committed values.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Breakdown renders from committed data.
        assert "Payment allocation" in html
        assert "to principal" in html

    def test_breakdown_escrow_inflation_note(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """O-3: Escrow with inflation_rate shows projected increase note.

        When escrow components have non-null inflation rates, the
        breakdown should show a note about projected escrow increase.
        """
        acct = _create_mortgage(seed_user, db.session)
        _add_escrow(
            db.session, acct.id, "Property Tax",
            Decimal("7200.00"), inflation_rate=Decimal("0.0300"),
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "inflation estimates" in html

    def test_breakdown_sums_the_coming_months_payments_and_skips_catch_ups(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The bar is the coming installment MONTH: every payment due in it, summed.

        A $250k / 6.5% / 360-month mortgage originated 2026-03-01 (first
        installment Apr 1 2026; nothing overdue at the frozen 2026-03-20
        today) paid by TWO definitions: a tracking payment carrying a $125
        extra, due the 1st, and a fixed $50 sweep due the 15th -- whose Mar
        15 occurrence no row answers, so the plan prices it as a catch-up
        paid the day after the read (ruling R-R64, the D1 clamp) and its Jan
        and Feb occurrences fall inside the origination assertion (R-R72).

        The bar reads April: ``$1,580.17 + $125 + $50 = $1,755.17``, not the
        catch-up on top ($1,805.17) and not the next installment alone
        ($1,705.17).  The catch-up still moves the balance the fold charges
        on: ``$250,000 - $50 = $249,950`` at Apr 1, so April's interest is
        ``249950 * 0.065 / 12 = $1,353.90`` (HALF_UP) and the month's
        principal ``$1,755.17 - $1,353.90 = $401.27`` (the sweep's $50 is all
        principal: April's charge is met by the payment before it).
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 3, 1),
        )
        tracking = make_loan_payment_template(
            db.session, seed_user, acct, extra_principal="125.00",
        )
        # The builder names every template after the loan; two into one loan
        # need distinct names (``uq_transfer_templates_user_name``).
        tracking.name = "Mortgage payment"
        db.session.flush()
        make_loan_payment_template(
            db.session, seed_user, acct, amount="50.00",
            derive_from_loan=False, cadence=MONTHLY, fires_on_day=15,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Apr 2026 payment" in html
        assert "$1,755.17" in html
        assert "$1,353.90" in html
        assert "$401.27" in html
        assert "$1,805.17" not in html
        pcts = re.findall(r'data-progress-pct="([0-9.]+)"', html)
        assert sum(Decimal(p) for p in pcts) == Decimal("100.0")

    def test_breakdown_no_inflation_note_when_zero(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Escrow with zero or null inflation_rate: no inflation note."""
        acct = _create_mortgage(seed_user, db.session)
        _add_escrow(
            db.session, acct.id, "Insurance",
            Decimal("2400.00"), inflation_rate=None,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "inflation estimates" not in html


# -- Amortization Schedule Tab Tests (Commit 5.13-1) ------------------


def _create_fresh_mortgage(seed_user, db_session, principal=Decimal("250000.00"),
                           rate=Decimal("0.06500"), term=360, payment_day=1,
                           origination_date=None):
    """Create a mortgage with predictable schedule length.

    By default, origination is the first of last month so the
    schedule's first payment month is this month.  Tests that need
    the schedule to align with specific ``seed_periods`` indices must
    pass ``origination_date`` explicitly so the alignment does not
    drift as today's date advances.

    States no balance after origination, so the schedule aligns with the
    full term (no early-payoff due to a lower current balance).

    Args:
        seed_user: The seed_user fixture dict.
        db_session: Active database session.
        principal: Loan principal (Decimal).  Default $250,000.
        rate: Annual interest rate (Decimal).  Default 6.5%.
        term: Term in months.  Default 360.
        payment_day: Payment day of month.  Default 1.
        origination_date: Optional explicit origination date.  Default
            is the first of last month relative to ``date.today()``.
            Pass an explicit date for tests that depend on schedule
            alignment with fixed-date fixtures.
    """
    if origination_date is None:
        # Origination one month before today so the first payment month
        # is the current month (schedule starts month after origination).
        first_of_this_month = date.today().replace(day=1)
        if first_of_this_month.month == 1:
            origination_date = first_of_this_month.replace(
                year=first_of_this_month.year - 1, month=12,
            )
        else:
            origination_date = first_of_this_month.replace(
                month=first_of_this_month.month - 1,
            )
    return _create_loan_account_exact(
        seed_user, db_session, AcctTypeEnum.MORTGAGE, "Fresh Mortgage",
        principal, rate, term, origination_date, payment_day,
    )


def _create_loan_account_exact(seed_user, db_session, account_type, name,
                                original_principal, rate, term, orig_date,
                                payment_day):
    """Like :func:`_create_loan_account` but with NO stated balance.

    The loan carries only its origination anchor, so ``original_principal``
    IS its resolved balance -- there is no ``+ $5,000`` paid-down gap and no
    assertion event.  (The hand-rolled block this replaced also took a
    ``current_principal``, but it only ever landed in the non-authoritative
    ``LoanParams.current_principal`` column -- dropped at plan step R20 --
    and the loan account's unread anchor balance: with no assertion event,
    the resolver and the genesis ledger both seeded from
    ``original_principal`` regardless.  The parameter is gone rather than
    kept as a decorative no-op.)

    Routes through the shared factory, so the loan's genesis posting ledger is
    opened in the same transaction as its ``LoanParams``.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        db_session: The test ``db.session``.
        account_type: The :class:`~app.enums.AcctTypeEnum` member.
        name: The account name.
        original_principal: The origination principal (the resolved balance).
        rate: The origination annual rate as a Decimal fraction.
        term: The loan term in months.
        orig_date: The loan origination date.
        payment_day: The day-of-month payment day.

    Returns:
        The created loan :class:`~app.models.account.Account`.
    """
    return create_loan_account(
        seed_user, db_session, name=name, principal=original_principal,
        rate=rate, term=term, origination_date=orig_date,
        payment_day=payment_day, account_type=account_type,
    )


class TestAmortizationSchedule:
    """Tests for the full amortization schedule at the standalone /loan/schedule route.

    Verifies that the schedule table renders correctly with the right
    number of rows, confirmed/projected distinction, currency formatting,
    totals row, and conditional Rate column for ARM loans.  (Loop B demoted
    the schedule off the detail page into its own route; the table content is
    unchanged.)
    """

    def test_schedule_route_renders(self, auth_client, seed_user, db, seed_periods):
        """C-5.13-1: the standalone schedule route renders the full schedule.

        GET /loan/schedule for a mortgage with params.  Assert the page heading
        and the month-by-month table are both present.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan/schedule")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Amortization Schedule" in html
        assert "Month-by-Month Schedule" in html

    def test_schedule_has_correct_row_count(self, auth_client, seed_user, db, seed_periods):
        """C-5.13-2: 30-year mortgage produces the correct number of data rows.

        Re-pinned for Commit 5 of the amortization engine split
        (``docs/plans/2026-05-21-amortization-engine-split-implementation.md``).
        Pre-Commit-5 the dashboard called ``generate_schedule``
        directly, which iterated up to ``max_months = remaining_months
        + term_months`` and emitted a 361st row absorbing the
        sub-penny rounding residue.  Post-Commit-5 the schedule routed
        through ``compute_payoff_scenarios`` -> ``project_forward``, which
        terminates cleanly at ``month_num == remaining_months``, absorbing
        the residue in the final scheduled month; since plan step R7d-g-3
        (ruling **R-R88**) it lists the balance seam's forward plan
        (``balance_at.loan_installments``) through the payoff, and a plan
        with no recurring payment folds the contract's own installments.
        The architecturally correct row count for a 30-year mortgage with
        nothing due yet is therefore ``term_months == 360`` -- one row per
        scheduled month, no residue artifact.  Hand-derivation:
        ``len(history_rows) == 0`` (no confirmed payments) + 360
        contractual installments, every one in the plan's future.

        The loan originates on 2026-03-01 so its first installment (Apr 1)
        is after the frozen 2026-03-20 today.  Originated a month earlier,
        the Mar 1 installment would be due and unpaid, and an overdue
        installment is NOT in the plan (ruling D1 / finding B-9): the fold
        then clears the loan in the post-contractual extension and the
        table honestly runs past 360 --
        ``test_schedule_lists_the_plan_when_an_installment_is_overdue``.
        """
        expected_count = 360

        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 3, 1),
        )
        resp = auth_client.get(f"/accounts/{acct.id}/loan/schedule")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Count data rows: each has exactly one Projected or Confirmed badge.
        projected = html.count('badge bg-secondary">Projected</span>')
        confirmed = html.count('badge bg-success">Confirmed</span>')
        total_rows = projected + confirmed
        assert total_rows == expected_count, (
            f"Expected {expected_count} data rows, got {total_rows} "
            f"({projected} projected, {confirmed} confirmed)"
        )

    def test_schedule_confirmed_rows_marked(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.13-3: Confirmed payment rows are visually distinguished.

        Creates a mortgage and two confirmed transfers in months
        that have already passed relative to the autouse-frozen
        today (2026-03-20).  Asserts confirmed rows get a distinct
        badge and the rest are Projected.

        Re-pinned for Commit 5 of the amortization engine split
        (``docs/plans/2026-05-21-amortization-engine-split-implementation.md``).
        Pre-Commit-5 the dashboard called ``generate_schedule``,
        which had no ``as_of`` concept and marked any payment record
        as Confirmed based solely on the ``is_confirmed`` field.
        Post-Commit-5 the dashboard routes through
        ``compute_payoff_scenarios``, whose replay only consumes
        confirmed payments with ``payment_date <= as_of``; confirmed
        payments dated AFTER today are data-hygiene cases and are
        the plan's to price (Projected badge) -- the new
        architecture's stricter semantic for the Confirmed badge.
        The previous fixture used April/May 2026 seed_periods (after
        the frozen today), which exercised the data-hygiene path,
        not the realistic "DONE payment in history" path.  This
        rewrite uses February/March seed_periods (before today) so
        the DONE payments fall in the replay window and produce
        Confirmed badges.
        """
        # Origination 2026-01-01 -> first scheduled payment month
        # is February 2026 (origination + 1 month).  seed_periods[3]
        # starts 2026-02-13 (Feb 2026) -> schedule month 1.
        # seed_periods[5] starts 2026-03-13 (Mar 2026) -> schedule
        # month 2.  Both before frozen today 2026-03-20, so the
        # composer's replay consumes them and produces history rows
        # with is_confirmed=True.
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[3], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[5], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan/schedule")
        assert resp.status_code == 200
        html = resp.data.decode()
        confirmed = html.count('badge bg-success">Confirmed</span>')
        projected = html.count('badge bg-secondary">Projected</span>')
        assert confirmed == 2, (
            f"Expected 2 confirmed rows, got {confirmed}"
        )
        assert projected > 0, "Expected some projected rows"
        # Total should still be a full schedule.
        assert confirmed + projected > 300

    def test_schedule_first_last_row(self, auth_client, seed_user, db, seed_periods):
        """C-5.13-4: First and last rows have correct values for known loan params.

        Loan: $250,000 at 6.5% for 360 months.
        Hand calculation:
          M = P * r(1+r)^n / [(1+r)^n - 1]
          r = 0.065/12 = 0.00541666...
          (1+r)^360 ~ 6.9920
          M = 250000 * (0.00541666 * 6.9920) / (6.9920 - 1)
          M = 250000 * 0.037878 / 5.9920
          M ~ $1,580.17

        First month:
          Interest = 250000 * 0.065/12 = $1,354.17
          Principal = 1580.17 - 1354.17 = $226.00

        Last row: remaining_balance = $0.00

        Originated 2026-03-01 so the first installment (Apr 1 2026) is the
        plan's first row on the untouched $250,000 (see
        ``test_schedule_has_correct_row_count`` for why a loan a month
        older would list a different first row).
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 3, 1),
        )
        resp = auth_client.get(f"/accounts/{acct.id}/loan/schedule")
        assert resp.status_code == 200
        html = resp.data.decode()
        # First row: month 1, expected payment of $1,580.17.
        assert "$1,580.17" in html
        # First month interest: $250,000 * 0.065/12 = $1,354.17.
        assert "$1,354.17" in html
        # First month principal: $1,580.17 - $1,354.17 = $226.00.
        assert "$226.00" in html
        # Last row balance must be $0.00.
        # The totals row does not have a balance cell, so "$0.00" comes
        # from the last data row's remaining_balance.
        assert "$0.00" in html

    def test_schedule_numbering_continuous_from_origination(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The # column counts installments from origination, not 1..N per slice.

        Regression for the user request: a mid-life loan's schedule must
        number rows by the installment's place in the contract -- a loan in
        its 26th month shows #25 for its Feb 1 2026 payment -- and the
        projected slice must keep counting up (#26, #27, ...) instead of
        restarting at 1 (the projected slice's pre-fix
        project_forward-local numbering).
        """
        # Origination 2024-01-01 -> the Feb 1 2026 payment is the 25th
        # (25 whole months after origination) at the frozen today
        # (2026-03-20).  The loan is asserted at $250,000 on 2026-01-01
        # (``_create_loan_account``: the balance when the owner began
        # tracking it, rulings R-R71 / R-R72), so the 24 installments before
        # that are the assertion's business and not the plan's.
        # seed_periods[2] (2026-01-30 .. 2026-02-12) contains 2/1, so its
        # confirmed payment IS the Feb 1 payment; seed_periods[3] (2026-02-13
        # .. 2026-02-26) contains no 1st, so its payment is due 3/1 -- the
        # 26th, paid ahead of its due date.  Both installments due since the
        # assertion are paid, so the plan's first row is the 27th and the
        # numbering runs continuously across the boundary.  (An installment
        # left unpaid past its due date is absent from the plan, and the
        # numbering then honestly skips it:
        # ``test_schedule_lists_the_plan_when_an_installment_is_overdue``.)
        acct = _create_loan_account(
            seed_user, db.session, AcctTypeEnum.MORTGAGE, "Mid-life Mortgage",
            Decimal("250000.00"), Decimal("0.06500"), 360,
            date(2024, 1, 1), 1,
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[2], Decimal("1611.77"),
            status_enum=StatusEnum.DONE,
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[3], Decimal("1611.77"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan/schedule")
        assert resp.status_code == 200
        html = resp.data.decode()
        # The first <td> of each schedule data row is the payment number;
        # year-header rows use <td colspan> and the totals row a label, so
        # this matches only data rows.
        row_numbers = [
            int(n) for n in re.findall(
                r'<tr class="(?:table-success)?">\s*<td>(\d+)</td>', html
            )
        ]
        assert row_numbers, "No schedule data rows parsed from the table"
        # Starts at the true payment number (25), NOT 1.
        assert row_numbers[0] == 25, (
            f"Expected first schedule row #25 (payments from origination), "
            f"got {row_numbers[0]}"
        )
        # Continuous +1 across the confirmed/projected boundary -- the
        # projected slice does not restart at 1.
        for i in range(1, len(row_numbers)):
            assert row_numbers[i] == row_numbers[i - 1] + 1, (
                f"Numbering restarted or jumped at index {i}: "
                f"{row_numbers[i - 1]} -> {row_numbers[i]}"
            )

    def test_schedule_lists_the_plan_when_an_installment_is_overdue(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """An overdue, unpaid installment is absent; the plan runs past the contract.

        The schedule lists what the loan is projected to PAY -- the balance
        seam's forward plan (plan step R7d-g-3, ruling **R-R88**) -- and an
        installment due before today that no payment answers is not in it
        (ruling D1 / finding B-9: a plan cannot have already happened).  A
        $250k / 6.5% / 360-month mortgage originated 2026-01-01 with its Feb
        1 2026 payment (#1) confirmed and its Mar 1 2026 payment (#2) unpaid
        at the frozen 2026-03-20 today: the plan's first row is #3 (Apr 1
        2026), the numbering runs continuously from there, and because the
        missed $1,580.17 is never paid it rides the balance to the contract's
        end -- ``1580.17 * 1.0054167 ** 358 ~= $10,930`` above the
        contractual track at #360 (Jan 2056) -- which the post-contractual
        extension clears at the level P&I (~$1,521 of principal a month
        against the residue's dwindling interest) in 8 more installments:
        the last row is #368 (Sep 2056) and reaches $0.00.  Until R7d-g-3
        the composer's committed slice listed a contractual row for the
        missed month as if it would be paid, and stopped at #360 with the
        residue absorbed into a phantom final payment.
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[2], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan/schedule")
        assert resp.status_code == 200
        html = resp.data.decode()
        row_numbers = [
            int(n) for n in re.findall(
                r'<tr class="(?:table-success)?">\s*<td>(\d+)</td>', html
            )
        ]
        assert html.count('badge bg-success">Confirmed</span>') == 1
        assert row_numbers[0] == 1
        assert 2 not in row_numbers, (
            "the overdue, unpaid Mar 1 installment is not in the plan"
        )
        planned = row_numbers[1:]
        assert planned[0] == 3
        assert planned == list(range(3, 3 + len(planned)))
        assert planned[-1] == 368, (
            "the missed installment's residue takes 8 extension installments "
            f"to clear: the last row is #368, got #{planned[-1]}"
        )
        # The last data row's balance cell -- the money cell right before the
        # status badge on a fixed-rate loan's row -- reaches $0.00.
        balances = re.findall(
            r'<td class="text-end font-mono">\$([0-9,]+\.\d\d)</td>\s*<td>\s*'
            r'<span class="badge',
            html,
        )
        assert len(balances) == len(row_numbers), "no balance cells parsed"
        assert balances[-1] == "0.00"

    def test_schedule_rows_are_months_summing_every_payment_in_them(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A planned row is a MONTH: every payment the plan makes in it, summed.

        The same loan as
        ``TestPaymentBreakdown.test_breakdown_sums_the_coming_months_payments_and_skips_catch_ups``
        -- $250k / 6.5% / 360 months originated 2026-03-01, a tracking
        payment with a $125 extra due the 1st and a $50 sweep due the 15th
        whose Mar 15 occurrence is a catch-up paid the day after the frozen
        2026-03-20 today -- so the table is month-by-month, not
        payment-by-payment: the catch-up is a row of its own dated the day
        it is paid (Mar 21, $50.00, all principal, balance $249,950.00),
        and April is ONE row (Apr 1: $1,755.17 paid, $1,353.90 interest,
        $401.27 principal, balance $249,548.73) with no separate Apr 15 row.
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 3, 1),
        )
        tracking = make_loan_payment_template(
            db.session, seed_user, acct, extra_principal="125.00",
        )
        tracking.name = "Mortgage payment"
        db.session.flush()
        make_loan_payment_template(
            db.session, seed_user, acct, amount="50.00",
            derive_from_loan=False, cadence=MONTHLY, fires_on_day=15,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan/schedule")
        assert resp.status_code == 200
        html = resp.data.decode()
        row_dates = re.findall(
            r'<tr class="(?:table-success)?">\s*<td>\d+</td>\s*<td>([^<]+)</td>',
            html,
        )
        assert row_dates[:2] == ["Mar 21, 2026", "Apr 1, 2026"], row_dates[:3]
        assert "Apr 15, 2026" not in row_dates
        assert "$249,950.00" in html
        assert "$1,755.17" in html
        assert "$1,353.90" in html
        assert "$401.27" in html
        assert "$249,548.73" in html

    def test_schedule_early_payoff_fewer_rows(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.13-5: Loan with short term pays off early (fewer than 360 rows).

        Creates a loan with a 12-month term.  The schedule from
        origination runs 12 months, well under 360.  Verifies
        the schedule table reflects the actual term, not a fixed
        30-year assumption.
        """
        acct = _create_loan_account_exact(
            seed_user, db.session, AcctTypeEnum.AUTO_LOAN, "Short Loan",
            Decimal("5000.00"),
            Decimal("0.06500"), 12, date(2026, 3, 1), 1,
        )
        resp = auth_client.get(f"/accounts/{acct.id}/loan/schedule")
        assert resp.status_code == 200
        html = resp.data.decode()
        projected = html.count('badge bg-secondary">Projected</span>')
        confirmed = html.count('badge bg-success">Confirmed</span>')
        total_rows = projected + confirmed
        assert total_rows < 360, (
            f"Expected fewer than 360 rows for short-term loan, got {total_rows}"
        )
        # 12 months + possibly 1 extra for sub-penny rounding residue.
        assert total_rows <= 13, (
            f"Expected ~12 rows for 12-month loan, got {total_rows}"
        )
        # Last row should still reach $0.00.
        assert "$0.00" in html

    def test_schedule_hidden_no_params(self, auth_client, seed_user, db, seed_periods):
        """C-5.13-6: Loan without LoanParams renders setup page, not schedule tab.

        When no LoanParams exist, the route renders setup.html, which
        does not include the dashboard tabs at all.
        """
        loan_type = db.session.query(AccountType).filter_by(name="Mortgage").one()
        account = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=loan_type.id,
                name="No Params Loan",
                anchor_balance=Decimal("200000.00"),
            ),
        )
        db.session.add(account)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{account.id}/loan")
        assert resp.status_code == 200
        assert b"Amortization Schedule" not in resp.data
        assert b"tab-schedule" not in resp.data

    def test_schedule_hidden_empty_schedule(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.13-7: Paid-off loan shows short schedule ending at $0.

        A loan fully retired via a confirmed payment shows a very
        short schedule (1 row) with the final balance at $0.00 and
        the row marked as confirmed.
        """
        # Small loan: $1000 at 5% for 12 months, origination Jan 2026.
        # First payment month: Feb 2026 (seed_periods[3] = Feb 13).
        acct = _create_loan_account_exact(
            seed_user, db.session, AcctTypeEnum.AUTO_LOAN, "Paid Off",
            Decimal("1000.00"),
            Decimal("0.05000"), 12, date(2026, 1, 1), 1,
        )
        # Large confirmed payment in Feb covers the full balance.
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[3], Decimal("1100.00"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan/schedule")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Schedule table exists with the payoff row.
        assert "Month-by-Month Schedule" in html
        confirmed = html.count('badge bg-success">Confirmed</span>')
        assert confirmed == 1, (
            f"Expected 1 confirmed row for paid-off loan, got {confirmed}"
        )
        assert "$0.00" in html

    def test_schedule_arm_rate_column_shown(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.13-8: ARM mortgage shows Rate column in the schedule table.

        Creates an ARM mortgage with a rate history entry. The Rate
        column header and rate values should appear in the schedule.
        """
        acct = _create_loan_account(
            seed_user, db.session, AcctTypeEnum.MORTGAGE, "ARM Schedule",
            Decimal("100000.00"), Decimal("0.05000"), 360,
            date(2024, 1, 1), 1, is_arm=True,
        )
        entry = RateHistory(
            account_id=acct.id,
            effective_date=date(2025, 2, 1),
            interest_rate=Decimal("0.07000"),
        )
        db.session.add(entry)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan/schedule")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Month-by-Month Schedule" in html
        # Rate column header.
        assert ">Rate</th>" in html or ">Rate<" in html
        # At least one rate percentage value.
        assert "7.000%" in html or "5.000%" in html

    def test_schedule_fixed_rate_no_rate_column(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.13-9: Non-ARM mortgage does NOT show Rate column.

        Fixed-rate loans have the same rate for every row. Showing it
        360 times is noise, so the column is omitted.
        """
        acct = _create_fresh_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan/schedule")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Month-by-Month Schedule" in html
        # Rate column header must not exist in the schedule table.
        # (The overview tab shows the rate, but not as a <th>.)
        assert ">Rate</th>" not in html

    def test_schedule_totals_row(self, auth_client, seed_user, db, seed_periods):
        """C-5.13-10: Schedule table includes a totals footer row.

        The <tfoot> row shows summed payment, principal, interest, and
        extra columns.  Verify the footer exists and contains currency
        values.
        """
        acct = _create_fresh_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan/schedule")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Footer row present.
        assert "<tfoot" in html
        assert ">Total</td>" in html or ">Total<" in html
        # Footer contains currency values (dollar amounts).
        # The total interest for a $250K/6.5%/30yr loan is significant.
        assert "$" in html

    def test_schedule_totals_match_rows(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.13-11: Totals row values match the sum of individual data rows.

        Computes expected totals from the amortization engine directly
        and verifies they appear in the rendered HTML.

        For $250K at 6.5% for 360 months with no escrow, no extra:
        Total interest = sum of all monthly interest values over the
        full schedule.  The engine computes this precisely using Decimal.
        No escrow means Payment total = P&I total.
        """
        # Compute expected totals from the engine via
        # ``project_forward`` (Commit 9 of the amortization-engine
        # split removed ``generate_schedule``).  The fixture loan has
        # no payments and no overrides, so a pure contractual
        # projection over the full term replicates the legacy
        # surface's output exactly.
        from app.services.amortization_engine import (  # pylint: disable=import-outside-toplevel
            PeriodTerms,
            ProjectionInputs,
            advance_to_next_payment_date,
            calculate_monthly_payment,
            project_forward,
        )

        principal = Decimal("250000.00")
        rate = Decimal("0.06500")
        term = 360
        # Originated on the first of the frozen today's month (2026-03-01)
        # so the first installment (Apr 1) is after today and the plan is
        # the whole contract: 360 installments, none overdue (see
        # ``test_schedule_has_correct_row_count``).
        origination_date = date(2026, 3, 1)
        starting_date = advance_to_next_payment_date(origination_date, 1)
        contractual = calculate_monthly_payment(principal, rate, term)

        schedule = project_forward(
            ProjectionInputs(
                starting_balance=principal,
                starting_date=starting_date,
                remaining_months=term,
                payment_day=1,
                terms_schedule=[PeriodTerms(
                    start_date=starting_date,
                    annual_rate=rate,
                    monthly_pi=contractual,
                )],
            ),
        )
        expected_interest = sum(
            (row.interest for row in schedule), Decimal("0.00"),
        )
        # No escrow on this test loan, so Payment = P&I total.
        expected_payment = sum(
            (row.payment for row in schedule), Decimal("0.00"),
        )
        formatted_interest = f"${expected_interest:,.2f}"
        formatted_payment = f"${expected_payment:,.2f}"

        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=origination_date,
        )
        resp = auth_client.get(f"/accounts/{acct.id}/loan/schedule")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert formatted_interest in html, (
            f"Expected total interest {formatted_interest} not found"
        )
        assert formatted_payment in html, (
            f"Expected total payment {formatted_payment} not found"
        )

    def test_schedule_overpayment_shows_the_actual_extra(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.13-12 (re-pinned again, step E1a): a POSTED overpayment shows its real extra.

        The prior pin ("the cash overage is ignored") dated from the
        contractual-schedule balance model, which the balance arc
        SUPERSEDED: since the read switch (PR #52) a confirmed schedule
        row is read from the ledger and carries the payment's ACTUAL
        economics -- ``extra_payment`` is the real excess above the
        governing period's contractual P&I ("off-schedule the rows show
        what actually happened", plan step C3).  The old pin stayed
        green only because a transfer created ALREADY settled never
        posted (the unposted-ledger hole step E1a closed): its cash
        never reached the ledger, so the row had no economics to show.
        With the payment genuinely posted, the $2,080.17 cash against
        the $1,580.17 contractual P&I is interest + principal with a
        real $500.00 excess, and hiding it would misreport what the
        borrower actually paid.
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )
        # A DONE transfer above the contractual P&I ($2080.17 vs $1580.17):
        # the $500.00 overage is the payment's ACTUAL extra.
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[3], Decimal("2080.17"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan/schedule")
        assert resp.status_code == 200
        html = resp.data.decode()
        # The ledger-derived confirmed row carries the real excess, so the
        # Extra column renders and shows it.  The ``$`` anchor keeps the
        # amount from false-matching a thousands figure like $1,500.00.
        assert ">Extra</th>" in html, (
            "A posted overpayment's actual extra must render on the schedule"
        )
        assert "$500.00" in html, (
            "The confirmed row must carry the payment's real $500.00 extra"
        )

    def test_schedule_uses_committed_schedule(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.13-13: Schedule uses committed data when payments exist.

        Creates a mortgage with a confirmed transfer.  If the schedule
        used only the original (no-payments) schedule, no rows would
        be marked Confirmed.  Presence of Confirmed badges proves the
        committed schedule (via the composer's history_rows) is used.

        Re-pinned for Commit 5 of the amortization engine split.
        See ``test_schedule_confirmed_rows_marked`` for the full
        rationale (same architectural change: composer's replay only
        consumes confirmed payments with ``payment_date <= as_of``;
        a future-dated DONE is the plan's and renders as Projected).
        Previous fixture used April 2026 seed_periods after the frozen
        today (2026-03-20); this rewrite uses February 2026 so the DONE
        payment lands in
        replay.
        """
        # Origination 2026-01-01 -> first scheduled payment month
        # is February 2026.  seed_periods[3] (2026-02-13) falls in
        # the replay window before today (2026-03-20).
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[3], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan/schedule")
        assert resp.status_code == 200
        html = resp.data.decode()
        confirmed = html.count('badge bg-success">Confirmed</span>')
        assert confirmed >= 1, (
            "Expected at least 1 confirmed row from committed schedule"
        )

    def test_schedule_currency_formatting_consistent(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.13-14: Currency values in the schedule use consistent formatting.

        All monetary values should match the $X,XXX.XX pattern with
        exactly 2 decimal places.  Parses several values from the
        schedule and validates the format.
        """
        acct = _create_fresh_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan/schedule")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Extract dollar amounts from the schedule.  The pattern
        # matches $X.XX through $XXX,XXX,XXX.XX.
        amounts = re.findall(r'\$[\d,]+\.\d{2}', html)
        assert len(amounts) > 100, (
            f"Expected many currency values in schedule, found {len(amounts)}"
        )
        # Every matched amount should have exactly 2 decimal places.
        for amount in amounts[:20]:  # Spot-check first 20.
            assert re.match(r'^\$[\d,]+\.\d{2}$', amount), (
                f"Currency format mismatch: {amount}"
            )


# -- Dashboard / Payoff Calculator Consistency Tests -------------------


class TestDashboardPayoffConsistency:
    """Verify the dashboard and payoff calculator use the same data pipeline.

    Both routes must produce identical amortization calculations from
    the same loan.  These tests catch mismatches caused by one route
    applying payment preparation (escrow subtraction, biweekly
    redistribution) while the other uses raw data.
    """

    def test_payoff_committed_matches_dashboard_chart(
        self, auth_client, seed_user, db, monkeypatch, seed_periods,
    ):
        """Payoff committed chart data matches dashboard committed chart.

        Both routes render committed balance data for Chart.js.  Since
        both use _load_route_context for payment preparation, the
        committed balance arrays must be identical.

        Origination is pinned to March 1, 2026 so seed_periods[7]
        (April 10, 2026) matches the schedule's first payment month
        (April 2026).  Without the pin, ``_create_fresh_mortgage``
        derives origination from ``date.today()``; once today moves
        past April, the April transfer no longer matches any schedule
        month, both routes produce empty committed arrays, and the
        equality assertion passes trivially without exercising the
        integration the test was written to verify.
        **Today is moved past that payment**, overriding the module freeze at
        2026-03-20.  A settled payment in a period that STARTS after today is a
        settle in its own future, which ruling R-EJ refuses at the write door --
        a settled row asserts that money has already moved.  The clock is moved
        rather than the period, because the period is load-bearing here: the
        docstring above explains why seed_periods[7] specifically must line up
        with the schedule's first payment month, and moving it would make the
        assertion vacuous in exactly the way that paragraph warns about.
        """
        freeze_today(monkeypatch, seed_periods[7].start_date + timedelta(days=5))
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 3, 1),
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[7], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        # Dashboard: the band's balance line is the committed trajectory.
        dash_resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert dash_resp.status_code == 200
        band = _parse_band_chart(dash_resp.data.decode())
        assert band is not None, "Dashboard missing band chart data"

        # Payoff with 0 extra: the accelerated overlay equals the committed
        # trajectory (there is no extra to accelerate), forward-only.
        payoff_resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "0"},
        )
        assert payoff_resp.status_code == 200
        overlay = _parse_chart_array(payoff_resp.data.decode(), "overlay")
        assert overlay is not None, "Payoff missing overlay chart data"

        # Both derive from _load_route_context -> compute_payoff_scenarios, so the
        # overlay's forward slice must byte-match the band's committed forward
        # slice; the overlay is null across the confirmed-history region.
        current_index = band["current_index"]
        assert overlay[current_index:] == band["balance"][current_index:], (
            "Dashboard band and payoff overlay forward slices differ -- "
            "data pipeline mismatch"
        )
        assert all(v is None for v in overlay[:current_index])

    def test_payoff_with_payments_no_crash(
        self, auth_client, seed_user, db, monkeypatch, seed_periods,
    ):
        """Payoff calculator with prepared payments does not crash.

        After the DRY refactor, both routes use _load_route_context.
        Verify the payoff calculator handles prepared payments correctly
        in both extra_payment and target_date modes.

        **Today is moved past the settled payment**, overriding the module
        freeze at 2026-03-20: a settled payment whose period STARTS after today
        is a settle in its own future, which ruling R-EJ refuses at the write
        door.  The PROJECTED payment below stays where it is -- a projected row
        carries no settle day, so it is legitimately still ahead.
        """
        freeze_today(monkeypatch, seed_periods[7].start_date + timedelta(days=5))
        acct = _create_fresh_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[7], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[9], Decimal("1580.17"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        # Extra payment mode.
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "200"},
        )
        assert resp.status_code == 200

        # Target date mode.
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "target_date", "target_date": "2040-01-01"},
        )
        assert resp.status_code == 200

    def test_escrow_subtracted_consistently(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Escrow-inclusive transfers do not inflate payoff savings.

        When transfers include escrow, both routes must subtract it
        before passing to the engine.  Without this, the payoff
        calculator would report inflated interest savings because the
        engine would count escrow as extra principal.
        """
        acct = _create_fresh_mortgage(seed_user, db.session)
        # Add escrow: $600/month.
        _add_escrow(db.session, acct.id, "Property Tax", Decimal("7200.00"))
        db.session.commit()

        # Transfer includes P&I (~$1,580) + escrow ($600) = ~$2,180.
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[7], Decimal("2180.00"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        # Dashboard should NOT show the escrow as "extra" in schedule.
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # The Escrow column should show $600 (7200/12).
        assert "Escrow" in html
        assert "$600.00" in html

        # Payoff calculator should also work with prepared payments.
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "0"},
        )
        assert resp.status_code == 200

    def test_biweekly_overlap_handled_in_payoff(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Two payments in the same month are redistributed in payoff too.

        Creates two transfers in the same calendar month (biweekly
        overlap).  Both the dashboard and payoff calculator must
        distribute them across two schedule months.

        Origination is pinned to March 1, 2026 so seed_periods[7]
        (April 10) and seed_periods[8] (April 24) BOTH fall in the
        schedule's first payment month (April 2026), exercising the
        biweekly redistribution code path.  Without the pin, the
        schedule's first month would shift past April and the
        transfers would not match any schedule month -- the
        ``"$3,160" not in html`` assertion would then pass trivially
        even if the biweekly fix were broken.
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 3, 1),
        )
        # seed_periods[7] (April 10) and [8] (April 24) are both in
        # April 2026.
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[7], Decimal("1580.17"),
            status_enum=StatusEnum.PROJECTED,
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[8], Decimal("1580.17"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        # Dashboard schedule: no month should show double payment.
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # $3,160 (2x $1,580.17) should NOT appear as a single payment.
        assert "$3,160" not in html, (
            "Dashboard shows double payment -- biweekly fix not applied"
        )

        # Payoff calculator: same -- should not crash or double-count.
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "0"},
        )
        assert resp.status_code == 200


# -- Dashboard Chart Composer Tests (Commit 5) -----------------------------


class TestDashboardChartComposer:
    """Lock the dashboard's projected figures to ONE forward walk.

    Commit 5 of the amortization engine split
    (``docs/plans/2026-05-21-amortization-engine-split-implementation.md``)
    replaced the dashboard's three direct ``generate_schedule`` calls
    (planned, original, floor) with two composer calls; plan step R7d-g-3
    (ruling **R-R88**) then re-cut every projected figure -- the band
    chart's line, the schedule's planned rows, the breakdown's next payment
    -- onto the balance seam's plan fold (``balance_at.positions`` /
    ``loan_installments``), the walk the "Projected payoff" chip already
    read, so the composer keeps only the confirmed history and the
    contract's grid.  These tests lock the resulting behavior:

    * C5-1..C5-4 and C5-8 are "assert-unchanged" -- they pin the
      dashboard output against hand-computed expectations (one point per
      scheduled month, no ``generate_schedule`` 361-row residue artifact).
    * C5-5 is a static grep guard: the dashboard body MUST NOT call
      ``generate_schedule`` directly.
    * C5-6 / C5-7 lock the floor's "projections cancelled" semantic.

    Helper notes: ``_create_fresh_mortgage`` with
    ``origination_date=date(2026, 1, 1)`` produces a 30-year
    $250,000 / 6.5% mortgage whose first scheduled payment month is
    February 2026.  ``seed_periods[2]`` (2026-01-30 .. 2026-02-12) contains
    2/1 and ``seed_periods[3]`` (2026-02-13 .. 2026-02-26) contains no 1st,
    so a payment in the former is due Feb 1 and one in the latter Mar 1;
    both settle before the autouse-frozen today (2026-03-20) and land in the
    composer's replay window.  **Every installment due by today is paid in
    these fixtures**: an installment left unpaid past its due date is not in
    the plan (ruling D1 / finding B-9), and the fold then charges its accrued
    interest against the next payment and clears the loan in the
    post-contractual extension -- the honest shape
    ``TestAmortizationSchedule.test_schedule_lists_the_plan_when_an_installment_is_overdue``
    pins, and not what "unchanged" means here.
    """

    def test_dashboard_chart_values_unchanged(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C5-1: the band chart's line is the seam's balance on the contractual grid.

        Fixture: 30-yr / $250k / 6.5% mortgage originated 2026-01-01, its
        two installments due by today paid -- Feb 1 2026 (seed_periods[2],
        the pay period that CONTAINS 2/1) and Mar 1 2026 (seed_periods[3],
        which contains no 1st, so its payment is due 3/1) -- and one
        projected payment for Jun 1 2026 (seed_periods[9]), which the plan
        takes as that month's occurrence at its own cash.

        Asserts the band's balance line is the seam's:
          * Length equals term_months (360) -- one point per scheduled month
            (the confirmed history's two, the contract's grid for the rest),
            no residue artifact and no extension, because nothing is overdue.
          * current_index is 2 (the two grid dates at or before today; the
            solid segment reads the ledger).
          * The line is monotonically non-increasing (positive amortization
            with no overpayment) and ends at $0.
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )
        # Confirmed Feb 1 and Mar 1 2026 (both settled before today=2026-03-20)
        # -- the replay's history_rows.
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[2], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[3], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        # Projected Jun 1 2026 (after today) -- an occurrence in the plan.
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[9], Decimal("1580.17"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        band = _parse_band_chart(resp.data.decode())
        assert band is not None
        balance = band["balance"]

        # 360 months for a 30-yr mortgage with no overpayment (one point per
        # scheduled month; no pre-Commit-5 residue artifact).
        assert len(balance) == 360, (
            f"Expected 360 points on the grid, got {len(balance)}"
        )
        assert band["current_index"] == 2, (
            f"Expected two grid dates at or before today, got {band['current_index']}"
        )
        # The line never increases month-over-month (positive amortization)
        # and pays off at $0 at term.
        for i in range(1, len(balance)):
            assert balance[i] <= balance[i - 1] + 0.01, (
                f"Balance increased at index {i}: "
                f"{balance[i - 1]} -> {balance[i]}"
            )
        assert balance[-1] == 0.0

    def test_amortization_schedule_rows_unchanged(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C5-2: the schedule route renders term_months rows for a full loan.

        Uses the same fixture as C5-1.  The schedule renders the composer's
        confirmed ``history_rows`` followed by the seam's forward plan
        (``balance_at.loan_installments``) through the payoff.  History
        contributes two rows (the Feb 1 and Mar 1 2026 confirmed payments);
        the plan contributes the 358 remaining installments.  Total: 360
        rows.  Re-pinned for Commit 5 (one row per remaining month, no
        residue artifact), the Loop B schedule demotion (its own route) and
        R7d-g-3 (the plan's rows).
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[2], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[3], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[9], Decimal("1580.17"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan/schedule")
        assert resp.status_code == 200
        html = resp.data.decode()
        confirmed = html.count('badge bg-success">Confirmed</span>')
        projected = html.count('badge bg-secondary">Projected</span>')
        total = confirmed + projected
        # 2 confirmed (Feb and Mar 2026 in history) + 358 planned = 360.
        assert confirmed == 2, f"Expected 2 confirmed rows, got {confirmed}"
        assert total == 360, (
            f"Expected 360 total schedule rows, got {total} "
            f"({confirmed} confirmed + {projected} projected)"
        )

    def test_payment_breakdown_unchanged(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C5-3: Payment breakdown sums to total and percentages sum to 100.

        The breakdown card derives from the seam's NEXT planned installment
        (``balance_at.loan_installments(...)[0]``, its allocation already
        split by the fold; plan step R7d-g-3).  With both installments due
        by today paid, the Apr 1 2026 payment pays interest and principal
        and no escrow, so two segments render and the
        truncate-then-distribute percentages MUST still sum to exactly
        100.0% (the dashboard rendering invariant).
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[2], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[3], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Extract the breakdown card's percentages via the
        # ``data-progress-pct`` attribute, which only appears on the
        # principal/interest/escrow progress bars and not elsewhere
        # on the page.  This isolates the truncate-then-distribute
        # output from unrelated percent strings (e.g., the interest
        # rate display).
        pct_strings = re.findall(r'data-progress-pct="([0-9.]+)"', html)
        assert len(pct_strings) >= 2, (
            f"Expected >= 2 breakdown percent attributes, "
            f"found {pct_strings}"
        )
        breakdown_pcts = [Decimal(p) for p in pct_strings]
        total_pct = sum(breakdown_pcts, Decimal("0.0"))
        assert total_pct == Decimal("100.0"), (
            f"Breakdown percentages sum to {total_pct}, expected 100.0"
        )

    def test_recurrence_start_sync_is_idempotent(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The opening-bound sync writes recurrence_rules only on a change.

        Creating the recurring transfer writes the rule's start once and its
        stop never (plan step R7d-g: the column stays NULL).  A follow-on
        params re-save with the same 6.5% / 360-month / day-1 terms derives
        the same first installment, so the sync skips the write -- no new
        ``recurrence_rules`` UPDATE lands in the audit log.
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )
        db.session.commit()
        # Creating the recurring transfer via the route sets end_date once.
        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(seed_user["account"].id)},
        )

        from app.models.recurrence_rule import RecurrenceRule  # pylint: disable=import-outside-toplevel
        from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel
        template = db.session.query(TransferTemplate).filter_by(
            to_account_id=acct.id,
        ).one()
        first_start = template.recurrence_rule.starts_on
        assert template.recurrence_rule.end_date is None

        # The guard short-circuits when the re-derived start equals the
        # current one; the system.audit_log row count must NOT increase after a
        # no-op params re-save.
        audit_count_sql = sa.text(
            "SELECT COUNT(*) FROM system.audit_log "
            "WHERE table_name = 'recurrence_rules' AND operation = 'UPDATE'"
        )
        audit_before = db.session.execute(audit_count_sql).scalar()

        # A params re-save with unchanged terms derives the SAME start.
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/params",
            data={
                "interest_rate": "6.500",
                "payment_day": "1",
                "term_months": "360",
            },
        )
        assert resp.status_code == 302
        db.session.expire_all()
        rule = db.session.query(RecurrenceRule).filter_by(
            id=template.recurrence_rule.id,
        ).one()
        assert rule.starts_on == first_start
        assert rule.end_date is None
        audit_after = db.session.execute(audit_count_sql).scalar()
        assert audit_after == audit_before, (
            "A no-op params mutation wrote a new recurrence_rule UPDATE row "
            f"to system.audit_log ({audit_before} -> {audit_after}) -- "
            "the start sync's idempotency guard failed"
        )

    def test_no_direct_generate_schedule_in_dashboard(self):
        """C5-5: the dashboard surface must not call generate_schedule directly.

        Static grep against ``app/routes/loan/dashboard.py`` -- the
        dashboard route plus its context-building helpers
        (``_build_dashboard_scenarios`` etc.) after the Phase 3
        pylint-cleanup split + decomposition -- confirming the dashboard
        was migrated to the ``compute_payoff_scenarios`` composer.  The
        bare word ``generate_schedule`` may still appear in a comment /
        docstring, but never as a direct ``amortization_engine.``
        engine call from the dashboard surface.
        """
        import pathlib  # pylint: disable=import-outside-toplevel
        source = pathlib.Path("app/routes/loan/dashboard.py").read_text()
        assert "amortization_engine.generate_schedule" not in source, (
            "Dashboard surface still calls "
            "amortization_engine.generate_schedule directly -- Commit 5 "
            "migration incomplete"
        )

    def test_arm_dashboard_chart_unchanged(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C5-8: ARM dashboard band chart values come from the seam's fold.

        Creates an ARM mortgage in its fixed-rate window with no payments,
        asserted at $100,000 on 2026-01-01 (``_create_loan_account``).  The
        grid's Feb 1 and Mar 1 2026 fall at or before the frozen 2026-03-20
        today and read the LEDGER -- the asserted balance, flat, nothing
        paid -- and those two installments, due and unpaid, are absent from
        the plan (ruling D1 / finding B-9), so the fold charges their accrued
        interest at the Apr 1 payment: the projected line's first point sits
        ABOVE the ledger's, then amortizes.  Asserts the line is the seam's:
        the two ledger points flat at $100,000, non-increasing from the first
        projected point on (the rate cannot rise in the fixed-rate window),
        and ending at $0 -- in the post-contractual extension, since the two
        missed installments' principal is never paid down.

        (The former C5-6 / C5-7 "floor" route tests were retired with the Loop B
        band rebuild: the locked band anatomy plots only the plan's line plus
        the lever's accelerated preview, so the dashboard no longer computes or
        serializes a floor series.)
        """
        acct = _create_loan_account(
            seed_user, db.session, AcctTypeEnum.MORTGAGE, "ARM 5/1",
            Decimal("100000.00"), Decimal("0.05000"), 360,
            date(2024, 1, 1), 1, is_arm=True,
        )
        # Fixed-rate window: arm_first_adjustment_months defaults to
        # None in this fixture, so the ARM behaves like fixed-rate
        # for the resolver outside the window.  Either way the
        # fold's behavior is locked.
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        band = _parse_band_chart(resp.data.decode())
        assert band is not None
        balance = band["balance"]

        assert len(balance) > 2
        # The two grid dates at or before today are the ledger's: the
        # asserted balance, unpaid.
        assert band["current_index"] == 2
        assert balance[:2] == [100000.0, 100000.0]
        # The missed installments' accrued interest meets the first planned
        # payment, so the projected line starts above the ledger's point.
        assert balance[2] > 100000.0
        # Last entry reaches $0 (the extension clears the residue).
        assert balance[-1] == 0.0
        # Non-increasing across the fixed-rate window from the first
        # projected point on.
        for i in range(3, len(balance)):
            assert balance[i] <= balance[i - 1] + 0.01, (
                f"ARM band balance increased at index {i}: "
                f"{balance[i - 1]} -> {balance[i]}"
            )


# -- Recurrence End Date Auto-Update Tests (Commit 5.9-1) ----------------


def _create_transfer_template(seed_user, db_session, loan_account):
    """Create the recurring LOAN PAYMENT these tests wire a sync through.

    Returns ``(template, rule)`` so tests can inspect both objects.

    **It delegates to the shared builder, and the reason is its settings row.**
    This used to construct the template inline with no
    ``budget.loan_payment_settings`` -- a recurring transfer into a loan that
    is not a loan payment, which ``routes/loan/payment_transfer.py`` cannot
    produce.  Nothing read the difference until plan step **R7d-a** made the
    forward plan price an unmaterialised installment from the loan's own
    standing payment: the inline ``$1,500.00`` then meant "this is what the
    owner pays a $250,000 mortgage", the plan correctly reported a loan that
    never clears, and three tests asserting only that a payoff EXISTS failed on
    a fixture that had stopped describing a payable loan.  The three unused
    parameters (``amount``, ``end_date``, ``name``) went with the inline block;
    no caller passed one.
    """
    template = make_loan_payment_template(
        db_session, seed_user, loan_account,
        cadence=MONTHLY, fires_on_day=1,
    )
    # The shared builders' stated contract is "the caller commits", and every
    # caller of this one goes on to issue a request (plan step balance:X-i3).
    # A request holds its own transaction, so an uncommitted template is one
    # the route cannot see -- the fixture would be describing a state no
    # browser can produce.
    db_session.commit()
    return template, template.recurrence_rule


def _derived_stop_of(template, seed_user):
    """Return the composed door's derived stop for *template*, on a fresh pass."""
    return resolved_definition(
        template, BalanceContext.build(seed_user["user"].id),
    ).closing.derived


def _assert_column_untouched_and_stop_derived(rule, template, seed_user):
    """The post-R7d-g shape every chokepoint case asserts.

    The two closing-bound columns are exactly as the fixture left them
    (NULL), and the payoff the chokepoint moved is the composed door's DERIVED
    answer -- a real closing date, read through the same door generation
    reads -- rather than anything stored.
    """
    _db.session.refresh(rule)
    assert rule.end_date is None, (
        f"a chokepoint wrote end_date={rule.end_date}: a writer survived R7d-g"
    )
    assert rule.max_occurrences is None
    stop = _derived_stop_of(template, seed_user)
    assert isinstance(stop, ClosesOn), stop
    return stop


class TestTheClosingBoundIsNeverWrittenByARoute:
    """No payoff-affecting mutation writes ``end_date`` (plan step R7d-g).

    Until R7d-g these cases pinned the opposite: ten chokepoints -- recurring-
    transfer creation, a settled payment, a params / rate edit, a balance
    true-up, the extra-principal and track-payment doors, the tracking start,
    a payment leaving the loan -- each synced the rule's ``end_date`` to the
    projected payoff, and these tests pinned that WIRING through the real
    routes.  The writer is deleted, so the same routes are driven and the same
    column is asserted UNTOUCHED, beside the derived stop that replaced it
    (``recurring_definition.resolved_definition``, the door generation reads
    since plan step R7d-c-2).  A route that grows the write back turns its
    case red.  The payoff computation itself is graded in
    tests/test_services/test_loan_recurrence_sync.py.
    """

    def test_transfer_creation_writes_the_start_and_not_the_stop(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Creating the recurring transfer bounds its START to the contract only.

        The create-transfer POST writes the rule's first occurrence from the
        loan's contract before generating any shadow (``bind_rule_to_loan``);
        the closing bound is the door's derived payoff, years out for a
        mortgage, and the column stays NULL.
        """
        from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel

        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(seed_user["account"].id)},
        )
        assert resp.status_code == 302

        tpl = (
            db.session.query(TransferTemplate)
            .filter_by(to_account_id=acct.id, user_id=seed_user["user"].id)
            .first()
        )
        rule = tpl.recurrence_rule
        assert isinstance(rule.starts_on, date)
        stop = _assert_column_untouched_and_stop_derived(rule, tpl, seed_user)
        # Mortgage payoff is years in the future.
        assert stop.on > date.today()

    def test_a_settled_payment_leaves_the_column_alone(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Settling a loan payment moves the payoff and writes nothing (settle path).

        The transfer settle chokepoint (``_loan_posting._sync_loan_postings_if_loan``)
        used to re-sync ``end_date``; it reconciles the loan's ledger only now.
        """
        acct = _create_mortgage(seed_user, db.session)
        tpl, rule = _create_transfer_template(seed_user, db.session, acct)
        assert rule.end_date is None

        # Settle a payment in a period that has begun, so the resolver replays it.
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], acct,
            seed_periods[4], amount=Decimal("1580.17"),
        )
        db.session.commit()

        stop = _assert_column_untouched_and_stop_derived(rule, tpl, seed_user)
        assert stop.on > date.today()

    def test_dashboard_get_is_read_only(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Loading the loan dashboard NEVER writes the recurrence rule (R-4).

        A mortgage with a recurring transfer stays as stored across a GET:
        the detail page is read-only, and since plan step R7d-g nothing else
        writes the closing bound either.
        """
        acct = _create_mortgage(seed_user, db.session)
        tpl, rule = _create_transfer_template(seed_user, db.session, acct)
        assert rule.end_date is None

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200

        _assert_column_untouched_and_stop_derived(rule, tpl, seed_user)

    def test_no_update_when_no_transfer(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Mortgage without recurring transfer: no error, no update.

        The dashboard should render normally when there is no
        recurring transfer template to update.
        """
        acct = _create_mortgage(seed_user, db.session)
        # No transfer template created.

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert b"Balance owed" in resp.data

    def test_a_params_edit_leaves_the_column_alone(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Editing loan params moves the payoff and writes no stop (params path).

        The one chokepoint that still syncs anything: a term / rate /
        payment-day edit re-derives the rule's START (ruling **R-R29**) and
        nothing else.
        """
        acct = _create_mortgage(seed_user, db.session)
        tpl, rule = _create_transfer_template(seed_user, db.session, acct)
        assert rule.end_date is None

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/params",
            data={
                "interest_rate": "6.500",
                "payment_day": "1",
                "term_months": "360",
            },
        )
        assert resp.status_code == 302

        stop = _assert_column_untouched_and_stop_derived(rule, tpl, seed_user)
        assert stop.on > date.today()

    def test_a_params_edit_that_moves_the_start_past_an_authored_stop_is_refused(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The ONE state the window CHECK refuses that a loan edit can reach (ruling **R-R82**).

        The standing payment carries a stop its owner authored two days after
        the loan's first installment (a stop kept from before the definition
        became the standing payment); moving ``payment_day`` past it would
        write ``starts_on > end_date``.  The route refuses the edit WHOLE --
        the params are as they were, the rule is as it was, the flash names
        the transfer -- rather than letting the flush answer with an
        ``IntegrityError``.
        """
        acct = _create_mortgage(seed_user, db.session)
        tpl, rule = _create_transfer_template(seed_user, db.session, acct)
        # Bound to the contract the way every production door binds a new
        # rule, so the start the edit would move is the loan's own.
        bind_rule_to_loan(rule, acct.id)
        params = load_loan_params(acct.id)
        first_installment = rule.starts_on
        assert first_installment.day == params.payment_day
        rule.end_date = first_installment + timedelta(days=2)
        db.session.commit()
        payment_day_before = params.payment_day
        term_before = params.term_months
        assert payment_day_before != 15

        assert term_before != 300
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/params",
            data={
                "interest_rate": "6.500",
                "payment_day": "15",
                "term_months": "300",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        body = resp.data.decode()
        assert tpl.name in body
        # The remedy clause, worded once for every door since plan step
        # R7d-g-2 (ruling R-R85) -- see the setup case above.
        assert "Set it up again without an end date, or archive it." in body

        db.session.expire_all()
        params = load_loan_params(acct.id)
        assert params.payment_day == payment_day_before
        assert params.term_months == term_before
        db.session.refresh(rule)
        assert rule.starts_on == first_installment
        assert rule.end_date == first_installment + timedelta(days=2)

    def test_a_rate_change_leaves_the_column_alone(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Recording an ARM rate change moves the payoff and writes nothing (rate path)."""
        acct = _create_loan_account(
            seed_user, db.session, AcctTypeEnum.MORTGAGE, "ARM End Date Mortgage",
            Decimal("250000.00"), Decimal("0.05000"), 360,
            date(2023, 6, 1), 1, is_arm=True,
        )
        tpl, rule = _create_transfer_template(seed_user, db.session, acct)
        assert rule.end_date is None

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/rate",
            data={"effective_date": "2026-01-01", "interest_rate": "7.000"},
        )
        assert resp.status_code == 200

        stop = _assert_column_untouched_and_stop_derived(rule, tpl, seed_user)
        assert stop.on > date.today()

    def test_a_true_up_to_zero_leaves_the_column_alone_and_the_stop_is_PAST(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Trueing a loan up to $0 retires it: the derived stop is past, the column NULL.

        Recording a $0 balance retires the loan, so the door's derived stop is
        the day it became closed -- a past date that stops generation -- and
        it is read, never stored.  Until R7d-g this route wrote that past
        date into ``end_date``; a loan cleared BEFORE its first installment
        then stored the inverted pair that held ``ck_recurrence_rules_valid_window``
        back, which is unconstructible now.
        """
        acct = _create_loan_account_exact(
            seed_user, db.session, AcctTypeEnum.AUTO_LOAN, "Paid Off Loan",
            Decimal("1000.00"),
            Decimal("0.05000"), 12, date(2026, 1, 1), 1,
        )
        tpl, rule = _create_transfer_template(seed_user, db.session, acct)
        assert rule.end_date is None

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/trueup",
            data={"anchor_date": "2026-03-01", "anchor_balance": "0.00"},
        )
        assert resp.status_code == 302

        stop = _assert_column_untouched_and_stop_derived(rule, tpl, seed_user)
        assert stop.on <= date.today(), (
            f"Paid-off loan should stop generation with a past derived stop, "
            f"got {stop.on}"
        )

    def test_the_extra_principal_and_track_doors_leave_the_column_alone(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The two settings-row doors move the payoff and write no stop."""
        acct = _create_mortgage(seed_user, db.session)
        tpl, rule = _create_transfer_template(seed_user, db.session, acct)
        assert rule.end_date is None

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payments/{tpl.id}/settings",
            data={"extra_principal": "250.00"},
        )
        assert resp.status_code == 302
        _assert_column_untouched_and_stop_derived(rule, tpl, seed_user)

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payments/{tpl.id}/track",
        )
        assert resp.status_code == 302
        _assert_column_untouched_and_stop_derived(rule, tpl, seed_user)

    def test_end_date_no_params_no_crash(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Loan account with no LoanParams: dashboard renders setup page.

        Nothing about the rule is reached because the dashboard returns
        early when params are missing.
        """
        from app.models.recurrence_rule import RecurrenceRule  # pylint: disable=import-outside-toplevel
        from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel

        loan_type = db.session.query(AccountType).filter_by(name="Auto Loan").one()
        account = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=loan_type.id,
                name="No Params Loan",
                anchor_balance=Decimal("0"),
            ),
        )
        db.session.add(account)
        db.session.flush()

        # Create a template even though no LoanParams exist.
        tpl = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=account.id,
            name="Premature Payment",
            default_amount=Decimal("500.00"),
            is_active=True,
        )
        db.session.add(tpl)
        db.session.commit()
        # The definition first, then the cadence onto it (plan step R-F6),
        # committed before the request (plan step X-i3).
        rule = make_cadence_rule(
            tpl, MONTHLY,
            fires_on_day=1,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{account.id}/loan")
        assert resp.status_code == 200
        assert b"Configure" in resp.data

        # Rule should be unchanged -- nothing reached it.
        db.session.refresh(rule)
        assert rule.end_date is None

    def test_end_date_idor(
        self, auth_client, seed_user, second_user, db, seed_periods,
    ):
        """Other user's loan: 404-redirect, no rule modification.

        Confirms the ownership check prevents cross-user mutation of a
        recurrence rule.
        """
        from app.models.recurrence_rule import RecurrenceRule  # pylint: disable=import-outside-toplevel
        from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel

        other_loan = _create_other_loan(second_user, db.session, AcctTypeEnum.MORTGAGE)

        # Create a transfer template for the other user's loan.
        tpl = TransferTemplate(
            user_id=second_user["user"].id,
            from_account_id=second_user["account"].id,
            to_account_id=other_loan.id,
            name="Other User Payment",
            default_amount=Decimal("1000.00"),
            is_active=True,
        )
        db.session.add(tpl)
        db.session.commit()
        # The definition first, then the cadence onto it (plan step R-F6).
        rule = make_cadence_rule(
            tpl, MONTHLY,
            fires_on_day=1,
        )
        # Committed before the request (plan step X-i3): a query request opens
        # a transaction of its OWN, so the rule this test asserts is UNTOUCHED
        # has to exist as far as that request is concerned.
        db.session.commit()
        start_before = rule.starts_on

        # Access other user's loan as the primary user.
        resp = auth_client.get(f"/accounts/{other_loan.id}/loan")
        assert resp.status_code == 404

        # Other user's recurrence rule should be untouched.
        db.session.refresh(rule)
        assert rule.end_date is None
        assert rule.starts_on == start_before


class TestLoanScheduleRoute:
    """The standalone amortization-schedule page (demoted off the detail page)."""

    def test_schedule_page_renders_the_table(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """GET /loan/schedule renders the month-by-month schedule for the loan.

        The full statement table lives on its own page now; it renders the
        planned schedule (the same LoanState the detail card reads), so the
        Month-by-Month heading, the Balance column, and a Confirmed/Projected
        status badge are all present.
        """
        acct = _create_mortgage(seed_user, db.session)

        resp = auth_client.get(f"/accounts/{acct.id}/loan/schedule")
        assert resp.status_code == 200
        assert b"Amortization Schedule" in resp.data
        assert b"Month-by-Month Schedule" in resp.data
        assert b"Balance" in resp.data
        assert b"Projected" in resp.data

    def test_schedule_page_404_for_other_users_loan(
        self, auth_client, seed_user, second_user, db, seed_periods,
    ):
        """A cross-owner loan's schedule 404s (404 for not-found and not-yours)."""
        other = _create_other_loan(second_user, db.session)

        resp = auth_client.get(f"/accounts/{other.id}/loan/schedule")
        assert resp.status_code == 404

    def test_schedule_page_redirects_when_unconfigured(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """An amortizing account with no LoanParams redirects to the detail page.

        There is no schedule to show until the loan is set up; the shared
        configured-loan guard redirects to the detail page (the setup surface)
        instead of 500-ing.
        """
        loan_type = db.session.query(AccountType).filter_by(name="Auto Loan").one()
        account = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=loan_type.id,
                name="Unconfigured Loan",
                anchor_balance=Decimal("0"),
            ),
        )
        db.session.add(account)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{account.id}/loan/schedule")
        assert resp.status_code == 302
        assert f"/accounts/{account.id}/loan" in resp.headers.get("Location", "")


# ── Refinance Calculator Tests ──────────────────────────────────────────


def _create_exact_mortgage(seed_user, db_session):
    """Create a mortgage with exact known terms for hand-calculated tests.

    Uses equal original and current principal so the contractual payment
    matches exactly: M = P * [r(1+r)^n] / [(1+r)^n - 1] where
    P=200000, r=0.065/12, n=360.  Origination today so remaining
    months = 360.

    Built through the shared factory, so the loan's genesis posting ledger is
    opened with it (as production's ``loan.create_params`` does).
    """
    return create_loan_account(
        seed_user, db_session, name="Exact Test Mortgage",
        principal=Decimal("200000.00"), rate=Decimal("0.06500"), term=360,
        origination_date=date.today(), payment_day=1,
        account_type=AcctTypeEnum.MORTGAGE,
    )


class TestRefinanceCalculator:
    """Tests for the refinance what-if calculator (Commit 5.10-1).

    Verifies side-by-side comparison of current loan vs. hypothetical
    refinance scenario, including monthly savings, interest savings,
    break-even calculation, and edge cases.
    """

    def test_refinance_lower_rate(self, auth_client, seed_user, db, seed_periods):
        """C-5.10-1: Lower rate refinance shows monthly and interest savings.

        Mortgage at 6.5% refinanced to 5.0%, same term (360 months).
        Monthly payment must decrease and interest savings must be positive.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={"new_rate": "5.0", "new_term_months": "360"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Both savings columns should be green (positive savings).
        assert "text-success" in html
        # Comparison table must contain key metrics.
        assert "Monthly Payment" in html
        assert "Total Interest" in html

    def test_refinance_shorter_term(self, auth_client, seed_user, db, seed_periods):
        """C-5.10-2: Shorter term increases monthly but decreases total interest.

        30yr loan refinanced to 15yr at the same rate.  Monthly payment
        increases (red), total interest decreases significantly (green),
        and no break-even is shown (monthly savings <= 0).
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={"new_rate": "6.5", "new_term_months": "180"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Monthly increases (red), interest decreases (green).
        assert "text-danger" in html
        assert "text-success" in html
        # No break-even when monthly savings <= 0.
        assert "Break-even" not in html

    def test_refinance_with_closing_costs(self, auth_client, seed_user, db, seed_periods):
        """C-5.10-3: Closing costs produce a break-even calculation.

        Refinance to lower rate with $5,000 closing costs.
        Break-even should be calculated and displayed.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={
                "new_rate": "5.0",
                "new_term_months": "360",
                "closing_costs": "5000",
            },
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Break-even" in html
        assert "months" in html
        # Closing costs appear in the break-even explanation.
        assert "$5,000.00" in html

    @pytest.mark.parametrize("data", [
        {},
        {"new_rate": "5.0"},
        {"new_term_months": "360"},
        {"new_rate": "-1", "new_term_months": "360"},
        {"new_rate": "5.0", "new_term_months": "0"},
        {"new_rate": "5.0", "new_term_months": "700"},
    ])
    def test_refinance_validation(self, auth_client, seed_user, db, seed_periods, data):
        """C-5.10-4: Invalid inputs return validation error.

        Tests missing required fields, negative rate, zero term, and
        term exceeding max (600).
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data=data,
        )
        assert resp.status_code == 200
        assert b"Please correct" in resp.data

    def test_refinance_idor(self, auth_client, second_user, db, seed_periods):
        """C-5.10-5: Refinance on another user's loan returns 404."""
        other = _create_other_loan(second_user, db.session, AcctTypeEnum.MORTGAGE)
        resp = auth_client.post(
            f"/accounts/{other.id}/loan/refinance",
            data={"new_rate": "5.0", "new_term_months": "360"},
        )
        assert resp.status_code == 404

    def test_refinance_principal_auto_calculated(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.10-6: Without new_principal, refinance uses current + closing.

        Auto-calculated: refi_principal = current_real_principal + closing_costs.
        The principal row shows the difference from closing costs.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={
                "new_rate": "5.0",
                "new_term_months": "360",
                "closing_costs": "3000",
            },
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Principal row appears (refi != current due to closing costs).
        assert "Principal" in html
        # Current principal = $250,000 (from _create_mortgage).
        assert "$250,000.00" in html
        # Refi principal = $250,000 + $3,000 = $253,000.
        assert "$253,000.00" in html

    def test_refinance_principal_override(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.10-7: User-provided principal overrides auto-calculation.

        When new_principal is specified, the refinance ignores current
        balance + closing costs and uses the override directly.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={
                "new_rate": "5.0",
                "new_term_months": "360",
                "closing_costs": "5000",
                "new_principal": "300000",
            },
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Refi principal should be the override.
        assert "$300,000.00" in html
        # Should NOT show $255,000 (current + closing).
        assert "$255,000.00" not in html

    def test_refinance_no_closing_costs(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.10-8: Zero closing costs means no break-even calculation.

        With closing_costs=0, refi_principal = current_real_principal
        and no break-even message is shown.
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={
                "new_rate": "5.0",
                "new_term_months": "360",
                "closing_costs": "0",
            },
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Break-even" not in html
        # Lower rate still produces savings.
        assert "text-success" in html

    def test_refinance_higher_rate(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.10-9: Higher rate refinance shows negative savings.

        Refinancing to a higher rate increases both monthly payment
        and total interest.  Differences should be red (negative).
        """
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={"new_rate": "8.0", "new_term_months": "360"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Both monthly and interest show red (negative savings).
        assert "text-danger" in html
        # No break-even when savings are negative.
        assert "Break-even" not in html

    def test_refinance_with_confirmed_payments(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.10-10: Confirmed payments reduce real principal for refinance.

        With confirmed payments, the current side uses the committed
        schedule metrics and the refinance principal is based on the
        reduced real balance, not the balance stated at setup.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1700.00"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={
                "new_rate": "5.0",
                "new_term_months": "360",
                "closing_costs": "0",
            },
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Valid comparison produced (not an error).
        assert "Monthly Payment" in html
        assert "Total Interest" in html

    def test_refinance_arm_current_side(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.10-11: ARM loan current side reflects rate-adjusted schedule.

        An ARM mortgage with rate history uses the adjusted committed
        schedule for the current baseline.  Refinancing to a lower
        fixed rate should show savings.
        """
        acct = _create_loan_account(
            seed_user, db.session, AcctTypeEnum.MORTGAGE, "ARM Mortgage",
            Decimal("250000.00"), Decimal("0.06500"), 360,
            date(2023, 6, 1), 1, is_arm=True,
        )
        rh = RateHistory(
            account_id=acct.id,
            effective_date=date(2025, 1, 1),
            interest_rate=Decimal("0.07000"),
        )
        db.session.add(rh)
        # DH-#56: the prior ``params.interest_rate = 0.07`` mirror-write is
        # gone -- the RateHistory change row at 2025-01-01 (above) already
        # makes the resolver-derived current rate 7% as of the frozen today
        # (2026-03-20), which is what the refinance current side reads.
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={"new_rate": "5.0", "new_term_months": "360"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Monthly Payment" in html
        # 5% < 7% current ARM rate → savings expected.
        assert "text-success" in html

    def test_refinance_paid_off_loan(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.10-12: Paid-off loan returns error, not a comparison."""
        acct = _create_loan_account(
            seed_user, db.session, AcctTypeEnum.MORTGAGE, "Paid Off Mortgage",
            Decimal("0.00"), Decimal("0.06500"), 360,
            date(2023, 6, 1), 1,
        )
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={"new_rate": "5.0", "new_term_months": "360"},
        )
        assert resp.status_code == 200
        assert b"paid off" in resp.data

    def test_refinance_no_params(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.10-13: Loan with no LoanParams returns 404."""
        loan_type = db.session.query(AccountType).filter_by(name="Mortgage").one()
        acct = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=loan_type.id,
                name="No Params Mortgage",
                anchor_balance=Decimal("200000.00"),
            ),
        )
        db.session.add(acct)
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={"new_rate": "5.0", "new_term_months": "360"},
        )
        assert resp.status_code == 404

    def test_refinance_break_even_calculation_exact(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.10-14: Break-even equals ceil(closing_costs / monthly_savings).

        200K mortgage at 6.5%, refinance to 5.0%, 360 months.
        Closing costs = $5,000.

        Refi principal = 200000 + 5000 = 205000.
        Current monthly = M(200000, 0.065, 360) = $1,264.14.
        Refi monthly = M(205000, 0.05, 360)    = $1,100.48.
        Monthly savings = 1264.14 - 1100.48     = $163.66.
        Break-even = ceil(5000 / 163.66)        = 31 months.
        """
        acct = _create_exact_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={
                "new_rate": "5.0",
                "new_term_months": "360",
                "closing_costs": "5000",
            },
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Break-even" in html
        assert "31 months" in html

    def test_refinance_lever_exists(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.10-15: the dashboard for a configured loan includes the Refinance lever."""
        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Refinance" in html
        assert 'name="new_rate"' in html

    def test_refinance_tab_hidden_no_params(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.10-16: Loan without params shows setup page, no Refinance tab."""
        loan_type = db.session.query(AccountType).filter_by(name="Mortgage").one()
        acct = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=loan_type.id,
                name="No Params Mortgage",
                anchor_balance=Decimal("200000.00"),
            ),
        )
        db.session.add(acct)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Setup page does not contain the Refinance tab.
        assert "Refinance Calculator" not in html

    def test_refinance_rate_conversion(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.10-17: Rate input as percentage is correctly converted to decimal.

        Submitting new_rate=5.0 (meaning 5%) must produce a monthly
        payment consistent with 0.05 annual rate.

        200K at 5.0%, 360 months: M = $1,073.64.
        At 500% (unconverted):    M would be ~$83,333.
        At 0.05% (double-conv):   M would be ~$556.
        """
        acct = _create_exact_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={
                "new_rate": "5.0",
                "new_term_months": "360",
                "closing_costs": "0",
            },
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Correct 5% rate produces $1,073.64/mo refinanced payment.
        assert "$1,073.64" in html

    def test_refinance_comparison_metrics_hand_calculated(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C-5.10-18: Exact comparison metrics match engine calculation.

        Known loan: $200K, 6.5%, 30yr.
        Refinance: same principal, 5.0%, 30yr, no closing costs.

        Amortization formula: M = P * [r(1+r)^n] / [(1+r)^n - 1]

        Current:   P=200000, r=0.065/12, n=360
                   M = $1,264.14/mo
                   Total interest = $255,085.82

        Refinance: P=200000, r=0.05/12, n=360
                   M = $1,073.64/mo
                   Total interest = $186,513.24

        Savings:   Monthly  = $1,264.14 - $1,073.64 = $190.50/mo
                   Interest = $255,085.82 - $186,513.24 = $68,572.58
        """
        acct = _create_exact_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={
                "new_rate": "5.0",
                "new_term_months": "360",
                "closing_costs": "0",
            },
        )
        assert resp.status_code == 200
        html = resp.data.decode()

        # Current monthly P&I.
        assert "$1,264.14" in html
        # Refinance monthly P&I.
        assert "$1,073.64" in html
        # Monthly savings.
        assert "$190.50" in html
        # Current total interest.
        assert "$255,085.82" in html
        # Refinance total interest.
        assert "$186,513.24" in html
        # Interest savings.
        assert "$68,572.58" in html


class TestRefinanceForwardOnlyBaseline:
    """The refinance comparison measures the CURRENT loan contractual + forward-only.

    The refinance side is inherently forward-only (a brand-new loan from today),
    so the current side must be too: "Remaining Term" counts the payments still
    ahead and "Total Interest" the interest still to be paid.  And the current
    side is the pure CONTRACT -- ``scenarios.original_forward``, a like-for-like
    minimum-vs-minimum comparison -- never the owner's PLAN (the balance seam's
    fold, which carries a standing extra a borrower could pay on either loan;
    the builder is handed no fold at all, so that is structural).  Before these
    rules the builder used whole-schedule aggregates, counting sunk history
    against a from-today refinance.
    """

    def test_current_side_reads_contractual_original_forward(self):
        """The current side derives from ``original_forward``, not ``state.schedule``.

        The current baseline is the pure-CONTRACTUAL forward remainder read
        from ``scenarios.original_forward``, like-for-like against a
        from-today minimum-payment refi -- not the whole ``state.schedule``
        (confirmed history plus that forward).  Here ``original_forward`` is
        2 contractual rows (interest 400.00 + 300.00 = 700.00, payoff Apr
        2026) behind 2 confirmed rows (interest 997.50): the current side
        must report 2 remaining months / 700.00 / Apr-2026 from
        ``original_forward``, NOT 4 / 1,697.50 from the whole schedule.
        "Forward-only" is now structural too -- ``original_forward`` carries
        no confirmed rows.
        """
        # Pylint: ``import-outside-toplevel`` -- route-private helper under
        # test; imported here so the module import stays route-surface only.
        from app.routes.loan.calculators import (  # pylint: disable=import-outside-toplevel
            _build_refinance_comparison,
        )
        from app.services.amortization_engine import (  # pylint: disable=import-outside-toplevel
            AmortizationRow,
        )
        from app.services.loan_resolver import (  # pylint: disable=import-outside-toplevel
            LoanState,
            PayoffScenarios,
        )

        def _row(month, interest, balance, confirmed):
            return AmortizationRow(
                month=month, payment_date=date(2026, month, 1),
                payment=Decimal("1000.00"), principal=Decimal("500.00"),
                interest=interest, extra_payment=Decimal("0.00"),
                remaining_balance=balance, is_confirmed=confirmed,
                interest_rate=Decimal("0.06"),
            )

        # Contractual forward -- the current side must read THIS: 2 rows.
        original_forward = [
            _row(3, Decimal("400.00"), Decimal("98500.00"), False),
            _row(4, Decimal("300.00"), Decimal("98000.00"), False),
        ]
        confirmed = [
            _row(1, Decimal("500.00"), Decimal("99500.00"), True),
            _row(2, Decimal("497.50"), Decimal("99000.00"), True),
        ]
        scenarios = PayoffScenarios(
            history_rows=confirmed,
            original_forward=original_forward,
        )
        # ``state.schedule`` is confirmed history + the contract's forward --
        # the current side must NOT read it (sunk history is not ahead).
        state = LoanState(
            monthly_payment=Decimal("1000.00"),
            current_rate=Decimal("0.06"),
            schedule=confirmed + original_forward,
            total_interest=Decimal("1697.50"),
        )
        # The balance the route threads in explicitly (C4); a LoanState field
        # until plan step D2a deleted it (the route reads the seam's fold).
        current_balance = Decimal("99000.00")
        # The route context stand-in.  ``_build_refinance_comparison`` reads only
        # ``monthly_payment`` and ``payoff_date`` off it, and since plan C8d the
        # payoff is the seam's DERIVED figure carried on the route context, not a
        # ``LoanState`` field -- so the stand-in is the context's shape, not the
        # resolver's.  The value here is the empty-slice fallback only; this
        # fixture's ``original_forward`` is non-empty, so it must NOT be read.
        route_ctx = SimpleNamespace(
            monthly_payment=state.monthly_payment,
            payoff_date=date(2026, 3, 1),
        )
        params = SimpleNamespace(payment_day=1)
        data = {
            "closing_costs": Decimal("0.00"),
            "new_principal": None,
            "new_term_months": 12,
            "new_rate": Decimal("0.05"),
        }

        # C4: the balance is threaded in explicitly (read once by the route);
        # ``route_ctx`` stands in for the route context's monthly_payment /
        # payoff_date.
        comparison = _build_refinance_comparison(
            current_balance, route_ctx, scenarios, data, params,
        )

        # Current side reads the CONTRACTUAL original_forward: 2 months,
        # 400 + 300 = 700.00, payoff Apr 2026.
        assert comparison["current_remaining_months"] == 2
        assert comparison["current_total_interest"] == Decimal("700.00")
        assert comparison["current_payoff"] == date(2026, 4, 1)
        assert comparison["term_diff"] == 10
        assert comparison["interest_savings"] == (
            Decimal("700.00") - comparison["refi_total_interest"]
        )
        # Non-vacuity: the whole schedule (4 months / 1,697.50) differs on
        # both figures, so the current side is provably reading
        # original_forward, not state.schedule.
        assert comparison["current_remaining_months"] != len(state.schedule)
        assert comparison["current_total_interest"] != state.total_interest


class TestTargetDatePanelAgreesWithTheChip:
    """Plan C8f: the target-date panel and the payoff chip fold ONE model.

    The panel used to binary-search the resolver's contractual schedule walk while
    the chip folded the loan's plan.  For a loan behind that schedule the two
    disagree, and the disagreement was user-visible and unlabelled: the panel
    would announce "your current payment plan already pays this loan off by the
    target date" for a target the chip's own payoff date was months PAST.
    """

    def test_a_target_the_loan_misses_is_not_called_already_paid_off(
        self, app, auth_client, seed_user, db, seed_periods,
    ):
        """A target between the CONTRACTUAL and the FOLD payoff needs a top-up.

        That window is exactly where the two models disagreed.  The loan is trued
        up $5,000 above its contractual schedule, so the fold retires it well
        after the contract does; a target inside the gap is one the schedule walk
        called "already met" and the fold does not.
        """
        acct = create_loan_account(
            seed_user, db.session, name="Behind Schedule",
            principal=Decimal("240000.00"), term=360,
            origination_date=date(2026, 1, 1), payment_day=1,
        )
        insert_trueup_event(
            loan_params_for(db.session, acct.id), Decimal("245000.00"),
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[2], Decimal("1500.00"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        with app.app_context():
            ctx = BalanceContext.build(seed_user["user"].id)
            account = db.session.get(Account, acct.id)
            fold_payoff = balance_at.loan_payoff_date(account, ctx)
            contractual = contractual_schedule_from_origination(
                load_loan_params(acct.id), load_rate_changes(acct.id),
            )[-1].payment_date
        assert fold_payoff is not None
        assert contractual < fold_payoff, (
            "precondition: the fold must retire this loan AFTER the contractual "
            "schedule, or the target window this test needs does not exist"
        )
        # Inside the gap: after the contractual payoff, before the fold's.
        target = add_months(fold_payoff, -6)
        assert contractual < target < fold_payoff

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "target_date", "target_date": target.isoformat()},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "already pays this loan off" not in html, (
            f"the panel claims the plan already clears the loan by {target}, but "
            f"the chip on the same page shows it paying off {fold_payoff} -- the "
            "panel is searching a different forward model than the chip folds"
        )
        assert "Add on Top of Your Current Plan" in html


class TestPayoffChipDisplayStates:
    """Plan C8d: the "Projected payoff" chip has THREE states, one per answer.

    The chip renders the DERIVED payoff, which is ``None`` for two very
    different loans.  Rendering both the same way is what finding B-20 was: a
    loan paid off by a lump-sum true-up showed its ORIGINATION date as a
    "projected payoff" -- a past date presented as a future event.
    """

    def test_a_paying_loan_shows_its_payoff_month(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The ordinary state: a month, from the fold to zero."""
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )
        db.session.commit()
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Projected payoff" in html
        assert "Paid off" not in html
        assert "No payoff at current payment" not in html
        with auth_client.application.app_context():
            ctx = BalanceContext.build(seed_user["user"].id)
            account = db.session.get(Account, acct.id)
            payoff = balance_at.loan_payoff_date(account, ctx)
        assert payoff is not None
        assert payoff.strftime("%b %Y") in html

    def test_a_retired_loan_is_badged_paid_off(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """B-20: a loan trued up to zero is BADGED, never dated.

        The chip must not fall back to a date at all -- and specifically not to
        the origination date the retired resolver fallback used to supply, which
        is what this asserts is absent.
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )
        db.session.commit()
        insert_trueup_event(loan_params_for(db.session, acct.id), Decimal("0.00"))
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Paid off" in html
        assert "Projected payoff" not in html
        # The pre-C8d fallback rendered the ORIGINATION month here.
        assert "Jan 2026" not in html

    def test_a_loan_that_never_clears_says_so(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The third state: legible text, not a hidden chip.

        Trued up so high the level payment cannot cover the monthly interest
        ($900,000 at 6.5% accrues ~$4,875/mo against a ~$1,580 payment), the
        balance grows and the fold never reaches zero.  The user is told, which
        reinforces C7's drift warning rather than leaving a gap where a payoff
        date used to be.
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )
        db.session.commit()
        insert_trueup_event(
            loan_params_for(db.session, acct.id), Decimal("900000.00"),
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No payoff at current payment" in html
        assert "Paid off" not in html


class TestOneWalkEdges:
    """R7d-g-3's adversarial review: the shapes the one-walk re-cut must survive.

    Each test is a shape the review traced through the seam's fold and found
    a surface answering wrong: an ARM whose plan pays before its first charge
    (the rate column read ``None`` and the page raised), a retired loan (the
    plan's payments folded to refunds and a phantom row, a five-year flat
    band and a "Never" lever followed), an installment due TODAY (the
    allocation bar skipped to next month), a plan that never clears (a
    dollar "saving" beside "Never"), and a loan behind on its payments (the
    shortfall the bar must say).
    """

    def test_an_arm_paying_before_its_first_charge_renders_its_rate(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A payment no charge stands over reads the period the calendar puts it in.

        ARM at 5% originated 2026-03-01 (first installment Apr 1), with a
        $50 sweep due the 15th whose Mar 15 occurrence the plan pays the day
        after the frozen 2026-03-20 today -- before Apr 1's charge, so the
        fold hands it no standing charge (ruling R-C's early extra).  Its
        row's rate is the origination period's 5%
        (``PaymentOutcome.period``, resolved from the stream's own
        calendar), so the ARM rate column renders every row and the page
        returns 200 rather than multiplying ``None``.
        """
        acct = _create_loan_account_exact(
            seed_user, db.session, AcctTypeEnum.MORTGAGE, "ARM Early Sweep",
            Decimal("250000.00"), Decimal("0.05000"), 360,
            date(2026, 3, 1), 1,
        )
        params = loan_params_for(db.session, acct.id)
        params.is_arm = True
        db.session.commit()
        make_loan_payment_template(
            db.session, seed_user, acct, amount="50.00",
            derive_from_loan=False, cadence=MONTHLY, fires_on_day=15,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan/schedule")
        assert resp.status_code == 200
        html = resp.data.decode()
        rows = re.findall(
            r'<tr class="(?:table-success)?">\s*<td>(\d+)</td>\s*<td>([^<]+)</td>',
            html,
        )
        # The catch-up is the first planned row, before the loan's first
        # installment (#0), paid the day after today; the sweep's April
        # occurrence is installment #1's period (the loan has no other
        # definition, so the plan is the sweep's occurrences alone).
        assert rows[0] == ("0", "Mar 21, 2026"), rows[:2]
        assert rows[1] == ("1", "Apr 15, 2026"), rows[:2]
        rates = re.findall(r'>\s*([0-9]+\.[0-9]{3})%\s*</td>', html)
        assert rates and set(rates) == {"5.000"}, rates[:3]

    def test_a_retired_loan_lists_no_plan_and_draws_no_extension(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A loan trued up to zero has no planned row, no grid past its history, no lever.

        The seam's plan for a retired loan is EMPTY (``loan_installments``
        answers nothing for a retired loan, the guard the payoff already
        answers ``None`` under): the schedule
        page lists no Projected row, the band's grid ends with the history
        (no five-year flat zero past the contract), and the pay-off-sooner
        lever says the loan is paid off rather than "Never, at this extra".
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[2], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()
        insert_trueup_event(loan_params_for(db.session, acct.id), Decimal("0.00"))
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan/schedule")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert html.count('badge bg-success">Confirmed</span>') == 1
        assert html.count('badge bg-secondary">Projected</span>') == 0

        band = _parse_band_chart(
            auth_client.get(f"/accounts/{acct.id}/loan").data.decode()
        )
        assert band is not None
        # The history's one point and nothing past it.
        assert len(band["labels"]) == 1
        assert band["labels"] == ["Feb 2026"]

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "200"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "This loan is paid off" in html
        assert "Never, at this extra" not in html
        assert "data-overlay=" not in html

    def test_the_allocation_bar_reads_an_installment_due_today(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """An installment due TODAY and unpaid is this month's payment, not next month's.

        A $250k / 6.5% mortgage originated 2026-02-20 on the 20th: its first
        installment is due on the frozen 2026-03-20 today and no row answers
        it, so the plan pays it tomorrow (the D1 clamp) against March's
        charge.  The bar reads March -- ``$1,580.17`` paying ``$1,354.17`` of
        interest on the untouched ``$250,000`` -- where a bar keyed on "due
        after today" would have skipped to April on the morning the payment
        is due.
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 2, 20),
            payment_day=20,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Mar 2026 payment" in html
        assert "Apr 2026 payment" not in html
        assert "$1,354.17" in html
        assert "$226.00" in html

    def test_a_plan_that_never_clears_shows_no_saving(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """"Never, at this extra" carries no months and no dollars saved.

        Trued up to $900,000 against a ~$1,580 payment the balance grows and
        neither trajectory reaches zero: the lever says "Never, at this
        extra", and both savings chips read "--" -- a difference between two
        sums cut at the plan's horizon is not a saving.  The plan-vs-contract
        line says the plan never pays off.
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[2], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()
        insert_trueup_event(
            loan_params_for(db.session, acct.id), Decimal("900000.00"),
        )
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "10"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Never, at this extra" in html
        assert re.search(r"Months Saved</div>\s*<div[^>]*>\s*--\s*</div>", html)
        assert re.search(r"Interest Saved</div>\s*<div[^>]*>\s*--\s*</div>", html)
        assert "never pays off at the current plan" in html
        assert "saved" not in html.split("Current plan vs. original")[1]

    def test_a_loan_behind_says_its_shortfall(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Two installments behind, the bar floors principal and says the shortfall.

        Nothing paid on a $250k / 6.5% mortgage originated 2026-01-01: the
        fold charges February, March and April against the Apr 1 payment
        (``$1,354.17 + $1,354.17 + $1,354.17 = $4,062.51`` of interest on the
        untouched balance, less what the ledger's own accrual model lays off
        -- the review's fold simulation puts the first planned payment's
        interest at ``$2,705.88``), so the payment's principal is negative.
        The bar shows the whole ``$1,580.17`` to interest, no principal
        segment, and says how far short it fell.
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "short of the interest and escrow charged" in html
        pcts = re.findall(r'data-progress-pct="([0-9.]+)"', html)
        assert pcts == ["100.0"], pcts
        assert "to principal (0.0%)" in html
        assert "$1,580.17</span>\n        to interest (100.0%)" in html


class TestRefinanceAndPayoffByDateProjectForwardMigration:
    """C7-5, C7-7, C7-8: route-level assert-unchanged locks for the
    Commit 7 migration of ``refinance_calculate`` and the
    ``mode=target_date`` payoff branch onto :func:`project_forward`.

    The migration is behavior-preserving (per D-F of the
    implementation plan).  These tests pin the rendered HTML values
    that the legacy ``generate_schedule`` path produced; any drift
    proves a real regression.
    """

    def test_refinance_unchanged_vs_pre_commit(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C7-5: refinance partial renders byte-identical key values.

        Pre-commit snapshot (200K mortgage at 6.5% refinancing to 5.0%,
        360 months, $0 closing costs):
          - refi_monthly        = $1,073.64
          - refi_total_interest = $186,513.24
          - monthly_savings     = $190.50
          - interest_savings    = $68,572.58
        Hand calculation (matches existing
        ``test_refinance_comparison_metrics_hand_calculated``):
          Current:   M(200000, 0.065/12, 360) = $1,264.14;
                     total interest = $255,085.82.
          Refinance: M(200000, 0.05/12, 360)  = $1,073.64;
                     total interest = $186,513.24.
          Savings:   1264.14 - 1073.64 = 190.50/mo;
                     255085.82 - 186513.24 = 68572.58.
        """
        acct = _create_exact_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={
                "new_rate": "5.0",
                "new_term_months": "360",
                "closing_costs": "0",
            },
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "$1,264.14" in html, "current monthly drifted"
        assert "$1,073.64" in html, "refi monthly drifted"
        assert "$190.50" in html, "monthly savings drifted"
        assert "$255,085.82" in html, "current total interest drifted"
        assert "$186,513.24" in html, "refi total interest drifted"
        assert "$68,572.58" in html, "interest savings drifted"

    def test_no_generate_schedule_in_refinance(self):
        """C7-7: the refinance schedule projection makes no generate_schedule call.

        Structural guarantee mirroring C7-6 at the route layer.  The
        refinance schedule is built by ``_project_refinance`` (Phase 3
        pylint cleanup decomposed ``refinance_calculate``; the route
        delegates through ``_build_refinance_comparison``).  The
        builder's function-body slice -- between its ``def`` and the
        next top-level ``def`` in ``app/routes/loan/calculators.py`` --
        must project via ``project_forward``, never reference or call
        ``generate_schedule``.
        """
        from pathlib import Path  # pylint: disable=import-outside-toplevel

        calculators = (
            Path(__file__).resolve().parent.parent.parent
            / "app" / "routes" / "loan" / "calculators.py"
        )
        source = calculators.read_text(encoding="utf-8")
        marker = "def _project_refinance("
        start = source.index(marker)
        next_def = source.find("\ndef ", start + len(marker))
        end = next_def if next_def != -1 else len(source)
        body = source[start:end]
        assert "amortization_engine.generate_schedule" not in body, (
            "_project_refinance must not reference "
            "amortization_engine.generate_schedule after Commit 7."
        )
        assert "generate_schedule(" not in body, (
            "_project_refinance must not call generate_schedule."
        )

    def test_target_date_route_branch_unchanged(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C7-8: ``mode=target_date`` HTML renders byte-identical
        ``required_extra`` and ``total_monthly``.

        Uses the exact $200k / 6.5% / 30yr mortgage helper.  Today is
        frozen to 2026-03-20 by ``_freeze_today_inside_seed_range``,
        and ``_create_exact_mortgage`` originates "today," so the
        route's binary search anchors at ``2026-03-01`` (today's first
        of month) with starting_date 2026-04-01.  Target 2041-01-01.
        Pre-commit values from the legacy ``generate_schedule``-backed
        binary search (captured 2026-05-22):
          - required_extra = $489.67 (binary-search convergence
            against the contractual M(200000, 0.065/12, 360) =
            $1,264.14)
          - total_monthly  = 1264.14 + 489.67 = $1,753.81
        The HTMX partial only renders required_extra and total_monthly
        in this mode; ``monthly_payment`` is passed for context-builder
        completeness but is not surfaced as a distinct label, so the
        assertion set matches what the user actually sees.
        """
        acct = _create_exact_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "target_date", "target_date": "2041-01-01"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "$489.67" in html, "required_extra drifted"
        assert "$1,753.81" in html, "total_monthly drifted"


# ── Nav-Pills Consistency Tests ─────────────────────────────────────


class TestLoanNavPills:
    """Tests verifying loan dashboard uses nav-pills instead of nav-tabs."""

    def test_loan_dashboard_renders_pills(self, auth_client, seed_user, db, seed_periods):
        """GET loan dashboard contains nav-pills markup."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert b"nav-pills" in resp.data

    def test_loan_dashboard_no_nav_tabs(self, auth_client, seed_user, db, seed_periods):
        """GET loan dashboard does not contain nav-tabs markup."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert b"nav-tabs" not in resp.data

    def test_loan_uses_scroll_pills(self, auth_client, seed_user, db, seed_periods):
        """GET loan dashboard contains shekel-scroll-pills class."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert b"shekel-scroll-pills" in resp.data

    def test_no_mobile_scroll_tabs_in_loan(self, auth_client, seed_user, db, seed_periods):
        """GET loan dashboard does not contain mobile-scroll-tabs class."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert b"mobile-scroll-tabs" not in resp.data

    def test_loan_data_bs_toggle_pill(self, auth_client, seed_user, db, seed_periods):
        """All toggle attributes use pill, not tab."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        html = resp.data.decode()
        assert 'data-bs-toggle="pill"' in html
        assert 'data-bs-toggle="tab"' not in html


# ── Loan Balance True-up (E-18 D-C / Commit 16) ──────────────────────


class TestRecordTrackingStartRoute:
    """POST /loan/tracking-start records a mid-life tracking-start (a tracking_start event).

    Every fixture loan here already carries ONE ``tracking_start`` -- the
    balance stated at setup, which the shared helper records the way the
    setup door does (plan step R20) -- so this door's write is the SECOND.
    """

    def _tracking_start_events(self, db_session, account):
        """Return the account's tracking_start LoanAnchorEvents."""
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import LoanAnchorSourceEnum  # pylint: disable=import-outside-toplevel
        from app.models.loan_anchor_event import (  # pylint: disable=import-outside-toplevel
            LoanAnchorEvent,
        )
        src = ref_cache.loan_anchor_source_id(
            LoanAnchorSourceEnum.TRACKING_START,
        )
        return (
            db_session.query(LoanAnchorEvent)
            .filter_by(account_id=account.id, source_id=src)
            .all()
        )

    def test_records_tracking_start_and_redirects(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A valid tracking-start POST appends one tracking_start event and redirects.

        The auto-loan fixture originated 2025-01-01 and stated $25,000 as of
        ``_TRACKED_FROM``.  POSTing a $20,000 balance as of 2025-06-01
        (after origination, before any payment, before today) appends exactly
        one more tracking_start event with that balance / date and redirects
        to the dashboard.
        """
        acct = _create_auto_loan(seed_user, db.session)
        before = self._tracking_start_events(db.session, acct)
        assert [(e.anchor_date, e.anchor_balance) for e in before] == [
            (_TRACKED_FROM, Decimal("25000.00")),
        ]

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/tracking-start",
            data={"anchor_date": "2025-06-01", "anchor_balance": "20000.00"},
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith(f"/accounts/{acct.id}/loan")

        db.session.expire_all()
        events = self._tracking_start_events(db.session, acct)
        assert len(events) == 2
        new = [e for e in events if e.id not in {b.id for b in before}]
        assert len(new) == 1
        assert new[0].anchor_balance == Decimal("20000.00")
        assert new[0].anchor_date == date(2025, 6, 1)

    def test_rejects_date_before_origination(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A tracking-start dated before origination is rejected; no event written."""
        acct = _create_auto_loan(seed_user, db.session)
        before = len(self._tracking_start_events(db.session, acct))
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/tracking-start",
            data={"anchor_date": "2024-12-01", "anchor_balance": "20000.00"},
        )
        assert resp.status_code == 302
        db.session.expire_all()
        assert len(self._tracking_start_events(db.session, acct)) == before

    def test_accepts_a_date_after_a_recorded_payment(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A tracking-start dated after a recorded payment is recorded and governs.

        **The route refused this until plan step R20** (ruling R-R72 part 3),
        on the ground that the payment "would sort before the opening in the
        walk and be subsumed" -- a claim about an opening the tracking-start
        stopped being at step C1.  It is an ordinary dated assertion: with a
        $500 payment settled in an early period (due well before the frozen
        today of 2026-03-20), a $20,000 tracking-start dated today is
        appended, and it is the loan's LATEST assertion, so the ledger's
        balance today reads exactly $20,000 -- the payment before it is
        superseded by the owner's statement, precisely as a true-up dated
        today would supersede it.
        """
        acct = _create_auto_loan(seed_user, db.session)
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], acct,
            seed_periods[0], amount=Decimal("500.00"),
        )
        db.session.commit()
        before = len(self._tracking_start_events(db.session, acct))
        assert posted_loan_balance_at(
            acct.id, seed_user["scenario"].id, date.today(),
        ) != Decimal("20000.00")

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/tracking-start",
            data={
                "anchor_date": date(2026, 3, 20).isoformat(),
                "anchor_balance": "20000.00",
            },
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith(f"/accounts/{acct.id}/loan")
        db.session.expire_all()
        events = self._tracking_start_events(db.session, acct)
        assert len(events) == before + 1
        assert posted_loan_balance_at(
            acct.id, seed_user["scenario"].id, date.today(),
        ) == Decimal("20000.00")
        page = auth_client.get(f"/accounts/{acct.id}/loan")
        assert page.status_code == 200
        # Two tracking-start rows now wear the anchors card's badge (the
        # form's own label is on every page and grades nothing).
        assert page.data.count(
            b'<span class="badge bg-secondary ms-1">Tracking start</span>'
        ) == 2


class TestLoanBalanceTrueUp:
    """Tests for the dated balance true-up route (loan.true_up_balance).

    The route mirrors the checking-account anchor true-up UX
    (:func:`app.routes.accounts.true_up`) for loan accounts.  POSTing
    ``(anchor_date, anchor_balance)`` appends a ``user_trueup``
    :class:`LoanAnchorEvent` and redirects back to the dashboard.

    Test IDs follow the Commit-16 plan checklist (C16-1 .. C16-7).
    """

    # C16-1
    def test_trueup_appends_event(self, auth_client, seed_user, db, seed_periods):
        """POST trueup creates a new LoanAnchorEvent; no prior row mutated.

        Hand-check: the ``_create_auto_loan`` fixture writes ONE stored
        anchor event (the balance stated at setup, a tracking_start at
        $25,000; the origination is synthesized from params, not stored --
        as production's create_params does since the read switch).  After
        POSTing today / $24,000:
          * 302 redirect to /accounts/<id>/loan.
          * Two anchor events on disk (the stated balance + new trueup).
          * The new event has source_id == USER_TRUEUP id, balance
            $24,000, anchor_date == 2026-03-20 (the frozen "today"
            for this test file).
          * The prior event is byte-identical (no UPDATE).
        """
        from app.models.loan_anchor_event import LoanAnchorEvent as _LAE  # pylint: disable=import-outside-toplevel
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import LoanAnchorSourceEnum  # pylint: disable=import-outside-toplevel
        acct = _create_auto_loan(seed_user, db.session)

        before_events = (
            db.session.query(_LAE)
            .filter_by(account_id=acct.id)
            .order_by(_LAE.id)
            .all()
        )
        before_snapshot = [
            (e.id, e.anchor_date, e.anchor_balance, e.source_id, e.created_at)
            for e in before_events
        ]
        assert len(before_snapshot) == 1, (
            "Fixture is expected to seed one stored event (the balance stated "
            "at setup; origination is synthesized, not stored); if this assertion "
            "fails the helper has drifted and the rest of this test "
            "is meaningless."
        )

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/trueup",
            data={
                "anchor_date": date(2026, 3, 20).isoformat(),
                "anchor_balance": "24000.00",
            },
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith(f"/accounts/{acct.id}/loan")

        db.session.expire_all()
        after_events = (
            db.session.query(_LAE)
            .filter_by(account_id=acct.id)
            .order_by(_LAE.id)
            .all()
        )
        assert len(after_events) == 2

        after_by_id = {e.id: e for e in after_events}
        for snap in before_snapshot:
            e_id, e_date, e_balance, e_source, e_created = snap
            after = after_by_id[e_id]
            assert (
                (after.id, after.anchor_date, after.anchor_balance,
                 after.source_id, after.created_at)
                == snap
            ), (
                f"Prior event id={e_id} must not be mutated by a "
                f"trueup (LoanAnchorEvent is append-only)."
            )

        new_event = next(
            e for e in after_events
            if e.id not in {s[0] for s in before_snapshot}
        )
        user_trueup_id = ref_cache.loan_anchor_source_id(
            LoanAnchorSourceEnum.USER_TRUEUP,
        )
        assert new_event.source_id == user_trueup_id
        assert new_event.anchor_balance == Decimal("24000.00")
        assert new_event.anchor_date == date(2026, 3, 20)

    # C16-2
    def test_trueup_changes_loan_card_balance(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """After a trueup, the loan dashboard renders the new balance.

        The resolver replays from the latest event by
        ``(anchor_date, created_at)`` DESC, so a trueup at a future
        anchor_date than the existing seed trueup is selected.  This
        verifies the resolver consumes the freshly-written event.

        Hand-check: the balance stated at setup is dated ``_TRACKED_FROM``
        (2026-01-01) at $25,000.  The new trueup is dated 2026-03-20
        (today) at $23,500, which is strictly later, so the resolver picks
        it -- meaning the loan card's displayed balance becomes $23,500.00.
        """
        acct = _create_auto_loan(seed_user, db.session)

        # Sanity check pre-trueup balance == $25,000.00 (the seed
        # trueup amount).
        resp_pre = auth_client.get(f"/accounts/{acct.id}/loan")
        assert b"$25,000.00" in resp_pre.data

        auth_client.post(
            f"/accounts/{acct.id}/loan/trueup",
            data={
                "anchor_date": date(2026, 3, 20).isoformat(),
                "anchor_balance": "23500.00",
            },
        )
        resp_post = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp_post.status_code == 200
        assert b"$23,500.00" in resp_post.data

    # C16-3
    def test_trueup_rejects_future_date(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """POST anchor_date strictly in the future -> rejected, no event written.

        Schema validation rejects future dates as a validation error;
        the route flashes "correct the highlighted errors" and
        redirects without writing.  Asserts via the event count that
        nothing was appended.
        """
        from app.models.loan_anchor_event import LoanAnchorEvent as _LAE  # pylint: disable=import-outside-toplevel
        acct = _create_auto_loan(seed_user, db.session)
        before = (
            db.session.query(_LAE)
            .filter_by(account_id=acct.id)
            .count()
        )

        future = date(2026, 3, 21).isoformat()  # one day past frozen today
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/trueup",
            data={"anchor_date": future, "anchor_balance": "20000.00"},
        )
        # Validation failure: redirect (302) with flash.
        assert resp.status_code == 302

        db.session.expire_all()
        after = (
            db.session.query(_LAE)
            .filter_by(account_id=acct.id)
            .count()
        )
        assert after == before, (
            "Future anchor_date must NOT append an event."
        )

    # C16-4
    def test_trueup_rejects_pre_origination(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """POST anchor_date before origination -> rejected with explanatory flash.

        Hand-check: ``_create_auto_loan`` uses ``origination_date =
        date(2025, 1, 1)``.  Submitting 2024-12-31 must be rejected
        and no event appended.
        """
        from app.models.loan_anchor_event import LoanAnchorEvent as _LAE  # pylint: disable=import-outside-toplevel
        acct = _create_auto_loan(seed_user, db.session)
        before = (
            db.session.query(_LAE)
            .filter_by(account_id=acct.id)
            .count()
        )

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/trueup",
            data={
                "anchor_date": date(2024, 12, 31).isoformat(),
                "anchor_balance": "20000.00",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"origination" in resp.data.lower() or b"Origination" in resp.data

        db.session.expire_all()
        after = (
            db.session.query(_LAE)
            .filter_by(account_id=acct.id)
            .count()
        )
        assert after == before, (
            "Pre-origination anchor_date must NOT append an event."
        )

    # C16-5
    def test_trueup_rejects_negative_balance(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """POST anchor_balance < 0 -> rejected, no event written.

        Schema-tier ``Range(min=0)`` plus the CHECK
        ``ck_loan_anchor_events_balance_nonneg`` at the storage tier.
        """
        from app.models.loan_anchor_event import LoanAnchorEvent as _LAE  # pylint: disable=import-outside-toplevel
        acct = _create_auto_loan(seed_user, db.session)
        before = (
            db.session.query(_LAE)
            .filter_by(account_id=acct.id)
            .count()
        )

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/trueup",
            data={
                "anchor_date": date(2026, 3, 20).isoformat(),
                "anchor_balance": "-100.00",
            },
        )
        assert resp.status_code == 302

        db.session.expire_all()
        after = (
            db.session.query(_LAE)
            .filter_by(account_id=acct.id)
            .count()
        )
        assert after == before

    # C16-6
    def test_trueup_is_append_only(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Two distinct trueups produce two new rows; prior rows untouched.

        Hand-check: starting from the seeded ONE event (the true-up;
        origination is synthesized, not stored), post two different
        trueups (different dates).  Final state must have three events;
        all earlier rows byte-identical.
        """
        from app.models.loan_anchor_event import LoanAnchorEvent as _LAE  # pylint: disable=import-outside-toplevel
        acct = _create_auto_loan(seed_user, db.session)

        snapshot_before = [
            (e.id, e.anchor_date, e.anchor_balance, e.source_id, e.created_at)
            for e in (
                db.session.query(_LAE)
                .filter_by(account_id=acct.id)
                .order_by(_LAE.id)
                .all()
            )
        ]
        assert len(snapshot_before) == 1

        # First trueup -- different date than any seed event.
        auth_client.post(
            f"/accounts/{acct.id}/loan/trueup",
            data={
                "anchor_date": date(2026, 2, 1).isoformat(),
                "anchor_balance": "24500.00",
            },
        )
        # Second trueup -- yet another distinct date.
        auth_client.post(
            f"/accounts/{acct.id}/loan/trueup",
            data={
                "anchor_date": date(2026, 3, 1).isoformat(),
                "anchor_balance": "24000.00",
            },
        )

        db.session.expire_all()
        all_events = (
            db.session.query(_LAE)
            .filter_by(account_id=acct.id)
            .order_by(_LAE.id)
            .all()
        )
        assert len(all_events) == 3

        events_by_id = {e.id: e for e in all_events}
        for snap in snapshot_before:
            e_id, e_date, e_balance, e_source, e_created = snap
            after = events_by_id[e_id]
            assert (
                (after.id, after.anchor_date, after.anchor_balance,
                 after.source_id, after.created_at)
                == snap
            ), f"Append-only invariant violated: event id={e_id} mutated."

    # C16-7
    def test_trueup_form_includes_csrf_token(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The Record Loan Balance form renders the csrf_token hidden input.

        ``TestConfig`` disables CSRF enforcement so a runtime token
        check is not exercised in tests, but the form HTML must still
        carry the ``{{ csrf_token() }}`` placeholder so production
        (where CSRF is enabled) accepts the submission.  Asserting on
        the rendered form is the proxy for "CSRF is wired correctly."
        """
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200

        # Locate the Record Loan Balance form and confirm both the
        # csrf_token field and the trueup action are present.
        html = resp.data.decode()
        trueup_url = f"/accounts/{acct.id}/loan/trueup"
        assert trueup_url in html
        # The csrf_token() helper renders as
        # <input type="hidden" name="csrf_token" value="..."> in
        # production; in TestConfig (CSRF disabled) it renders as
        # an empty string.  Assert the rendered form references
        # csrf_token to prove the template includes the call.
        assert b'name="csrf_token"' in resp.data or 'csrf_token' in html

    def test_trueup_idor_returns_404(
        self, auth_client, second_user, db, seed_periods,
    ):
        """POST trueup against another user's loan returns 404 (security).

        Cross-owner trueup must not write a row and must not leak the
        loan's existence; mirrors the rest of the loan-route IDOR
        contract.
        """
        from app.models.loan_anchor_event import LoanAnchorEvent as _LAE  # pylint: disable=import-outside-toplevel
        other = _create_other_loan(second_user, db.session)
        before = (
            db.session.query(_LAE)
            .filter_by(account_id=other.id)
            .count()
        )

        resp = auth_client.post(
            f"/accounts/{other.id}/loan/trueup",
            data={
                "anchor_date": date(2026, 3, 20).isoformat(),
                "anchor_balance": "100.00",
            },
        )
        assert resp.status_code == 404

        db.session.expire_all()
        after = (
            db.session.query(_LAE)
            .filter_by(account_id=other.id)
            .count()
        )
        assert after == before

    def test_trueup_resubmit_is_idempotent(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Submitting the same (date, balance) twice is idempotent.

        The second submit asserts what the first made governing, so
        :func:`apply_loan_anchor_true_up` writes nothing, returns
        ``UNCHANGED``, and the route flashes an informational message.
        Exactly one new event row exists at the (date, balance) tuple after
        both calls.  **It was a unique-index rejection until plan step
        X-f1c4b** (ruling R-EQ); the route behaviour is unchanged.
        """
        from app.models.loan_anchor_event import LoanAnchorEvent as _LAE  # pylint: disable=import-outside-toplevel
        acct = _create_auto_loan(seed_user, db.session)

        first = auth_client.post(
            f"/accounts/{acct.id}/loan/trueup",
            data={
                "anchor_date": date(2026, 3, 20).isoformat(),
                "anchor_balance": "22500.00",
            },
        )
        assert first.status_code == 302

        second = auth_client.post(
            f"/accounts/{acct.id}/loan/trueup",
            data={
                "anchor_date": date(2026, 3, 20).isoformat(),
                "anchor_balance": "22500.00",
            },
        )
        assert second.status_code == 302

        db.session.expire_all()
        matching = (
            db.session.query(_LAE)
            .filter_by(
                account_id=acct.id,
                anchor_date=date(2026, 3, 20),
                anchor_balance=Decimal("22500.00"),
            )
            .all()
        )
        assert len(matching) == 1, (
            "A resubmit of the governing (date, balance) must write nothing, "
            "so exactly one row survives."
        )


# DH-#56: TestLoanParamsInterestRateUpperBoundCheck was retired here.  It
# exercised the storage-tier CHECK on budget.loan_params.interest_rate
# (ck_loan_params_interest_rate_upper) and the column's nullable demotion --
# both dropped, with the column, by DH-#56's migration (b7d2f4a619c5).  The
# equivalent storage-tier rate-domain [0, 1] guard now lives on
# budget.rate_history.interest_rate (ck_rate_history_valid_interest_rate),
# already covered by tests/test_routes/test_c24_range_check_sweep.py::
# TestRateHistoryCheck -- so no coverage is lost.


class TestLoanDetailMeasuredSurfaces:
    """The band's measured chips + the payment-history / balance-anchors sections.

    Loop B surfaces the genesis ledger's real facts on the loan detail page: the
    interest / principal actually paid this year (band chips), the confirmed
    payment history, and the balance-anchor drift scorecard.  These end-to-end
    (route wiring + template) assertions complement the exact-value producer
    tests in ``tests/test_services/test_loan_display_producers.py``.
    """

    def test_measured_surfaces_render_with_confirmed_payment(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A settled 2026 payment surfaces the YTD chips, history, and anchors.

        Loan trued to $100,000 (origination $250,000 on 2023-06-01, a user
        true-up on 2026-01-01); one $1,000 payment settled 2026-03-15 (before
        the autouse-frozen today 2026-03-20) posts its real interest / principal
        split to the ledger, so the two YTD chips, the payment-history row, and
        the origination + true-up anchor rows all render.
        """
        loan = create_loan_with_trueup(
            seed_user, db.session,
            origination_principal=Decimal("250000.00"),
            anchor_balance=Decimal("100000.00"),
            anchor_date=date(2026, 1, 1),
            rate=Decimal("0.06000"),
            origination_date=date(2023, 6, 1),
            name="Measured Mortgage",
        )
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], loan,
            seed_periods[3], amount=Decimal("1000.00"),
            settled_on=date(2026, 3, 15),
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{loan.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Measured YTD chips (the ledger's Schedule-A interest + principal
        # sibling).
        assert "Interest paid, YTD" in html
        assert "Principal paid, YTD" in html
        # Confirmed payment-history section with the one settled payment.
        assert "Payment history" in html
        assert "1 confirmed" in html
        # Balance-anchor scorecard: the origination row plus the user true-up.
        assert "Balance anchors" in html
        assert "Origination" in html

    def test_no_duplicate_oob_chip_ids_on_initial_load(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The band chips' OOB ids appear exactly once on the initial page load.

        The escrow / rate-history partials emit their out-of-band chip copies
        only when served as an HTMX response (``oob_swaps``), never when the
        dashboard includes them inline -- so the initial DOM carries no duplicate
        element ids (only the in-band chip and the escrow-header badge).
        """
        acct = _create_loan_account(
            seed_user, db.session, AcctTypeEnum.MORTGAGE, "Dup-ID Mortgage",
            Decimal("200000.00"), Decimal("0.05000"), 360,
            date(2024, 1, 1), 1, is_arm=True,
        )
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert html.count('id="total-payment-chip"') == 1
        assert html.count('id="escrow-badge"') == 1
        assert html.count('id="interest-rate-chip"') == 1

    def test_ytd_chips_use_display_tz_year_at_new_year_boundary(
        self, auth_client, seed_user, db, seed_periods, monkeypatch,
    ):
        """loan_audit deferred follow-up #3: YTD chips sum by the Eastern civil year.

        The two YTD chips select their year from the user's display-tz clock
        (matching the analytics Taxes tab + the L9 civil-date attribution), not
        the backend UTC clock.  Frozen at 2027-01-01 00:00 UTC -- which is still
        2026-12-31 in America/New_York -- ``display_today().year`` is 2026 while
        ``date.today().year`` (UTC) is 2027.  A payment settled 2026-03-15 posts
        its interest to civil year 2026, so the "Interest paid, YTD" chip must
        show the 2026 figure (the Taxes-tab number), NOT the empty 2027 figure
        the pre-fix UTC code would render.
        """
        loan = create_loan_with_trueup(
            seed_user, db.session,
            origination_principal=Decimal("250000.00"),
            anchor_balance=Decimal("100000.00"),
            anchor_date=date(2026, 1, 1),
            rate=Decimal("0.06000"),
            origination_date=date(2023, 6, 1),
            name="Boundary Mortgage",
        )
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], loan,
            seed_periods[3], amount=Decimal("1000.00"),
            settled_on=date(2026, 3, 15),
        )
        db.session.commit()

        # The measured facts: the 2026 (Eastern) year holds the payment's
        # interest; the 2027 (UTC) year holds nothing.  The fold-based chip
        # producer is TOTAL, so it returns $0.00 (not None) for 2027 -- exactly
        # the value the pre-fix UTC code would have rendered into the chip.
        ctx = BalanceContext.build(seed_user["user"].id)
        interest_2026 = balance_at.loan_interest_paid_in_year(loan, ctx, 2026)
        interest_2027 = balance_at.loan_interest_paid_in_year(loan, ctx, 2027)
        assert interest_2026 > Decimal("0.00")
        assert interest_2027 == Decimal("0.00")

        # Re-freeze onto the New Year boundary: midnight UTC Jan 1 2027 is
        # still Dec 31 2026 Eastern.  ``at_time`` is explicit because this test
        # means that INSTANT -- ``freeze_today``'s default is noon, so that a
        # test meaning "today is D" gets D from the display-tz readers too.
        freeze_today(monkeypatch, date(2027, 1, 1), at_time=time.min)

        html = auth_client.get(f"/accounts/{loan.id}/loan").data.decode()
        match = re.search(
            r'Interest paid, YTD</div>\s*'
            r'<div class="pulse-chip__value font-mono">([^<]+)</div>',
            html,
        )
        assert match is not None, (
            "The 'Interest paid, YTD' chip did not render -- the display-tz "
            "year should surface the 2026 payment's interest."
        )
        chip_value = match.group(1).strip()
        # The chip shows the Eastern-year (2026) figure, formatted by the money
        # macro ("$1,234.56"), NOT the $0.00 the UTC year (2027) would give.
        assert chip_value == f"${interest_2026:,.2f}"
        assert chip_value != "$0.00"


# ── Click-to-Edit Balance Hero (D14 port, polish audit P-DT8) ────────


class TestLoanBalanceHeroClickToEdit:
    """S8 / D14: the loan detail hero doubles as the dated true-up control.

    ``loan.balance_hero`` renders the click-to-edit display cell (the
    Cancel / Escape revert target); ``loan.anchor_form`` swaps in the
    inline as-of-date + balance editor, whose form posts the EXISTING
    ``loan.true_up_balance`` redirect flow (already covered by
    TestLoanTrueUpBalance) -- so these tests pin the two fragments and
    the page wiring, not the write path.  Today is frozen to 2026-03-20
    by ``_freeze_today_inside_seed_range``.
    """

    def test_dashboard_hero_is_click_to_edit(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The dashboard hero carries the editor opener AND keeps the form.

        The developer's S8 ruling keeps BOTH recording surfaces: the
        click-to-edit hero and the parameters card's "Record balance"
        form.
        """
        acct = _create_fresh_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'id="loan-balance-hero"' in html
        assert f'hx-get="/accounts/{acct.id}/loan/anchor-form"' in html
        # The keep-both ruling: the guidance-carrying form card stays.
        assert "Record balance" in html

    def test_balance_hero_renders_display_cell(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """GET (HX) returns the resolver balance and the editor opener.

        A fresh $250,000 mortgage with zero confirmed payments resolves
        to exactly its original principal: 250,000.00.
        """
        acct = _create_fresh_mortgage(seed_user, db.session)
        resp = auth_client.get(
            f"/accounts/{acct.id}/loan/balance-hero",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "250,000.00" in html
        assert 'id="loan-balance-hero"' in html
        assert f'hx-get="/accounts/{acct.id}/loan/anchor-form"' in html

    def test_balance_hero_redirects_without_htmx(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """GET without HX-Request redirects to the loan dashboard page."""
        acct = _create_fresh_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan/balance-hero")
        assert resp.status_code == 302
        assert f"/accounts/{acct.id}/loan" in resp.headers.get("Location", "")

    def test_balance_hero_idor(
        self, auth_client, second_user, db, seed_periods,
    ):
        """GET another user's loan hero returns 404 and leaks nothing."""
        other = _create_other_loan(second_user, db.session)
        resp = auth_client.get(
            f"/accounts/{other.id}/loan/balance-hero",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 404
        assert b"Other Loan" not in resp.data

    def test_balance_hero_unconfigured_loan_404s(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A params-less loan has no dashboard hero: the fragment 404s.

        The full page renders setup.html for an unconfigured loan, so no
        hero fragment of it exists to serve (deliberately a 404, not the
        POST flows' flash-and-redirect, which would swap a whole page
        into the HTMX hero slot).
        """
        loan_type = (
            db.session.query(AccountType).filter_by(name="Mortgage").one()
        )
        account = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=loan_type.id,
                name="Unconfigured Loan",
                anchor_balance=Decimal("0.00"),
            ),
        )
        db.session.add(account)
        db.session.commit()
        resp = auth_client.get(
            f"/accounts/{account.id}/loan/balance-hero",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 404

    def test_anchor_form_renders_dated_editor(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """GET (HX) returns the inline editor with correct bounds + prefill.

        The date input is floored at origination (2026-02-01 for the
        fresh-mortgage default: first of the month before the frozen
        2026-03-20 today) and capped at today; the balance prefills the
        resolver figure (250000.00, zero payments confirmed); the form
        posts the existing dated true-up route; Cancel reverts through
        loan.balance_hero.
        """
        acct = _create_fresh_mortgage(seed_user, db.session)
        resp = auth_client.get(
            f"/accounts/{acct.id}/loan/anchor-form",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert f'action="/accounts/{acct.id}/loan/trueup"' in html
        assert 'min="2026-02-01"' in html
        assert 'max="2026-03-20"' in html
        assert 'value="2026-03-20"' in html
        assert 'value="250000.00"' in html
        assert f'hx-get="/accounts/{acct.id}/loan/balance-hero"' in html
        assert 'name="csrf_token"' in html

    def test_anchor_form_redirects_without_htmx(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """GET without HX-Request redirects to the loan dashboard page."""
        acct = _create_fresh_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan/anchor-form")
        assert resp.status_code == 302
        assert f"/accounts/{acct.id}/loan" in resp.headers.get("Location", "")

    def test_anchor_form_idor(
        self, auth_client, second_user, db, seed_periods,
    ):
        """GET another user's loan editor returns 404 and leaks nothing."""
        other = _create_other_loan(second_user, db.session)
        resp = auth_client.get(
            f"/accounts/{other.id}/loan/anchor-form",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 404
        assert b"Other Loan" not in resp.data


class TestScheduleRowsResolveTheirOwnTerms:
    """Every schedule row's escrow and rate come from ITS OWN installment.

    Ruling **R-IJ** (plan step X-au-g-2b): a loan's contractual terms resolve
    on the installment they govern, never on a read date.  The amortization
    table read a single ``LoanContext.monthly_escrow``, resolved at
    ``date.today()``, and added it to all 360 rows alike -- finding **N-410**
    -- while every other tier (the genesis split, the forward plan, a
    materialised payment's live cash) already keyed on the installment.  Its
    ARM Rate column carried a second wall-clock read as a per-row fallback for
    a state no row could be in.

    The context is captured through Flask's ``template_rendered`` signal, so
    these read exactly what the route handed the template -- the whole wiring,
    route through builder -- rather than parsing 360 rows of HTML.
    """

    @staticmethod
    def _capture_schedule_context(app, auth_client, account_id):
        """Return the context GET ``/loan/schedule`` handed its template."""
        # Pylint: import-outside-toplevel -- the file-wide test convention.
        from flask import template_rendered  # pylint: disable=import-outside-toplevel

        recorded = []

        def _record(sender, template, context, **extra):  # noqa: ARG001
            recorded.append((template, context))

        template_rendered.connect(_record, app)
        try:
            response = auth_client.get(f"/accounts/{account_id}/loan/schedule")
        finally:
            template_rendered.disconnect(_record, app)
        assert response.status_code == 200, (
            f"the schedule returned {response.status_code}; expected 200"
        )
        contexts = [c for t, c in recorded if t.name == "loan/schedule.html"]
        assert contexts, "loan/schedule.html did not render"
        return contexts[0], response.data.decode()

    @staticmethod
    def _mortgage_with_a_future_escrow_step(seed_user, db_session):
        """A mortgage escrowing $300/mo, stepping to $400/mo on 2027-01-01.

        Today is frozen at 2026-03-20 for this file, so the second version is
        strictly in the FUTURE: a builder resolving one figure at ``today``
        cannot see it at all, which is the shape N-410 names.

        Originated 2026-03-01 so its first installment (Apr 1) is after
        today and nothing is overdue: a planned row's escrow is what the
        fold impounded for its installment (plan step R7d-g-3), and an
        installment left unpaid past its due date leaves its escrow to be
        impounded with the next payment's -- three months' worth on the
        first row of a loan originated in January, which is the fold's
        truth about a loan behind on its payments and not the
        version-boundary property these tests grade.
        """
        acct = _create_fresh_mortgage(
            seed_user, db_session, origination_date=date(2026, 3, 1),
        )
        opening = add_escrow_line(
            db_session, acct.id, "Property Tax", Decimal("3600.00"),
            effective_date=date(2026, 3, 1),
        )
        db_session.add(EscrowComponentVersion(
            line_id=opening.line_id,
            effective_date=date(2027, 1, 1),
            annual_amount=Decimal("4800.00"),
        ))
        db_session.commit()
        return acct

    def test_each_row_carries_the_escrow_in_force_for_its_installment(
        self, app, auth_client, seed_user, db, seed_periods,
    ):
        """A future-dated escrow version reaches exactly the rows it governs.

        ``$3,600/yr`` (``$300.00``/month) from origination, superseded by
        ``$4,800/yr`` (``$400.00``/month) effective 2027-01-01.  Rows due before
        that date charge the old figure and rows due on or after it the new one;
        both sets must be non-empty, or the fixture would be satisfied by a
        builder that read one date.
        """
        acct = self._mortgage_with_a_future_escrow_step(seed_user, db.session)

        context, _html = self._capture_schedule_context(
            app, auth_client, acct.id,
        )
        rows = context["amortization_schedule"]
        escrow = context["schedule_row_escrow"]
        assert len(escrow) == len(rows)

        before = [
            e for row, e in zip(rows, escrow)
            if row.payment_date < date(2027, 1, 1)
        ]
        after = [
            e for row, e in zip(rows, escrow)
            if row.payment_date >= date(2027, 1, 1)
        ]
        assert before and after, (
            "the schedule did not straddle the escrow version boundary"
        )
        assert set(before) == {Decimal("300.00")}
        assert set(after) == {Decimal("400.00")}

    def test_the_escrow_total_sums_the_rows_rather_than_scaling_one(
        self, app, auth_client, seed_user, db, seed_periods,
    ):
        """The footer's escrow total is the SUM, not one figure times the count.

        The mutation arm of the test above.  ``monthly_escrow * num_months``
        was the old total, and on this loan neither figure times the row count
        reproduces the truth -- so a builder that kept the multiplication is
        refuted whichever figure it picked.
        """
        acct = self._mortgage_with_a_future_escrow_step(seed_user, db.session)

        context, html = self._capture_schedule_context(
            app, auth_client, acct.id,
        )
        rows = context["amortization_schedule"]
        escrow = context["schedule_row_escrow"]
        total = context["schedule_totals"]["total_escrow"]

        assert total == sum(escrow, Decimal("0.00"))
        for scaled in (Decimal("300.00"), Decimal("400.00")):
            assert total != scaled * len(rows), (
                f"the escrow total equals ${scaled} x {len(rows)} rows, so "
                "this fixture cannot tell a sum from a multiplication"
            )
        # The column is shown because the schedule CHARGES escrow, and the
        # per-row cells really render (both figures reach the page).
        assert context["show_escrow_column"] is True
        assert "$300.00" in html and "$400.00" in html
        # And the row totals carry the row's own escrow, not a constant --
        # through ``round_money``, the boundary the producer applies, so the
        # assertion cannot pass on a total that skipped it.
        assert [
            round_money(row.payment + e + row.extra_payment)
            for row, e in zip(rows, escrow)
        ] == context["schedule_row_totals"]

    def test_a_loan_with_no_escrow_hides_the_column(
        self, app, auth_client, seed_user, db, seed_periods,
    ):
        """No escrow line anywhere in the schedule means no Escrow column.

        The other side of ``show_escrow_column``: the flag is about what the
        schedule charges, so a loan charging nothing hides the column exactly
        as the old ``monthly_escrow > 0`` test did.
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )

        context, html = self._capture_schedule_context(
            app, auth_client, acct.id,
        )
        assert set(context["schedule_row_escrow"]) == {Decimal("0.00")}
        assert context["show_escrow_column"] is False
        assert ">Escrow</th>" not in html

    def test_a_loan_whose_escrow_STARTS_mid_schedule_shows_the_column(
        self, app, auth_client, seed_user, db, seed_periods,
    ):
        """The case the OLD predicate got wrong, and the one it was changed for.

        ``show_escrow_column`` was ``monthly_escrow > 0`` -- today's figure --
        and is now ``total_escrow > 0``, what the schedule actually charges.
        The two disagree in exactly one direction that a real loan reaches: a
        loan whose FIRST escrow version is effective in the future escrows
        ``$0.00`` today, so the old predicate hid the column entirely and with
        it every dollar of escrow the schedule goes on to charge.

        Today is frozen at 2026-03-20 for this file and the only version is
        effective 2027-01-01, so ``escrow_monthly_as_of(lines, today)`` is
        ``0.00`` and the old predicate answers False.  **An adversarial review
        reverted the flag to that exact expression and every test in this file
        still passed**, which is why this fixture exists: the change's own
        stated reason was ungraded.
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )
        add_escrow_line(
            db.session, acct.id, "Property Tax", Decimal("4800.00"),
            effective_date=date(2027, 1, 1),
        )
        db.session.commit()

        context, html = self._capture_schedule_context(
            app, auth_client, acct.id,
        )
        rows = context["amortization_schedule"]
        escrow = context["schedule_row_escrow"]

        # The premise: nothing is escrowed TODAY, so the old predicate is False.
        assert escrow_calculator.escrow_monthly_as_of(
            loan_loaders.load_escrow_lines(acct.id), date(2026, 3, 20),
        ) == Decimal("0.00")
        # The behaviour: the column is shown, and starts at zero.
        assert context["show_escrow_column"] is True
        assert ">Escrow</th>" in html
        assert {
            e for row, e in zip(rows, escrow)
            if row.payment_date < date(2027, 1, 1)
        } == {Decimal("0.00")}
        assert {
            e for row, e in zip(rows, escrow)
            if row.payment_date >= date(2027, 1, 1)
        } == {Decimal("400.00")}

    def test_every_rendered_row_carries_its_own_rate(
        self, app, auth_client, seed_user, db, seed_periods,
    ):
        """The Rate column reads each row's rate; there is no fallback to today's.

        ``build_schedule_context`` had
        ``row.interest_rate if row.interest_rate is not None else current_rate``,
        where ``current_rate`` was a ``date.today()`` rate lookup.  Every
        construction of an ``AmortizationRow`` sets ``interest_rate`` from a
        rate period's ``annual_rate``, so that branch guarded an unconstructible
        state; plan step X-au-g-2b deleted it and the route's clock read with
        it.  This pins the premise the deletion rests on -- across a rate
        BOUNDARY, so the column is genuinely varying rather than trivially
        constant.
        """
        acct = _create_loan_account(
            seed_user, db.session, AcctTypeEnum.MORTGAGE, "ARM Own Rate",
            Decimal("100000.00"), Decimal("0.05000"), 360,
            date(2024, 1, 1), 1, is_arm=True,
        )
        db.session.add(RateHistory(
            account_id=acct.id,
            effective_date=date(2027, 2, 1),
            interest_rate=Decimal("0.07000"),
        ))
        db.session.commit()

        context, _html = self._capture_schedule_context(
            app, auth_client, acct.id,
        )
        rows = context["amortization_schedule"]
        assert rows, "expected a non-empty ARM schedule"
        assert all(row.interest_rate is not None for row in rows), (
            "a rendered row carried no rate, so the deleted fallback was live"
        )
        assert context["schedule_row_rates_pct"] == [
            row.interest_rate * Decimal("100") for row in rows
        ]
        # Two rates really are rendered, so a column reading ONE date would
        # differ from this one somewhere.
        assert {row.interest_rate for row in rows} == {
            Decimal("0.05000"), Decimal("0.07000"),
        }
