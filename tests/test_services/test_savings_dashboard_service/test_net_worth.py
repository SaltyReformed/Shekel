"""Savings dashboard service tests: the net-worth hero, series, producer edge cases, category
classifier and composition.

Split from ``tests/test_services/test_savings_dashboard_service.py`` by ``tools/split`` (ruling
R-BAL301).
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.models.ref import AccountType
from app.services import savings_dashboard_service
from app.services import account_service
from app.services.balance_at import BalanceContext

from tests._test_helpers import last_covered_day

from ._helpers import _projection, _add_savings_account, _add_mortgage_account


class TestNetWorthHero:
    """Tests for the cockpit's today net-worth figures.

    ``compute_net_worth_today`` reduces over each account's resolver
    ``current_balance``, which is HELD for every kind -- negative when owed, a
    configured loan's included (ruling R-CC47, plan step credit_card:CC-5-5c):
    total assets sums the assets' balances, total liabilities is what the
    liabilities OWE (``liability_sign.owed`` of each balance, summed), net
    worth is the plain sum of every balance -- equal to assets minus
    liabilities by arithmetic -- and liquid is the liquid-account balance sum.
    """

    def test_assets_minus_liabilities(
        self, app, db, seed_user, seed_periods,
    ):
        """Net worth is total assets minus what the liabilities owe.

        Checking ($1,000) + Savings ($4,000) are assets; a $240,000
        mortgage is a liability.  With no transactions each asset's
        ``current_balance`` equals its flat anchor and the mortgage, never
        paid, holds its -240,000.00 opening (it owes $240,000.00), so:
          total_assets       = 1000.00 + 4000.00 = 5000.00
          total_liabilities  = owed(-240000.00) = 240000.00
          net_worth          = 5000.00 - 240000.00 = -235000.00
        """
        with app.app_context():
            _add_savings_account(
                seed_user, Decimal("4000.00"),
            )
            _add_mortgage_account(
                seed_user, Decimal("240000.00"),
            )

            nw = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )["net_worth"]

            # 1000.00 + 4000.00 = 5000.00
            assert nw.today.total_assets == Decimal("5000.00")
            # The mortgage owes its origination principal (it holds minus it).
            assert nw.today.total_liabilities == Decimal("240000.00")
            # 5000.00 - 240000.00 = -235000.00
            assert nw.today.net_worth == Decimal("-235000.00")

    def test_total_liabilities_is_positive_magnitude(
        self, app, db, seed_user, seed_periods,
    ):
        """A liability contributes a POSITIVE total_liabilities, not negative."""
        with app.app_context():
            _add_mortgage_account(
                seed_user, Decimal("240000.00"),
            )

            nw = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )["net_worth"]

            assert nw.today.total_liabilities == Decimal("240000.00")
            assert nw.today.total_liabilities > Decimal("0.00")

    def test_a_negative_balance_liability_still_adds_its_magnitude(
        self, app, db, seed_user, seed_periods,
    ):
        """A liability that HOLDS a negative balance adds what it owes.

        The reduction is ``total_liabilities += ad.owed`` --
        ``liability_sign.owed`` of the held balance (ruling R-CC47, plan step
        credit_card:CC-5-5c) -- and net worth is the plain sum of every held
        balance.  A card owing $500.00 holds -500.00.  It is not the only
        negatively-held liability in this file: since CC-5-5c every configured
        loan holds its debt negative too, so a regression to a bare
        ``+= balance`` also fails every mortgage case here.

        Checking ($1,000) is the only asset; the card anchors at -$500.00:
          total_assets       = 1000.00
          total_liabilities  = owed(-500.00) = 500.00
          net_worth          = 1000.00 + (-500.00) = 500.00

        Summed as the held balance instead the card would read -500.00 of
        liabilities and ADD to net worth (1000.00 - -500.00 = 1500.00),
        reporting a debt as an asset.  What this case cannot tell apart is
        ``owed()`` from the ``abs()`` it replaced -- they agree on a card that
        owes; the card holding a credit in
        ``tests/test_services/test_one_liability_sign.py`` is the case that
        parts them.
        """
        with app.app_context():
            cc_type = (
                db.session.query(AccountType)
                .filter_by(name="Credit Card", user_id=None).one()
            )
            card = account_service.create_account(account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=cc_type.id,
                name="Visa",
                anchor_balance=Decimal("-500.00"),
            ))
            db.session.add(card)
            db.session.commit()

            nw = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )["net_worth"]

            # Seed Checking only.
            assert nw.today.total_assets == Decimal("1000.00")
            # owed(-500.00) = 500.00 -- what it owes, not the held balance.
            assert nw.today.total_liabilities == Decimal("500.00")
            # 1000.00 - 500.00 = 500.00 (NOT 1500.00, the held-balance answer).
            assert nw.today.net_worth == Decimal("500.00")

    def test_liquid_excludes_non_liquid(
        self, app, db, seed_user, seed_periods,
    ):
        """Liquid sums only is_liquid accounts; a mortgage is excluded.

        Checking ($1,000, liquid) + Savings ($4,000, liquid) count; the
        $240,000 mortgage (non-liquid liability) does not:
          liquid = 1000.00 + 4000.00 = 5000.00
        while total_assets (also 5000.00 here) and net worth carry the
        mortgage.  Liquid != assets in general; this fixture keeps them
        equal only because the sole non-liquid account is the liability.
        """
        with app.app_context():
            _add_savings_account(
                seed_user, Decimal("4000.00"),
            )
            _add_mortgage_account(
                seed_user, Decimal("240000.00"),
            )

            nw = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )["net_worth"]

            # 1000.00 + 4000.00 = 5000.00 (mortgage excluded from liquid).
            assert nw.today.liquid == Decimal("5000.00")


class TestNetWorthSeries:
    """Tests for the cockpit's forward net-worth trend series."""

    def test_default_series_spans_history_tail_and_forward(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Series leads with the honest history tail, then the forward run.

        ``seed_periods_today`` places today in period index 4 of a
        10-period window and anchors the seed Checking account at period
        index 0.  Checking is a PLAIN (cash) account, so the honest history
        reaches back to its anchor (index 0): the tail is the 4 elapsed
        periods (indices 0-3, fewer than the 6-period cap) and the forward
        run is indices 4-9, so the series spans all 10 periods and
        ``current_index`` -- the count of leading history points, the
        solid/dashed boundary -- is 4.  The expected values are
        fixture-derived literals, NOT re-derived from the production window
        logic, so an off-by-one there surfaces here.  (``seed_periods``, a
        fixed 2026-01-02 window now entirely in the past, has no current
        period -- it would make this a vacuous empty series.)
        """
        with app.app_context():
            series = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )["net_worth"].series

            # history tail (indices 0-3) + forward (indices 4-9) = 10 points
            assert len(series.periods) == 10
            assert len(series.net) == 10
            # The parallel ``assets`` / ``liabilities`` totals were deleted at
            # plan step X-s1 (one fact under two keys); the bands they summed
            # carry the same length.
            assert len(series.composition["asset"]) == 10
            assert len(series.composition["liability"]) == 10
            # current period (index 4) sits at position 4: 4 history points
            # precede it (indices 0, 1, 2, 3).
            assert series.current_index == 4
            # The window's IDENTITY, asserted through the field the chart
            # actually reads.  It read ``p.period_index`` until plan step X-w6
            # deleted that field for having no production consumer (ruling
            # R-CL) -- and this test was its only meaningful reader, so the
            # property it pinned moves onto ``end_date`` rather than being lost:
            # the first four points ARE the four elapsed periods, and the fifth
            # IS the current one.
            assert [p.end_date for p in series.periods[:4]] == [
                last_covered_day(seed_periods_today[i]) for i in range(4)
            ]
            assert series.periods[4].end_date == last_covered_day(
                seed_periods_today[4],
            )

    def test_net_equals_assets_minus_liabilities_each_point(
        self, app, db, seed_user, seed_periods,
    ):
        """series net[i] == sum(asset bands)[i] - liability band[i], every point.

        Holds even with a mortgage whose amortization drives the
        liability band down period by period: the asset-plus /
        liability-minus split shares one sum with the net reduction.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.savings_dashboard_service._net_worth import (
            _ASSET_BANDS,
        )
        with app.app_context():
            _add_savings_account(
                seed_user, Decimal("4000.00"),
            )
            _add_mortgage_account(
                seed_user, Decimal("240000.00"),
            )

            series = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )["net_worth"].series

            assert len(series.net) > 0
            for i in range(len(series.net)):
                # ``net`` is the asset bands less the liability band.  It was
                # asserted against the parallel ``assets`` / ``liabilities``
                # totals until plan step X-s1 deleted those -- they were the
                # same sums under a second name, so this reads them from the
                # bands the chart actually draws.
                assert series.net[i] == (
                    sum(
                        (series.composition[b][i] for b in _ASSET_BANDS),
                        Decimal("0.00"),
                    )
                    - series.composition["liability"][i]
                )

    def test_series_liability_band_holds_a_negative_balance_magnitude(
        self, app, db, seed_user, seed_periods,
    ):
        """A negatively-held liability adds what it OWES to the series band.

        The per-period reduction (``_sum_composition_at_period``) crosses each
        liability's held balance through ``liability_sign.owed`` itself -- a
        SECOND site from the hero's (ruling R-CC47, plan step
        credit_card:CC-5-5c; both took ``abs`` until then) -- and this is what
        pins it.  A card owing $500.00 holds -500.00, and with no rows its
        fold holds its anchor across every point:
          composition["liability"][i]   = owed(-500.00) = 500.00
          net[i]                        = 1000.00 - 500.00 = 500.00

        Summing the held balance instead, the band would read -500.00 and
        net[i] would read 1500.00 -- the card ADDING to net worth -- while the
        today hero on the SAME page still read 500.00.  Two producers
        contradicting each other on one screen is the failure this arc exists
        to end, so both crossing sites need their own control; the hero's is
        ``test_a_negative_balance_liability_still_adds_its_magnitude``.  Like
        that one, this cannot tell ``owed()`` from ``abs()``; the card holding
        a credit in ``tests/test_services/test_one_liability_sign.py`` does.
        """
        with app.app_context():
            cc_type = (
                db.session.query(AccountType)
                .filter_by(name="Credit Card", user_id=None).one()
            )
            card = account_service.create_account(account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=cc_type.id,
                name="Visa",
                anchor_balance=Decimal("-500.00"),
            ))
            db.session.add(card)
            db.session.commit()

            series = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )["net_worth"].series

            assert len(series.composition["liability"]) > 0
            for i in range(len(series.composition["liability"])):
                # owed(-500.00) = 500.00 at every point (no rows move the fold).
                assert series.composition["liability"][i] == Decimal(
                    "500.00",
                )
                # Seed Checking 1000.00 - 500.00 = 500.00 (NOT 1500.00).
                assert series.net[i] == Decimal("500.00")

    def test_current_period_point_equals_hero_for_liquid_only(
        self, app, db, seed_user, seed_periods,
    ):
        """For a CHECKING/SAVINGS-only fixture, the current-period series
        point equals the today hero.

        With no transactions every balance is flat, so the current
        period's net worth (``series.net[current_index]``) equals the
        today hero:
          Checking 1000.00 + Savings 4000.00 = 5000.00.
        A flat liquid-only set has the same value at every point, so the
        history-tail points equal it too -- asserted to lock that the
        widened window did not skew the figures.
        """
        with app.app_context():
            _add_savings_account(
                seed_user, Decimal("4000.00"),
            )

            nw = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )["net_worth"]

            current = nw.series.current_index
            # 1000.00 + 4000.00 = 5000.00, identical hero and current point.
            assert nw.today.net_worth == Decimal("5000.00")
            assert nw.series.net[current] == Decimal("5000.00")
            assert nw.series.net[current] == nw.today.net_worth
            # Flat liquid-only: every trend point (history tail + forward).
            assert all(v == Decimal("5000.00") for v in nw.series.net)

    def test_current_period_point_agrees_with_hero_for_amortizing_loan(
        self, app, db, seed_user, seed_periods_today,
    ):
        """For a loan, the current-period series point EQUALS the hero.

        This test used to assert the opposite -- that the two figures
        deliberately "read DIFFERENT sources" and must keep diverging.  They must
        not.  A page whose loan tile and whose net-worth trend disagree about the
        same loan on the same day is a page contradicting itself, and that
        contradiction was the symptom that opened this whole arc::

            the /savings loan tile and the net-worth trend's own 'today'
            point disagree: tile=240000.00 trend=236544.21

        The divergence had one cause: the hero read the loan's honest balance,
        while the trend's dense map walked the amortization SCHEDULE and let ~14
        unpaid, purely projected installments pay the principal down.  The mortgage
        has NO confirmed payments, so not one dollar of principal was ever paid,
        and $240,000 is the only true answer.  Both producers now read the confirmed
        ledger for a period that has begun, so both give it.

        The $240,000 is STATED today at setup (the mid-life-import shape, the
        setup door's own ``tracking_start`` since plan step R20) so that the
        future amortizes: since plan step R16-b-2 (ruling R-R71) the plan
        charges every contractual month after the loan's latest assertion, and
        with only its 2025 origination the fourteen unrecorded months would
        stand as arrears and the trend would FALL (a delinquent balance grows).
        The assertion moves nothing today -- the balance is $240,000 either way.

        Checking $1,000 and a $240,000 never-paid mortgage:
          hero net        = 1000.00 - 240000.00 = -239000.00
          series[current] = 1000.00 - 240000.00 = -239000.00  (agrees)
          series[future]  = 1000.00 - (amortized < 240000.00) > -239000.00
        """
        with app.app_context():
            _add_mortgage_account(
                seed_user, Decimal("240000.00"), tracked_from=date.today(),
            )

            nw = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )["net_worth"]

            current = nw.series.current_index
            # No confirmed payment: 1000.00 (checking) - 240000.00 (mortgage).
            assert nw.today.net_worth == Decimal("-239000.00")
            # The tile and the trend's own 'today' point agree, to the cent.
            # The message reads the SAME attributes the assertion does (plan
            # step X-w6).  It kept the pre-X-w3 subscripts, which raise
            # ``TypeError`` on the frozen region -- and an assert message is
            # lazy, so it would have raised at the one moment it exists for:
            # the moment the tile and the trend disagree.
            assert nw.series.net[current] == nw.today.net_worth, (
                f"the /savings hero ({nw.today.net_worth}) and the net-worth "
                f"trend's current-period point "
                f"({nw.series.net[current]}) disagree; the page is "
                f"contradicting itself about the same loan on the same day"
            )
            # Amortization is real in the FUTURE, where the projection answers:
            # the last trend point sits above the flat-debt line.
            assert nw.series.net[-1] > nw.today.net_worth


