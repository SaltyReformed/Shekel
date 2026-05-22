"""Tests for the shared ID-derived Jinja-globals registration helper.

Locks the F-7 / Commit 6 invariant: the single source of truth for
the Jinja globals lives in :mod:`app.jinja_globals`, both
``create_app()`` and the conftest's per-test re-seat route through
it, and every previously-missing constant is now present so any
template that references one no longer raises ``UndefinedError``
at request time.
"""

# pylint: disable=import-outside-toplevel

from app import ref_cache
from app.enums import (
    CalcMethodEnum, DeductionTimingEnum, GoalModeEnum, IncomeUnitEnum,
)
from app.jinja_globals import register_ref_id_globals


def test_register_ref_id_globals_populates_previously_missing_entries(app):
    """All eight C-28-era missing constants are present after registration.

    The conftest list pre-Commit-6 omitted these; templates that
    referenced any of them at test time raised UndefinedError.
    """
    with app.app_context():
        register_ref_id_globals(app)

        assert app.jinja_env.globals["TIMING_PRE_TAX"] == (
            ref_cache.deduction_timing_id(DeductionTimingEnum.PRE_TAX)
        )
        assert app.jinja_env.globals["TIMING_POST_TAX"] == (
            ref_cache.deduction_timing_id(DeductionTimingEnum.POST_TAX)
        )
        assert app.jinja_env.globals["CALC_PERCENTAGE"] == (
            ref_cache.calc_method_id(CalcMethodEnum.PERCENTAGE)
        )
        assert app.jinja_env.globals["CALC_FLAT"] == (
            ref_cache.calc_method_id(CalcMethodEnum.FLAT)
        )
        assert app.jinja_env.globals["GOAL_MODE_FIXED"] == (
            ref_cache.goal_mode_id(GoalModeEnum.FIXED)
        )
        assert app.jinja_env.globals["GOAL_MODE_INCOME_RELATIVE"] == (
            ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
        )
        assert app.jinja_env.globals["INCOME_UNIT_PAYCHECKS"] == (
            ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)
        )
        assert app.jinja_env.globals["INCOME_UNIT_MONTHS"] == (
            ref_cache.income_unit_id(IncomeUnitEnum.MONTHS)
        )


def test_register_ref_id_globals_is_idempotent(app):
    """Calling the helper twice in succession is a no-op (same values).

    Each per-test ref-cache reseat calls the helper; subsequent
    invocations during a session must produce identical Jinja
    globals dicts so templates rendered between calls see a
    consistent view.
    """
    with app.app_context():
        register_ref_id_globals(app)
        first = dict(app.jinja_env.globals)

        register_ref_id_globals(app)
        second = dict(app.jinja_env.globals)

        # Same keys, same values.
        registered_keys = {
            "STATUS_PROJECTED", "STATUS_DONE", "STATUS_RECEIVED",
            "STATUS_CREDIT", "STATUS_CANCELLED", "STATUS_SETTLED",
            "TXN_TYPE_INCOME", "TXN_TYPE_EXPENSE",
            "ACCT_TYPE_CHECKING", "ACCT_TYPE_SAVINGS", "ACCT_TYPE_HYSA",
            "ACCT_TYPE_MONEY_MARKET", "ACCT_TYPE_CD", "ACCT_TYPE_HSA",
            "ACCT_TYPE_CREDIT_CARD", "ACCT_TYPE_MORTGAGE",
            "ACCT_TYPE_AUTO_LOAN", "ACCT_TYPE_STUDENT_LOAN",
            "ACCT_TYPE_PERSONAL_LOAN", "ACCT_TYPE_HELOC",
            "ACCT_TYPE_401K", "ACCT_TYPE_ROTH_401K",
            "ACCT_TYPE_TRADITIONAL_IRA", "ACCT_TYPE_ROTH_IRA",
            "ACCT_TYPE_BROKERAGE", "ACCT_TYPE_529",
            "REC_EVERY_N_PERIODS", "REC_MONTHLY", "REC_MONTHLY_FIRST",
            "REC_QUARTERLY", "REC_SEMI_ANNUAL", "REC_ANNUAL", "REC_ONCE",
            "ACCT_CAT_ASSET", "ACCT_CAT_LIABILITY",
            "ACCT_CAT_RETIREMENT", "ACCT_CAT_INVESTMENT",
            "TIMING_PRE_TAX", "TIMING_POST_TAX",
            "CALC_PERCENTAGE", "CALC_FLAT",
            "GOAL_MODE_FIXED", "GOAL_MODE_INCOME_RELATIVE",
            "INCOME_UNIT_PAYCHECKS", "INCOME_UNIT_MONTHS",
        }
        # Exactly 45 ID-derived globals.
        assert len(registered_keys) == 45
        for key in registered_keys:
            assert key in first, f"first pass missing {key}"
            assert first[key] == second[key], (
                f"value drifted across idempotent calls for {key}"
            )


def test_goal_form_renders_with_fixed_mode_id_constant(auth_client, app):
    """GET /savings/goals/new embeds the GOAL_MODE_FIXED id in the form.

    ``app/templates/savings/goal_form.html`` consumes the
    ``GOAL_MODE_FIXED`` global at two sites: the
    ``data-fixed-mode-id="..."`` attribute on the mode selector and
    a ``selected``-by-default branch.  Before Commit 6 the
    conftest's reseat helper omitted this constant; the route
    response would have rendered an empty ``data-fixed-mode-id=""``
    attribute (Jinja Undefined coerces to empty string in
    expression context with the default environment) and the
    JavaScript that gates the income-relative fields on the id
    comparison would have silently broken.
    """
    with app.app_context():
        expected_fixed_id = ref_cache.goal_mode_id(GoalModeEnum.FIXED)

    resp = auth_client.get("/savings/goals/new")
    assert resp.status_code == 200
    html = resp.data.decode()
    # The id must be embedded as an integer literal in the attribute.
    assert f'data-fixed-mode-id="{expected_fixed_id}"' in html
