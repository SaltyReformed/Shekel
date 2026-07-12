"""Route tests for the Property (physical-asset) feature.

Covers create -> auto-create appreciation params + setup-page redirect (and
the regression that a Property is NOT treated as an investment), the
Property detail page, the appreciation-rate update, the loan-side "secured
by" collateral link, deletion behaviour (DB SET NULL + params cleanup), and
the equity-over-time chart producer + its route context contract
(``docs/design/account_detail_audit.md``, "Property equity chart" + the
2026-07-11 Loop A lock / Loop B build contract).
"""

import json
from datetime import date
from decimal import Decimal

from flask import template_rendered

from app.models.account import Account
from app.models.asset_appreciation_params import AssetAppreciationParams
from app.models.investment_params import InvestmentParams
from app.models.ref import AccountType
from app.routes.accounts import detail as property_detail_module
from app.services import account_service, property_equity_chart
from app.services.loan_loaders import load_rate_changes
from app.services.loan_resolution import (
    contractual_schedule_from_origination,
    resolve_account_loan,
)
from app.utils.dates import add_months
from app.utils.money import round_money
from tests._test_helpers import create_loan_account


def _property_type_id(db):
    """Return the seeded Property account-type id."""
    return db.session.query(AccountType).filter_by(name="Property").one().id


def _make_property(
    db, seed_user, periods, name="House", rate=None,
    anchor_balance=Decimal("400000.00"),
):
    """Create a Property via the service (no params unless ``rate`` given).

    The default $400,000 anchor posts a Step-5 opening correction at create
    time, which makes the account archive-only under hard-delete Guard 5;
    the hard-delete test passes ``Decimal("0.00")`` (a zero opening books
    nothing, keeping the row deletable).
    """
    acct = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=_property_type_id(db),
            name=name,
            anchor_balance=anchor_balance,
            anchor_period_id=periods[0].id,
        ),
    )
    db.session.add(acct)
    db.session.flush()
    if rate is not None:
        db.session.add(AssetAppreciationParams(
            account_id=acct.id, annual_appreciation_rate=rate,
        ))
    db.session.commit()
    return acct


class TestCreateProperty:
    """Creating a Property routes through the appreciation setup page."""

    def test_create_auto_creates_params_and_redirects(
        self, app, auth_client, db, seed_user, seed_periods_today,
    ):
        """Property create seeds a zero-rate params row, redirects to setup."""
        with app.app_context():
            type_id = _property_type_id(db)

        resp = auth_client.post("/accounts", data={
            "name": "My House",
            "account_type_id": type_id,
            "anchor_balance": "400000.00",
        })
        assert resp.status_code == 302
        location = resp.headers["Location"]
        assert "/property" in location
        assert "setup=1" in location

        with app.app_context():
            acct = (
                db.session.query(Account)
                .filter_by(user_id=seed_user["user"].id, name="My House")
                .one()
            )
            params = (
                db.session.query(AssetAppreciationParams)
                .filter_by(account_id=acct.id)
                .first()
            )
            assert params is not None
            # E-12 zero sentinel until the user sets a real rate.
            assert params.annual_appreciation_rate == Decimal("0")
            # Regression: a Property must NOT be auto-created as an investment.
            assert (
                db.session.query(InvestmentParams)
                .filter_by(account_id=acct.id)
                .first()
                is None
            )


class TestPropertyDetailPage:
    """The detail page renders the equity figures and the rate form."""

    def test_detail_renders_equity(self, app, auth_client, db, seed_user, seed_periods_today):
        """GET the property page shows market value, equity, and LTV.

        Label strings match the Fable 5 band rebuild
        (docs/design/account_detail_audit.md, Surface 5 verdict +
        decisions 6-7): the hero label is "Equity" and the supporting
        chips are "Market value" / "Secured debt" / "Loan-to-value".
        """
        with app.app_context():
            acct = _make_property(
                db, seed_user, seed_periods_today, rate=Decimal("0.03000"),
            )
            acct_id = acct.id

        resp = auth_client.get(f"/accounts/{acct_id}/property")
        assert resp.status_code == 200
        body = resp.data
        assert b"Equity" in body
        assert b"Market value" in body
        assert b"Secured debt" in body
        assert b"Loan-to-value" in body
        assert b"Secured by" in body
        # Market value renders (entered as 400000.00).
        assert b"400,000" in body

    def test_update_appreciation_rate(self, app, auth_client, db, seed_user, seed_periods_today):
        """POSTing a percent rate stores the decimal fraction."""
        with app.app_context():
            acct = _make_property(
                db, seed_user, seed_periods_today, rate=Decimal("0.00000"),
            )
            acct_id = acct.id

        resp = auth_client.post(
            f"/accounts/{acct_id}/property/params",
            data={"appreciation_rate": "3.5"},
        )
        assert resp.status_code == 302
        with app.app_context():
            params = (
                db.session.query(AssetAppreciationParams)
                .filter_by(account_id=acct_id)
                .one()
            )
            # 3.5% entered -> stored as the 0.035 decimal fraction.
            assert params.annual_appreciation_rate == Decimal("0.03500")


