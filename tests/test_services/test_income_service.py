"""
Shekel Budget App -- Income Service Tests (C17 / F-20 / MED-06 / F-032).

Pins the raise-aware paycheck-engine producer contract:

- The helper returns ``Decimal("0")`` when no active SalaryProfile exists.
- The helper returns ``annual_salary`` over the owner's PAYCHECK COUNT --
  derived from their cadence since plan step R-F16 -- byte-identical to the
  engine for a no-raise profile.
- The helper APPLIES applicable ``SalaryRaise`` rows so the post-raise
  per-period gross is returned -- the F-032 worked example: $104,000
  base with a 3% raise effective in the as-of period yields $4,120.00
  per period, not the pre-Commit-17 off-engine $4,000.00.
- Every downstream consumer (savings, year-end, retirement, investment)
  reads the same engine-derived value through the helper for a
  raise-applicable user.

Test fixture math (hand-computed):

- ``annual_salary = $104,000`` + 3% one-time raise effective 2026-03
- Post-raise annual = ``104000 * 1.03 = 107,120``
- Per-period (10-period-year fallback to ROUND_HALF_UP):
  ``107120 / 26 = 4,120.000...`` -> ``Decimal("4120.00")``
- Pre-fix (no raise applied): ``104000 / 26 = 4,000.00`` -> the
  pre-Commit-17 value the off-engine sites returned.
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.ref import FilingStatus, RaiseType, Status, TaxType, TransactionType
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from app.models.tax_config import FicaConfig, StateTaxConfig
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.services.pay_calendar import calendar_for
from app.services import (
    balance_at,
    income_service,
    paycheck_calculator,
    savings_dashboard_service,
)
from app.services.tax_config_service import (
    load_tax_configs,
    load_tax_configs_for_year,
)
from app.services.balance_at import BalanceContext
from tests._test_helpers import (
    all_periods,
    freeze_today,
    make_investment_account,
    payroll_basis,
)
from app.models.amount_ownership import AmountOwnership


# Hand-computed expected values (see module docstring for derivation).
_RAISE_APPLIED_GROSS = Decimal("4120.00")  # 104000 * 1.03 / 26
_NO_RAISE_GROSS = Decimal("4000.00")  # 104000 / 26
_AS_OF_AFTER_RAISE = date(2026, 3, 15)  # inside seed_periods period 5
_AS_OF_BEFORE_RAISE = date(2026, 1, 5)  # inside seed_periods period 0


def _create_profile(
    user_id: int, scenario_id: int, *, annual_salary: str = "104000.00",
) -> SalaryProfile:
    """Create an active SalaryProfile for the user.

    Helper isolates the FilingStatus lookup + required-column boilerplate
    so each test reads as fixture composition rather than ORM ceremony.
    """
    filing = db.session.query(FilingStatus).first()
    profile = SalaryProfile(
        user_id=user_id,
        scenario_id=scenario_id,
        filing_status_id=filing.id,
        name="Test Salary",
        annual_salary=Decimal(annual_salary),
        state_code="NC",
        is_active=True,
    )
    db.session.add(profile)
    db.session.flush()
    return profile


def _add_one_time_raise(
    profile: SalaryProfile, *, percentage: str = "0.0300",
    effective_month: int = 3, effective_year: int = 2026,
) -> SalaryRaise:
    """Attach a one-time percentage raise to the profile."""
    merit = db.session.query(RaiseType).filter_by(name="merit").one()
    salary_raise = SalaryRaise(
        salary_profile_id=profile.id,
        raise_type_id=merit.id,
        effective_month=effective_month,
        effective_year=effective_year,
        percentage=Decimal(percentage),
        is_recurring=False,
    )
    db.session.add(salary_raise)
    db.session.flush()
    return salary_raise


def _make_salary_template(seed_user, profile, *, name="Paycheck"):
    """Create an Income template and link ``profile`` to it.

    The producer treats a transaction as salary-linked iff its
    ``template_id`` maps to an active SalaryProfile for the scenario, so
    the test must set ``profile.template_id`` to the created template.
    """
    income_type = (
        db.session.query(TransactionType).filter_by(name="Income").one()
    )
    category = next(iter(seed_user["categories"].values()))
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=category.id,
        transaction_type_id=income_type.id,
        name=name,
        default_amount=Decimal("4000.00"),
    )
    db.session.add(template)
    db.session.flush()
    profile.template_id = template.id
    db.session.flush()
    return template


def _make_txn(
    seed_user, period, *, template=None, type_name="Income",
    status_name="Projected", is_override=False, estimated_amount="1.00",
):
    """Create a single Transaction in ``period`` for the producer tests."""
    txn_type = (
        db.session.query(TransactionType).filter_by(name=type_name).one()
    )
    status = db.session.query(Status).filter_by(name=status_name).one()
    category = next(iter(seed_user["categories"].values()))
    txn = Transaction(
        account_id=seed_user["account"].id,
        template_id=template.id if template is not None else None,
        user_id=period.user_id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        status_id=status.id,
        name="producer-test txn",
        category_id=category.id,
        transaction_type_id=txn_type.id,
        amount_ownership=AmountOwnership.own(Decimal(estimated_amount)),
        is_override=is_override,
    )
    db.session.add(txn)
    db.session.flush()
    return txn


def _live_net_map(user_id, scenario_id, rows):
    """The salary override map, as the read-time repair produces it per row.

    Plan step X-au-c2b split ``live_projected_net`` into an owner-scoped
    DERIVATION (:func:`income_service.salary_pricing`) and a per-row lookup, so
    the map these tests grade is now something a caller builds rather than
    something the producer returns.  Assembled here so every assertion below
    keeps its original shape and figures.
    """
    pricing = income_service.salary_pricing(user_id, scenario_id)
    answers = {}
    for txn in rows:
        net = income_service.live_projected_net(txn, pricing)
        if net is not None:
            answers[txn.id] = net
    return answers


class TestLiveProjectedNet:
    """Unit tests for ``income_service.live_projected_net`` (Workstream B).

    Locks the two properties the live-recompute relies on: the producer
    (a) recomputes the net LIVE from the salary profile, ignoring the
    stored ``estimated_amount`` (so a stale cache cannot leak through),
    and (b) filters to exactly the Projected, non-overridden,
    salary-linked income rows.
    """

    def test_recomputes_live_ignoring_stored_amount(
        self, app, db, seed_user, seed_periods,
    ):
        """A Projected salary-linked income row maps to the LIVE net.

        The transaction's stored ``estimated_amount`` is deliberately set
        to $1.00 (a stale/wrong value).  The producer must return the
        live net for the transaction's period -- proving it recomputes
        from the profile and never trusts the cached column.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            profile = _create_profile(user_id, scenario_id)
            template = _make_salary_template(seed_user, profile)
            db.session.commit()

            period = all_periods(user_id)[5]
            txn = _make_txn(
                seed_user, period, template=template,
                estimated_amount="1.00",
            )
            db.session.commit()

            overrides = _live_net_map(user_id, scenario_id, [txn])

            # $104,000 profile, no raise, no tax configs seeded -> net =
            # gross = 104000 / 26 = $4,000.00 (hand-computed; the sibling
            # balance-resolver test pins the same value for this setup).
            # The producer must return this LIVE net, never the stale $1.00.
            expected_net = Decimal("4000.00")
            assert overrides == {txn.id: expected_net}
            assert overrides[txn.id] != Decimal("1.00")

    def test_filters_to_projected_nonoverride_salary_income(
        self, app, db, seed_user, seed_periods,
    ):
        """Only Projected, non-overridden, salary-linked income is overridden.

        Builds five rows and asserts the override dict contains exactly
        the one Projected non-override income row linked to the salary
        profile -- Received income (historical), an overridden row (user
        value respected), non-salary income (template has no profile),
        and an expense are all omitted, so a caller's fallback to the
        stored amount applies to them.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            profile = _create_profile(user_id, scenario_id)
            template = _make_salary_template(seed_user, profile)
            income_type = (
                db.session.query(TransactionType).filter_by(name="Income").one()
            )
            category = next(iter(seed_user["categories"].values()))
            other_template = TransactionTemplate(
                user_id=user_id,
                account_id=seed_user["account"].id,
                category_id=category.id,
                transaction_type_id=income_type.id,
                name="Non-salary income",
                default_amount=Decimal("50.00"),
            )
            db.session.add(other_template)
            db.session.commit()

            periods = all_periods(user_id)
            # Distinct periods avoid the (template, period, scenario)
            # non-override unique index.
            wanted = _make_txn(seed_user, periods[5], template=template)
            received = _make_txn(
                seed_user, periods[6], template=template,
                status_name="Received",
            )
            overridden = _make_txn(
                seed_user, periods[7], template=template, is_override=True,
            )
            non_salary = _make_txn(
                seed_user, periods[5], template=other_template,
            )
            expense = _make_txn(
                seed_user, periods[5], template=None, type_name="Expense",
            )
            db.session.commit()

            overrides = _live_net_map(
                user_id, scenario_id,
                [wanted, received, overridden, non_salary, expense],
            )

            assert set(overrides) == {wanted.id}, (
                "Only the Projected, non-override, salary-linked income "
                f"row should be overridden; got ids {sorted(overrides)}"
            )

    def test_empty_when_no_candidates(self, app, db, seed_user, seed_periods):
        """No salary-linked Projected income -> empty dict (fast no-op)."""
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            # Empty transaction list.
            assert _live_net_map(user_id, scenario_id, []) == {}

            # An income row whose template has no SalaryProfile -> omitted.
            income_type = (
                db.session.query(TransactionType).filter_by(name="Income").one()
            )
            category = next(iter(seed_user["categories"].values()))
            unlinked = TransactionTemplate(
                user_id=user_id,
                account_id=seed_user["account"].id,
                category_id=category.id,
                transaction_type_id=income_type.id,
                name="Unlinked income",
                default_amount=Decimal("100.00"),
            )
            db.session.add(unlinked)
            db.session.commit()
            txn = _make_txn(
                seed_user, all_periods(user_id)[3],
                template=unlinked,
            )
            db.session.commit()
            assert _live_net_map(user_id, scenario_id, [txn]) == {}



def _derived(user_id):
    """The owner's saved schedule AS THE PAYCHECK ENGINE takes it.

    That engine moved onto :class:`~app.services.pay_calendar.DerivedPeriod`
    at pay-calendar plan step C2-f2d-3, and ``income_service`` derives this
    same window internally -- so the oracles below are handed the shape the
    producer under test uses, while the ORM rows beside them stay for the
    fixtures that WRITE a ``pay_period_id``.
    """
    return calendar_for(user_id).saved()


class TestLiveIncomeThroughBalanceResolver:
    """Workstream B integration: balance surfaces recompute projected salary
    income live, so a stale stored ``estimated_amount`` never reaches a
    balance or subtotal.  This is the drift-without-regeneration lock -- the
    exact failure mode (a code change staling the grid) that motivated the
    income resolver.
    """

    def test_stale_stored_income_overridden_by_live_net(
        self, app, db, seed_user, seed_periods,
    ):
        """A projected salary income row with a stale $1.00 stored amount
        contributes its LIVE net to the grid's income row AND to the
        rendered BALANCE -- never the stale stored value.

        $104,000 profile, no deductions, no tax configs seeded -> net =
        gross = 104000/26 = $4,000.00.  The transaction is stored at $1.00
        (simulating a cache invalidated by a profile/code change with no
        regeneration); both surfaces must show $4,000.00.

        The income row was read through ``cash_ledger.period_subtotal`` until
        plan step X-c2b3 deleted it; it is now the shipped
        ``GridColumn.income``, which is what the grid footer renders.  The
        $4,000.00 is unchanged because the live override map is the same rule on
        both bases -- which is the property this test exists to pin.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = seed_user["scenario"]
            bctx = BalanceContext.build(seed_user["user"].id)
            account = seed_user["account"]
            profile = _create_profile(user_id, scenario.id)
            template = _make_salary_template(seed_user, profile)
            db.session.commit()

            periods = all_periods(user_id)
            period = periods[5]
            _make_txn(
                seed_user, period, template=template,
                estimated_amount="1.00",
            )
            db.session.commit()

            tax_configs = load_tax_configs_for_year(
                user_id, profile, period.start_date.year,
            )
            breakdowns = paycheck_calculator.project_salary(
                payroll_basis(profile, _derived(user_id)), _derived(user_id),
                tax_configs,
                calibration=profile.calibration,
            )
            expected_net = {
                bd.period.period_id: bd.earnings.net_pay for bd in breakdowns
            }[period.id]
            # Sanity: the live net genuinely differs from the stale stored.
            assert expected_net == Decimal("4000.00")
            assert expected_net != Decimal("1.00")

            # The grid's income row reflects the live net.
            column = balance_at.grid_balance_view(
                account, bctx,
            ).columns[period.id]
            assert column.income == expected_net, (
                f"GridColumn.income should be live {expected_net}, "
                f"got {column.income} (stale stored was 1.00)"
            )

            # The BALANCE moves by the live net too, not just the rendered
            # income row -- the property that makes the override a basis rather
            # than a display value.  Re-pointed off the deleted anchor-forward
            # walk onto the cash view at plan step X-g4b; the delta is
            # unchanged because both read one ``live_amount_overrides`` map.
            result = balance_at.cash_balance_map(
                account, bctx,
            )
            idx = next(i for i, p in enumerate(periods) if p.id == period.id)
            prior = result[periods[idx - 1].id]
            assert result[period.id] - prior == expected_net, (
                "the income period's balance delta should be the live net "
                f"{expected_net}, got {result[period.id] - prior}"
            )

    def test_overridden_income_row_keeps_user_value(
        self, app, db, seed_user, seed_periods,
    ):
        """A user-overridden salary income row is NOT recomputed.

        ``is_override=True`` means the user deliberately set the amount;
        the resolver must respect it (the producer excludes it), so the grid's
        income row reflects the stored $1234.56, not the live net.  Read through
        ``GridColumn.income`` since plan step X-c2b3 deleted
        ``cash_ledger.period_subtotal``.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = seed_user["scenario"]
            bctx = BalanceContext.build(seed_user["user"].id)
            account = seed_user["account"]
            profile = _create_profile(user_id, scenario.id)
            template = _make_salary_template(seed_user, profile)
            db.session.commit()

            period = all_periods(user_id)[5]
            _make_txn(
                seed_user, period, template=template, is_override=True,
                estimated_amount="1234.56",
            )
            db.session.commit()

            column = balance_at.grid_balance_view(
                account, bctx,
            ).columns[period.id]
            assert column.income == Decimal("1234.56"), (
                "An overridden income row must keep the user's amount, "
                f"got {column.income}"
            )


class TestGetCurrentGrossBiweekly:
    """Direct unit tests for ``income_service.get_current_gross_biweekly``."""

    def test_c17_1_raise_applied_yields_engine_per_period_gross(
        self, app, db, seed_user, seed_periods,
    ):
        """C17-1: applicable raise -> raise-aware engine gross_biweekly.

        Hand arithmetic: ``104000 * 1.03 / 26 = 4120.00``.  Pre-Commit-17
        the off-engine sites returned ``104000 / 26 = 4000.00`` because
        the raise was silently dropped.  The helper invokes the paycheck
        engine for the as-of period, which folds the raise into the
        post-raise annual salary before dividing.
        """
        with app.app_context():
            profile = _create_profile(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            _add_one_time_raise(profile)
            db.session.commit()

            result = income_service.get_current_gross_biweekly(
                seed_user["user"].id,
                calendar_for(seed_user["user"].id), as_of=_AS_OF_AFTER_RAISE,
            )

            assert result == _RAISE_APPLIED_GROSS

    def test_c17_2_no_raise_yields_byte_identical_pre_fix_value(
        self, app, db, seed_user, seed_periods,
    ):
        """C17-2: no raises -> engine value equals the pre-fix value.

        With zero raises, the post-raise annual salary equals the base
        annual salary, so the engine's ``104000 / 26`` matches the
        pre-fix ``104000 / 26 = 4000.00`` exactly.  Locks the "no
        regression for non-raised users" property.
        """
        with app.app_context():
            _create_profile(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            db.session.commit()

            result = income_service.get_current_gross_biweekly(
                seed_user["user"].id,
                calendar_for(seed_user["user"].id), as_of=_AS_OF_AFTER_RAISE,
            )

            assert result == _NO_RAISE_GROSS

    def test_c17_3_no_active_profile_returns_zero(
        self, app, db, seed_user, seed_periods,
    ):
        """C17-3: missing active profile -> ``Decimal("0")``.

        Preserves the pre-fix fallback contract -- every off-engine
        site defaulted ``salary_gross_biweekly = Decimal("0")`` when
        the user had no active profile.  The helper matches.
        """
        with app.app_context():
            # No SalaryProfile inserted -- seed_user does not create one.
            result = income_service.get_current_gross_biweekly(
                seed_user["user"].id,
                calendar_for(seed_user["user"].id), as_of=_AS_OF_AFTER_RAISE,
            )

            assert result == Decimal("0")

    def test_raise_does_not_apply_before_effective_month(
        self, app, db, seed_user, seed_periods,
    ):
        """A raise effective March must NOT apply to a January period.

        Locks the engine's per-period semantic: the raise factor enters
        the gross only for periods whose start_date is on or after the
        effective month.  Without this, the helper would over-state
        income for pre-raise periods.
        """
        with app.app_context():
            profile = _create_profile(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            _add_one_time_raise(profile)
            db.session.commit()

            result = income_service.get_current_gross_biweekly(
                seed_user["user"].id,
                calendar_for(seed_user["user"].id), as_of=_AS_OF_BEFORE_RAISE,
            )

            assert result == _NO_RAISE_GROSS

    def test_scenario_id_filter_scopes_lookup(
        self, app, db, seed_user, seed_periods,
    ):
        """``scenario_id`` keyword restricts the SalaryProfile lookup.

        The year-end consumer passes ``scenario_id=scenario.id`` so the
        per-scenario profile resolution stays consistent with how
        year-end aggregates the rest of its inputs.  A profile in a
        different scenario must NOT be returned.
        """
        with app.app_context():
            # Insert profile in seed_user's baseline scenario.
            _create_profile(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            db.session.commit()

            # Lookup with a different (non-existent) scenario_id returns
            # zero -- no profile matches the filter.
            result = income_service.get_current_gross_biweekly(
                seed_user["user"].id,
                calendar_for(seed_user["user"].id),
                scenario_id=seed_user["scenario"].id + 9999,
                as_of=_AS_OF_AFTER_RAISE,
            )
            assert result == Decimal("0")

            # Same call with the correct scenario_id resolves the profile.
            result_match = income_service.get_current_gross_biweekly(
                seed_user["user"].id,
                calendar_for(seed_user["user"].id),
                scenario_id=seed_user["scenario"].id,
                as_of=_AS_OF_AFTER_RAISE,
            )
            assert result_match == _NO_RAISE_GROSS


class TestConsumerIntegration:
    """C17-4: every downstream consumer reads the same engine value."""

    def test_c17_4_savings_year_end_investment_agree_on_raised_gross(
        self, app, db, seed_user, seed_periods_today,
    ):
        """C17-4: four consumers route through the engine-derived value.

        Sets up one raise-applicable scenario and calls each consumer's
        private helper (or the producer that fans the value out).  All
        four must report the same engine-derived per-period gross.  The
        fixture uses ``seed_periods_today`` so ``date.today()`` falls
        in a period whose ``period_month >= effective_month`` -- the
        raise effective Jan 2026 applies to every 2026 period.

        Hand arithmetic: ``104000 * 1.03 / 26 = 4120.00``.
        """
        with app.app_context():
            scenario = seed_user["scenario"]
            bctx = BalanceContext.build(seed_user["user"].id)
            user_id = seed_user["user"].id
            profile = _create_profile(user_id, scenario.id)
            _add_one_time_raise(
                profile, effective_month=1, effective_year=2026,
            )
            db.session.commit()

            # Producer: the canonical helper itself.
            canonical = income_service.get_current_gross_biweekly(
                user_id, calendar_for(user_id),
            )
            assert canonical == _RAISE_APPLIED_GROSS

            # Savings consumer: after the Level-1 balance-seam reroute the
            # savings package no longer loads the gross itself -- each
            # investment tile delegates its projection to the ``balance_at``
            # seam, which loads the engine gross in
            # ``_contribution_inputs_for_account`` (fetched ONLY when the account has
            # investment params, the seam's investment-only scoping).  So the
            # savings consumer's gross now routes seam -> income_service; lock
            # it at the seam's own loading point.  A real INVESTMENT account is
            # required or the seam skips the gross fetch by design (returning
            # ZERO), which is asserted below as the scoping control.
            inv = make_investment_account(
                seed_user, db.session, seed_periods_today[0],
                Decimal("10000.00"),
            )
            seam_inputs = balance_at._contribution_inputs_for_account(
                inv, BalanceContext.build(user_id),
            )
            assert seam_inputs.salary_gross_biweekly == canonical

            # The scoping control: a non-investment account in the same user's
            # set gets NO gross, so the assertion above is pinning the
            # investment-only fetch rather than a value every account carries.
            checking_inputs = balance_at._contribution_inputs_for_account(
                seed_user["account"], BalanceContext.build(user_id),
            )
            assert checking_inputs.salary_gross_biweekly == Decimal("0")
            assert checking_inputs.investment_params is None

            # Investment consumer: Commit 17 introduced a thin
            # ``_salary_gross_biweekly`` wrapper around
            # ``income_service.get_current_gross_biweekly``; Commit 18
            # (F-22) removed the wrapper and routed
            # ``_projection_inputs_for_account`` through the canonical
            # helper directly.  Asserting the producer alone still
            # locks the producer/consumer agreement because the
            # investment dashboard now has no intermediate site that
            # could drift.
            investment_val = income_service.get_current_gross_biweekly(
                user_id, calendar_for(user_id),
            )
            assert investment_val == canonical


class TestLiveProjectedNetUsesPerYearTaxConfigs:
    """DH-#30: live_projected_net resolves tax configs PER period year.

    The recurrence engine GENERATES the stored grid amount using each
    period's OWN tax year; the live recompute must resolve the same way or
    the stored cache and the live value silently disagree -- the exact
    reconciliation contract live_projected_net advertises.  Pre-fix it
    loaded a single current-year config set and applied it across the whole
    ~2-year horizon, so a future-year period was recomputed against the
    wrong year's tax.
    """

    def test_future_year_txn_uses_future_year_state_rate(
        self, app, db, monkeypatch, seed_user, seed_periods_52,
    ):
        """A 2027 salary income row recomputes against 2027's state rate.

        Seeds NC flat state tax at different rates for 2026 (3.99%) and
        2027 (6.00%), then asserts a 2027 period's live net equals the
        2027-rate projection and differs from the 2026-rate one.  ``today``
        is frozen to 2026 so the pre-fix single current-year load would
        have used the 2026 rate -- the revert-proof property (without the
        freeze, a suite run in 2027 would make the old code pick 2027 by
        coincidence).
        """
        freeze_today(monkeypatch, date(2026, 6, 1))
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            profile = _create_profile(user_id, scenario_id)  # $104k, NC
            template = _make_salary_template(seed_user, profile)

            flat_type = db.session.query(TaxType).filter_by(name="flat").one()
            db.session.add_all([
                StateTaxConfig(
                    user_id=user_id, state_code="NC", tax_year=2026,
                    tax_type_id=flat_type.id,
                    filing_status_id=profile.filing_status_id,
                    flat_rate=Decimal("0.0399"),
                ),
                StateTaxConfig(
                    user_id=user_id, state_code="NC", tax_year=2027,
                    tax_type_id=flat_type.id,
                    filing_status_id=profile.filing_status_id,
                    flat_rate=Decimal("0.0600"),
                ),
            ])
            db.session.commit()

            periods = all_periods(user_id)
            period_2027 = next(
                (p for p in periods if p.start_date.year == 2027), None,
            )
            assert period_2027 is not None, "seed_periods_52 must reach 2027"
            txn = _make_txn(
                seed_user, period_2027, template=template,
                estimated_amount="1.00",
            )
            db.session.commit()

            # Engine-faithful expectations that isolate WHICH year's rate
            # was applied (the resolution under test, not the paycheck
            # math): project the same periods with one year's config set at
            # a time and read the 2027 period's net from each.
            net_2027_rate = {
                bd.period.period_id: bd.earnings.net_pay
                for bd in paycheck_calculator.project_salary(
                    payroll_basis(profile, _derived(user_id)),
                    _derived(user_id),
                    load_tax_configs(user_id, profile, tax_year=2027),
                    calibration=profile.calibration,
                )
            }[period_2027.id]
            net_2026_rate = {
                bd.period.period_id: bd.earnings.net_pay
                for bd in paycheck_calculator.project_salary(
                    payroll_basis(profile, _derived(user_id)),
                    _derived(user_id),
                    load_tax_configs(user_id, profile, tax_year=2026),
                    calibration=profile.calibration,
                )
            }[period_2027.id]
            # The two state rates genuinely diverge, so the test cannot
            # pass vacuously.
            assert net_2027_rate != net_2026_rate

            overrides = _live_net_map(user_id, scenario_id, [txn])
            assert overrides[txn.id] == net_2027_rate
            assert overrides[txn.id] != net_2026_rate


class TestTheProjectionDoesNotMoveWhenTheCalendarYearTURNS:
    """The New Year cliff: a projection may not change because a date passed.

    Tax configuration is seeded per year and nothing seeds the next one, so on
    every January 1 the CURRENT year is an unconfigured year.  The retired
    resolution rule substituted "the current calendar year", which cannot
    answer for the year it is itself: a request for the now-current year found
    nothing to redirect to and resolved to no configuration at all.  The
    paycheck engine reads a missing ``fica_config`` as ZERO Social Security
    (``tax_calculator.capped_social_security`` documents that arm for
    bootstrap), so the whole SS line silently vanished from every projected
    paycheck in that year and later.

    Measured on a clone of production 2026-08-11, before the fix: on
    2027-01-01, with no write and no user action, 40 of 51 live-priced salary
    rows changed and projected income over the horizon rose by **$8,460.50**;
    counting the 11 periods the grid's own rolling top-up creates that same
    day, **$10,914.93** over 51 of 62 rows.  One period went from
    ``NET 2,639.30`` (ss 205.19) to ``NET 2,844.49`` (ss 0.00).

    Neither figure was only a display defect.  A settle writes the live amount
    into ``estimated_amount`` before the status flip
    (``transaction_service._reconcile_cached_amount``), after which the row
    leaves this producer's Projected-only candidate set and nothing can repair
    it; and any salary or tax-config save regenerates every row from today
    forward.  The read-time gap had two write-back doors.
    """

    def _seed_2026_only(self, user_id, profile):
        """Seed NC state tax and FICA for 2026 and for no other year."""
        flat_type = db.session.query(TaxType).filter_by(name="flat").one()
        db.session.add_all([
            StateTaxConfig(
                user_id=user_id, state_code="NC", tax_year=2026,
                tax_type_id=flat_type.id,
                filing_status_id=profile.filing_status_id,
                flat_rate=Decimal("0.0399"),
            ),
            FicaConfig(
                user_id=user_id, tax_year=2026,
                ss_rate=Decimal("0.0620"),
                ss_wage_base=Decimal("184500.00"),
                medicare_rate=Decimal("0.0145"),
            ),
        ])
        db.session.commit()

    def test_a_2027_paycheck_is_priced_the_same_in_2026_and_in_2027(
        self, app, db, monkeypatch, seed_user, seed_periods_52,
    ):
        """The same row, the same inputs, two different "todays", one answer.

        The property the resolution rule exists for.  Nothing about the row or
        its inputs changes between the two reads -- only the wall clock -- so
        any difference is the app rewriting a projection because a date passed.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            profile = _create_profile(user_id, scenario_id)  # $104k, NC
            template = _make_salary_template(seed_user, profile)
            self._seed_2026_only(user_id, profile)

            periods = all_periods(user_id)
            period_2027 = next(
                (p for p in periods if p.start_date.year == 2027), None,
            )
            assert period_2027 is not None, "seed_periods_52 must reach 2027"
            txn = _make_txn(
                seed_user, period_2027, template=template,
                estimated_amount="1.00",
            )
            db.session.commit()

            freeze_today(monkeypatch, date(2026, 6, 1))
            priced_in_2026 = _live_net_map(user_id, scenario_id, [txn])[txn.id]

            freeze_today(monkeypatch, date(2027, 6, 1))
            priced_in_2027 = _live_net_map(user_id, scenario_id, [txn])[txn.id]

            assert priced_in_2027 == priced_in_2026

    def test_every_withholding_line_is_what_an_unresolved_year_deletes(
        self, app, db, seed_user, seed_periods_52,
    ):
        """Non-vacuity: an unresolved 2027 really does zero the withholding.

        Without this the sibling above could pass with both reads equally
        wrong.  It prices the same 2027 period against the EXACT-year loader
        -- which substitutes nothing and so returns the three ``None``s the
        retired rule produced on 2027-01-01 -- and shows every withholding
        line collapsing to zero, which raises the net by their sum.

        On production only the Social Security line moved, because that
        profile carries an ACTIVE calibration and the calibrated path takes
        federal and state from stored effective rates; only SS still reads
        ``fica_config``.  This fixture has no calibration, so it exercises the
        bracket path and all three lines move.  Both are the same defect --
        a config set that resolved to nothing -- seen through different tax
        paths.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            profile = _create_profile(user_id, scenario_id)  # $104k, NC
            _make_salary_template(seed_user, profile)
            self._seed_2026_only(user_id, profile)

            periods = all_periods(user_id)
            period_2027 = next(
                p for p in periods if p.start_date.year == 2027
            )

            derived = _derived(user_id)
            derived_2027 = next(
                p for p in derived if p.start_date.year == 2027
            )
            basis = payroll_basis(profile, derived)
            resolved = paycheck_calculator.calculate_paycheck(
                basis, derived_2027,
                load_tax_configs_for_year(user_id, profile, 2027),
            )
            unresolved = paycheck_calculator.calculate_paycheck(
                basis, derived_2027,
                load_tax_configs(user_id, profile, 2027),
            )

            # $104,000 / 26 = $4,000.00 gross, no pre-tax deductions, so each
            # line is a flat rate on the full gross:
            #   state    4000.00 * 0.0399 = 159.60
            #   SS       4000.00 * 0.0620 = 248.00  (under the $184,500 base)
            #   medicare 4000.00 * 0.0145 =  58.00
            #   federal                    =   0.00  (no bracket set seeded)
            # net = 4000.00 - 159.60 - 248.00 - 58.00 = 3,534.40
            assert resolved.earnings.gross_biweekly == Decimal("4000.00")
            assert resolved.taxes.state == Decimal("159.60")
            assert resolved.taxes.social_security == Decimal("248.00")
            assert resolved.taxes.medicare == Decimal("58.00")
            assert resolved.earnings.net_pay == Decimal("3534.40")

            assert unresolved.taxes.state == Decimal("0.00")
            assert unresolved.taxes.social_security == Decimal("0.00")
            assert unresolved.taxes.medicare == Decimal("0.00")
            assert unresolved.earnings.net_pay == Decimal("4000.00")

            # The whole withholding, handed back to the projection as income.
            assert (
                unresolved.earnings.net_pay - resolved.earnings.net_pay
                == Decimal("465.60")
            )
