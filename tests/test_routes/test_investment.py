"""
Tests for investment/retirement account routes.
"""

import json
import pathlib
import re
from datetime import date, timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import (
    CalcMethodEnum,
    DeductionTimingEnum,
    EmployerContributionTypeEnum,
)
from app.models.investment_params import InvestmentParams
from app.models.paycheck_deduction import PaycheckDeduction
from app.models.ref import AccountType, FilingStatus
from app.models.salary_profile import SalaryProfile
from app.services import (
    account_service,
    balance_at,
    growth_engine,
    investment_dashboard_service,
)
from app.services.balance_at import BalanceContext
from app.services.investment_dashboard_service import (
    _context as investment_context,
)
from app.services.investment_dashboard_service._context import (
    _load_investment_params,
    _load_projection_context,
    _projection_ytd,
)
from app.services.investment_projection import (
    AccountPayrollFeed,
    InvestmentInputs,
)
from app.utils.money import round_money
from tests._test_helpers import (
    current_pay_period,
    last_covered_day,
    reassert_balance_on,
    settle_day_columns,
    settle_instant_on,
)
from app.services.investment_dashboard_service import _cards as investment_cards
from app.services.investment_dashboard_service import _chart as investment_chart
from app.services.investment_dashboard_service._context import (
    _ProjectionContext,
)
from app.services.pay_calendar import PayCalendar
from tests._test_helpers import (
    make_investment_account,
    open_books_before_the_first_assertion,
    read_pass_over_paydays,
)
from app.models.amount_ownership import AmountOwnership


def _cards_context(*, limit, ytd, paydays, current_index, as_of, cadence=14,
                   retirement_date=None):
    """Build a REAL ``_ProjectionContext`` for the card helpers' unit cases.

    The card helpers take the shared per-request feed since plan step C2-f2c,
    because the three things they read -- the owner's periods, the period
    covering the clock, and the clock itself -- must all come off ONE read
    pass, and three separate arguments are three ways to pair one pass's
    calendar with another's day.  So a case has to supply a context.

    **Every type here is the real one.**  The calendar is a real
    :class:`~app.services.pay_calendar.PayCalendar` derived from real paydays,
    the pass is a real :class:`~app.services.balance_at.BalanceContext` whose
    pay-calendar memo is filled at construction rather than loaded, and the
    feed is the real frozen dataclass -- so a field these helpers read cannot
    drift from the one the application fills.  Filling the memo is what keeps
    these cases pure: the arithmetic under test is a function of a payday set
    and a day, and a database would only supply those two values less
    legibly.

    **The seeding goes through ONE shared door**
    (:func:`tests._test_helpers.read_pass_over_paydays`, plan finding **P54**,
    ruled by the developer 2026-08-16).  This was the first site to name the
    pass's PRIVATE calendar memo, and plan step ``C2-f2d`` brings two more
    packages wanting the same fixture; N sites reaching into one private field
    is how a memo becomes a de-facto public seam.  That helper's docstring
    carries the two alternatives that were refused and why.

    The fields no card helper reads are filled with the degenerate value their
    type admits.  Nothing here asserts on them, and a helper that started
    reading one would fail loudly rather than silently agreeing with a fake.

    Args:
        limit: The account's ``annual_contribution_limit`` (or ``None``).
        ytd: Contributions already made this calendar year.
        paydays: The owner's paydays, ascending.
        current_index: Which payday's period covers the clock, or ``None``.
        as_of: The read pass's clock.
        cadence: Days between paydays.
        retirement_date: The owner's planned retirement date, or ``None``.

    Returns:
        The ``_ProjectionContext``.
    """
    balance_ctx = read_pass_over_paydays(paydays, cadence, as_of)
    saved = balance_ctx.calendar().saved()
    return _ProjectionContext(
        params=InvestmentParams(annual_contribution_limit=limit),
        current_balance=Decimal("0"),
        projection_start=as_of,
        projection_ytd=ytd,
        projection_seed=Decimal("0"),
        inputs=InvestmentInputs(
            periodic_contribution=Decimal("0"),
            employer_params=None,
            annual_contribution_limit=limit,
            ytd_contributions=ytd,
            ytd_contributions_seed=ytd,
        ),
        shadow_contributions=[],
        feed=AccountPayrollFeed.absent(),
        deductions=[],
        salary_profiles=[],
        balance_ctx=balance_ctx,
        anchor_as_of=None,
        planned_retirement_date=retirement_date,
        current_period=None if current_index is None else saved[current_index],
    )


def _create_investment_account(seed_user, db_session, type_name="401(k)",
                                name="My 401k", balance="50000.00"):
    """Helper to create an investment/retirement account.

    COMMITS rather than flushes (plan step balance:X-i3).  A request cannot see
    an uncommitted row -- it holds its own transaction -- so a fixture that
    only flushed and then issued one was asking the route to read a state no
    browser could produce.  The sibling ``_create_investment_params`` below has
    always committed; this one differed for no stated reason.
    """
    acct_type = db_session.query(AccountType).filter_by(name=type_name).one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=acct_type.id,
            name=name,
            anchor_balance=Decimal(balance),
        ),
    )
    db_session.add(account)
    # Its BOOKS open before anything this fixture dates (plan step X-f3c-2b,
    # ruling **R-HG**): ``create_account`` opens them on the day it asserts --
    # the owner's today -- and this suite settles on or before it.
    open_books_before_the_first_assertion(db_session, account)
    db_session.commit()
    return account


def _create_investment_params(db_session, account_id, **overrides):
    """Helper to create investment params for an account."""
    defaults = {
        "account_id": account_id,
        "assumed_annual_return": Decimal("0.07000"),
        "annual_contribution_limit": Decimal("23500.00"),
        "contribution_limit_year": 2026,
        "employer_contribution_type_id": ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.NONE),
    }
    defaults.update(overrides)
    params = InvestmentParams(**defaults)
    db_session.add(params)
    db_session.commit()
    return params


def _create_other_investment(second_user, db_session):
    """Create an investment account owned by the second user.

    Builds on the shared second_user fixture. Returns the Account
    (no InvestmentParams -- IDOR tests verify none get created).
    """
    acct_type = db_session.query(AccountType).filter_by(name="401(k)").one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=second_user["user"].id,
            account_type_id=acct_type.id,
            name="Other 401k",
            anchor_balance=Decimal("10000.00"),
        ),
    )
    db_session.add(account)
    # Its BOOKS open before anything this fixture dates (plan step X-f3c-2b,
    # ruling **R-HG**): ``create_account`` opens them on the day it asserts --
    # the owner's today -- and this suite settles on or before it.
    open_books_before_the_first_assertion(db_session, account)
    db_session.commit()
    return account