class TestNetWorthProducerEdgeCases:
    """Edge-case coverage for the cockpit net-worth producers."""

    def test_no_accounts_today_is_all_zero(self):
        """With no accounts the today figures are all zero."""
        # pylint: disable=import-outside-toplevel
        from app.services.savings_dashboard_service._net_worth import (
            compute_net_worth_today,
        )
        today = compute_net_worth_today([])
        assert today.net_worth == Decimal("0.00")
        assert today.total_assets == Decimal("0.00")
        assert today.total_liabilities == Decimal("0.00")
        assert today.liquid == Decimal("0.00")

    def test_no_projections_series_is_empty_window(self):
        """With no projections and no forward periods the series is empty.

        The field set is pinned for the reason the key set was: ``assets`` and
        ``liabilities`` were deleted at plan step X-s1 for being the band sums
        under a second key, and a producer that re-publishes them fails here.
        ``current_index`` is a FIELD rather than a key the caller mutates on
        afterwards since plan step X-w3 (ruling R-CI), so it is in the literal.
        """
        # pylint: disable=import-outside-toplevel
        from dataclasses import fields
        from app.services.savings_dashboard_service._net_worth import (
            _COMPOSITION_BANDS,
            compute_net_worth_series,
        )
        series = compute_net_worth_series([], [], 0)
        assert series.periods == []
        assert series.net == []
        assert series.current_index == 0
        assert {f.name for f in fields(series)} == {
            "periods", "net", "composition", "current_index",
        }
        # Every composition band is present but empty (no periods to sum).
        assert series.composition == {band: [] for band in _COMPOSITION_BANDS}

    def test_liabilities_only_today_is_negative(self):
        """An accounts-set of only liabilities yields negative net worth.

        One liability account and no assets.  Its ``current_balance`` is HELD
        (plan step credit_card:CC-5-5c), so it holds -500.00 and owes 500.00:
          total_assets      = 0.00
          total_liabilities = owed(-500.00) = 500.00
          net_worth         = 0.00 - 500.00 = -500.00
        Classification is by the account type's category_id, so this test
        builds a stand-in account whose type's category is the LIABILITY
        ref id (IDs for logic, never a name string).
        """
        # pylint: disable=import-outside-toplevel
        from types import SimpleNamespace
        from app.enums import AcctCategoryEnum
        from app.services.savings_dashboard_service._net_worth import (
            compute_net_worth_today,
        )
        liability_cat_id = ref_cache.acct_category_id(
            AcctCategoryEnum.LIABILITY,
        )
        acct_type = SimpleNamespace(
            category_id=liability_cat_id, is_liquid=False,
        )
        account = SimpleNamespace(account_type=acct_type)
        today = compute_net_worth_today([
            _projection(account, Decimal("-500.00")),
        ])
        assert today.total_assets == Decimal("0.00")
        assert today.total_liabilities == Decimal("500.00")
        # 0.00 - 500.00 = -500.00
        assert today.net_worth == Decimal("-500.00")
        assert today.liquid == Decimal("0.00")

    def test_single_asset_account(self):
        """A single non-liability liquid account: net worth equals its balance.

        One asset account with a 750.00 current balance:
          net_worth = total_assets = liquid = 750.00, liabilities 0.00.
        """
        # pylint: disable=import-outside-toplevel
        from types import SimpleNamespace
        from app.services.savings_dashboard_service._net_worth import (
            compute_net_worth_today,
        )
        acct_type = SimpleNamespace(category_id=-999, is_liquid=True)
        account = SimpleNamespace(account_type=acct_type)
        today = compute_net_worth_today([
            _projection(account, Decimal("750.00")),
        ])
        assert today.net_worth == Decimal("750.00")
        assert today.total_assets == Decimal("750.00")
        assert today.total_liabilities == Decimal("0.00")
        assert today.liquid == Decimal("750.00")

    def test_zero_balance_account_contributes_zero_not_absent(self):
        """A zero-balance asset contributes 0.00, it is not skipped.

        Two asset accounts, one 600.00 and one 0.00 (a real zero, not a
        missing balance).  The zero account still participates:
          total_assets = 600.00 + 0.00 = 600.00, net worth 600.00.
        Asserting 600.00 (not, say, an absent-account artifact) pins that
        a zero balance is summed rather than dropped.
        """
        # pylint: disable=import-outside-toplevel
        from types import SimpleNamespace
        from app.services.savings_dashboard_service._net_worth import (
            compute_net_worth_today,
        )
        acct_type = SimpleNamespace(category_id=-999, is_liquid=True)
        funded = SimpleNamespace(account_type=acct_type)
        empty = SimpleNamespace(account_type=acct_type)
        today = compute_net_worth_today([
            _projection(funded, Decimal("600.00")),
            _projection(empty, Decimal("0.00")),
        ])
        # 600.00 + 0.00 = 600.00 (the zero account is summed, not absent).
        assert today.net_worth == Decimal("600.00")
        assert today.total_assets == Decimal("600.00")
        assert today.liquid == Decimal("600.00")


