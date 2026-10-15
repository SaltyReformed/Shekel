"""Savings dashboard service tests: group subtotals, sparklines, property equity, the balance
cell, and the revolving footer.

Split from ``tests/test_services/test_savings_dashboard_service.py`` by ``tools/split`` (ruling
R-BAL301).
"""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import AcctCategoryEnum
from app.models.account import Account
from app.models.ref import AccountType
from app.services import savings_dashboard_service
from app.services import account_service
from app.services.balance_at import BalanceContext
from app.services.pay_calendar import DerivedPeriod

from ._helpers import (
    _projection,
    _create_small_loan,
    _add_savings_account,
    _add_mortgage_account,
    _add_property_account,
)


class TestGroupSubtotals:
    """Tests for the per-category grid subtotals (Loop B Phase 2).

    ``group_subtotals`` carries one ``Decimal`` per category in
    ``grouped_accounts`` -- the sum of the figures that group's tiles show,
    :attr:`~.._types.AccountProjection.shown_balance`: the account's
    ``current_balance``, and for a liability what that HELD balance owes
    (ruling R-CC48, plan step credit_card:CC-5-5c) -- computed in the service
    so the template never does money math.
    """

    def test_asset_subtotal_sums_group_balances(
        self, app, db, seed_user, seed_periods,
    ):
        """The asset subtotal sums every asset account's current balance.

        The seed Checking ($1,000) plus a $4,000 Savings are both assets;
        with no transactions each current_balance is its flat anchor, so:
          asset subtotal = 1000.00 + 4000.00 = 5000.00
        """
        with app.app_context():
            _add_savings_account(
                seed_user, Decimal("4000.00"),
            )

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            # 1000.00 (Checking) + 4000.00 (Savings) = 5000.00
            assert result["group_subtotals"]["asset"] == Decimal("5000.00")

    def test_liability_subtotal_is_positive_owed(
        self, app, db, seed_user, seed_periods,
    ):
        """A liability group subtotals to the positive owed balance.

        A $240,000 mortgage with no confirmed payments holds its origination
        principal as -240,000.00 (ruling R-CC47, plan step
        credit_card:CC-5-5c), and the liability subtotal is what that owes --
        the held balance crossed once through ``shown_figure`` (ruling
        R-CC48), the positive $240,000.00 the tile beside it shows.  The
        template colors it with the danger token, keyed on the category: the
        figure's sign says owed or credit, and the color says liability.
        """
        with app.app_context():
            _add_mortgage_account(
                seed_user, Decimal("240000.00"),
            )

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            subtotals = result["group_subtotals"]
            assert subtotals["liability"] == Decimal("240000.00")
            assert subtotals["liability"] > Decimal("0.00")

    def test_subtotal_keys_match_grouped_accounts(
        self, app, db, seed_user, seed_periods,
    ):
        """Every grouped category has a subtotal, in the same order.

        The template reads ``group_subtotals[label]`` inside its
        ``grouped_accounts.items()`` loop, so the key sets and their order
        must line up exactly.
        """
        with app.app_context():
            _add_savings_account(
                seed_user, Decimal("4000.00"),
            )
            _add_mortgage_account(
                seed_user, Decimal("240000.00"),
            )

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            assert (
                list(result["group_subtotals"].keys())
                == list(result["grouped_accounts"].keys())
            )

    def test_every_account_in_a_group_is_counted(self):
        """A group's subtotal is the sum of ALL its accounts, none dropped.

        Direct unit test of the producer: three asset accounts in one group,
        including a zero-balance one, which must be ADDED rather than skipped
        -- a populated group must never look empty, and a real ``$0.00``
        account is a member like any other.

        **This test used to pin the opposite question** -- that a ``None``
        balance "contributes 0.00 rather than being dropped" -- and plan step
        X-v2 deleted the state it described (ruling R-CA).
        ``AccountProjection.current_balance`` is non-nullable now: the ``None``
        it tested for had exactly one cause (a user with no baseline scenario),
        that user's page never renders, and treating "the app cannot answer
        this balance" as ``$0.00`` was the eighth copy of finding N-113's
        fabrication.  The developer confirmed the behaviour change per
        CLAUDE.md rule 5.
        """
        # pylint: disable=import-outside-toplevel
        from collections import OrderedDict
        from types import SimpleNamespace
        from app.services.savings_dashboard_service._display import (
            _compute_group_subtotals,
        )
        grouped = OrderedDict([(
            "asset",
            [
                _projection(SimpleNamespace(account_type=None),
                            Decimal("600.00")),
                _projection(SimpleNamespace(account_type=None),
                            Decimal("0.00")),
                _projection(SimpleNamespace(account_type=None),
                            Decimal("40.25")),
            ],
        )])
        subtotals = _compute_group_subtotals(grouped)
        # 600.00 + 0.00 + 40.25
        assert subtotals["asset"] == Decimal("640.25")