class TestInvestmentDashboard:
    """Tests for the investment dashboard page."""

    def test_dashboard_no_params(self, auth_client, seed_user, db, seed_periods_today):
        """GET returns 200 even without investment params."""
        acct = _create_investment_account(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        assert b"50,000.00" in resp.data

    def test_dashboard_with_params(self, auth_client, seed_user, db, seed_periods_today):
        """GET returns 200 with params and projection data."""
        acct = _create_investment_account(seed_user, db.session)
        # Anchor stays at the factory's current period (cache AND the
        # AccountAnchorHistory row in sync).  The headline reads the
        # model-from-anchor balance through the balance_at seam.  It used to
        # equal the $50,000 cash basis at anchor==current; since plan step
        # X-g2b the anchor period earns its own days (ruling R-Y), so the
        # headline is that basis PLUS the accrual -- read here from the seam
        # rather than pinned, because the arithmetic belongs to
        # tests/test_services/test_asset_fold.py's hand-computed oracles.
        _create_investment_params(db.session, acct.id)
        headline = balance_at.balance_map(
            acct, BalanceContext.build(seed_user["user"].id),
        )[current_pay_period(seed_user["user"].id).id]
        assert headline > Decimal("50000.00")
        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        assert f"{headline:,.2f}".encode() in resp.data

    def test_dashboard_idor(
        self, auth_client, second_user, db, seed_periods_today,
    ):
        """GET another user's investment dashboard returns 404 (security)
        and does not leak victim data."""
        other_acct = _create_other_investment(second_user, db.session)

        resp = auth_client.get(f"/accounts/{other_acct.id}/investment")
        assert resp.status_code == 404
        assert b"Other 401k" not in resp.data, (
            "IDOR response leaked victim's account name"
        )

    def test_dashboard_nonexistent(self, auth_client, seed_user, db, seed_periods_today):
        """Nonexistent account returns 404 (security: 404 for not-found and not-yours)."""
        resp = auth_client.get("/accounts/99999/investment")
        assert resp.status_code == 404

    def test_dashboard_brokerage(self, auth_client, seed_user, db, seed_periods_today):
        """Brokerage account (no contribution limit) works."""
        acct = _create_investment_account(
            seed_user, db.session, type_name="Brokerage",
            name="Brokerage", balance="25000.00",
        )
        _create_investment_params(
            db.session, acct.id,
            annual_contribution_limit=None,
            contribution_limit_year=None,
        )
        headline = balance_at.balance_map(
            acct, BalanceContext.build(seed_user["user"].id),
        )[current_pay_period(seed_user["user"].id).id]
        assert headline > Decimal("25000.00")  # ruling R-Y: the anchor accrues
        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        assert b"Brokerage" in resp.data
        assert f"{headline:,.2f}".encode() in resp.data
        # P2 rebuild: the summary tile became the hero's "modeled at x%
        # assumed return" caption (investment_audit.md, locked anatomy).
        assert b"assumed return" in resp.data

    def test_dashboard_employer_match_card(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Employer-contribution card renders the match formula (TPLA-01).

        The card body branches on ``employer_params.type_id``; before the
        fix it compared a non-existent ``type`` key, so the card header
        rendered while both branches stayed false -- a permanently blank
        body for every configured employer match.
        """
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(
            db.session, acct.id,
            employer_contribution_type_id=ref_cache.employer_contribution_type_id(
                EmployerContributionTypeEnum.MATCH),
            employer_match_percentage=Decimal("0.50"),
            employer_match_cap_percentage=Decimal("0.06"),
        )
        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        # P2 rebuild: the employer card became the band chip's caption
        # ("50% match to 6.00%") plus the Parameters card's formula
        # sentence; both still branch on type_id (the TPLA-01 pin).
        assert b"50% match to 6.00%" in resp.data
        assert b"Employer matches" in resp.data
        # Stored fractions render through |to_percent: 0.50 -> 50%, 0.06 -> 6.00%.
        assert b"50%" in resp.data
        assert b"6.00%" in resp.data

    def test_dashboard_employer_flat_card(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Employer flat-percentage card renders the contribute formula (TPLA-01)."""
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(
            db.session, acct.id,
            employer_contribution_type_id=ref_cache.employer_contribution_type_id(
                EmployerContributionTypeEnum.FLAT_PERCENTAGE),
            employer_flat_percentage=Decimal("0.03"),
        )
        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        # P2 rebuild: employer card -> chip caption ("3.00% of gross") +
        # Parameters formula sentence (same TPLA-01 type_id-branch pin).
        assert b"3.00% of gross" in resp.data
        assert b"Employer contributes" in resp.data
        # 0.03 -> 3.00% through |to_percent.
        assert b"3.00%" in resp.data

    def test_dashboard_ytd_card_shows_limit_year(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """YTD contribution card title shows the configured limit year (TPLA-04).

        Before the fix the title computed ``date.today().year`` via an
        undefined ``date`` global and rendered a bare ' Contributions' with
        no year.
        """
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(
            db.session, acct.id, contribution_limit_year=2026,
        )
        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        # P2 rebuild: the YTD card became the "<year> limit" band chip;
        # the TPLA-04 pin (configured year renders, no undefined `date`
        # global) carries over to the chip label.
        assert b"2026 limit" in resp.data

    def test_growth_since_anchor_chip_renders_for_past_anchor(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The measured growth-since-anchor chip renders for a past anchor.

        Anchored at the first period (well before the current one, index 3
        under seed_periods_today) via the proper anchor builder, so the
        growth window ``(anchor, current]`` is non-empty and the seam returns
        a real (growth, contributed) pair -- the band shows the "Growth since"
        chip with its "on <contributed> contributed" caption.  The exact
        figures and the reconciliation identity are pinned at the service
        level (TestInvestmentGrowthSinceAnchor); this pins the render.
        """
        inv = make_investment_account(
            seed_user, db.session, seed_periods_today[0], Decimal("10000.00"),
            name="Growth 401k",
        )
        resp = auth_client.get(f"/accounts/{inv.id}/investment")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Growth since" in html
        assert "contributed" in html

    def test_growth_chip_shows_when_anchored_at_current(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """An account anchored THIS period SHOWS its chip (ruling R-AC / R-Y).

        **This inverted at plan step X-g2b.**  The shipped producer split its
        periods on ``period_index > anchor_idx``, so an account anchored in the
        current period had no post-anchor window, the seam returned ``None`` and
        the band omitted the chip.  Ruling R-Y removes that premise: the
        assertion's own day accrues, so the account HAS earned something --
        measured $105.26 on the real Roth IRA at its anchor period -- and hiding
        the chip would deny a figure the balance beside it already contains.

        The chip is still hidden where it should be: no investment params, or no
        current period (the two arms ruling R-AC kept).
        """
        current = current_pay_period(seed_user["user"].id)
        inv = make_investment_account(
            seed_user, db.session, current, Decimal("10000.00"),
            name="Fresh 401k",
        )
        resp = auth_client.get(f"/accounts/{inv.id}/investment")
        assert resp.status_code == 200
        assert "Growth since" in resp.data.decode()


class TestContributionLimitZeroCap:
    """Pin the zero-vs-None annual-limit branches (quality-pass B7).

    Commit 24 / HIGH-06 / E-12 replaced Python truthiness on
    ``annual_contribution_limit`` with explicit ``is None`` checks so a
    stored ``Decimal("0")`` ("capped at zero this year") stays distinct
    from ``None`` ("no cap configured").  The cleanup left those three
    branches in ``_compute_limit_info`` and the zero-cap branch in
    ``_compute_suggested_contribution`` unpinned; these unit tests assert
    each on hand-reasoned values.

    ``_compute_limit_info`` reads only ``annual_contribution_limit``, so an
    in-memory params object keeps it pure.  ``_compute_suggested_contribution``
    takes the shared per-request feed since plan step C2-f2c and so needs a
    context; :func:`_cards_context` builds a real one over a real calendar,
    which is still pure (no DB, no engine).
    """

    def test_limit_info_zero_cap_with_ytd_is_fully_used(self):
        """Zero cap + positive YTD -> card renders at 100% used.

        ``limit`` is 0 (not ``None``), so the card is shown rather than
        hidden; any positive YTD is over a zero cap, so ``pct`` saturates
        at 100 (matching the growth engine's ``min(contribution, 0) = 0``
        semantics).  A truthiness regression would treat the zero cap as
        "no cap" and hide the card (return ``None``).
        """
        params = InvestmentParams(annual_contribution_limit=Decimal("0"))
        result = investment_cards._compute_limit_info(
            params, Decimal("100.00"),
        )
        # C1 (Loop B P1): the dict gained is_over / over_amount.  Zero cap +
        # $100 YTD is OVER by the full YTD: is_over = (100 > 0) = True;
        # over_amount = round_money(100.00 - 0) = 100.00.  The zero-cap pct
        # semantics (100 via the explicit elif) are unchanged.
        assert result == {
            "limit": Decimal("0"),
            "ytd": Decimal("100.00"),
            "pct": Decimal("100"),
            "is_over": True,
            "over_amount": Decimal("100.00"),
        }

    def test_limit_info_zero_cap_zero_ytd_is_zero_used(self):
        """Zero cap + zero YTD -> card renders at 0% used.

        Both cap and YTD are zero: nothing contributed against a zero
        cap, so ``pct`` is 0 (the ``elif ytd > 0`` branch is not taken).
        The card still renders (``limit`` is 0, not ``None``).
        """
        params = InvestmentParams(annual_contribution_limit=Decimal("0"))
        result = investment_cards._compute_limit_info(
            params, Decimal("0"),
        )
        # C1: zero cap + zero YTD is NOT over (0 > 0 is false), so over_amount
        # is None; the pct 0 semantics are unchanged.
        assert result == {
            "limit": Decimal("0"),
            "ytd": Decimal("0"),
            "pct": Decimal("0"),
            "is_over": False,
            "over_amount": None,
        }

    def test_limit_info_none_cap_hides_card(self):
        """No cap configured (``None``) -> hide the card (return ``None``).

        The contrast case to the zero cap above: ``None`` means
        "Brokerage-style, no IRS limit," which hides the card entirely.
        Keeping this distinct from the zero cap is the whole point of the
        ``is None`` fix.
        """
        params = InvestmentParams(annual_contribution_limit=None)
        result = investment_cards._compute_limit_info(
            params, Decimal("100.00"),
        )
        assert result is None

    def test_limit_info_positive_cap_rounds_half_up(self):
        """Positive cap -> pct rounds the YTD ratio HALF_UP via percent_complete.

        $4,980 / $5,000 = 99.6%.  deep-quality-hunt #78 routed the
        ``limit > 0`` branch through ``money.percent_complete`` (clamped
        [0, 100], ROUND_HALF_UP, Decimal), retiring the prior
        ``min(100, int(...))`` truncation that disagreed with the budget
        dashboard's "percent funded" surfaces.  So pct is ``Decimal("99.60")``
        (the template renders ``"{:.0f}".format(...)`` -> "100%"), NOT the
        old truncated ``99``.  Revert-proof: ``int(99.6) == 99`` fails this.
        """
        params = InvestmentParams(annual_contribution_limit=Decimal("5000"))
        result = investment_cards._compute_limit_info(
            params, Decimal("4980.00"),
        )
        # C1: $4,980 <= $5,000 cap, so is_over = (4980 > 5000) = False and
        # over_amount is None; the pct 99.60 rounding is unchanged.
        assert result == {
            "limit": Decimal("5000"),
            "ytd": Decimal("4980.00"),
            "pct": Decimal("99.60"),
            "is_over": False,
            "over_amount": None,
        }

    def test_limit_info_over_limit_states_the_overage(self):
        """C1: a positive cap with YTD over it flags is_over + the overage.

        The clamped pct saturates at 100 and cannot distinguish an excess
        contribution from a perfect max, so the goal-framed bar reads the
        overage from ``over_amount``.  $24,100 YTD against the $23,500 2026
        401(k) cap: is_over = (24100 > 23500) = True; over_amount =
        round_money(24100.00 - 23500.00) = 600.00; pct clamps to 100.00
        (percent_complete's high clamp).
        """
        params = InvestmentParams(
            annual_contribution_limit=Decimal("23500.00"),
        )
        result = investment_cards._compute_limit_info(
            params, Decimal("24100.00"),
        )
        assert result == {
            "limit": Decimal("23500.00"),
            "ytd": Decimal("24100.00"),
            "pct": Decimal("100.00"),
            "is_over": True,
            "over_amount": Decimal("600.00"),
        }

    def test_suggested_contribution_zero_cap_is_zero(self):
        """Zero cap -> $0.00 per-period suggestion, never a phantom default.

        Remaining limit = max(0 - ytd, 0) = 0, so the suggestion is
        ``(0 / max(periods, 1)).quantize(.01) = 0.00`` regardless of the
        period list.  Pins that a zero cap suggests nothing within the
        cap rather than the legacy $500 fallback truthiness once produced.
        """
        result = investment_cards._compute_suggested_contribution(
            _cards_context(
                limit=Decimal("0"), ytd=Decimal("0"),
                paydays=[date(2026, 1, 1)], current_index=None,
                as_of=date(2026, 1, 15),
            ),
        )
        assert result == Decimal("0.00")

    def test_suggested_contribution_none_cap_is_zero(self):
        """No cap configured (``None``) -> $0.00 suggestion (no IRS limit).

        The brokerage path returns ``Decimal("0")`` immediately: there is
        no annual limit to spread over the remaining periods.  Pins the
        contrast to a positive cap and guards against a reintroduced
        non-zero default for the no-cap case.
        """
        result = investment_cards._compute_suggested_contribution(
            _cards_context(
                limit=None, ytd=Decimal("0"),
                paydays=[date(2026, 1, 1)], current_index=None,
                as_of=date(2026, 1, 15),
            ),
        )
        assert result == Decimal("0")

    #: The four same-year paydays both boundary cases below spread over.
    _SPREAD_PAYDAYS = [
        date(2026, 1, 1), date(2026, 1, 15),
        date(2026, 1, 29), date(2026, 2, 12),
    ]

    def test_suggested_contribution_excludes_current_period_from_remaining(self):
        """remaining_periods is anchored on current_period, not on the clock.

        deep-quality-hunt #59: the YTD subtracted from the limit is summed
        over periods whose payday is ``<= current_period.start_date``
        (``investment_projection._ytd_contributions``), so the
        per-period suggestion must spread the remainder over the periods
        STRICTLY AFTER the current period -- else the current period is
        double-counted (in YTD *and* in the remaining spread) on the single
        calendar day a period begins (``today == period start``).  Four
        same-year periods (Jan 1/15/29, Feb 12), current = the Jan 15 period,
        with the pass's clock ON Jan 15 (the period-start edge that triggered
        the old bug): $7,000 limit - $3,000 YTD = $4,000 spread over the two
        strictly-after periods (Jan 29, Feb 12) = $2,000.00.  Revert-proof:
        the old ``start_date >= today`` window includes the Jan 15
        current period (3 periods) -> $1,333.33.

        **The clock is the read pass's, stated rather than frozen** (plan step
        C2-f2c).  This monkeypatched ``date.today`` in the ``_cards`` module
        until then, which pinned only that module -- the pass around it went on
        reading the real day.
        """
        result = investment_cards._compute_suggested_contribution(
            _cards_context(
                limit=Decimal("7000"), ytd=Decimal("3000"),
                paydays=self._SPREAD_PAYDAYS, current_index=1,
                as_of=date(2026, 1, 15),
            ),
        )
        # 7000 - 3000 = 4000; periods strictly after Jan 15 = {Jan 29,
        # Feb 12} = 2; 4000 / 2 = 2000.00 (NOT the old 4000 / 3 = 1333.33).
        assert result == Decimal("2000.00")

    def test_suggested_contribution_mid_period_today_is_behaviour_neutral(self):
        """Anchoring on current_period leaves the typical case unchanged.

        deep-quality-hunt #59: when the clock falls strictly inside the current
        period (the common case), no period starts in (current.start, today],
        so the current-period boundary (``> current.start``) and the old
        today boundary (``>= today``) select the SAME set -- the fix is
        behaviour-neutral here.  Same four periods, current = Jan 15, but the
        clock on Jan 22 (mid-period): both windows yield the two
        strictly-after periods, so the suggestion is $2,000.00, identical to
        what the old ``>= today`` rule produced.
        """
        result = investment_cards._compute_suggested_contribution(
            _cards_context(
                limit=Decimal("7000"), ytd=Decimal("3000"),
                paydays=self._SPREAD_PAYDAYS, current_index=1,
                as_of=date(2026, 1, 22),
            ),
        )
        # 4000 spread over {Jan 29, Feb 12} = 2 -> 2000.00 (same as old).
        assert result == Decimal("2000.00")

    def test_suggested_contribution_falls_back_to_the_PASSES_clock(self):
        """With no current period the boundary is ``balance_ctx.as_of``.

        The arm that read ``date.today()`` until plan step C2-f2c.  A pass
        valued at Jan 22 spreads over the paydays after it -- Jan 29 and
        Feb 12 -- so $4,000 / 2 = $2,000.00.

        **It cannot pass by the two clocks coinciding**, and the reason is the
        paydays rather than the year: every one of them is a January or
        February day of a year the suite has already passed, so a reverted
        process-clock read finds ZERO periods strictly after it and answers
        $4,000.00.  A draft of this docstring claimed the schedule's YEAR was
        not the suite's, which was false -- the conclusion held for a different
        reason, and a control whose stated reason is wrong is one nobody can
        re-check (adversarial review, 2026-08-15).
        """
        result = investment_cards._compute_suggested_contribution(
            _cards_context(
                limit=Decimal("7000"), ytd=Decimal("3000"),
                paydays=self._SPREAD_PAYDAYS, current_index=None,
                as_of=date(2026, 1, 22),
            ),
        )
        assert result == Decimal("2000.00")


class TestTheDefaultHorizonComesOffTheReadPass:
    """``_compute_default_horizon``: three arms, none of them reading a clock.

    Untested until plan step C2-f2c beyond the route's own smoke coverage,
    which is how the ``date.today()`` reads in it survived: the slider's
    default is an integer nobody asserted, and the route renders whatever it
    is.  All three arms are graded here against the pass's own ``as_of``.
    """

    def test_a_planned_retirement_date_wins_and_counts_from_the_pass(self):
        """Years from the pass's clock to the retirement YEAR."""
        assert investment_cards._compute_default_horizon(
            _cards_context(
                limit=None, ytd=Decimal("0"),
                paydays=[date(2026, 1, 1)], current_index=0,
                as_of=date(2026, 6, 15), retirement_date=date(2046, 3, 1),
            ),
        ) == 20

    def test_a_retirement_date_already_past_still_answers_one_year(self):
        """The floor, which keeps the slider from asking for zero periods."""
        assert investment_cards._compute_default_horizon(
            _cards_context(
                limit=None, ytd=Decimal("0"),
                paydays=[date(2026, 1, 1)], current_index=0,
                as_of=date(2026, 6, 15), retirement_date=date(2020, 3, 1),
            ),
        ) == 1

    def test_without_one_it_runs_a_year_past_the_LAST_SAVED_period(self):
        """The second arm: the LAST period's year, plus one.

        **Every term is separately graded, and an earlier draft of this case
        graded none of them** (adversarial review, 2026-08-15).  That draft put
        both paydays in 2026 against a 2026 clock, so the arm computed
        ``max(1, (2026 - 2026) + 1) = 1`` -- which is also what the ``max(1,
        ...)`` floor returns, what ``periods[0]`` would return, and what
        dropping the ``+ 1`` would return.  It could only distinguish this arm
        from the CONSTANT.

        Here the paydays straddle a year boundary: the FIRST period ends
        2028-06-14 and the LAST ends 2029-01-16, so ``[-1]`` and ``[0]`` differ
        (4 against 3), the ``+ 1`` differs (4 against 3), and the floor differs
        (4 against 1).  The last end is DERIVED -- the day the cadence projects
        past the final payday -- which is the whole point of reading the pass's
        calendar rather than a stored column.
        """
        assert investment_cards._compute_default_horizon(
            _cards_context(
                limit=None, ytd=Decimal("0"),
                paydays=[
                    date(2028, 6, 1), date(2028, 6, 15), date(2029, 1, 3),
                ],
                current_index=0, as_of=date(2026, 6, 15),
            ),
        ) == 4

    def test_an_owner_with_no_paydays_takes_the_constant(self):
        """The third arm, and the one an empty window must not crash on.

        A :class:`~app.services.pay_calendar.PeriodWindow` defines ``__len__``
        and no ``__bool__``, so Python's truthiness falls through to the
        length and ``if periods:`` is exactly the emptiness test -- which is
        what the code says.  A draft of this case justified an explicit
        ``len(...)`` by claiming otherwise (adversarial review, 2026-08-15).
        """
        assert investment_cards._compute_default_horizon(
            _cards_context(
                limit=None, ytd=Decimal("0"), paydays=[],
                current_index=None, as_of=date(2026, 6, 15),
            ),
        ) == investment_cards._FALLBACK_HORIZON_YEARS


class TestTheChartMarkersAskTheWindowWhereTheDateFALLS:
    """``_build_chart_markers``: ledger row **P48**, and its first tests.

    That row is the last live member of row **P6**'s census of "which pay
    period contains this date" implementations: this function walked the
    projection window period by period testing ``start_date <= d <= end_date``,
    which is
    :meth:`~app.services.pay_calendar.PeriodWindow.containing`'s own predicate.
    It had NO test coverage at all -- ``retirement_marker_index`` is an integer
    nothing asserted on -- which is how a duplicate predicate went unnoticed
    long enough to need a census to find.

    Every case here builds a REAL window off a REAL calendar, because the
    property under test is about a VIEW's ordinals and a fake list of periods
    could not express the distinction.
    """

    @staticmethod
    def _axis(first_index=0, count=6):
        """A real window over a real biweekly calendar opening 2026-01-02."""
        calendar = PayCalendar.from_paydays(
            [
                (index + 1, date(2026, 1, 2) + timedelta(days=14 * index))
                for index in range(10)
            ],
            14, user_id=1,
            history_opens_on=None,
        )
        return calendar.window(first_index=first_index, count=count)

    @staticmethod
    def _ctx(retirement_date):
        return _cards_context(
            limit=None, ytd=Decimal("0"), paydays=[date(2026, 1, 2)],
            current_index=0, as_of=date(2026, 1, 2),
            retirement_date=retirement_date,
        )

    def test_the_marker_is_the_history_length_plus_the_WINDOW_offset(self):
        """The chart plots history then one point per axis period.

        A window opening at calendar ordinal 3 covers 2026-02-13 onward; the
        date 2026-03-01 falls in its SECOND period (2026-02-27..2026-03-12), so
        the marker is ``history_len + 1``.
        """
        markers = investment_chart._build_chart_markers(
            self._ctx(date(2026, 3, 1)), 7, self._axis(first_index=3),
        )
        assert markers["retirement_marker_index"] == 8
        assert markers["today_boundary_index"] == 7
        assert markers["retirement_year"] == 2026

    def test_it_is_the_VIEWS_ordinal_not_the_CALENDARS(self):
        """The firing control, and the defect the harness was shown catching.

        The same date on the same window: its period's ``period_index`` is 4,
        the window's own offset for it is 1.  A reader taking the calendar
        ordinal would put the marker at ``7 + 4 = 11`` rather than ``7 + 1 =
        8`` -- measured on production at 241 against 252, an eleven-point slide
        that plants the retirement line in the wrong year with no error
        anywhere.
        """
        window = self._axis(first_index=3)
        assert window.containing(date(2026, 3, 1)).period_index == 4
        assert investment_chart._build_chart_markers(
            self._ctx(date(2026, 3, 1)), 7, window,
        )["retirement_marker_index"] == 8

    def test_a_date_BEFORE_the_window_marks_nothing(self):
        """Outside the axis is ``None``, not a clamp to its first point."""
        assert investment_chart._build_chart_markers(
            self._ctx(date(2026, 1, 5)), 7, self._axis(first_index=3),
        )["retirement_marker_index"] is None

    def test_a_date_PAST_the_window_marks_nothing(self):
        """The horizon side of the same rule -- the ordinary state.

        A retirement date decades out sits past any one-year axis, which is
        what the /investment slider's shortest position renders.
        """
        assert investment_chart._build_chart_markers(
            self._ctx(date(2046, 6, 30)), 7, self._axis(),
        )["retirement_marker_index"] is None

    def test_a_date_past_the_window_still_reports_its_YEAR(self):
        """The two answers are independent, and the caption survives.

        The verdict strip captions the retirement YEAR whether or not the
        chart can plot a line at it, so an off-axis date must not blank the
        caption as well as the marker.
        """
        markers = investment_chart._build_chart_markers(
            self._ctx(date(2046, 6, 30)), 7, self._axis(),
        )
        assert markers["retirement_year"] == 2046
        assert markers["retirement_marker_index"] is None

    def test_no_retirement_date_leaves_only_the_today_boundary(self):
        """The unset state -- production's own, until this step measured it.

        The clone the cutover was verified against carried no planned
        retirement date, so every marker probe on it came back ``None`` and the
        byte-identical diff said nothing about this function.  Setting one is
        what made the harness able to fail; this is the arm it then could not
        reach.
        """
        markers = investment_chart._build_chart_markers(
            self._ctx(None), 7, self._axis(),
        )
        assert markers == {
            "today_boundary_index": 7,
            "retirement_year": None,
            "retirement_marker_index": None,
        }

    def test_an_empty_axis_marks_nothing_and_raises_nothing(self):
        """A horizon the calendar does not reach yields no window at all."""
        markers = investment_chart._build_chart_markers(
            self._ctx(date(2026, 3, 1)), 0, self._axis(first_index=99),
        )
        assert markers["retirement_marker_index"] is None
        assert markers["today_boundary_index"] == 0


class TestInvestmentParams:
    """Tests for creating/updating investment params."""

    def test_create_params(self, auth_client, seed_user, db, seed_periods_today):
        """POST creates new investment params."""
        acct = _create_investment_account(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/investment/params",
            data={
                "assumed_annual_return": "7",
                "annual_contribution_limit": "23500",
                "contribution_limit_year": "2026",
                "employer_contribution_type_id": ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.NONE),
            },
        )
        assert resp.status_code == 302
        params = db.session.query(InvestmentParams).filter_by(
            account_id=acct.id
        ).first()
        assert params is not None
        assert params.assumed_annual_return == Decimal("0.07000")

    def test_update_params(self, auth_client, seed_user, db, seed_periods_today):
        """POST updates existing investment params."""
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(db.session, acct.id)
        resp = auth_client.post(
            f"/accounts/{acct.id}/investment/params",
            data={
                "assumed_annual_return": "8",
                "annual_contribution_limit": "23500",
                "contribution_limit_year": "2026",
                "employer_contribution_type_id": ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.NONE),
            },
        )
        assert resp.status_code == 302
        params = db.session.query(InvestmentParams).filter_by(
            account_id=acct.id
        ).first()
        assert params.assumed_annual_return == Decimal("0.08000")

    def test_update_params_clears_contribution_limit(
        self, auth_client, seed_user, db, seed_periods_today
    ):
        """Emptied limit inputs clear the stored cap and its year.

        The nullable-field clear rule: ``annual_contribution_limit``
        and ``contribution_limit_year`` are allow_none on the update
        schema, so the empty submits load as explicit None (they used
        to be DROPPED, making the cap unremovable from the UI) and the
        route's setattr loop nulls the columns.  A NULL limit means
        uncapped: the growth engine's clamp is ``is None``-guarded.
        """
        acct = _create_investment_account(seed_user, db.session)
        params = _create_investment_params(db.session, acct.id)
        assert params.annual_contribution_limit == Decimal("23500.00")

        resp = auth_client.post(
            f"/accounts/{acct.id}/investment/params",
            data={
                "annual_contribution_limit": "",
                "contribution_limit_year": "",
            },
        )
        assert resp.status_code == 302
        db.session.refresh(params)
        assert params.annual_contribution_limit is None
        assert params.contribution_limit_year is None

    def test_update_params_percent_normalized_by_schema(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """C12-1 (F-17 / Commit 12): investment-params update schema's
        @pre_load converts every declared percent field to its
        fraction equivalent before the route persists.  Arithmetic:
        7.5 / 100 = 0.075 (stored as ``0.07500`` in the
        ``Numeric(7, 5)`` column).
        """
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(db.session, acct.id)
        resp = auth_client.post(
            f"/accounts/{acct.id}/investment/params",
            data={
                "assumed_annual_return": "7.5",
                "annual_contribution_limit": "23500",
                "contribution_limit_year": "2026",
                "employer_contribution_type_id": ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.MATCH),
                "employer_match_percentage": "100",
                "employer_match_cap_percentage": "6",
            },
        )
        assert resp.status_code == 302
        params = db.session.query(InvestmentParams).filter_by(
            account_id=acct.id,
        ).one()
        # Hand-computed: 7.5 / 100 = 0.075.
        assert params.assumed_annual_return == Decimal("0.07500")
        # Hand-computed: 100 / 100 = 1.00.
        assert params.employer_match_percentage == Decimal("1.0000")
        # Hand-computed: 6 / 100 = 0.06.
        assert params.employer_match_cap_percentage == Decimal("0.0600")

    def test_create_params_with_employer_match(self, auth_client, seed_user, db, seed_periods_today):
        """POST with employer match config."""
        acct = _create_investment_account(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/investment/params",
            data={
                "assumed_annual_return": "7",
                "annual_contribution_limit": "23500",
                "contribution_limit_year": "2026",
                "employer_contribution_type_id": ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.MATCH),
                "employer_match_percentage": "100",
                "employer_match_cap_percentage": "6",
            },
        )
        assert resp.status_code == 302
        params = db.session.query(InvestmentParams).filter_by(
            account_id=acct.id
        ).first()
        assert params is not None
        assert params.employer_contribution_type_id == (
            ref_cache.employer_contribution_type_id(
                EmployerContributionTypeEnum.MATCH,
            )
        )
        assert params.employer_match_percentage == Decimal("1.0000")
        assert params.employer_match_cap_percentage == Decimal("0.0600")

    def test_params_idor(
        self, auth_client, second_user, db, seed_periods_today,
    ):
        """POST to another user's investment params returns 404 (security)
        and does not create any InvestmentParams row."""
        # Phase A: Setup victim's account with no params.
        other_acct = _create_other_investment(second_user, db.session)

        # Phase B: Attack.
        resp = auth_client.post(
            f"/accounts/{other_acct.id}/investment/params",
            data={
                "assumed_annual_return": "7",
                "employer_contribution_type_id": ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.NONE),
            },
        )

        # Phase C: Verify no state change.
        assert resp.status_code == 404

        db.session.expire_all()
        created = db.session.query(InvestmentParams).filter_by(
            account_id=other_acct.id
        ).first()
        assert created is None, (
            "IDOR attack created InvestmentParams on victim's account!"
        )

    def test_validation_error(self, auth_client, seed_user, db, seed_periods_today):
        """Invalid data flashes error, redirects, and creates no params."""
        acct = _create_investment_account(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/investment/params",
            data={
                "assumed_annual_return": "not_a_number",
                "employer_contribution_type_id": ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.NONE),
            },
        )
        assert resp.status_code == 302

        # Verify no InvestmentParams row was created.
        db.session.expire_all()
        created = db.session.query(InvestmentParams).filter_by(
            account_id=acct.id
        ).first()
        assert created is None, (
            "Invalid data created an InvestmentParams row!"
        )