class TestCategoryClassifier:
    """Tests for the shared id-based category classifier (P-AC1 Loop B P1).

    The classification is ONE answer per account per render since plan step
    X-z7 (ruling R-CT): :func:`app.services.account_category.account_category`
    resolves it onto :attr:`~.._types.AccountProjection.category`, and both
    consumers read that member -- the grid grouping and the net-worth
    composition split name a band through ``_display.category_key``, and the
    asset-vs-liability sign compares the same member against one enum value.
    So a band and the grid group cannot disagree, and neither can the sign.

    It classifies by the account type's integer ``category_id`` (IDs for logic,
    never a ``.name`` string).
    """

    def test_key_by_category_member(self, app):
        """Each real category names its display key; no category is 'other'."""
        with app.app_context():
            # pylint: disable=import-outside-toplevel
            from app.enums import AcctCategoryEnum
            from app.services.savings_dashboard_service._display import (
                category_key,
            )
            cases = [
                (AcctCategoryEnum.ASSET, "asset"),
                (AcctCategoryEnum.LIABILITY, "liability"),
                (AcctCategoryEnum.RETIREMENT, "retirement"),
                (AcctCategoryEnum.INVESTMENT, "investment"),
            ]
            for member, expected in cases:
                assert category_key(member) == expected
            # ``None`` -- the app models no category for this account -- is the
            # only path to the fall-through band.
            assert category_key(None) == "other"