class TestComputeSparklines:
    """Tests for the conditional per-account sparkline producer."""

    @staticmethod
    def _period(period_id):
        """A REAL :class:`DerivedPeriod` (only ``period_id`` is read).

        It was a ``SimpleNamespace(id=...)`` until pay-calendar plan step
        C2-f2d-3 moved this producer onto the derived calendar.  Building the
        production value rather than a duck type is what makes the case grade
        the attribute the producer actually reads: a stand-in with the wrong
        field name would have kept passing under the old spelling.
        """
        return DerivedPeriod(
            period_id=period_id,
            period_index=period_id - 1,
            start_date=date(2026, 1, 2) + timedelta(days=14 * (period_id - 1)),
            end_date=date(2026, 1, 15) + timedelta(days=14 * (period_id - 1)),
            end_is_projected=False,
        )

    @staticmethod
    def _map(account_id, balances):
        """One account's projection as ``_project_one_account`` emits it.

        It was the ``{account_id, balances, is_liability}`` dict a second
        producer built beside the projections until plan step X-w (ruling
        R-CG, finding N-114).  ``compute_sparklines`` reads the projection
        itself now, so this fixture builds the REAL frozen type -- the
        stand-in account carries only the ``id`` the producer keys on.
        """
        from types import SimpleNamespace  # pylint: disable=import-outside-toplevel
        return _projection(
            SimpleNamespace(id=account_id, account_type=None),
            Decimal("0.00"),
            balances,
        )

    def test_trending_account_is_included(self):
        """An account whose forward balance moves enough gets a series.

        A balance moving 10000 -> 8000 over five periods is a 20% spread,
        far above the 0.5% relative threshold, so it is informative and the
        full window series is returned.  The stand-in carries no account type,
        so it is not a liability and its map is drawn as it stands; a real
        loan's map is HELD (negative when owed) and its line is crossed to
        what it owes (plan step credit_card:CC-5-5c), which
        ``test_a_loans_sparkline_falls_as_what_it_owes_falls`` in
        ``tests/test_services/test_one_liability_sign.py`` pins.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.savings_dashboard_service._net_worth import (
            compute_sparklines,
        )
        periods = [self._period(i) for i in range(1, 6)]
        account_maps = [self._map(7, {
            1: Decimal("10000"), 2: Decimal("9500"), 3: Decimal("9000"),
            4: Decimal("8500"), 5: Decimal("8000"),
        })]

        result = compute_sparklines(account_maps, periods)

        assert result[7] == [
            Decimal("10000"), Decimal("9500"), Decimal("9000"),
            Decimal("8500"), Decimal("8000"),
        ]

    def test_flat_account_is_excluded(self):
        """A flat account (zero spread) is omitted -> figure fallback."""
        # pylint: disable=import-outside-toplevel
        from app.services.savings_dashboard_service._net_worth import (
            compute_sparklines,
        )
        periods = [self._period(i) for i in range(1, 6)]
        account_maps = [self._map(3, {i: Decimal("5000") for i in range(1, 6)})]

        assert compute_sparklines(account_maps, periods) == {}

    def test_too_few_points_excluded(self):
        """Fewer than the 4-point minimum cannot read as a trend."""
        # pylint: disable=import-outside-toplevel
        from app.services.savings_dashboard_service._net_worth import (
            compute_sparklines,
        )
        periods = [self._period(i) for i in range(1, 4)]  # 3 points
        account_maps = [self._map(9, {
            1: Decimal("100"), 2: Decimal("200"), 3: Decimal("300"),
        })]

        assert compute_sparklines(account_maps, periods) == {}

    def test_small_wobble_below_relative_threshold_excluded(self):
        """A spread under 0.5% of the account's magnitude is not a trend.

        Magnitude ~400,100 -> threshold 0.005 * 400,100 = 2,000.50; the
        100-wide wobble is below it, so a big account barely moving is
        treated as flat (the relative threshold keeps the test scale-free).
        """
        # pylint: disable=import-outside-toplevel
        from app.services.savings_dashboard_service._net_worth import (
            compute_sparklines,
        )
        periods = [self._period(i) for i in range(1, 6)]
        account_maps = [self._map(5, {
            1: Decimal("400000"), 2: Decimal("400050"), 3: Decimal("400100"),
            4: Decimal("400050"), 5: Decimal("400000"),
        })]

        assert compute_sparklines(account_maps, periods) == {}

    def test_window_is_capped_to_the_sparkline_period_count(self):
        """The series is sliced to at most _SPARKLINE_PERIODS forward points.

        With 20 forward periods of a clearly-trending account, the returned
        series is capped at the 13-period window rather than the full run.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.savings_dashboard_service._net_worth import (
            _SPARKLINE_PERIODS,
            compute_sparklines,
        )
        periods = [self._period(i) for i in range(1, 21)]  # 20 periods
        balances = {i: Decimal(str(1000 * i)) for i in range(1, 21)}
        account_maps = [self._map(8, balances)]

        result = compute_sparklines(account_maps, periods)

        assert len(result[8]) == _SPARKLINE_PERIODS