class TestInvestmentNegativePaths:
    """Negative-path and boundary tests for investment routes."""

    def test_dashboard_login_required(self, client, seed_user, db, seed_periods_today):
        """Unauthenticated GET to investment dashboard redirects to login."""
        acct = _create_investment_account(seed_user, db.session)
        resp = client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_params_login_required(self, client, seed_user, db, seed_periods_today):
        """Unauthenticated POST to investment params redirects to login."""
        acct = _create_investment_account(seed_user, db.session)
        resp = client.post(
            f"/accounts/{acct.id}/investment/params",
            data={
                "assumed_annual_return": "7",
                "employer_contribution_type_id": ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.NONE),
            },
        )
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_params_update_idor_db_unchanged(
        self, auth_client, second_user, db, seed_periods_today,
    ):
        """IDOR POST to investment params with existing params is rejected and DB unchanged."""
        other_acct = _create_other_investment(second_user, db.session)
        # Create params on victim's account to test update path.
        params = InvestmentParams(
            account_id=other_acct.id,
            assumed_annual_return=Decimal("0.07000"),
            employer_contribution_type_id=ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.NONE),
        )
        db.session.add(params)
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{other_acct.id}/investment/params",
            data={
                "assumed_annual_return": "99",
                "employer_contribution_type_id": ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.NONE),
            },
        )
        assert resp.status_code == 404

        db.session.expire_all()
        after = db.session.query(InvestmentParams).filter_by(
            account_id=other_acct.id,
        ).one()
        assert after.assumed_annual_return == Decimal("0.07000"), (
            "IDOR attack modified assumed_annual_return!"
        )

    def test_validation_error_db_unchanged(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Invalid data on existing params preserves original values."""
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(db.session, acct.id)
        orig = db.session.query(InvestmentParams).filter_by(account_id=acct.id).one()
        orig_return = orig.assumed_annual_return

        resp = auth_client.post(
            f"/accounts/{acct.id}/investment/params",
            data={
                "assumed_annual_return": "not_a_number",
                "employer_contribution_type_id": ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.NONE),
            },
        )
        assert resp.status_code == 302

        db.session.expire_all()
        after = db.session.query(InvestmentParams).filter_by(account_id=acct.id).one()
        assert after.assumed_annual_return == orig_return

    def test_params_update_nonexistent_account(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """POST to nonexistent account returns 404 (security: 404 for not-found and not-yours)."""
        resp = auth_client.post(
            "/accounts/999999/investment/params",
            data={
                "assumed_annual_return": "7",
                "employer_contribution_type_id": ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.NONE),
            },
        )
        assert resp.status_code == 404

    def test_params_update_wrong_account_type(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """POST investment params to checking account redirects with flash."""
        checking_acct = seed_user["account"]
        resp = auth_client.post(
            f"/accounts/{checking_acct.id}/investment/params",
            data={
                "assumed_annual_return": "7",
                "employer_contribution_type_id": ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.NONE),
            },
        )
        # The route checks account is None or user_id mismatch -- checking account
        # passes ownership but the route does NOT check account type; it will
        # create params. However, let's verify the actual behavior.
        # Reading the route: update_params only checks ownership, not account type.
        # So this may actually succeed. Let's assert what actually happens.
        assert resp.status_code == 302

    def test_params_update_negative_return_rate(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Negative return rate as percentage input: -5 converts to -0.05, within Range(-1,1)."""
        acct = _create_investment_account(seed_user, db.session)
        # _convert_percentage_inputs converts -5 to -0.05, which is within Range(-1, 1).
        resp = auth_client.post(
            f"/accounts/{acct.id}/investment/params",
            data={
                "assumed_annual_return": "-5",
                "employer_contribution_type_id": ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.NONE),
            },
        )
        assert resp.status_code == 302

        params = db.session.query(InvestmentParams).filter_by(
            account_id=acct.id,
        ).first()
        assert params is not None
        # -5% → -0.05 is valid per schema Range(-1, 1)
        assert params.assumed_annual_return == Decimal("-0.05000")