class TestNetWorthComposition:
    """Tests for the 2-year net-worth series' per-category composition split.

    ``compute_net_worth_series`` now also emits ``composition`` -- one Decimal
    band series per category (asset / retirement / investment / other /
    liability).  The split shares ONE per-period sum with the ``assets`` /
    ``liabilities`` / ``net`` totals, so it reconciles to them by
    construction, and each band reads the same balance the grid renders.
    """

    def test_bands_reconcile_to_totals_each_point(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Composition bands sum to assets / liabilities / net at every point.

        With Checking ($1,000) + Savings ($4,000) as assets, a 401(k) as the
        retirement band, and a $240,000 mortgage as the liability band, the
        asset-side bands must sum to ``assets`` and the liability band must
        equal ``liabilities`` at every trend point -- the split cannot drift
        from the totals it is derived from.
        """
        with app.app_context():
            # pylint: disable=import-outside-toplevel
            from tests._test_helpers import make_investment_account
            periods = seed_periods_today
            _add_savings_account(seed_user, Decimal("4000.00"))
            make_investment_account(
                seed_user, db.session, periods[0], Decimal("10000.00"),
            )
            _add_mortgage_account(seed_user, Decimal("240000.00"))

            series = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )["net_worth"].series
            comp = series.composition
            asset_bands = ("asset", "retirement", "investment", "other")

            assert len(series.net) > 0
            for i in range(len(series.net)):
                asset_side = sum(
                    (comp[band][i] for band in asset_bands), Decimal("0"),
                )
                # asset-side bands sum to the asset total; the liability band
                # is the liability total; net is their difference.
                # The bands ARE the totals: the parallel ``assets`` /
                # ``liabilities`` lanes this used to reconcile against were
                # deleted at plan step X-s1 for being that same sum under a
                # second key, so what remains is the identity itself.
                assert series.net[i] == (
                    asset_side - comp["liability"][i]
                )

    def test_bands_place_each_account_in_its_category(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The 401(k) lands in the retirement band, not the asset band.

        A reconciliation test alone cannot catch a mis-banding (a 401(k) in
        the asset band still sums into ``assets``); this pins that the
        retirement band at the current period equals the 401(k)'s own current
        balance, and the empty investment / other bands stay zero.
        """
        with app.app_context():
            # pylint: disable=import-outside-toplevel
            from tests._test_helpers import make_investment_account
            periods = seed_periods_today
            k401 = make_investment_account(
                seed_user, db.session, periods[0], Decimal("10000.00"),
            )

            data = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            series = data["net_worth"].series
            comp = series.composition
            current = series.current_index

            k401_balance = next(
                ad.current_balance for ad in data["account_data"]
                if ad.account.id == k401.id
            )
            # The 401(k) is the only retirement account, so the retirement
            # band at the current period IS its balance -- proving it is not
            # summed into the asset band.
            assert comp["retirement"][current] == k401_balance
            assert comp["retirement"][current] > Decimal("0.00")
            assert comp["investment"][current] == Decimal("0.00")
            assert comp["other"][current] == Decimal("0.00")