class TestCollateralLinkRoute:
    """The loan-side "secured by" picker sets and clears the link."""

    def test_link_loan_to_property(self, app, auth_client, db, seed_user, seed_periods_today):
        """Linking a loan to a Property sets the FK and the secured_loans backref."""
        with app.app_context():
            prop = _make_property(db, seed_user, seed_periods_today)
            loan = create_loan_account(seed_user, db.session, name="Mtg")
            prop_id, loan_id = prop.id, loan.id

        resp = auth_client.post(
            f"/accounts/{loan_id}/loan/collateral",
            data={"collateral_account_id": str(prop_id)},
        )
        assert resp.status_code == 302
        with app.app_context():
            loan = db.session.get(Account, loan_id)
            assert loan.collateral_account_id == prop_id
            prop = db.session.get(Account, prop_id)
            assert loan_id in [secured.id for secured in prop.secured_loans]

    def test_clear_link(self, app, auth_client, db, seed_user, seed_periods_today):
        """Submitting an empty value clears an existing link."""
        with app.app_context():
            prop = _make_property(db, seed_user, seed_periods_today)
            loan = create_loan_account(seed_user, db.session, name="Mtg")
            loan.collateral_account_id = prop.id
            db.session.commit()
            loan_id = loan.id

        resp = auth_client.post(
            f"/accounts/{loan_id}/loan/collateral",
            data={"collateral_account_id": ""},
        )
        assert resp.status_code == 302
        with app.app_context():
            loan = db.session.get(Account, loan_id)
            assert loan.collateral_account_id is None


class TestPropertyDeletion:
    """Deleting a Property clears the link (SET NULL) and its params row."""

    def test_delete_property_nulls_loan_link(self, app, db, seed_user, seed_periods_today):
        """ON DELETE SET NULL keeps the loan alive with its link cleared."""
        with app.app_context():
            # No appreciation params row -> a plain ORM delete suffices to
            # exercise the DB-level SET NULL on the loan's FK.
            prop = _make_property(db, seed_user, seed_periods_today)
            loan = create_loan_account(seed_user, db.session, name="Mtg")
            loan.collateral_account_id = prop.id
            db.session.commit()
            loan_id = loan.id

            db.session.delete(prop)
            db.session.commit()

            survived = db.session.get(Account, loan_id)
            assert survived is not None
            assert survived.collateral_account_id is None

    def test_hard_delete_property_cleans_up_params(
        self, app, auth_client, db, seed_user, seed_periods_today,
    ):
        """Hard-deleting a Property removes its appreciation params row."""
        with app.app_context():
            # $0 anchor: a non-zero anchor posts its Step-5 opening and the
            # hard delete would archive instead (Guard 5); the subject here
            # is the params-row cleanup on a real delete.
            prop = _make_property(
                db, seed_user, seed_periods_today, rate=Decimal("0.03000"),
                anchor_balance=Decimal("0.00"),
            )
            loan = create_loan_account(seed_user, db.session, name="Mtg")
            loan.collateral_account_id = prop.id
            db.session.commit()
            prop_id, loan_id = prop.id, loan.id

        resp = auth_client.post(f"/accounts/{prop_id}/hard-delete")
        assert resp.status_code == 302
        with app.app_context():
            # The Property and its params row are gone; the loan survives with
            # its link nulled by the FK.
            assert db.session.get(Account, prop_id) is None
            assert (
                db.session.query(AssetAppreciationParams)
                .filter_by(account_id=prop_id)
                .first()
                is None
            )
            survived = db.session.get(Account, loan_id)
            assert survived is not None
            assert survived.collateral_account_id is None


# ── Property equity-over-time chart (date-anchored rebuild) ─────────

