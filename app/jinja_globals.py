"""Single source of truth for ID-derived Jinja globals.

Registers every constant the templates consume by integer ID rather
than by ref-table ``name`` string (the project's "IDs for logic,
strings for display only" invariant -- see CLAUDE.md "Reference
tables: IDs for logic, strings for display only.").

Called from two places:

  1. ``app.create_app()`` once per app construction, after
     ``ref_cache.init(...)`` succeeds, so templates rendered by a
     normal request can read the constants without a database hit.
  2. ``tests/conftest.py::_refresh_ref_cache_and_jinja_globals``
     once per test, after the per-test drop+reclone has truncated
     ``ref.account_types`` (Phase 3b / C-28 / F-044) and the seed
     has been re-run with fresh sequence IDs.  Without this re-seat
     the previously-registered globals would point at IDs that no
     longer exist and every template referencing one would raise
     ``UndefinedError``.

The audit follow-up Commit 6 (F-7) extracted this helper after
verification showed the conftest copy of the registration list was
missing eight entries (``TIMING_PRE_TAX``, ``TIMING_POST_TAX``,
``CALC_PERCENTAGE``, ``CALC_FLAT``, ``GOAL_MODE_FIXED``,
``GOAL_MODE_INCOME_RELATIVE``, ``INCOME_UNIT_PAYCHECKS``,
``INCOME_UNIT_MONTHS``).  Folding both call sites through one
function makes future drift impossible: adding a new constant
requires editing exactly one list.
"""

from flask import Flask

from app import ref_cache
from app.enums import (
    AcctCategoryEnum,
    AcctTypeEnum,
    CalcMethodEnum,
    DeductionTimingEnum,
    GoalModeEnum,
    IncomeUnitEnum,
    RecurrencePatternEnum,
    StatusEnum,
    TxnTypeEnum,
)