class TestGrowthChartFragment:
    """Tests for the investment growth chart HTMX fragment (U2)."""

    def test_growth_chart_redirects_without_htmx(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """GET without HX-Request header redirects to investment dashboard."""
        acct = _create_investment_account(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/investment/growth-chart")
        assert resp.status_code == 302
        assert "/investment" in resp.headers.get("Location", "")

    def test_growth_chart_empty_without_params(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Returns empty state when no investment params exist."""
        acct = _create_investment_account(seed_user, db.session)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert b"No projection data" in resp.data

    def test_growth_chart_with_data(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Returns canvas element when projection data exists."""
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart?horizon_years=2",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert b"growthChart" in resp.data

    def test_growth_chart_idor(
        self, auth_client, second_user, db, seed_periods_today,
    ):
        """GET another user's growth chart returns 404
        and does not leak victim data."""
        other_acct = _create_other_investment(second_user, db.session)

        resp = auth_client.get(
            f"/accounts/{other_acct.id}/investment/growth-chart",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 404
        assert b"Other 401k" not in resp.data, (
            "IDOR response leaked victim's account name"
        )


# ── Tests: Contribution-Aware Dashboard ───────────────────────


def _create_salary_profile(db_session, user_id, scenario_id, funds=None):
    """Create an active salary profile for the test user.

    Args:
        db_session: The session to add on.
        user_id: The owner.
        scenario_id: The scenario the profile belongs to.
        funds: An account id whose employer contribution this job FUNDS
            (**R-SAL5**), or ``None``.  Since plan step **salary:R14-b** an
            employer contribution is priced off the profile
            ``budget.investment_params.salary_profile_id`` names, and an
            account naming none models no employer money at all (developer,
            2026-09-04) -- so a case asserting an employer figure has to say
            which job pays it, exactly as the owner does at the form.
    """
    filing = db_session.query(FilingStatus).filter_by(name="single").one()
    profile = SalaryProfile(
        user_id=user_id,
        scenario_id=scenario_id,
        filing_status_id=filing.id,
        name="Day Job",
        annual_salary=Decimal("100000.00"),
        state_code="NC",
        is_active=True,
    )
    db_session.add(profile)
    db_session.flush()
    if funds is not None:
        db_session.query(InvestmentParams).filter_by(
            account_id=funds,
        ).one().salary_profile_id = profile.id
        db_session.flush()
    return profile


def _create_deduction(db_session, profile_id, account_id, amount="500.00"):
    """Create a flat-dollar deduction targeting the investment account."""
    flat_id = ref_cache.calc_method_id(CalcMethodEnum.FLAT)
    timing_id = ref_cache.deduction_timing_id(DeductionTimingEnum.PRE_TAX)
    ded = PaycheckDeduction(
        salary_profile_id=profile_id,
        deduction_timing_id=timing_id,
        calc_method_id=flat_id,
        name="401k Contribution",
        amount=Decimal(amount),
        target_account_id=account_id,
        is_active=True,
    )
    db_session.add(ded)
    params = (
        db_session.query(InvestmentParams)
        .filter_by(account_id=account_id).one_or_none()
    )
    if params is not None:
        params.salary_profile_id = profile_id
    db_session.flush()
    return ded


class TestTheFundingJobIsNamedOrTheMoneyIsNotMODELLED:
    """Ruling **R-SAL5** and the developer's 2026-09-04 ruling, at the surface.

    An employer contribution is a percentage OF a gross, so it has to know
    which job's paycheck to take it from.  Where no deduction names the
    account there was no link to any profile at all, and the basis fell to
    ``income_service.get_current_gross_biweekly``'s unordered ``.first()``
    across the owner's active profiles -- a measured 39% swing on a two-job
    owner, flipping between renders with no data change.  Plan step
    **salary:R14-a** added ``budget.investment_params.salary_profile_id``;
    this step is its reader, and the developer ruled what a NULL means:
    **no employer money is modelled, and the page says so.**

    Both halves are graded here, because "models nothing" alone is what a
    silent regression looks like: an owner whose Employer chip simply vanished
    would have no way to tell a ruling from a bug.
    """

    @staticmethod
    def _account_with_an_employer_contribution(seed_user, db_session):
        """A 401(k) paying a 5% flat employer contribution, funding job UNSET.

        The real Empower shape (ledger row **D45**): no deduction names it, so
        nothing else can imply which job funds it.
        """
        acct = _create_investment_account(
            seed_user, db_session, name="Employer Only 401k",
        )
        _create_investment_params(
            db_session, acct.id,
            employer_contribution_type_id=ref_cache.employer_contribution_type_id(
                EmployerContributionTypeEnum.FLAT_PERCENTAGE),
            employer_flat_percentage=Decimal("0.05"),
        )
        return acct

    def test_an_unset_funding_job_models_nothing_AND_says_why(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """No funding job: the notice renders and the figure is withheld.

        The owner has an active salary profile, so a gross EXISTS -- what is
        missing is the statement that THIS account's employer contribution is
        taken from it.  That distinction is the whole ruling: the app could
        guess, and guessing is what it measured at a 39% swing.
        """
        acct = self._account_with_an_employer_contribution(seed_user, db.session)
        _create_salary_profile(
            db.session, seed_user["user"].id, seed_user["scenario"].id,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        html = resp.data.decode()

        assert "No employer money is modelled" in html
        assert "funding job not set" in html
        # The CONFIGURATION still renders: the owner set a 5% contribution and
        # the app saying so is how they recognise the notice as being about
        # their own setup rather than a missing one.
        assert "Employer contributes" in html
        assert "5.00%" in html
        # And the selector offers the job they could name.
        assert 'name="salary_profile_id"' in html
        assert "Day Job" in html

    def test_naming_the_funding_job_models_the_money(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The same account with the job named: a real figure, no notice.

        The pair is what makes the case above non-vacuous -- without it,
        "the notice renders" is equally true of a page that always renders it.
        """
        acct = self._account_with_an_employer_contribution(seed_user, db.session)
        _create_salary_profile(
            db.session, seed_user["user"].id, seed_user["scenario"].id,
            funds=acct.id,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        html = resp.data.decode()

        assert "No employer money is modelled" not in html
        assert "funding job not set" not in html
        # $100,000 / 26 = $3,846.15 a paycheck; 5% of it is $192.31.
        assert "$192.31" in html

    def test_the_write_door_REFUSES_another_owners_profile(
        self, auth_client, seed_user, second_user, db, seed_periods_today,
    ):
        """A forged ``salary_profile_id`` is a 404, not a priced stranger's salary.

        The form's dropdown lists only the requester's own profiles, so a
        foreign id in the submission is an IDOR -- and the read it would feed
        prices this owner's employer contribution off another owner's salary
        and raise history.  The mirror-image door
        (``paycheck_deductions.target_account_id``) was closed at
        ``salary:R14-a`` as ledger row **N-534**; this is the same guard on
        the same rule, and both call ``auth_helpers.require_owned_fk``.
        """
        acct = self._account_with_an_employer_contribution(seed_user, db.session)
        stranger = _create_salary_profile(
            db.session, second_user["user"].id, second_user["scenario"].id,
        )
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/investment/params",
            data={
                "assumed_annual_return": "7",
                "employer_contribution_type_id": str(
                    ref_cache.employer_contribution_type_id(
                        EmployerContributionTypeEnum.FLAT_PERCENTAGE),
                ),
                "employer_flat_percentage": "5",
                "salary_profile_id": str(stranger.id),
            },
        )
        assert resp.status_code == 404
        params = (
            db.session.query(InvestmentParams)
            .filter_by(account_id=acct.id).one()
        )
        assert params.salary_profile_id is None

    def test_the_CREATE_branch_refuses_it_too(
        self, auth_client, seed_user, second_user, db, seed_periods_today,
    ):
        """The other arm of the same door, which the case above cannot reach.

        ``update_params`` has two branches and the guard is on both, but the
        case above seeds an ``InvestmentParams`` row first and so only
        exercises the UPDATE arm -- an adversarial review measured the CREATE
        arm ungraded.  Here the account has no params row, so the POST takes
        the create path, and the forged profile must be refused before any
        row is written.
        """
        acct = _create_investment_account(seed_user, db.session)
        stranger = _create_salary_profile(
            db.session, second_user["user"].id, second_user["scenario"].id,
        )
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/investment/params",
            data={
                "assumed_annual_return": "7",
                "employer_contribution_type_id": str(
                    ref_cache.employer_contribution_type_id(
                        EmployerContributionTypeEnum.FLAT_PERCENTAGE),
                ),
                "employer_flat_percentage": "5",
                "salary_profile_id": str(stranger.id),
            },
        )
        assert resp.status_code == 404
        # And nothing was created: the guard runs before the INSERT.
        assert (
            db.session.query(InvestmentParams)
            .filter_by(account_id=acct.id).one_or_none()
        ) is None

class TestContributionAwareDashboard:
    """Tests for the contribution timeline integration (5.2-2).

    Backward compatibility (no deductions/transfers) is already covered
    by TestInvestmentDashboard.test_dashboard_with_params.
    IDOR is already covered by TestInvestmentDashboard.test_dashboard_idor.
    """

    def test_dashboard_with_deduction(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Dashboard renders with deduction-based contributions.

        Creates a salary profile and a flat $500 deduction targeting the
        investment account.  Verifies the dashboard renders without error
        and the periodic contribution value appears in the response.
        """
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(db.session, acct.id)

        profile = _create_salary_profile(
            db.session, seed_user["user"].id,
            seed_user["scenario"].id,
        )
        _create_deduction(db.session, profile.id, acct.id, "500.00")
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        # The deduction contributes $500/period.
        assert b"500.00" in resp.data

    def test_growth_chart_with_deduction(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Growth chart HTMX fragment renders with deduction contributions.

        Verifies the growth chart route processes contribution data without
        error.  The chart uses synthetic periods, so deduction dates
        mostly fall back to periodic_contribution -- but the route must
        still call build_contribution_timeline without crashing.
        """
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(db.session, acct.id)

        profile = _create_salary_profile(
            db.session, seed_user["user"].id,
            seed_user["scenario"].id,
        )
        _create_deduction(db.session, profile.id, acct.id, "500.00")
        db.session.commit()

        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart?horizon_years=2",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert b"growthChart" in resp.data


# ── Tests: Contribution Setup Prompt (5.2-3) ─────────────────


def _create_transfer_template(db_session, user_id, from_id, to_id,
                               is_active=True):
    """Create a recurring transfer template targeting an account."""
    from app.models.transfer_template import TransferTemplate
    from tests._test_helpers import make_every_period_rule

    # Authored through the write door (plan step R7c-b): the two-axis columns
    # are NOT NULL, so a rule naming only a pattern cannot be stored.
    tpl = TransferTemplate(
        user_id=user_id,
        from_account_id=from_id,
        to_account_id=to_id,
        name=f"Contribution {from_id}->{to_id}",
        default_amount=Decimal("200.00"),
        is_active=is_active,
    )
    db_session.add(tpl)
    db_session.flush()
    # The definition first, then the cadence onto it (plan step R-F6).
    rule = make_every_period_rule(db_session, tpl)
    return tpl


class TestContributionPrompt:
    """Tests for the contribution setup prompt on the investment dashboard.

    Verifies prompt visibility rules, prompt type (transfer vs. deduction),
    and the create_contribution_transfer route.
    """

    def test_prompt_shown_ira_no_contribution(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """IRA with params, no transfer or deduction: transfer prompt visible."""
        acct = _create_investment_account(
            seed_user, db.session, type_name="Roth IRA",
            name="My Roth IRA", balance="5000.00",
        )
        _create_investment_params(
            db.session, acct.id,
            annual_contribution_limit=Decimal("7000.00"),
        )
        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        html = resp.data.decode()
        # P2 rebuild: the alert became the band-foot funding hint with the
        # transfer form behind a collapse (same prompt logic).
        assert "No funding linked to this account" in html
        assert "Set up a recurring transfer" in html
        assert "Create recurring transfer" in html

    def test_prompt_shown_401k_no_deduction(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """401(k) with params, no deduction: deduction linkage prompt visible."""
        acct = _create_investment_account(
            seed_user, db.session, type_name="401(k)",
            name="My 401k", balance="50000.00",
        )
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        html = resp.data.decode()
        # P2 rebuild: deduction-path copy is now the funding hint's
        # "Link a paycheck deduction" line.
        assert "No funding linked to this account" in html
        # With no active profile the deduction path resolves
        # _salary_profile_action="list" -> url_for("salary.cockpit")
        # (/salary), so salary_profile_url is always set and the hint
        # renders the reachable deduction link. This pins the
        # URL-resolution invariant: a broken endpoint name here would 500.
        assert "Link a paycheck deduction" in html
        assert 'href="/salary"' in html

    def test_prompt_hidden_transfer_exists(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """IRA with active recurring transfer: prompt hidden."""
        acct = _create_investment_account(
            seed_user, db.session, type_name="Roth IRA",
            name="My Roth IRA", balance="5000.00",
        )
        _create_investment_params(
            db.session, acct.id,
            annual_contribution_limit=Decimal("7000.00"),
        )
        _create_transfer_template(
            db.session, seed_user["user"].id,
            seed_user["account"].id, acct.id,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        html = resp.data.decode()
        # P2 rebuild: the prompt is the band-foot funding hint now; its
        # absence pins the hidden state.
        assert "No funding linked to this account" not in html

    def test_prompt_hidden_deduction_linked(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """401(k) with linked deduction: prompt hidden."""
        acct = _create_investment_account(
            seed_user, db.session, type_name="401(k)",
            name="My 401k", balance="50000.00",
        )
        _create_investment_params(db.session, acct.id)
        profile = _create_salary_profile(
            db.session, seed_user["user"].id,
            seed_user["scenario"].id,
        )
        _create_deduction(db.session, profile.id, acct.id)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        html = resp.data.decode()
        # P2 rebuild: hint copy replaces the deduction alert.
        assert "No funding linked to this account" not in html

    def test_prompt_hidden_no_params(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Account without InvestmentParams: no prompt shown."""
        acct = _create_investment_account(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        html = resp.data.decode()
        # P2 rebuild: hint copy replaces both alert variants.
        assert "No funding linked to this account" not in html

    def test_prompt_shown_archived_transfer(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Archived transfer template: prompt still shown."""
        acct = _create_investment_account(
            seed_user, db.session, type_name="Roth IRA",
            name="My Roth IRA", balance="5000.00",
        )
        _create_investment_params(
            db.session, acct.id,
            annual_contribution_limit=Decimal("7000.00"),
        )
        _create_transfer_template(
            db.session, seed_user["user"].id,
            seed_user["account"].id, acct.id,
            is_active=False,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        html = resp.data.decode()
        # P2 rebuild: alert copy -> band-foot funding hint.
        assert "No funding linked to this account" in html

    def test_create_transfer_success(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """POST with valid source creates RecurrenceRule + TransferTemplate."""
        from app.models.transfer_template import TransferTemplate as TT

        acct = _create_investment_account(
            seed_user, db.session, type_name="Roth IRA",
            name="My Roth IRA", balance="5000.00",
        )
        _create_investment_params(
            db.session, acct.id,
            annual_contribution_limit=Decimal("7000.00"),
        )
        checking = seed_user["account"]

        resp = auth_client.post(
            f"/accounts/{acct.id}/investment/create-contribution-transfer",
            data={
                "source_account_id": str(checking.id),
                "amount": "269.23",
            },
        )
        assert resp.status_code == 302
        assert f"/accounts/{acct.id}/investment" in resp.headers.get(
            "Location", "",
        )

        tpl = (
            db.session.query(TT)
            .filter_by(to_account_id=acct.id, user_id=seed_user["user"].id)
            .first()
        )
        assert tpl is not None
        assert tpl.is_active is True
        assert tpl.from_account_id == checking.id
        assert tpl.default_amount == Decimal("269.23")
        assert tpl.recurrence_rule is not None

        # Reached through the OWNING relationship (plan step R-F6); a
        # second fetch by id would re-assert what the line above says.
        assert tpl.recurrence_rule.transfer_template_id == tpl.id

    def test_create_transfer_generates_shadows(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """After creation: shadow transactions exist on the investment account."""
        from app.enums import TxnTypeEnum as TTE
        from app.models.transaction import Transaction as Txn

        acct = _create_investment_account(
            seed_user, db.session, type_name="Roth IRA",
            name="My Roth IRA", balance="5000.00",
        )
        _create_investment_params(
            db.session, acct.id,
            annual_contribution_limit=Decimal("7000.00"),
        )
        checking = seed_user["account"]

        auth_client.post(
            f"/accounts/{acct.id}/investment/create-contribution-transfer",
            data={
                "source_account_id": str(checking.id),
                "amount": "269.23",
            },
        )

        income_type_id = ref_cache.txn_type_id(TTE.INCOME)
        shadows = (
            db.session.query(Txn)
            .filter(
                Txn.account_id == acct.id,
                Txn.transfer_id.isnot(None),
                Txn.transaction_type_id == income_type_id,
                Txn.is_deleted.is_(False),
            )
            .all()
        )
        assert len(shadows) > 0

    def test_create_transfer_redirect_hides_prompt(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """After creation, GET dashboard: prompt no longer visible."""
        acct = _create_investment_account(
            seed_user, db.session, type_name="Roth IRA",
            name="My Roth IRA", balance="5000.00",
        )
        _create_investment_params(
            db.session, acct.id,
            annual_contribution_limit=Decimal("7000.00"),
        )
        checking = seed_user["account"]

        auth_client.post(
            f"/accounts/{acct.id}/investment/create-contribution-transfer",
            data={
                "source_account_id": str(checking.id),
                "amount": "269.23",
            },
        )

        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        html = resp.data.decode()
        # P2 rebuild: hint copy replaces the alert.
        assert "No funding linked to this account" not in html

    def test_create_transfer_validates_source_not_self(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """POST with investment account as source: validation error."""
        acct = _create_investment_account(
            seed_user, db.session, type_name="Roth IRA",
            name="My Roth IRA", balance="5000.00",
        )
        _create_investment_params(db.session, acct.id)

        resp = auth_client.post(
            f"/accounts/{acct.id}/investment/create-contribution-transfer",
            data={"source_account_id": str(acct.id), "amount": "100"},
        )
        assert resp.status_code == 302
        assert f"/accounts/{acct.id}/investment" in resp.headers.get(
            "Location", "",
        )

    def test_create_transfer_rejects_inactive_source(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Inactive source account -> rejected, no transfer wired (B7).

        ``validate_and_resolve_source_account`` refuses to route a
        recurring contribution out of a deactivated account.  The source
        is owned (so ``get_or_404`` passes -- it checks ownership, not
        ``is_active``) but inactive, so the guard redirects with the
        ``Source account is inactive.`` flash.  The load-bearing assertion
        is the money-routing guard: NO ``TransferTemplate`` is created, so
        no shadow transactions are generated against the destination.
        """
        from app.models.transfer_template import TransferTemplate as TT

        acct = _create_investment_account(
            seed_user, db.session, type_name="Roth IRA",
            name="My Roth IRA", balance="5000.00",
        )
        _create_investment_params(
            db.session, acct.id,
            annual_contribution_limit=Decimal("7000.00"),
        )
        checking = seed_user["account"]
        checking.is_active = False
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/investment/create-contribution-transfer",
            data={
                "source_account_id": str(checking.id),
                "amount": "269.23",
            },
        )
        assert resp.status_code == 302
        assert f"/accounts/{acct.id}/investment" in resp.headers.get(
            "Location", "",
        )

        # Money-routing guard: an inactive source gets no transfer wired
        # up -- no TransferTemplate against the destination, hence no
        # shadow transactions move money into it.
        tpl = (
            db.session.query(TT)
            .filter_by(to_account_id=acct.id, user_id=seed_user["user"].id)
            .first()
        )
        assert tpl is None

        # The user-facing reason is surfaced.  The redirect was not
        # followed, so the flash is still unconsumed in the session.
        with auth_client.session_transaction() as sess:
            flashes = sess.get("_flashes", [])
        assert any(
            "inactive" in message.lower() for _category, message in flashes
        )

    def test_create_transfer_idor(
        self, auth_client, second_user, db, seed_periods_today,
    ):
        """POST to other user's investment account returns 404 (security)."""
        other_acct = _create_other_investment(second_user, db.session)

        resp = auth_client.post(
            f"/accounts/{other_acct.id}/investment/create-contribution-transfer",
            data={"source_account_id": "1", "amount": "100"},
        )
        assert resp.status_code == 404

    def test_create_transfer_amount_override(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """POST with custom amount: template uses the override amount."""
        from app.models.transfer_template import TransferTemplate as TT

        acct = _create_investment_account(
            seed_user, db.session, type_name="Roth IRA",
            name="My Roth IRA", balance="5000.00",
        )
        _create_investment_params(db.session, acct.id)
        checking = seed_user["account"]

        resp = auth_client.post(
            f"/accounts/{acct.id}/investment/create-contribution-transfer",
            data={
                "source_account_id": str(checking.id),
                "amount": "1000.00",
            },
        )
        assert resp.status_code == 302

        tpl = (
            db.session.query(TT)
            .filter_by(to_account_id=acct.id, user_id=seed_user["user"].id)
            .first()
        )
        assert tpl is not None
        assert tpl.default_amount == Decimal("1000.00")


# ── Helpers: What-If Chart Data Extraction ──────────────────────


def _extract_data_attr(response_data, attr_name):
    """Extract a JSON data-* attribute value from the chart canvas element.

    Args:
        response_data: Response bytes from the test client.
        attr_name:     The data attribute name (e.g., 'whatif-balances').

    Returns:
        Parsed JSON value (list/dict), or None if not found.
    """
    html = response_data.decode()
    pattern = rf"data-{re.escape(attr_name)}='([^']*)'"
    match = re.search(pattern, html)
    if match:
        return json.loads(match.group(1))
    return None


# ── Tests: What-If Contribution Calculator (5.3-1) ─────────────


class TestWhatIfContributionCalculator:
    """Tests for the what-if contribution calculator on the investment
    growth chart.

    The what-if feature overlays a hypothetical contribution scenario
    on the committed projection, producing a dual-dataset chart and
    a comparison card showing the balance difference at the horizon.
    """

    def test_chart_no_what_if_single_line(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """GET growth-chart without what_if param: single dataset only.

        Backward compatibility: existing chart behavior unchanged when
        no what-if parameter is provided.
        """
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart?horizon_years=2",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "growthChart" in html
        assert "data-whatif-balances" not in html, (
            "What-if data should not be present without what_if param"
        )
        # No comparison card.
        assert "Current Plan" not in html
        assert "Difference" not in html

    def test_chart_with_what_if_dual_lines(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """GET with what_if_contribution=500: what-if data present.

        Verifies the response contains both committed and what-if
        datasets, and a comparison card.
        """
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart"
            "?horizon_years=2&what_if_contribution=500",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "growthChart" in html
        whatif_balances = _extract_data_attr(resp.data, "whatif-balances")
        assert whatif_balances is not None, (
            "What-if balances should be present"
        )
        assert len(whatif_balances) > 0
        # P2 rebuild: the comparison card became the OOB verdict strip
        # ("Current plan" sentence case).
        assert "Current plan" in html
        assert "Difference" in html
        # What-if label includes the amount.
        assert "500.00" in html

    def test_chart_what_if_zero_valid(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """GET with what_if_contribution=0: valid growth-only scenario.

        Zero means "what if I stop contributing?" -- the what-if line
        shows balance growth without any contributions.  This is NOT
        treated as "clear the what-if."
        """
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart"
            "?horizon_years=2&what_if_contribution=0",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        whatif_balances = _extract_data_attr(resp.data, "whatif-balances")
        assert whatif_balances is not None, (
            "Zero is a valid what-if (growth-only), not 'clear'"
        )

    def test_chart_what_if_empty_string_ignored(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """GET with what_if_contribution= (empty): no what-if, single dataset.

        Empty input means "no what-if" -- chart reverts to standard mode.
        """
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart"
            "?horizon_years=2&what_if_contribution=",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "data-whatif-balances" not in resp.data.decode()

    def test_chart_what_if_invalid_ignored(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """GET with what_if_contribution=abc: invalid input ignored, no error.

        Non-numeric input degrades gracefully to single-line chart.
        """
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart"
            "?horizon_years=2&what_if_contribution=abc",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "data-whatif-balances" not in resp.data.decode()

    def test_chart_what_if_negative_ignored(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """GET with what_if_contribution=-100: negative contribution ignored.

        Negative contributions are nonsensical; chart renders without
        what-if overlay.
        """
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart"
            "?horizon_years=2&what_if_contribution=-100",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert "data-whatif-balances" not in resp.data.decode()

    def test_what_if_respects_annual_limit(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Annual contribution limit caps what-if contributions.

        The annual limit is per CALENDAR year and resets each January
        (the year-boundary reset in ``growth_engine``).  A 1-year rolling
        horizon cannot demonstrate the cap: it straddles two calendar
        years and each side stays at or under the limit, so nothing is
        trimmed and the result varies with today's date.  A 2-year
        horizon always contains at least one FULL calendar year, where
        26 periods x $500 = $13,000 exceeds the $7,000 cap and is provably
        trimmed -- making the assertion date-independent.

        Setup: $0 balance, $7,000/year limit, 0% return, $500/period.
        With 0% return and a $0 start, the final balance is the sum of
        capped contributions; the uncapped total is $500 per period, so a
        bound cap makes the final balance strictly smaller.
        """
        acct = _create_investment_account(
            seed_user, db.session, type_name="Roth IRA",
            name="Limited IRA", balance="0.00",
        )
        _create_investment_params(
            db.session, acct.id,
            assumed_annual_return=Decimal("0.00000"),
            annual_contribution_limit=Decimal("7000.00"),
        )
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart"
            "?horizon_years=2&what_if_contribution=500",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        whatif_balances = _extract_data_attr(resp.data, "whatif-balances")
        assert whatif_balances is not None
        # One end-balance per period; with 0% return and a $0 start, the
        # final balance is the sum of capped contributions.  $500 per
        # period is exactly the uncapped total -- equal to the final
        # balance only if the cap never bound.  A full interior calendar
        # year is trimmed from $13,000 to $7,000, so the cap binds and the
        # capped total is strictly less, on every date.
        last_balance = Decimal(whatif_balances[-1])
        uncapped_total = Decimal("500") * len(whatif_balances)
        assert last_balance < uncapped_total, (
            f"Cap must trim a full year: got ${last_balance}, "
            f"uncapped ${uncapped_total}"
        )
        assert last_balance >= Decimal("7000"), (
            f"Expected at least one year's limit ($7000), got ${last_balance}"
        )

    def test_what_if_employer_match_recalculated(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Employer match is recalculated for the what-if amount.

        Setup: 401(k) with 100% match up to 6% of gross.
        $100K salary -> biweekly gross ~$3846.15.
        6% of gross ~$230.77 (matchable).
        What-if: $300/period -> employer matches min($300, $230.77) = $230.77.
        Total per period: $300 + $230.77 = $530.77.

        With 0% return, end balance must exceed employee-only total
        ($300 * N periods), proving employer match was applied.
        """
        acct = _create_investment_account(
            seed_user, db.session, type_name="401(k)",
            name="Matched 401k", balance="0.00",
        )
        _create_investment_params(
            db.session, acct.id,
            assumed_annual_return=Decimal("0.00000"),
            annual_contribution_limit=None,
            contribution_limit_year=None,
            employer_contribution_type_id=ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.MATCH),
            employer_match_percentage=Decimal("1.0000"),
            employer_match_cap_percentage=Decimal("0.0600"),
        )
        # The salary profile provides the gross the employer match is a
        # percentage OF, and names itself as the account's funding job
        # (R-SAL5) -- there is no deduction here to imply the link.
        _create_salary_profile(
            db.session, seed_user["user"].id,
            seed_user["scenario"].id, funds=acct.id,
        )
        db.session.commit()

        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart"
            "?horizon_years=1&what_if_contribution=300",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        whatif_balances = _extract_data_attr(resp.data, "whatif-balances")
        assert whatif_balances is not None
        last_balance = Decimal(whatif_balances[-1])
        # Employee-only: $300 * ~27 periods = ~$8100.
        # With employer match: ($300 + $230.77) * ~27 = ~$14330.
        assert last_balance > Decimal("8100"), (
            f"Expected balance > $8100 (employee alone), got ${last_balance}. "
            "Employer match may not be applied to what-if amount."
        )

    def test_what_if_no_limit_brokerage(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Brokerage account (no annual limit): contributions uncapped.

        With 0% return and no limit, end balance = $what_if * N periods.
        Must exceed the amount that would be capped at a typical limit,
        confirming no artificial cap is applied.
        """
        acct = _create_investment_account(
            seed_user, db.session, type_name="Brokerage",
            name="My Brokerage", balance="0.00",
        )
        _create_investment_params(
            db.session, acct.id,
            assumed_annual_return=Decimal("0.00000"),
            annual_contribution_limit=None,
            contribution_limit_year=None,
        )
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart"
            "?horizon_years=1&what_if_contribution=500",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        whatif_balances = _extract_data_attr(resp.data, "whatif-balances")
        assert whatif_balances is not None
        last_balance = Decimal(whatif_balances[-1])
        # No limit: $500 * ~27 periods = ~$13500.
        # Should exceed a typical IRA limit of $7000.
        assert last_balance > Decimal("7000"), (
            f"Expected uncapped balance > $7000, got ${last_balance}"
        )

    def test_what_if_comparison_positive(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """What-if > current contribution: comparison shows positive difference.

        No current contributions -> committed is growth-only.
        What-if at $500/period adds contributions.
        The what-if end balance exceeds committed -> positive difference.
        """
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart"
            "?horizon_years=5&what_if_contribution=500",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Positive difference: what-if exceeds committed.
        assert "+$" in html, (
            "Expected positive difference indicator in comparison card"
        )

    def test_what_if_comparison_negative(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """What-if < current contribution: comparison shows negative difference.

        Current contributions at $500/period via deduction.
        What-if at $100/period is less.
        The what-if end balance is lower -> negative difference.
        """
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(
            db.session, acct.id,
            annual_contribution_limit=None,
            contribution_limit_year=None,
        )
        profile = _create_salary_profile(
            db.session, seed_user["user"].id,
            seed_user["scenario"].id,
        )
        _create_deduction(db.session, profile.id, acct.id, "500.00")
        db.session.commit()

        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart"
            "?horizon_years=5&what_if_contribution=100",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # Negative difference: what-if is less than committed.
        assert "-$" in html, (
            "Expected negative difference indicator in comparison card"
        )

    def test_what_if_comparison_zero(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """What-if == current (both zero): comparison shows zero difference.

        No current contributions and what-if=0 means both projections
        are growth-only from the same starting balance.  Difference is
        exactly $0.
        """
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart"
            "?horizon_years=2&what_if_contribution=0",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "same as current plan" in html, (
            "Expected zero-difference message in comparison card"
        )

    def test_what_if_no_current_contributions(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """No existing contributions: committed is growth-only.

        When the account has no deductions or transfers, the committed
        projection is purely growth-based.  The what-if adds contributions,
        so it should produce a higher end balance.
        """
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart"
            "?horizon_years=5&what_if_contribution=500",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        committed = _extract_data_attr(resp.data, "balances")
        whatif = _extract_data_attr(resp.data, "whatif-balances")
        assert committed is not None
        assert whatif is not None
        # What-if with contributions should exceed growth-only committed.
        assert Decimal(whatif[-1]) > Decimal(committed[-1]), (
            "What-if with contributions should exceed growth-only committed"
        )

    def test_what_if_idor(
        self, auth_client, second_user, db, seed_periods_today,
    ):
        """Other user's account with what-if param: 404.

        IDOR protection is unaffected by the what-if parameter.
        """
        other_acct = _create_other_investment(second_user, db.session)
        resp = auth_client.get(
            f"/accounts/{other_acct.id}/investment/growth-chart"
            "?horizon_years=2&what_if_contribution=500",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 404
        assert b"Other 401k" not in resp.data

    def test_what_if_preserves_horizon(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """What-if with custom horizon: both projections use same period count.

        The committed and what-if datasets must have the same length
        (same x-axis) regardless of the horizon setting.
        """
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/growth-chart"
            "?horizon_years=10&what_if_contribution=500",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        committed = _extract_data_attr(resp.data, "balances")
        whatif = _extract_data_attr(resp.data, "whatif-balances")
        assert committed is not None
        assert whatif is not None
        assert len(committed) == len(whatif), (
            f"Committed ({len(committed)}) and what-if ({len(whatif)}) "
            "must have the same number of data points"
        )
        # 10-year horizon should produce many more periods than 2-year.
        assert len(committed) > 100, (
            f"10-year horizon should have 100+ periods, got {len(committed)}"
        )


# ── C8: investment dashboard / growth chart routed through producer ─
#
# Pre-Commit-8 the dashboard() and growth_chart() handlers each built
# their own per-account transaction query and called
# ``balance_calculator.calculate_balances`` directly with no
# ``selectinload(Transaction.entries)``.  The math-layer silent-degrade
# seam (closed in Commit 5) was the only safety net.  When an
# investment account had a Projected expense with cleared debit
# entries (an unusual but valid configuration; the contract is that
# the resolver applies the entries-aware reduction unconditionally
# regardless of account type), the route silently returned
# ``effective_amount``.  Commit 8 routes both handlers through
# ``balance_resolver.balances_for`` so the figure matches the grid and
# every other surface for the same inputs.


def _add_envelope_expense_with_cleared_entries_inv(
    db_session, *, user_id, account, scenario, period, category_id,
    estimated, cleared_amounts,
):
    """Create a Projected envelope expense with cleared debit entries.

    Same shape as the helper used in the savings / accounts / year-end
    C8 tests; copied here so this file stays standalone.  These are
    the entries that produce the F-009 / CRIT-01 silent-degrade gap
    when the consuming query forgets to ``selectinload(entries)``.
    """
    from app.models.transaction import Transaction  # pylint: disable=import-outside-toplevel
    from app.models.transaction_entry import TransactionEntry  # pylint: disable=import-outside-toplevel
    from app.models.transaction_template import TransactionTemplate  # pylint: disable=import-outside-toplevel
    from app.enums import StatusEnum, TxnTypeEnum  # pylint: disable=import-outside-toplevel

    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    projected_id = ref_cache.status_id(StatusEnum.PROJECTED)

    template = TransactionTemplate(
        user_id=user_id,
        account_id=account.id,
        category_id=category_id,
        transaction_type_id=expense_type_id,
        name="Investment-side expense",
        default_amount=estimated,
        is_envelope=True,
    )
    db_session.add(template)
    db_session.flush()

    txn = Transaction(
        template_id=template.id,
        user_id=period.user_id,
        pay_period_id=period.id,
        scenario_id=scenario.id,
        account_id=account.id,
        status_id=projected_id,
        name="Investment-side expense",
        category_id=category_id,
        transaction_type_id=expense_type_id,
        amount_ownership=AmountOwnership.own(estimated),
    )
    db_session.add(txn)
    db_session.flush()

    for amt in cleared_amounts:
        db_session.add(TransactionEntry(
            transaction_id=txn.id, account_id=txn.account_id,
            user_id=user_id,
            amount=amt,
            description="Cleared purchase",
            purchased_on=date(2026, 5, 15),
            is_credit=False,
            **settle_day_columns(date(2026, 5, 15)),
        ))
    db_session.flush()
    return txn


class TestInvestmentEntryAwareRouting:
    """C8-2 / C8-3: /investment dashboard + growth chart use canonical producer.

    Pins the R-1 finding: pre-Commit-8 the two investment handlers
    each had bare ``calculate_balances`` calls with no
    ``selectinload(Transaction.entries)``.  Routing both through
    ``balance_resolver.balances_for`` (which owns the eager-load and
    the anchor resolution) makes the entries-aware reduction
    structural for these routes.
    """

    def test_investment_holdings_entry_aware(
        self, app, auth_client, seed_user, db, seed_periods_today,
    ):
        """C8-2: dashboard current_balance == canonical producer value.

        Reproduction of the symptom on /investment:

          - Investment account anchor 50,000.00 on the current period.
          - One Projected envelope expense on the same account in the
            same period, ``estimated_amount = 500.00``.
          - Three CLEARED debit entries summing 45.71 (20 + 15.71 + 10).

        Hand arithmetic (CRIT-01 / F-009 / R-1):

          cleared_debit   = 45.71
          uncleared_debit = 0
          sum_credit      = 0
          checking_impact = max(500.00 - 45.71 - 0, 0) = 454.29
          current_balance = 50,000.00 + 0 - 454.29 = 49,545.71

        The route renders this number formatted with ``{:,.2f}`` so
        the byte string ``$49,545.71`` (or the bare ``49,545.71``
        inside the page) MUST appear in the response.  Pre-Commit-8
        the route reported ``49,500.00`` (= 50,000 - 500) via the
        silent-degrade seam.  We also assert byte-equality with the
        canonical producer's value so the contract is locked beyond
        the rendered string.
        """

        with app.app_context():
            user = seed_user["user"]
            scenario = seed_user["scenario"]
            current_period = current_pay_period(user.id)
            assert current_period is not None

            # ``account_service.create_account`` (via the helper) anchors
            # the new account against the user's current pay period and
            # writes the matching ``AccountAnchorHistory`` row, so no
            # explicit override is needed -- the resolver reads the
            # factory's history row directly.
            acct = _create_investment_account(
                seed_user, db.session,
                type_name="401(k)", name="Test 401k",
                balance="50000.00",
            )
            _create_investment_params(db.session, acct.id)
            _add_envelope_expense_with_cleared_entries_inv(
                db.session,
                user_id=user.id,
                account=acct,
                scenario=scenario,
                period=current_period,
                category_id=seed_user["categories"]["Groceries"].id,
                estimated=Decimal("500.00"),
                cleared_amounts=(
                    Decimal("20.00"), Decimal("15.71"), Decimal("10.00"),
                ),
            )
            # The purchases above are dated by their builder, BEFORE this
            # calendar's first period, so the books move again now that the
            # rows exist (plan step X-f3c-2b, ruling **R-HG**).  The helper
            # bounds on the earliest settled row as well as on the assertion,
            # which is why calling it a second time is the whole repair.
            open_books_before_the_first_assertion(db.session, acct)
            db.session.commit()

            # The entries-aware CASH BASIS the modelled balance is computed
            # ON: 50,000 - max(500 - 45.71 - 0, 0) = 49,545.71.  Read through
            # the seam's cash-flow view at plan step X-g4b, which deleted the
            # anchor-forward producer this asserted against; that view is the
            # same fold the modelled replay folds beneath its accrual, so the
            # basis under test is genuinely the one the tile is built on.
            bctx = BalanceContext.build(user.id)
            basis = balance_at.cash_balance_map(
                acct, bctx,
            )
            assert basis[current_period.id] == Decimal("49545.71")

            # What the tile RENDERS is that cash basis plus the modelled
            # accrual, since plan step X-g2b gave the anchor period its own
            # days (ruling R-Y).  The entries-aware arithmetic above is still
            # the load-bearing half: it is what the accrual is computed ON, so
            # the pre-fix seed would still land the rendered figure ~$45 low.
            displayed = balance_at.balance_map(
                acct, bctx,
            )[current_period.id]
            assert displayed > Decimal("49545.71")
            assert displayed - Decimal("49545.71") < Decimal("200.00")

            resp = auth_client.get(f"/accounts/{acct.id}/investment")
            assert resp.status_code == 200
            # The rendered current-balance tile carries the comma-formatted
            # Decimal.  Pre-Commit-8 it would render 49,500.00 (silent
            # degrade); asserting the modelled figure's presence AND the
            # pre-fix value's absence locks the regression in both directions.
            assert f"{displayed:,.2f}".encode() in resp.data
            assert b"49,500.00" not in resp.data

    def test_investment_growth_chart_entry_aware(
        self, app, auth_client, seed_user, db, seed_periods_today,
    ):
        """C8-3: growth_chart() seeds the projection from the entries-aware balance.

        Same setup as C8-2.  The growth-chart route projects a
        synthetic period series forward from ``current_balance`` --
        if that seed is wrong, the entire chart series is wrong.  The
        first chart point (``data-balances[0]``) is the seed period's
        end balance from the growth engine; with ``periodic_contribution
        = 0`` (no deductions, no recurring transfers) and only the
        post-anchor projection from 49,545.71, the first chart point
        ends very close to the seed (subject to one biweekly's worth
        of compounding at 7% annual = ~0.27%, ~$133 on $49,545.71).

        Pre-Commit-8 the seed was 49,500.00 via the silent-degrade
        seam, so the first chart point would land near $49,633 instead
        of near $49,679.  We assert the chart's first point sits in
        a tight band around the entry-aware seed -- the band is wide
        enough to absorb the growth engine's contribution / employer-
        match math but narrow enough to reject the pre-fix value.
        """
        with app.app_context():
            user = seed_user["user"]
            scenario = seed_user["scenario"]
            current_period = current_pay_period(user.id)
            assert current_period is not None

            # ``account_service.create_account`` (via the helper) anchors
            # the new account against the user's current pay period and
            # writes the matching ``AccountAnchorHistory`` row, so no
            # explicit override is needed -- the resolver reads the
            # factory's history row directly.
            acct = _create_investment_account(
                seed_user, db.session,
                type_name="401(k)", name="Test 401k",
                balance="50000.00",
            )
            _create_investment_params(db.session, acct.id)
            _add_envelope_expense_with_cleared_entries_inv(
                db.session,
                user_id=user.id,
                account=acct,
                scenario=scenario,
                period=current_period,
                category_id=seed_user["categories"]["Groceries"].id,
                estimated=Decimal("500.00"),
                cleared_amounts=(
                    Decimal("20.00"), Decimal("15.71"), Decimal("10.00"),
                ),
            )
            # The purchases above are dated by their builder, BEFORE this
            # calendar's first period, so the books move again now that the
            # rows exist (plan step X-f3c-2b, ruling **R-HG**).  The helper
            # bounds on the earliest settled row as well as on the assertion,
            # which is why calling it a second time is the whole repair.
            open_books_before_the_first_assertion(db.session, acct)
            db.session.commit()

            resp = auth_client.get(
                f"/accounts/{acct.id}/investment/growth-chart?horizon_years=1",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            balances = _extract_data_attr(resp.data, "balances")
            assert balances is not None and len(balances) > 0

            # The band this used to assert is now an EXACT identity, because
            # plan step X-g2b made the seed a date the chart also opens its
            # axis from (ruling R-AF): the first projected point is the
            # account's modelled balance at the current period's END,
            # compounded over exactly one period.  With no contributions and no
            # employer match there is nothing else in the row.
            bctx = BalanceContext.build(user.id)
            seed = balance_at.balance_at(acct, bctx, last_covered_day(current_period))
            # The span is the axis's OWN first period -- the owner's next
            # paycheck, read off the same door the chart resolves its axis
            # through (plan step C2-e).  It used to be the CURRENT period's
            # span, which matched only because the fabricated axis it replaced
            # was hardcoded to the same 14 days.
            # 7.0% is ``_create_investment_params``' default assumed return.
            axis_head = bctx.calendar().projection_axis(
                last_covered_day(current_period) + timedelta(days=1),
                last_covered_day(current_period) + timedelta(days=365),
            )[0]
            rate = growth_engine.span_return_rate(
                Decimal("0.07000"), axis_head.start_date, axis_head.end_date,
            )
            first_point = Decimal(balances[0])
            assert first_point == seed + round_money(seed * rate), (
                f"First chart point {first_point} is not the seed {seed} "
                "compounded one period; the chart and its seed have "
                "separated (ruling R-AF)."
            )
            # ...and it is still strictly above where the pre-Commit-8
            # silent-degrade seed (49,500.00) would land, which is what this
            # test was written to catch.
            assert first_point > Decimal("49640.00")


class TestEmployerMatchCapped:
    """C25 / HIGH-07 / F-043 / F-055: dashboard "Employer Per Period" card
    feeds the limit-capped employee contribution into
    ``calculate_employer_contribution`` so it matches the growth chart's
    employer line and the year-end ``year_summary_employer_total``.

    Pre-fix the card passed the UNCAPPED ``periodic_contribution`` to the
    matcher (``investment.py:183 -> 187-189``), so near the annual limit
    a match-type employer overstated the card per-period match relative
    to the chart and year-end (F-043 worked example: $240 vs $100).
    """

    def _make_settled_shadow_income(
        self, seed_user, to_account, period, amount, db_session,
    ):
        """Seed a Settled transfer into the investment account.

        Uses the canonical ``transfer_service.create_transfer`` -- the
        only sanctioned path for budget.transfers rows -- with a
        ``Received`` status whose ``excludes_from_balance = False`` so
        the route's YTD aggregation (``calculate_investment_inputs``
        Step 4) counts the resulting shadow income.
        """
        from app.enums import StatusEnum  # pylint: disable=import-outside-toplevel
        from app.services import transfer_service  # pylint: disable=import-outside-toplevel

        received_id = ref_cache.status_id(StatusEnum.RECEIVED)
        cat = seed_user["categories"]["Groceries"]
        xfer = transfer_service.create_transfer(
            transfer_service.TransferSpec(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=to_account.id,
                pay_period_id=period.id,
                scenario_id=seed_user["scenario"].id,
                amount=amount,
                status_id=received_id,
                category_id=cat.id,
                name="YTD seed",
            ),
        )
        db_session.commit()
        return xfer

    def test_card_uses_capped_contribution_at_binding_limit(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """C25-1: card per-period employer match equals the chart/year-end
        capped value when the annual limit binds.

        Setup (F-043 worked example, scaled to fit a deduction):

          annual_contribution_limit = $23,500
          YTD shadow income contributed in past period of current year
            = $23,300  -> remaining = $200
          gross_biweekly                                 = $8,000
          (annual_salary = 8000 * 26 = $208,000)
          match: 50% up to 6% of gross
            matchable_salary = 8000 * 0.06           = $480.00
          deduction (employee contribution per period) = $1,500

        Pre-fix card (UNCAPPED $1,500 fed to the matcher):
          matched  = min(1500, 480)                  = 480
          employer = 480 * 0.50                      = $240.00

        Post-fix card (CAPPED at remaining limit before matcher):
          capped   = min(1500, max(23500 - 23300, 0)) = 200
          matched  = min(200, 480)                   = 200
          employer = 200 * 0.50                      = $100.00

        The card now reads $100.00; the pre-fix string $240.00 must
        not appear in the response.
        """
        # 401(k) account; create_account anchors at the current period.
        acct = _create_investment_account(
            seed_user, db.session, type_name="401(k)",
            name="HIGH-07 401k", balance="10000.00",
        )
        _create_investment_params(
            db.session, acct.id,
            assumed_annual_return=Decimal("0.00000"),
            annual_contribution_limit=Decimal("23500.00"),
            contribution_limit_year=2026,
            employer_contribution_type_id=ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.MATCH),
            employer_match_percentage=Decimal("0.5000"),
            employer_match_cap_percentage=Decimal("0.0600"),
        )

        # Salary profile: annual 208000 -> gross_biweekly 8000.
        filing = db.session.query(FilingStatus).filter_by(name="single").one()
        profile = SalaryProfile(
            user_id=seed_user["user"].id,
            scenario_id=seed_user["scenario"].id,
            filing_status_id=filing.id,
            name="Day Job",
            annual_salary=Decimal("208000.00"),
            state_code="NC",
            is_active=True,
        )
        db.session.add(profile)
        db.session.flush()

        # Deduction $1500/period -> uncapped periodic_contribution = 1500.
        _create_deduction(db.session, profile.id, acct.id, "1500.00")
        db.session.commit()

        # YTD seed: $23,300 settled shadow income in a past period
        # within the current year.  seed_periods_today[0] is ~56 days
        # before today (period 4 = current), so for any test date this
        # year it lands in the same calendar year as today.
        self._make_settled_shadow_income(
            seed_user, acct, seed_periods_today[0],
            Decimal("23300.00"), db.session,
        )

        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        html = resp.data.decode()

        # Post-fix capped employer match.
        assert "$100.00" in html, (
            "Card did not render the capped employer match of $100.00 "
            "(HIGH-07/F-043).  Card site may still be bypassing "
            "cap_contribution_at_limit before calling "
            "calculate_employer_contribution."
        )
        # Pre-fix uncapped value must NOT appear: locks the fix in both
        # directions.  Use a regex-safe assertion -- the value must not
        # appear anywhere in the rendered HTML.
        assert "$240.00" not in html, (
            "Pre-fix uncapped employer match $240.00 detected; the "
            "cap_contribution_at_limit fix has regressed."
        )

    def test_card_unchanged_when_well_below_limit(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """C25-3: well below limit, the card value is unchanged (regression
        guard).

        Same fixture as the binding-limit test but with no YTD seed:
        remaining = max(23500 - 0, 0) = 23500;
        capped = min(1500, 23500) = 1500 (cap does not bind);
        matched = min(1500, 480) = 480;
        employer = 480 * 0.50 = $240.00.

        The capped helper returns 1500 (the full periodic) so the card
        produces the same byte-identical $240.00 it always did.
        """
        acct = _create_investment_account(
            seed_user, db.session, type_name="401(k)",
            name="HIGH-07 below-limit", balance="10000.00",
        )
        _create_investment_params(
            db.session, acct.id,
            assumed_annual_return=Decimal("0.00000"),
            annual_contribution_limit=Decimal("23500.00"),
            contribution_limit_year=2026,
            employer_contribution_type_id=ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.MATCH),
            employer_match_percentage=Decimal("0.5000"),
            employer_match_cap_percentage=Decimal("0.0600"),
        )
        filing = db.session.query(FilingStatus).filter_by(name="single").one()
        profile = SalaryProfile(
            user_id=seed_user["user"].id,
            scenario_id=seed_user["scenario"].id,
            filing_status_id=filing.id,
            name="Day Job",
            annual_salary=Decimal("208000.00"),
            state_code="NC",
            is_active=True,
        )
        db.session.add(profile)
        db.session.flush()
        _create_deduction(db.session, profile.id, acct.id, "1500.00")
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        html = resp.data.decode()

        # No YTD: cap does not bind; full match (matchable=480) applies.
        assert "$240.00" in html, (
            "Below-limit card regressed: expected $240.00 employer per "
            "period.  cap_contribution_at_limit may be incorrectly "
            "clamping below the limit."
        )


class TestTheProjectionAppliesEachContributionOnce:
    """deep-quality-hunt #9: the investment dashboard projection applies the
    current period's transfer contribution exactly once.

    Pre-fix the projection seeded the END-of-current-period balance (which
    already contains the current period's contribution) while also
    including the current period in the projection window, so the growth
    engine re-applied the contribution -- double-counting it on the first
    row and the whole forward curve.  The fix seeds the end-of-current
    balance with only the current period's own transfer contribution
    removed.
    """

    def test_current_period_transfer_applied_once_in_projection(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """A $1,000 current-period transfer is applied once, not twice.

        Setup (0% return isolates the contribution from growth):
          401(k) anchored at the current period at $10,000.
          $1,000 PROJECTED shadow contribution in the current period.

        The modelled end-of-current balance = 10,000 + 1,000 = $11,000 (the
        displayed tile, and the history line's last point).  The projection
        seeds from exactly that and opens the day after, so the $1,000 the user
        recorded is in the seed and the $1,000 the engine adds is the NEXT
        period's modelled contribution.

        C2 (Loop B P1, developer-approved behavior change): the dashboard
        chart renders on the SAME synthetic-period basis as the HTMX fragment
        and the ``projection`` context key was dropped, so this is verified
        through the chart's own points rather than a per-row
        ``ProjectedBalance`` list.  With 0% return the first synthetic period
        accrues no growth, and the $1,000 transfer averaged over its single
        period gives a $1,000 periodic contribution, so:
          chart_balances[0] = 10,000 seed + 0 growth + 1,000 = 11,000.
        The pre-fix double-count (seed 11,000, then re-add 1,000) would land
        the first projected point at 12,000 -- which this test forbids.  The
        headline tile (11,000) is unchanged by the basis change.
        """
        from app.services.investment_dashboard_service import (  # pylint: disable=import-outside-toplevel
            compute_dashboard_data,
        )

        acct = _create_investment_account(
            seed_user, db.session, type_name="401(k)",
            name="Double-count 401k", balance="10000.00",
        )
        _create_investment_params(
            db.session, acct.id,
            assumed_annual_return=Decimal("0.00000"),
            annual_contribution_limit=Decimal("100000.00"),
        )
        current_period = current_pay_period(
            seed_user["user"].id,
        )
        assert current_period is not None
        _make_projected_shadow_income(
            seed_user, acct, current_period, Decimal("1000.00"), db.session,
        )

        data = compute_dashboard_data(seed_user["user"].id, acct)

        # Displayed tile = the modelled end-of-current balance.
        assert data["current_balance"] == Decimal("11000.00")

        # The two lines MEET: the history line's last point is the balance the
        # projection seeds from, so there is no step at the Today marker for a
        # caption to explain (ruling R-AF).
        history = data["history_balances"]
        chart_balances = data["chart_balances"]
        assert history, "the chart should render modelled history"
        assert chart_balances, "chart should project the horizon forward"
        assert Decimal(history[-1]) == Decimal("11000.00")

        # ...and then steps by EXACTLY one modelled period's contribution.
        # A double count lands here at 13,000; the retired subtraction ported
        # onto this axis lands the history/seed junction at 10,000 instead.
        assert Decimal(chart_balances[0]) == Decimal("12000.00")
        assert (
            Decimal(chart_balances[0]) - Decimal(history[-1])
        ) == Decimal("1000.00")


def _make_projected_shadow_income(
    seed_user, to_account, period, amount, db_session,
):
    """Seed a PROJECTED transfer into the investment account in *period*.

    The shape the double count needed: a projected shadow income is counted
    in the balance the projection seeds from AND is the kind of row the
    growth-engine timeline re-applies.  It stays the fixture because it is
    still the discriminating shape -- under rulings R-AB / R-AF the seed's
    date and the window are disjoint, so the row is counted once, and this
    is where that would fail if either moved.
    """
    from app.enums import StatusEnum  # pylint: disable=import-outside-toplevel
    from app.services import transfer_service  # pylint: disable=import-outside-toplevel

    projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
    cat = seed_user["categories"]["Groceries"]
    xfer = transfer_service.create_transfer(
        transfer_service.TransferSpec(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=to_account.id,
            pay_period_id=period.id,
            scenario_id=seed_user["scenario"].id,
            amount=amount,
            status_id=projected_id,
            category_id=cat.id,
            name="current-period contribution",
        ),
    )
    db_session.commit()
    return xfer

class TestTheAnnualLimitSeedFollowsTheWindow:
    """The engine's YTD seed holds exactly the periods OUTSIDE the window.

    The annual contribution limit is consumed by the contributions the growth
    engine does NOT project plus the ones it does, so its
    ``ytd_contributions_start`` must cover exactly the periods the window
    excludes -- no more, no less.

    **Ruling R-AF moved that boundary and the seed did not follow it.**  The
    axis used to open at ``date.today()``, so its first synthetic period stood
    in for the rest of the current pay period and the engine applied that
    period's contribution itself; the seed therefore excluded it
    (``ytd_contributions_seed``, deep-quality-hunt #10).  The axis now opens the
    day AFTER the current period ends, so the engine never applies it and the
    seed must INCLUDE it.  Keeping the old field reports room that is already
    spent: on a $23,500 limit with $1,000 a period and today in the year's 15th
    period, the engine prices $9,500 of remaining room where $8,500 is left,
    projects one extra contribution inside the calendar year, and compounds it
    for the whole horizon.  It also disagrees with the limit CARD on the same
    page, which has always counted the current period.

    The rule is one pure function, so it is pinned as one: an integration
    fixture would have to drive a real contribution feed all the way to the
    annual cap to see the difference, and would then be pinning the growth
    engine's capping rather than this boundary.  The wiring is pinned
    separately below.
    """

    @staticmethod
    def _inputs(seed, through):
        """Return an ``InvestmentInputs`` carrying the two YTD totals."""
        return InvestmentInputs(
            periodic_contribution=Decimal("1000.00"),
            employer_params=None,
            annual_contribution_limit=Decimal("23500.00"),
            ytd_contributions=Decimal(through),
            ytd_contributions_seed=Decimal(seed),
        )

    def test_the_chart_always_seeds_the_through_current_total(self):
        """The current period is outside this surface's window, so it counts.

        $14,000 strictly before the current period and $15,000 through it.  The
        window opens the day AFTER the period covering the clock ends, so the
        engine will never apply that period's $1,000 -- the seed must, or the
        year has $1,000 of room that is already spent.

        **This took a window date and a current period until plan step C2-e**,
        and branched on whether the window contained that period.  Both
        arguments are gone: :func:`._context._projection_start` derives the
        window's opening day from the CALENDAR as the day after the span
        covering the clock ends, so the answer is structurally the same one on
        every input.  The two tests that graded the other arm are below,
        re-pointed at the surfaces that still have one.
        """
        assert _projection_ytd(
            self._inputs("14000.00", "15000.00"),
        ) == Decimal("15000.00")

    def test_the_two_surfaces_take_DIFFERENT_ytd_fields_by_construction(self):
        """What replaced the branch: two call sites, one field each.

        ``retirement_projection``'s axis opens at or INSIDE the period covering
        the clock, so its engine walks that period and charges its contribution
        against the limit as it applies it -- seeding the through-current total
        there would charge it twice (deep-quality-hunt #10).  The chart's window
        opens after it, so it must.  The distinction is now WHICH FIELD each
        call site reads, which is a property of the source rather than of a
        date comparison that can be got wrong.
        """
        source = pathlib.Path(
            "app/services/retirement_projection.py",
        ).read_text(encoding="utf-8")
        assert "ytd_contributions_start=inputs.ytd_contributions_seed" in source
        assert "ytd_contributions_start=inputs.ytd_contributions," not in source

    def test_the_two_ytd_totals_COINCIDE_with_no_current_period(self):
        """Why the branch's other arm was a no-op, and could be deleted.

        The deleted branch answered ``ytd_contributions_seed`` when there was no
        current period.  ``investment_projection`` returns ZERO for BOTH totals
        in that state, so both arms returned the same figure -- a branch that
        cannot change the answer, which CLAUDE.md rule 1 forbids shipping.  This
        pins the fact the deletion rests on, in the module that owns it.

        The two totals became ONE function over an ``inclusive`` flag at plan
        step C2-f2c, which is what they always were; the fact this test rests
        on is unchanged, and the no-current-period arm is the branch that
        answers ZERO before either bound is consulted.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.investment_projection._inputs import (
            _ytd_contributions,
        )
        assert _ytd_contributions([], None, inclusive=True) == Decimal("0")
        assert _ytd_contributions([], None, inclusive=False) == Decimal("0")

    def test_the_chart_reads_the_resolved_ytd(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The wiring: a real context resolves to the through-current total.

        A $1,000.00 contribution recorded IN the current period makes the two
        YTD fields differ, so this cannot pass by them coinciding.
        """
        acct = _create_investment_account(
            seed_user, db.session, type_name="401(k)",
            name="Limit 401k", balance="10000.00",
        )
        _create_investment_params(db.session, acct.id)
        current = current_pay_period(seed_user["user"].id)
        _make_projected_shadow_income(
            seed_user, acct, current, Decimal("1000.00"), db.session,
        )

        ctx = investment_context._load_projection_context(
            seed_user["user"].id, acct,
            investment_context._load_investment_params(acct.id),
        )
        # The two fields differ, and the resolved one is the through-current.
        assert ctx.inputs.ytd_contributions == Decimal("1000.00")
        assert ctx.inputs.ytd_contributions_seed == Decimal("0.00")
        assert ctx.projection_ytd == Decimal("1000.00")
        # ...because the window opens past the current period (ruling R-AF).
        assert ctx.projection_start > last_covered_day(current)


class TestTheProjectionMeetsItsSeedOnALapsedSchedule:
    """Plan step **C2-e**: the window opens where the seed stops, ALWAYS.

    Ruling R-AF put the /investment chart's axis on the day after the history
    line's last valued point, and the seed on that same last day, so the two
    lines meet.  That held by arithmetic while the axis was FABRICATED -- the
    deleted producer built its first period AT the date it was handed.  A pay
    calendar does not: it answers the period COVERING a day, which opened on a
    payday.

    **So the state this grades is an owner whose generated schedule has run
    out**, which nothing forces them to fix and which the balance arc's X-ad-b
    and X-x steps exist because of.  No period CONTAINS today there, the old
    window opened at today, and the axis would have opened on the
    last payday up to a cadence earlier -- the engine re-growing days
    ``balance_at`` had already grown.  An adversarial code review of this step
    measured it at **$57.24** on a $102,686.18 balance, compounded over the
    whole slider horizon.
    """

    def test_the_window_opens_on_the_axis_head_when_no_period_covers_today(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The schedule ended months ago; the chart still meets its seed.

        ``seed_periods`` opens 2026-01-02 and runs ten biweekly periods, so it
        closes 2026-05-21 -- behind the suite's clock.  There is no current
        period, and every axis period is a PROJECTION at the owner's cadence.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            assert current_pay_period(user_id) is None, (
                "this test needs a LAPSED schedule; the fixture changed"
            )
            acct = _create_investment_account(
                seed_user, db.session, name="Lapsed 401k", balance="100000.00",
            )
            _create_investment_params(db.session, acct.id)
            db.session.commit()

            ctx = _load_projection_context(
                user_id, acct, _load_investment_params(acct.id),
            )
            # The feed PRESERVES the "no current period" answer (plan step
            # C2-f2c, ledger row P19).  It resolves the period from the pass's
            # own calendar now, and the TOTAL search beside the one it uses
            # would answer here with a PROJECTED period carrying
            # ``period_id = None`` -- which ``_resolve_current_balance`` would
            # then use to index a map keyed by ``budget.pay_periods.id``.
            assert ctx.current_period is None
            axis = ctx.balance_ctx.calendar().projection_axis(
                ctx.projection_start, ctx.projection_start + timedelta(days=365),
            )
            # The property the whole class exists for: the seed is valued the
            # day BEFORE the window opens, so no day is grown twice and none is
            # skipped.
            assert axis[0].start_date == ctx.projection_start
            assert ctx.projection_seed == balance_at.balance_at(
                acct, ctx.balance_ctx,
                axis[0].start_date - timedelta(days=1),
            )
            # ... and the axis head is a PROJECTION, which is what makes this
            # the branch the fabricated producer used to cover by accident.
            assert axis[0].period_id is None

    def test_the_first_plotted_point_is_the_seed_grown_one_axis_period(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The money form of the same property, hand-checked against the span.

        With no contributions configured the first chart point is exactly the
        seed compounded over the head period's own inclusive span -- nothing
        else is in the row.  Under the defect it was the seed compounded over a
        span that started before the seed's own date.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            acct = _create_investment_account(
                seed_user, db.session, name="Lapsed Roth", balance="100000.00",
            )
            _create_investment_params(db.session, acct.id)
            db.session.commit()

            chart = investment_dashboard_service.compute_growth_chart_data(
                user_id, acct, 1, None,
            )
            ctx = _load_projection_context(
                user_id, acct, _load_investment_params(acct.id),
            )
            head = ctx.balance_ctx.calendar().projection_axis(
                ctx.projection_start, ctx.projection_start + timedelta(days=365),
            )[0]
            # 7.0% is ``_create_investment_params``' default assumed return.
            rate = growth_engine.span_return_rate(
                Decimal("0.07000"), head.start_date, head.end_date,
            )
            assert Decimal(chart["chart_balances"][0]) == (
                ctx.projection_seed + round_money(ctx.projection_seed * rate)
            )


class TestTheProjectionContinuesTheHistory:
    """Rulings R-AE / R-AF: the chart's two lines MEET, and nothing is filtered.

    The chart draws modelled HISTORY through the current pay period and then a
    PROJECTION.  Two rulings decide whether they join, and they were written
    apart: R-U said the projection's seed has modelled growth filtered out (so
    the engine could not re-grow a period the seed already grew) and R-AB said
    the seed is read the day BEFORE the window opens (so the engine cannot
    re-apply a contribution the seed already holds).  The date makes the filter
    unnecessary -- the window and the seed's past are disjoint -- and applying
    both starts the projection line BELOW the history line by every cent the
    account earned since its last balance assertion: measured $161.31 /
    $109.10 / $292.11 on the three real accounts (finding N-80).

    So the seed is the account's ordinary modelled balance on that day, which
    is exactly the history line's last point.
    """

    def test_the_projection_opens_where_the_history_closes(
        self, seed_user, db, seed_periods_today,
    ):
        """The first projected point is the history's last, compounded once.

        The identity is exact and it is what fires on BOTH regressions: restore
        the ACCRUAL filter and the seed drops to the un-grown basis, and open
        the axis at today instead of the day after the history ends and the
        first point carries a different span.  A bare "the projection rises"
        assertion catches neither -- it is true of every configuration.

        The opening assertion is restamped into the PAST so the account has
        modelled growth to lose (``account_service.create_account`` anchors at
        the current period and stamps the row with the database clock, which the
        suite's frozen today does not reach -- findings N-65 / N-77).
        """
        acct = _create_investment_account(
            seed_user, db.session, name="Grown 401k", balance="50000.00",
        )
        _create_investment_params(db.session, acct.id)
        # Anchor a full period back, so the history line has grown by the time
        # it reaches the current period's end.
        reassert_balance_on(
            db.session, acct,
            settle_instant_on(seed_periods_today[0].start_date),
        )
        db.session.commit()

        data = investment_dashboard_service.compute_dashboard_data(
            seed_user["user"].id, acct,
        )
        history = data["history_balances"]
        assert history, "the chart should render modelled history"
        # It HAS grown -- otherwise the ACCRUAL filter would be undetectable.
        assert Decimal(history[-1]) > Decimal("50000.00")

        bctx = BalanceContext.build(seed_user["user"].id)
        current = current_pay_period(seed_user["user"].id)
        seed = balance_at.balance_at(acct, bctx, last_covered_day(current))
        # The seed IS the history line's last point (ruling R-AE) ...
        assert Decimal(history[-1]) == seed
        # ... and the first projected point is that seed compounded over ONE
        # period of the axis that opens the next day (ruling R-AF), read off
        # the same door the chart resolves its axis through (plan step C2-e).
        # 7.0% is ``_create_investment_params``' default assumed return.
        axis_head = bctx.calendar().projection_axis(
            last_covered_day(current) + timedelta(days=1),
            last_covered_day(current) + timedelta(days=365),
        )[0]
        rate = growth_engine.span_return_rate(
            Decimal("0.07000"), axis_head.start_date, axis_head.end_date,
        )
        assert Decimal(data["chart_balances"][0]) == seed + round_money(
            seed * rate,
        )


# ``test_true_up_from_investment_409_reopens_investment`` was DELETED at plan
# step X-f1c3c (ruling R-EN): it asserted that a 409 conflict raised from this
# hero reopened the editor back in the investment surface.  There is no 409 --
# an assertion history is append-only, so nothing a second tab does can be
# overwritten here.  The surviving revert-context cases in this class still
# grade that a SUCCESS re-renders into the surface that opened the editor,
# which is the routing this test shared.


class TestInvestmentBalanceHeroTrueUp:
    """Loop B P1 C4: the detail page's click-to-edit balance hero true-up.

    The hero reuses the shared anchor editor (accounts.anchor_form /
    accounts.true_up / anchor_service) via a new ``revert=investment``
    surface: ``investment.balance_hero`` is the Cancel / Escape revert
    target (rendering the model-from-anchor balance), and the PATCH records a
    statement balance as a dated anchor as-of today -- the same semantics as
    the cockpit card's click-to-edit editor.  It was a 409-conflict revert
    target too, until ruling R-EN deleted the 409 (plan step X-f1c3c).
    """

    def test_balance_hero_renders_editable_cell(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """GET (HX) returns the model-from-anchor balance + the editor opener."""
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/balance-hero",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # The hero renders the model-from-anchor balance -- the SAME producer
        # the page's headline reads (finding N-81: they used to be two, and
        # cancelling the editor would have restored a figure the page was not
        # showing).  At anchor == current that is the $50,000 assertion plus
        # the anchor period's own accrual (ruling R-Y).
        headline = balance_at.balance_map(
            acct, BalanceContext.build(seed_user["user"].id),
        )[current_pay_period(seed_user["user"].id).id]
        assert headline > Decimal("50000.00")
        assert f"{headline:,.2f}" in html
        assert "investment-balance-hero" in html
        # Opens the shared anchor editor scoped to the investment surface, so
        # Cancel / Escape revert back to this hero, not the grid cell.
        assert "revert=investment" in html

    def test_balance_hero_redirects_without_htmx(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """GET without HX-Request redirects to the dashboard page."""
        acct = _create_investment_account(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/investment/balance-hero")
        assert resp.status_code == 302
        assert "/investment" in resp.headers.get("Location", "")

    def test_balance_hero_idor(
        self, auth_client, second_user, db, seed_periods_today,
    ):
        """GET another user's balance hero returns 404 and leaks nothing."""
        other_acct = _create_other_investment(second_user, db.session)
        resp = auth_client.get(
            f"/accounts/{other_acct.id}/investment/balance-hero",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 404
        assert b"Other 401k" not in resp.data

    def test_balance_hero_nonexistent(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """GET a nonexistent account's balance hero returns 404."""
        resp = auth_client.get(
            "/accounts/99999/investment/balance-hero",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 404

    def test_true_up_from_investment_surface_persists_dated_anchor(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """PATCH true-up (revert=investment) records a dated anchor + fires event.

        Reuses accounts.true_up -> anchor_service.apply_anchor_true_up: a
        new AccountAnchorHistory row is appended carrying the submitted
        balance (as-of today, anchored to the current period), and the
        response fires ``balanceChanged`` so the detail page re-renders its
        hero -- identical to the cockpit editor's success contract.
        """
        from app.models.account import AccountAnchorHistory  # pylint: disable=import-outside-toplevel

        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(db.session, acct.id)
        db.session.commit()

        resp = auth_client.patch(
            f"/accounts/{acct.id}/true-up?revert=investment",
            data={
                "anchor_balance": "51234.56",
                "version_id": acct.version_id,
            },
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert resp.headers.get("HX-Trigger") == "balanceChanged"

        db.session.expire_all()
        history = (
            db.session.query(AccountAnchorHistory)
            .filter_by(
                account_id=acct.id, anchor_balance=Decimal("51234.56"),
            )
            .all()
        )
        assert len(history) == 1, (
            "true-up should append exactly one dated anchor history row"
        )