_FOUR_HUNDRED_K = Decimal("400000.00")


def _series_for(loan, scenario_id, today):
    """Build one loan's ``SecuredLoanSeries`` the way the route does.

    Resolves the loan once and clips its contractual-from-origination schedule
    to the months before the resolved schedule begins (the pre-tracking
    back-projection), mirroring ``detail._secured_loan_series`` so the producer
    tests feed it the rows production feeds it.
    """
    loan_params, state = resolve_account_loan(loan.id, scenario_id, today)
    full_contractual = contractual_schedule_from_origination(
        loan_params, load_rate_changes(loan.id),
    )
    tracking_start = state.schedule[0].payment_date if state.schedule else None
    back_projection = [
        row for row in full_contractual
        if tracking_start is None or row.payment_date < tracking_start
    ]
    return property_equity_chart.SecuredLoanSeries(
        back_projection=back_projection,
        schedule=state.schedule,
        current_balance=state.current_balance,
    )


def _expected_value(market_value, rate, month_date, today):
    """Hand-compute the compounded market value at ``month_date``.

    Mirrors ``growth_engine.period_return_rate`` exactly: the anchor grows by
    ``(1 + rate) ** (days / 365)`` where ``days`` is the INCLUSIVE span
    ``(month_date - today).days + 1``, rounded to cents.
    """
    days = Decimal(str((month_date - today).days + 1))
    factor = (Decimal("1") + rate) ** (days / Decimal("365"))
    return round_money(market_value * factor)