def register_ref_id_globals(app: Flask) -> None:
    """Register every ID-derived Jinja global on the given Flask app.

    Idempotent: each call overwrites the same set of keys with the
    same values resolved from ``ref_cache``, so repeated invocation
    (e.g. once per test after a ref-cache reseat) is safe and has
    no externally observable side effect beyond the final dict
    state.

    Pre-condition: ``ref_cache.init(...)`` has completed successfully
    against the current session.  If the caller observed any
    unavailable ref tables (the bootstrap-window branch in
    ``create_app``), it MUST skip this call -- the accessors below
    would raise ``KeyError`` for missing enum members.

    Args:
        app: The Flask application instance whose
            ``jinja_env.globals`` map will be populated.
    """
    # Status IDs
    app.jinja_env.globals["STATUS_PROJECTED"] = ref_cache.status_id(StatusEnum.PROJECTED)
    app.jinja_env.globals["STATUS_DONE"] = ref_cache.status_id(StatusEnum.DONE)
    app.jinja_env.globals["STATUS_RECEIVED"] = ref_cache.status_id(StatusEnum.RECEIVED)
    app.jinja_env.globals["STATUS_CREDIT"] = ref_cache.status_id(StatusEnum.CREDIT)
    app.jinja_env.globals["STATUS_CANCELLED"] = ref_cache.status_id(StatusEnum.CANCELLED)
    app.jinja_env.globals["STATUS_SETTLED"] = ref_cache.status_id(StatusEnum.SETTLED)

    # Transaction type IDs
    app.jinja_env.globals["TXN_TYPE_INCOME"] = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    app.jinja_env.globals["TXN_TYPE_EXPENSE"] = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)

    # Account type IDs -- all types registered so templates can use
    # integer comparisons instead of string-based name checks.
    app.jinja_env.globals["ACCT_TYPE_CHECKING"] = ref_cache.acct_type_id(AcctTypeEnum.CHECKING)
    app.jinja_env.globals["ACCT_TYPE_SAVINGS"] = ref_cache.acct_type_id(AcctTypeEnum.SAVINGS)
    app.jinja_env.globals["ACCT_TYPE_HYSA"] = ref_cache.acct_type_id(AcctTypeEnum.HYSA)
    app.jinja_env.globals["ACCT_TYPE_MONEY_MARKET"] = ref_cache.acct_type_id(AcctTypeEnum.MONEY_MARKET)
    app.jinja_env.globals["ACCT_TYPE_CD"] = ref_cache.acct_type_id(AcctTypeEnum.CD)
    app.jinja_env.globals["ACCT_TYPE_HSA"] = ref_cache.acct_type_id(AcctTypeEnum.HSA)
    app.jinja_env.globals["ACCT_TYPE_CREDIT_CARD"] = ref_cache.acct_type_id(AcctTypeEnum.CREDIT_CARD)
    app.jinja_env.globals["ACCT_TYPE_MORTGAGE"] = ref_cache.acct_type_id(AcctTypeEnum.MORTGAGE)
    app.jinja_env.globals["ACCT_TYPE_AUTO_LOAN"] = ref_cache.acct_type_id(AcctTypeEnum.AUTO_LOAN)
    app.jinja_env.globals["ACCT_TYPE_STUDENT_LOAN"] = ref_cache.acct_type_id(AcctTypeEnum.STUDENT_LOAN)
    app.jinja_env.globals["ACCT_TYPE_PERSONAL_LOAN"] = ref_cache.acct_type_id(AcctTypeEnum.PERSONAL_LOAN)
    app.jinja_env.globals["ACCT_TYPE_HELOC"] = ref_cache.acct_type_id(AcctTypeEnum.HELOC)
    app.jinja_env.globals["ACCT_TYPE_401K"] = ref_cache.acct_type_id(AcctTypeEnum.K401)
    app.jinja_env.globals["ACCT_TYPE_ROTH_401K"] = ref_cache.acct_type_id(AcctTypeEnum.ROTH_401K)
    app.jinja_env.globals["ACCT_TYPE_TRADITIONAL_IRA"] = ref_cache.acct_type_id(AcctTypeEnum.TRADITIONAL_IRA)
    app.jinja_env.globals["ACCT_TYPE_ROTH_IRA"] = ref_cache.acct_type_id(AcctTypeEnum.ROTH_IRA)
    app.jinja_env.globals["ACCT_TYPE_BROKERAGE"] = ref_cache.acct_type_id(AcctTypeEnum.BROKERAGE)
    app.jinja_env.globals["ACCT_TYPE_529"] = ref_cache.acct_type_id(AcctTypeEnum.PLAN_529)

    # Recurrence pattern IDs
    app.jinja_env.globals["REC_EVERY_N_PERIODS"] = ref_cache.recurrence_pattern_id(RecurrencePatternEnum.EVERY_N_PERIODS)
    app.jinja_env.globals["REC_MONTHLY"] = ref_cache.recurrence_pattern_id(RecurrencePatternEnum.MONTHLY)
    app.jinja_env.globals["REC_MONTHLY_FIRST"] = ref_cache.recurrence_pattern_id(RecurrencePatternEnum.MONTHLY_FIRST)
    app.jinja_env.globals["REC_QUARTERLY"] = ref_cache.recurrence_pattern_id(RecurrencePatternEnum.QUARTERLY)
    app.jinja_env.globals["REC_SEMI_ANNUAL"] = ref_cache.recurrence_pattern_id(RecurrencePatternEnum.SEMI_ANNUAL)
    app.jinja_env.globals["REC_ANNUAL"] = ref_cache.recurrence_pattern_id(RecurrencePatternEnum.ANNUAL)
    app.jinja_env.globals["REC_ONCE"] = ref_cache.recurrence_pattern_id(RecurrencePatternEnum.ONCE)

    # Account category IDs
    app.jinja_env.globals["ACCT_CAT_ASSET"] = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
    app.jinja_env.globals["ACCT_CAT_LIABILITY"] = ref_cache.acct_category_id(AcctCategoryEnum.LIABILITY)
    app.jinja_env.globals["ACCT_CAT_RETIREMENT"] = ref_cache.acct_category_id(AcctCategoryEnum.RETIREMENT)
    app.jinja_env.globals["ACCT_CAT_INVESTMENT"] = ref_cache.acct_category_id(AcctCategoryEnum.INVESTMENT)

    # Deduction timing IDs
    app.jinja_env.globals["TIMING_PRE_TAX"] = ref_cache.deduction_timing_id(DeductionTimingEnum.PRE_TAX)
    app.jinja_env.globals["TIMING_POST_TAX"] = ref_cache.deduction_timing_id(DeductionTimingEnum.POST_TAX)

    # Calc method IDs
    app.jinja_env.globals["CALC_PERCENTAGE"] = ref_cache.calc_method_id(CalcMethodEnum.PERCENTAGE)
    app.jinja_env.globals["CALC_FLAT"] = ref_cache.calc_method_id(CalcMethodEnum.FLAT)

    # Goal mode IDs
    app.jinja_env.globals["GOAL_MODE_FIXED"] = ref_cache.goal_mode_id(GoalModeEnum.FIXED)
    app.jinja_env.globals["GOAL_MODE_INCOME_RELATIVE"] = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)

    # Income unit IDs
    app.jinja_env.globals["INCOME_UNIT_PAYCHECKS"] = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)
    app.jinja_env.globals["INCOME_UNIT_MONTHS"] = ref_cache.income_unit_id(IncomeUnitEnum.MONTHS)
