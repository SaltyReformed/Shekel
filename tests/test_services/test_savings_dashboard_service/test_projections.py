"""Savings dashboard service tests: the projection shape, and the dense map that is total and says
so.

Split from ``tests/test_services/test_savings_dashboard_service.py`` by ``tools/split`` (ruling
R-BAL301).
"""

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import AcctCategoryEnum
from app.services import savings_dashboard_service
from app.services.balance_at import BalanceContext
from app.services.savings_dashboard_service._types import AccountProjection

from tests._test_helpers import all_periods, current_pay_period

from ._helpers import _derived_period, _projection, _create_small_loan


class TestTheProjectionShape:
    """The per-account projection is a value object, and its shape is pinned.

    Plan step X-t1, finding N-111.  ``_project_one_account`` returned an
    untyped dict with five required keys and four optional ones, and KEY
    MEMBERSHIP was the type discriminator: ``"loan_figures" in ad`` meant "this
    account is a loan".  Two measured defects came out of that container --
    B-16 (a retired loan reported as debt still owed, because the dict copied
    the seam's ``LoanFigures`` field by field and dropped the one field the
    question needed) and N-98 (19 years of contradiction between two surfaces
    on one page).

    These pin what the container now guarantees.  A future step that
    re-flattens the loan half, or re-stores a value the projection can derive,
    fails here.
    """

    def test_the_field_set_is_exactly_what_consumers_read(self):
        """The projection's fields are pinned, and the loan half is ONE of them.

        The analogue of the Horizon's published-key pin: a field added without
        a consumer, or the loan detail re-flattened into parallel
        ``loan_figures`` / ``loan_params`` fields (the shape plan step X-s2 had
        to unpick at the seam-batch layer), fails this literal.

        ``balances`` joined the set at plan step X-w (ruling R-CG, finding
        N-114), and its consumers are named so this stays the
        "field added without a consumer" gate it was written to be: the
        per-period net-worth reduction (``_sum_composition_at_period``), the
        card sparklines (``compute_sparklines``), and -- for every kind but a
        loan -- the current balance and the 3 / 6 / 12-month horizons that
        ``_project_one_account`` reads out of it.

        ``category`` joined at plan step X-z7 (ruling R-CT), and its consumers
        are likewise named: :attr:`~.._types.AccountProjection.is_liability`
        (the net-worth sign, the liability band, the revolving-debt figure and
        the cell's danger ink) and ``_display.category_key`` (the grid group
        card and the chart band).  It REPLACED a per-account container --
        X-z2's ``{account_id: category_key}`` map -- rather than adding a
        field beside one, which is the direction ruling R-CG set.
        """
        # pylint: disable=import-outside-toplevel
        from dataclasses import fields
        assert {f.name for f in fields(AccountProjection)} == {
            "account", "current_balance", "balances", "projected",
            "needs_setup", "category", "interest_params", "investment_params",
            "loan",
        }

    def test_it_is_frozen_and_a_mistyped_field_raises(self, app):
        """A projection cannot be mutated, and an unknown field is an error.

        Frozen because every consumer on the page reads the SAME object: the
        grid cell, the hero reduction, the debt summary, the Horizon bands and
        the goal balances.  One of them rewriting a figure the others have
        already read is the class of defect this arc exists to remove, and
        ``dataclasses.FrozenInstanceError`` makes it impossible rather than
        merely uncustomary.

        The second arm is the dict's real cost: ``ad["loan_figures"]`` on a
        cash account raised ``KeyError`` while ``ad.get("loan_figures")``
        answered ``None`` and Jinja's ``ad.loan_figures`` answered
        ``Undefined`` -- three spellings, one of them silent.  There is now one
        answer and it is loud.
        """
        # pylint: disable=import-outside-toplevel
        from dataclasses import FrozenInstanceError
        from types import SimpleNamespace
        with app.app_context():
            projection = _projection(
                SimpleNamespace(account_type=None), Decimal("1.00"),
            )
            with pytest.raises(FrozenInstanceError):
                projection.current_balance = Decimal("2.00")
            with pytest.raises(AttributeError):
                _ = projection.loan_figures

    def test_the_liability_flag_is_derived_not_stored(
        self, app, db, seed_user, seed_periods_today,
    ):
        """``is_liability`` IS the classifier, for a loan and for cash.

        It was a STORED key, and the page asked the same rule two ways over one
        set of balances: the grid cell read the stored flag while
        ``compute_net_worth_today`` -- summing the very balances those cells
        show -- re-derived it from the account.  Derived, they cannot differ.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.account_category import is_liability_account
        with app.app_context():
            loan = _create_small_loan(seed_user, db.session, name="Van")
            db.session.commit()

            data = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            by_id = {ad.account.id: ad for ad in data["account_data"]}
            assert by_id[loan.id].is_liability is True
            assert by_id[seed_user["account"].id].is_liability is False
            # And it is the classifier itself, not a copy that agrees today.
            for ad in data["account_data"]:
                assert ad.is_liability == is_liability_account(ad.account)

    def test_every_kind_carries_its_dense_period_map_including_a_loan(
        self, app, db, seed_user, seed_periods_today,
    ):
        """``balances`` is TOTAL over the pay periods, for cash AND for a loan.

        Plan step X-w, ruling R-CG, finding N-114.  The projection carried no
        dense map at all, and a SECOND per-account container was built beside it
        for the net-worth trend and the card sparklines -- storing the liability
        flag this projection derives.  Folding the map in is what deleted that
        container, and it only works if the map covers EVERY kind: a loan's tile
        reads none of it, but the trend and the liability band do, and the loans
        were exactly what the old batch left out.

        The loan arm is the one worth pinning to the cent.  Its
        ``current_balance`` is still the SCALAR
        (:func:`app.services.balance_at.balance_at`), not a read of this map,
        and the two agree because the seam clamps the current period's column to
        the read pass's ``as_of`` -- a property of the seam's construction, not
        of this module's, so it is asserted rather than assumed.  It was
        measured equal to the cent for both real loans on both databases at the
        step's trace.
        """
        with app.app_context():
            loan = _create_small_loan(seed_user, db.session, name="Van")
            db.session.commit()
            user_id = seed_user["user"].id
            all_period_ids = {
                p.id for p in all_periods(user_id)
            }
            current = current_pay_period(user_id)

            data = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(user_id),
            )
            by_id = {ad.account.id: ad for ad in data["account_data"]}

            for ad in data["account_data"]:
                assert set(ad.balances) == all_period_ids, (
                    f"{ad.account.name}'s dense map does not cover every pay "
                    f"period, so the net-worth trend would read a gap"
                )
            # The loan is in the map (it was excluded until this step) AND its
            # map agrees with the scalar the tile renders.
            assert by_id[loan.id].balances[current.id] == (
                by_id[loan.id].current_balance
            )


class TestTheDenseMapIsTotalAndSaysSo:
    """A missing period column FAILS LOUD at every reader of the dense map.

    Plan step X-w6, ruling R-CK.  ``_net_worth`` ended plan step X-w1 asking
    "is this map total over this window?" THREE ways: the projection's own read
    INDEXED, the per-period composition reduction wrote
    ``ad.balances.get(period_id, ZERO)``, and the sparkline producer wrote
    ``[balances[p.id] for p in window if p.id in balances]``.

    The two defaults are unreachable -- every window on this path is a slice of
    the period list the seam builds each map over -- and they degrade in
    opposite, silent, financially wrong directions if ever reached.  ``ZERO``
    banks a real account's balance at nothing for that band, so the composition
    stops reconciling to the hero the page asserts it equals.  The membership
    filter SHORTENS the series, and ``_serialize_sparklines`` normalizes on
    series LENGTH, so one dropped point moves every remaining point on the card.

    These pin the loud behaviour, which is what the ``Raises: KeyError`` blocks
    on all three readers claim and nothing asserted before this step.
    """

    @staticmethod
    def _account(account_id=1):
        """A stand-in ASSET account: ``id`` plus the category the reducer bands on.

        It carried ``account_type=None`` until plan step X-z7, when the band a
        non-liability lands in stopped coming from a map the test supplied and
        started coming from the account's own resolved category -- so an
        account with no type would band as ``other`` and the ``asset``
        assertion below would read ``$0.00`` while passing for the wrong
        reason.  Must be called inside an app context (``ref_cache``).
        """
        from types import SimpleNamespace  # pylint: disable=import-outside-toplevel
        return SimpleNamespace(
            id=account_id,
            account_type=SimpleNamespace(
                category_id=ref_cache.acct_category_id(AcctCategoryEnum.ASSET),
            ),
        )

    def test_the_composition_reduction_raises_on_a_missing_column(self, app):
        """A period the map has no column for is a KeyError, not a silent $0."""
        # pylint: disable=import-outside-toplevel
        from app.services.savings_dashboard_service._net_worth import (
            _sum_composition_at_period,
        )
        with app.app_context():
            ad = _projection(
                self._account(7), Decimal("100.00"), {1: Decimal("100.00")},
            )
            # Period 1 is present; period 2 is not.
            assert _sum_composition_at_period(1, [ad])["asset"] == (
                Decimal("100.00")
            )
            with pytest.raises(KeyError):
                _sum_composition_at_period(2, [ad])

    def test_the_sparkline_producer_raises_on_a_missing_column(self, app):
        """A window point the map lacks is a KeyError, not a shorter series.

        A shorter series is the dangerous answer: the route normalizes each
        sparkline into a fixed viewBox by its own LENGTH, so silently dropping
        one point re-positions every other point on that card.
        """
        # pylint: disable=import-outside-toplevel
        from types import SimpleNamespace
        from app.services.savings_dashboard_service._net_worth import (
            compute_sparklines,
        )
        with app.app_context():
            balances = {i: Decimal(str(1000 * i)) for i in range(1, 6)}
            ad = _projection(self._account(8), Decimal("1000.00"), balances)
            window = [_derived_period(i) for i in range(1, 6)]
            assert len(compute_sparklines([ad], window)[8]) == 5
            # One period beyond the map's domain.
            with pytest.raises(KeyError):
                compute_sparklines([ad], window + [_derived_period(99)])

    def test_the_projection_read_raises_on_a_missing_current_period(self, app):
        """The tile's rule states ``Raises: KeyError`` -- it does.

        Its contract has said so since plan step X-v2 (ruling R-CA) and nothing
        asserted it; this is the third reader of the same invariant, so it is
        pinned with the other two.  The read moved from the deleted
        ``_projections._current_balance_from_map`` into the tile rule's one
        home, ``_tile.tile_balance_on``, at plan step credit_card:CC-5-5d
        (ruling R-CC88); re-pointed with the developer's rule-5 confirmation
        (2026-09-23), the assertion unchanged.
        """
        # pylint: disable=import-outside-toplevel
        from types import SimpleNamespace
        from app.services.savings_dashboard_service._tile import (
            tile_balance_on,
        )
        with app.app_context():
            # The stand-in carried a ``current_anchor_balance`` for the
            # no-current-period arm; ruling R-EM sends that arm to the seam and
            # ruling R-EH deleted the column, so what this case pins is the
            # arm it always graded: with a current period the map is INDEXED.
            acct = SimpleNamespace()
            calendar = SimpleNamespace(
                period_containing=lambda _day: _derived_period(2),
            )
            ctx = SimpleNamespace(calendar=lambda: calendar)
            day = date(2026, 1, 20)
            assert tile_balance_on(
                acct, ctx, day, is_loan=False, balances={2: Decimal("42.00")},
            ) == Decimal("42.00")
            with pytest.raises(KeyError):
                tile_balance_on(
                    acct, ctx, day, is_loan=False,
                    balances={1: Decimal("42.00")},
                )