class TestPropertyEquityChartProducer:
    """The date-anchored producer: fallback, date-keyed value, calendar merge.

    The rebuild's H1 / H2 / H3 fixes plus the value/equity identity, asserted on
    the pure ``build_property_equity_chart`` (fed the rows the route resolves
    once).  Loans originate at a known date relative to ``today`` so the axis is
    date-robust in CI.
    """

    def test_no_loans_uses_ten_year_appreciation_fallback(self, app):
        """No secured loan at all -> the 120-month appreciation-only arc."""
        with app.app_context():
            today = date.today()
            chart = property_equity_chart.build_property_equity_chart(
                [], _FOUR_HUNDRED_K, Decimal("0.03000"), today,
            )
            assert chart.chart_state == "no_loans"
            assert chart.debt == []
            assert chart.equity == []
            assert chart.debt_tier == []
            assert chart.today_index == 0
            assert len(chart.value) == 120
            assert len(chart.labels) == 120
            assert chart.value[0] == _FOUR_HUNDRED_K
            assert chart.value[1] == _expected_value(
                _FOUR_HUNDRED_K, Decimal("0.03000"), add_months(today, 1), today,
            )

    def test_paid_off_loan_dropped_and_falls_back(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A zero-balance loan with a NON-EMPTY schedule still reaches the fallback.

        The H1 fix.  The old producer skipped a loan only when its schedule was
        empty, but a loan paid off through confirmed payments KEEPS its whole
        confirmed schedule -- so the paid-off fallback never fired.  The rebuild
        decides on the balance today, not schedule emptiness: present a real
        loan's (non-empty) schedule with a $0.00 current balance and the
        producer drops it, returning the loan-less arc.
        """
        with app.app_context():
            today = date.today()
            scenario_id = seed_user["scenario"].id
            loan = create_loan_account(
                seed_user, db.session, name="Real",
                principal=Decimal("2400.00"), term=24, origination_date=today,
            )
            _params, state = resolve_account_loan(loan.id, scenario_id, today)
            assert state.schedule != []  # precondition: the schedule is NON-empty
            paid_off = property_equity_chart.SecuredLoanSeries(
                back_projection=[], schedule=state.schedule,
                current_balance=Decimal("0.00"),
            )

            chart = property_equity_chart.build_property_equity_chart(
                [paid_off], _FOUR_HUNDRED_K, Decimal("0.03000"), today,
            )
            assert chart.chart_state == "no_loans"
            assert chart.debt == []
            assert len(chart.value) == 120

    def test_multi_loan_merge_is_date_aligned(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A younger loan's balance is never summed into a month before it existed.

        The H2 fix.  An old $300k loan (36 months ago) and a new $50k loan (1
        month ago) secure the property.  The axis starts at the OLD loan's first
        month, years before the new loan; at that earliest month the debt is the
        OLD loan's balance ALONE -- the new loan contributes $0.00 because it did
        not exist.  The old front-aligned merge summed the new loan in there.
        """
        with app.app_context():
            today = date.today()
            scenario_id = seed_user["scenario"].id
            prop = _make_property(
                db, seed_user, seed_periods_today, rate=Decimal("0"),
            )
            old = create_loan_account(
                seed_user, db.session, name="Old",
                principal=Decimal("300000.00"), term=360,
                origination_date=add_months(today, -36),
            )
            new = create_loan_account(
                seed_user, db.session, name="New",
                principal=Decimal("50000.00"), term=120,
                origination_date=add_months(today, -1),
            )
            old.collateral_account_id = prop.id
            new.collateral_account_id = prop.id
            db.session.commit()
            old_series = _series_for(old, scenario_id, today)
            new_series = _series_for(new, scenario_id, today)

            chart = property_equity_chart.build_property_equity_chart(
                [old_series, new_series], _FOUR_HUNDRED_K, Decimal("0"), today,
            )
            # Parallel series (the data contract).
            assert (
                len(chart.labels) == len(chart.value) == len(chart.debt)
                == len(chart.equity) == len(chart.debt_tier)
            )
            # The earliest axis month is the OLD loan's first row; the new loan
            # has no row there, so the debt is the old loan's balance ALONE.
            old_first = old_series.schedule[0].remaining_balance
            new_first = new_series.schedule[0].remaining_balance
            assert chart.debt[0] == old_first
            # Guard against the old front-aligned bug (new summed in at index 0).
            assert chart.debt[0] != old_first + new_first

    def test_past_dated_projected_months_hold_value_flat(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Past months hold the anchor flat -- never a fabricated compounded value.

        The H3 fix.  A loan originated 18 months ago with no confirmed payments
        gives the schedule past-dated PROJECTED rows.  The value line holds the
        $400,000 anchor flat for every month up to today (keyed on the DATE, not
        a confirmed-row count), so none of those past months gets the phantom
        value the old degenerate-span clamp produced; the line compounds only
        after today.
        """
        with app.app_context():
            today = date.today()
            scenario_id = seed_user["scenario"].id
            prop = _make_property(
                db, seed_user, seed_periods_today, rate=Decimal("0.03000"),
            )
            loan = create_loan_account(
                seed_user, db.session, name="Stale",
                principal=Decimal("100000.00"), term=120,
                origination_date=add_months(today, -18),
            )
            loan.collateral_account_id = prop.id
            db.session.commit()
            series = _series_for(loan, scenario_id, today)

            chart = property_equity_chart.build_property_equity_chart(
                [series], _FOUR_HUNDRED_K, Decimal("0.03000"), today,
            )
            # There ARE past months on the axis (origination predates today).
            assert chart.today_index > 0
            # Every month up to and including today holds the anchor flat.
            assert all(
                value == _FOUR_HUNDRED_K
                for value in chart.value[:chart.today_index + 1]
            )
            # The month after today compounds at 3%/yr from today.
            first_of_month = date(today.year, today.month, 1)
            assert chart.value[chart.today_index + 1] == _expected_value(
                _FOUR_HUNDRED_K, Decimal("0.03000"),
                add_months(first_of_month, 1), today,
            )

    def test_equity_is_value_minus_debt_each_month(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Equity is the exact per-month ``value - debt`` (the internal identity)."""
        with app.app_context():
            today = date.today()
            scenario_id = seed_user["scenario"].id
            prop = _make_property(
                db, seed_user, seed_periods_today, rate=Decimal("0.03000"),
            )
            loan = create_loan_account(
                seed_user, db.session, name="Mtg",
                principal=Decimal("2400.00"), term=36, origination_date=today,
            )
            loan.collateral_account_id = prop.id
            db.session.commit()
            series = _series_for(loan, scenario_id, today)

            chart = property_equity_chart.build_property_equity_chart(
                [series], _FOUR_HUNDRED_K, Decimal("0.03000"), today,
            )
            assert chart.equity == [
                chart.value[i] - chart.debt[i] for i in range(len(chart.debt))
            ]

    def test_zero_rate_holds_value_flat_with_debt(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The 0 appreciation sentinel holds value flat; the zero_rate state fires."""
        with app.app_context():
            today = date.today()
            scenario_id = seed_user["scenario"].id
            prop = _make_property(
                db, seed_user, seed_periods_today, rate=Decimal("0"),
            )
            loan = create_loan_account(
                seed_user, db.session, name="Flat",
                principal=Decimal("2400.00"), term=24, origination_date=today,
            )
            loan.collateral_account_id = prop.id
            db.session.commit()
            series = _series_for(loan, scenario_id, today)

            chart = property_equity_chart.build_property_equity_chart(
                [series], _FOUR_HUNDRED_K, Decimal("0"), today,
            )
            assert chart.chart_state == "zero_rate"
            assert all(value == _FOUR_HUNDRED_K for value in chart.value)


class TestPropertyDetailChartContext:
    """The property route hands the band its equity-chart context, resolving once.

    Asserts the ``accounts.property_detail`` render context (captured via the
    ``template_rendered`` signal) carries the ``has_equity_chart`` / ``chart_json``
    / ``chart_state`` contract ``property_detail.js`` reads, and that each secured
    loan is resolved exactly once (D1).
    """

    def _context(self, app, auth_client, account_id):
        """Return the context ``property_detail`` handed ``render_template``."""
        recorded = []

        def _record(sender, template, context, **extra):
            recorded.append((template, context))

        template_rendered.connect(_record, app)
        try:
            response = auth_client.get(f"/accounts/{account_id}/property")
        finally:
            template_rendered.disconnect(_record, app)

        assert response.status_code == 200
        matches = [
            ctx for tmpl, ctx in recorded
            if tmpl.name == "accounts/property_detail.html"
        ]
        assert matches, "property_detail.html did not render"
        return matches[0]

    def test_context_carries_serialized_equity_chart(
        self, app, auth_client, db, seed_user, seed_periods_today,
    ):
        """A property with a secured loan exposes the standard chart contract."""
        with app.app_context():
            prop = _make_property(
                db, seed_user, seed_periods_today, rate=Decimal("0.03000"),
            )
            loan = create_loan_account(
                seed_user, db.session, name="Mtg",
                principal=Decimal("2400.00"), term=24,
                origination_date=date.today(),
            )
            loan.collateral_account_id = prop.id
            db.session.commit()
            prop_id = prop.id

        context = self._context(app, auth_client, prop_id)
        assert context["has_equity_chart"] is True
        assert context["chart_state"] == "standard"
        chart = json.loads(context["chart_json"])
        assert set(chart) == {
            "labels", "value", "debt", "equity", "today_index", "debt_tier",
        }
        assert (
            len(chart["labels"]) == len(chart["value"]) == len(chart["debt"])
            == len(chart["equity"]) == len(chart["debt_tier"])
        )
        # The value line anchors at the $400,000 market value at today.
        assert chart["value"][chart["today_index"]] == 400000.0

    def test_resolves_each_secured_loan_once(
        self, app, auth_client, db, seed_user, seed_periods_today, monkeypatch,
    ):
        """Each secured loan is resolved exactly once per GET (D1: no double resolve).

        Both the equity hero and the chart read one resolution pass, so a
        call-counting spy sees exactly one ``resolve_account_loan`` for the
        secured loan.
        """
        with app.app_context():
            prop = _make_property(
                db, seed_user, seed_periods_today, rate=Decimal("0.03000"),
            )
            loan = create_loan_account(
                seed_user, db.session, name="Mtg",
                principal=Decimal("2400.00"), term=24,
                origination_date=date.today(),
            )
            loan.collateral_account_id = prop.id
            db.session.commit()
            prop_id, loan_id = prop.id, loan.id

        calls = []
        real_resolve = property_detail_module.resolve_account_loan

        def _spy(account_id, scenario_id, today):
            calls.append(account_id)
            return real_resolve(account_id, scenario_id, today)

        monkeypatch.setattr(
            property_detail_module, "resolve_account_loan", _spy,
        )
        response = auth_client.get(f"/accounts/{prop_id}/property")
        assert response.status_code == 200
        assert calls.count(loan_id) == 1

    def test_context_hides_chart_when_no_market_value(
        self, app, auth_client, db, seed_user, seed_periods_today,
    ):
        """A property with no market value set exposes the empty-state flag."""
        with app.app_context():
            prop = _make_property(
                db, seed_user, seed_periods_today, rate=Decimal("0.03000"),
                anchor_balance=Decimal("0.00"),
            )
            prop_id = prop.id

        context = self._context(app, auth_client, prop_id)
        assert context["has_equity_chart"] is False
        chart = json.loads(context["chart_json"])
        assert chart["value"] == []
        assert chart["debt"] == []