class TestPropertyEquityInContext:
    """Tests for the cockpit equity card data (Loop B Phase 2).

    ``property_equity`` lists ``{account, equity}`` for each Property
    account, reusing the Property detail page's home-equity producer so the
    cockpit equity figure equals the detail page's and the debt card's.
    """

    def test_property_equity_present_with_linked_mortgage(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A Property secured by a mortgage reports equity = value - debt.

        A $400,000 Property secured by a $240,000 mortgage (no confirmed
        payments, so the loan resolves to its origination principal):
          equity = 400000.00 - 240000.00 = 160000.00
          ltv    = 240000.00 / 400000.00 = 0.6000
        """
        with app.app_context():
            prop = _add_property_account(
                seed_user, Decimal("400000.00"),
            )
            mortgage = _add_mortgage_account(
                seed_user, Decimal("240000.00"),
            )
            mortgage.collateral_account_id = prop.id
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            equity_data = result["property_equity"]
            assert len(equity_data) == 1
            entry = equity_data[0]
            assert entry.account.id == prop.id
            # 400000.00 - 240000.00 = 160000.00; 240000/400000 = 0.6000
            assert entry.equity.market_value == Decimal("400000.00")
            assert entry.equity.total_debt == Decimal("240000.00")
            assert entry.equity.equity == Decimal("160000.00")
            assert entry.equity.ltv == Decimal("0.6000")

    def test_no_property_yields_empty_list(
        self, app, db, seed_user, seed_periods,
    ):
        """A user with no Property account gets an empty property_equity list."""
        with app.app_context():
            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            assert result["property_equity"] == []

    def test_unencumbered_property_is_all_equity(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A Property with no secured loan reports its full value as equity.

        A $300,000 Property with no linked mortgage:
          total_debt = 0; equity = market value = 300000.00; ltv = 0.0000
        """
        with app.app_context():
            prop = _add_property_account(
                seed_user, Decimal("300000.00"),
            )

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            equity_data = result["property_equity"]
            assert len(equity_data) == 1
            entry = equity_data[0]
            assert entry.account.id == prop.id
            assert entry.equity.total_debt == Decimal("0")
            assert entry.equity.equity == Decimal("300000.00")
            assert entry.equity.ltv == Decimal("0.0000")


class TestAccountBalanceCell:
    """Tests for compute_account_balance_cell -- the cockpit inline-edit revert producer."""

    def test_cell_balance_matches_grid_card(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The cell's current_balance equals the grid card's (one projection, SSOT).

        The Cancel / Escape revert producer must restore the exact figure
        the grid card showed, so it reuses the same per-account projection
        ``compute_dashboard_data`` runs.  Both read the resolver
        ``current_balance`` for the account, so the reverted cell and the
        grid card can never disagree.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            acct_id = seed_user["account"].id

            full = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(user_id),
            )
            grid_balance = next(
                ad.current_balance for ad in full["account_data"]
                if ad.account.id == acct_id
            )

            cell = savings_dashboard_service.compute_account_balance_cell(
                BalanceContext.build(user_id), acct_id,
            )
            assert cell is not None
            assert cell.account.id == acct_id
            assert cell.current_balance == grid_balance

    def test_cell_none_for_foreign_account(
        self, app, db, seed_user, seed_second_user,
    ):
        """A non-owned account id yields None (the route's 404 / IDOR gate).

        The producer loads only the caller's active accounts, so a second
        user's account is never found -- enforcing the 404-for-both
        security rule at the producer rather than a separate ownership query.
        """
        with app.app_context():
            cell = savings_dashboard_service.compute_account_balance_cell(
                BalanceContext.build(seed_user["user"].id),
                seed_second_user["account"].id,
            )
            assert cell is None

    def test_cell_none_for_archived_account(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An archived (inactive) account yields None.

        The producer loads only active accounts; an account archived between
        page load and the Cancel / Escape revert is no longer projected, so
        the producer returns None (a 404) rather than a stale figure.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            account = db.session.get(Account, acct_id)
            account.is_active = False
            db.session.commit()

            cell = savings_dashboard_service.compute_account_balance_cell(
                BalanceContext.build(seed_user["user"].id), acct_id,
            )
            assert cell is None

    def test_cell_flags_liability_for_loan(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A loan (LIABILITY category) cell carries is_liability=True.

        The cell contract feeds ``_cockpit_balance.html`` the flag that
        paints the owed balance in the danger token -- the same treatment
        the liabilities chip, group subtotal, and diverging-bar segment
        already use -- keyed on the account's category id, never the
        figure's sign (polish audit P-AC4).  A reverted liability cell
        therefore keeps its danger ink.
        """
        with app.app_context():
            loan = _create_small_loan(seed_user, db.session)

            cell = savings_dashboard_service.compute_account_balance_cell(
                BalanceContext.build(seed_user["user"].id), loan.id,
            )
            assert cell is not None
            assert cell.is_liability is True

    def test_cell_flags_asset_as_non_liability(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A Checking account (non-LIABILITY category) cell is_liability=False.

        The seed_user account is a Checking type (ASSET category), so its
        balance keeps the plain number ink -- the danger token is reserved
        for the liability side (polish audit P-AC4).
        """
        with app.app_context():
            cell = savings_dashboard_service.compute_account_balance_cell(
                BalanceContext.build(seed_user["user"].id),
                seed_user["account"].id,
            )
            assert cell is not None
            assert cell.is_liability is False


class TestTheRevolvingFooterCountsWhatEachAccountOwes:
    """The footer's ``excludes $X revolving`` is each account's OWED, floored.

    Developer ruling **R-CC49** (plan step credit_card:CC-5-5a, ledger row
    **CC-354**): "Each account's owed amount, floored at zero, then summed. A
    credit on one card is not debt and does not pay down another card; the
    footer counts the debt a payoff date leaves out."  Owed is the one
    flip, :func:`app.services.liability_sign.owed`, of the held balance.

    The three rules the ruling chose between each give a DIFFERENT figure on
    the second case, so it grades the choice and not just the sign: ``abs()``
    of each balance (the shipped rule) says ``$8,050.00``, the net sum says
    ``$7,950.00``, and the ruled per-account floor says ``$8,000.00``.
    """

    def test_a_card_holding_a_credit_adds_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """A card the issuer owes ``$50.00`` is no revolving debt.

        Anchored at ``+50.00`` held (the issuer owes the owner), so::

            owed                     = -(+50.00)          = -50.00
            floored                  = max(-50.00, 0.00)  =   0.00
            debt_without_payoff_date =                        0.00

        ``abs()`` reported ``$50.00`` here: a credit captioned as debt.  The
        configured loan is there because the footer is a caveat ON the loans'
        payoff date: with no loan there is no debt summary to carry it.
        """
        with app.app_context():
            # Pylint: ``import-outside-toplevel`` -- test-local helpers,
            # matching this module's convention of importing where used.
            from tests._test_helpers import (  # pylint: disable=import-outside-toplevel
                create_account_of_type, create_loan_account,
            )
            create_loan_account(
                seed_user, db.session, name="Car Loan",
                principal=Decimal("12000.00"), rate=Decimal("0.05000"),
                term=24, origination_date=date(2026, 1, 1),
            )
            create_account_of_type(
                seed_user, db.session, "Credit Card", "Amex",
                anchor_balance=Decimal("50.00"),
            )
            db.session.commit()

            summary = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )["debt_summary"]

            assert summary.debt_without_payoff_date == Decimal("0.00")

    def test_a_credit_does_not_pay_down_another_accounts_debt(
        self, app, db, seed_user, seed_periods,
    ):
        """The ruling's own example: ``$8,000.00``, not ``$8,050`` or ``$7,950``.

        Every liability with no payoff model, each anchored at its held
        balance, beside a configured loan the footer must NOT count::

            Visa (card)                  held -1,000.00  owed  1,000.00
            Amex (card)                  held    +50.00  owed    -50.00 -> 0.00
            Auto loan (no loan terms)    held -5,000.00  owed  5,000.00
            Family loan (custom type)    held -2,000.00  owed  2,000.00
            Car Loan (configured loan)   -- has a payoff model, excluded --

            debt_without_payoff_date = 1,000.00 + 0.00 + 5,000.00 + 2,000.00 = 8,000.00

        The auto loan is an AMORTIZING type with no ``LoanParams``, so the
        seam answers it from the replay (held) and it has no payoff model; the
        family loan is a user-created LIABILITY type with no flags.
        """
        with app.app_context():
            # Pylint: ``import-outside-toplevel`` -- test-local helpers,
            # matching this module's convention of importing where used.
            from tests._test_helpers import (  # pylint: disable=import-outside-toplevel
                create_account_of_type, create_loan_account,
            )
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
            family_type = AccountType(
                name="Family loan",
                category_id=ref_cache.acct_category_id(
                    AcctCategoryEnum.LIABILITY,
                ),
                user_id=seed_user["user"].id,
            )
            db.session.add(family_type)
            db.session.flush()
            db.session.add(account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=family_type.id,
                    name="Family loan",
                    anchor_balance=Decimal("-2000.00"),
                ),
            ))
            db.session.commit()

            summary = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )["debt_summary"]

            # 1,000.00 + 0.00 + 5,000.00 + 2,000.00 (the Car Loan excluded).
            assert summary.debt_without_payoff_date == Decimal("8000.00")

    def test_only_what_a_liability_with_no_payoff_model_owes_counts(
        self, app, db, seed_user, seed_periods,
    ):
        """Each clause of the rule, pinned where the floor alone cannot see it.

        Under the floor every POSITIVE owed figure is what survives, so an
        account whose owed figure is negative adds ``0.00`` whether or not the
        filter admitted it.  Each account here is chosen so that ONE wrong rule
        moves the total (CC-5-5a's adversarial review, finding F5): dropping
        the ``is_liability`` clause admits the overdrawn checking, whose owed
        figure is ``+300.00``; an ``abs()`` on a non-card liability counts the
        auto loan's credit; and dropping the ``loan`` clause is graded by the
        configured Car Loan, which owes and so would count::

            Visa (card)                 held -1,000.00 -> owed 1,000.00  counted
            Overdrawn checking (ASSET)  held   -300.00 -> not a liability
                (owed() would read +300.00 if ``is_liability`` were dropped)
            Auto loan, no terms         held   +300.00 -> owed -300.00 -> 0.00
                (a CREDIT under rulings R-CC47 / R-CC52; ``abs()`` of a
                non-card liability would count 300.00)
            Car Loan (configured loan)  -- has a payoff model, excluded --

            debt_without_payoff_date = 1,000.00

        and a configured loan OWING money is excluded: a held ``-100.00`` owes
        ``+100.00`` (``owed()`` of it), which only the ``loan`` clause keeps
        out.  That half is built from the REAL Car Loan projection with its
        figure replaced -- only the figure is synthetic, the loan detail the
        filter reads is the one production built.  It said an OVERPAID loan
        until plan step credit_card:CC-5-5c, when a configured loan's figure
        was its owed amount and a negative one meant overpaid; the seam
        reports it HELD since then (ruling R-CC47), so ``-100.00`` is a loan
        owing $100.00, and the real Car Loan (holding its -12,000.00 opening)
        already makes the ``loan`` clause load-bearing in the first assertion
        below.  An overpaid loan would now hold ``+100.00``, owe ``-100.00``,
        and floor to ``0.00`` whether or not the clause stood.
        """
        with app.app_context():
            # Pylint: ``import-outside-toplevel`` -- test-local helpers,
            # matching this module's convention of importing where used.
            # pylint: disable=import-outside-toplevel
            from tests._test_helpers import (
                create_account_of_type, create_loan_account,
            )
            from app.services.savings_dashboard_service._debt_line import (
                debt_without_payoff_model,
            )
            car_loan = create_loan_account(
                seed_user, db.session, name="Car Loan",
                principal=Decimal("12000.00"), rate=Decimal("0.05000"),
                term=24, origination_date=date(2026, 1, 1),
            )
            create_account_of_type(
                seed_user, db.session, "Credit Card", "Visa",
                anchor_balance=Decimal("-1000.00"),
            )
            create_account_of_type(
                seed_user, db.session, "Checking", "Overdrawn checking",
                anchor_balance=Decimal("-300.00"),
            )
            create_account_of_type(
                seed_user, db.session, "Auto Loan", "Auto loan, no terms",
                anchor_balance=Decimal("300.00"),
            )
            db.session.commit()

            data = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )

            # 1,000.00 (the Visa) + 0.00 (the auto loan's credit); the
            # overdrawn checking and the Car Loan are not in the rule.
            assert data["debt_summary"].debt_without_payoff_date == Decimal("1000.00")

            loan_ad = next(
                ad for ad in data["account_data"] if ad.account.id == car_loan.id
            )
            assert loan_ad.loan is not None
            # A configured loan owing $100.00 holds -100.00 (R-CC47).
            owing_100 = replace(loan_ad, current_balance=Decimal("-100.00"))
            # owed(-100.00) = +100.00 would count if the ``loan`` clause went.
            assert debt_without_payoff_model([owing_100]) == Decimal("0.00")
