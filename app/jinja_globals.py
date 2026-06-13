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
requires editing exactly one list -- ``_REF_ID_GLOBALS`` below.
"""

from collections.abc import Callable
from enum import Enum

from flask import Flask

from app import ref_cache
from app.enums import (
    AcctCategoryEnum,
    AcctTypeEnum,
    CalcMethodEnum,
    DeductionTimingEnum,
    EmployerContributionTypeEnum,
    GoalModeEnum,
    IncomeUnitEnum,
    RecurrencePatternEnum,
    StatusEnum,
    TxnTypeEnum,
)

# Every ID-derived Jinja global, grouped by the ``ref_cache`` accessor
# that resolves it.  Each group pairs one accessor with the
# ``{global_name: enum_member}`` map it applies; ``register_ref_id_globals``
# folds the whole table into ``app.jinja_env.globals``.  Adding a constant
# is one row here -- the single source of truth the F-7 extraction
# established (see the module docstring).
_REF_ID_GLOBALS: tuple[tuple[Callable[[Enum], int], dict[str, Enum]], ...] = (
    (ref_cache.status_id, {
        "STATUS_PROJECTED": StatusEnum.PROJECTED,
        "STATUS_DONE": StatusEnum.DONE,
        "STATUS_RECEIVED": StatusEnum.RECEIVED,
        "STATUS_CREDIT": StatusEnum.CREDIT,
        "STATUS_CANCELLED": StatusEnum.CANCELLED,
        "STATUS_SETTLED": StatusEnum.SETTLED,
    }),
    (ref_cache.txn_type_id, {
        "TXN_TYPE_INCOME": TxnTypeEnum.INCOME,
        "TXN_TYPE_EXPENSE": TxnTypeEnum.EXPENSE,
    }),
    # Account type IDs -- all types registered so templates can use
    # integer comparisons instead of string-based name checks.
    (ref_cache.acct_type_id, {
        "ACCT_TYPE_CHECKING": AcctTypeEnum.CHECKING,
        "ACCT_TYPE_SAVINGS": AcctTypeEnum.SAVINGS,
        "ACCT_TYPE_HYSA": AcctTypeEnum.HYSA,
        "ACCT_TYPE_MONEY_MARKET": AcctTypeEnum.MONEY_MARKET,
        "ACCT_TYPE_CD": AcctTypeEnum.CD,
        "ACCT_TYPE_HSA": AcctTypeEnum.HSA,
        "ACCT_TYPE_CREDIT_CARD": AcctTypeEnum.CREDIT_CARD,
        "ACCT_TYPE_MORTGAGE": AcctTypeEnum.MORTGAGE,
        "ACCT_TYPE_AUTO_LOAN": AcctTypeEnum.AUTO_LOAN,
        "ACCT_TYPE_STUDENT_LOAN": AcctTypeEnum.STUDENT_LOAN,
        "ACCT_TYPE_PERSONAL_LOAN": AcctTypeEnum.PERSONAL_LOAN,
        "ACCT_TYPE_HELOC": AcctTypeEnum.HELOC,
        "ACCT_TYPE_401K": AcctTypeEnum.K401,
        "ACCT_TYPE_ROTH_401K": AcctTypeEnum.ROTH_401K,
        "ACCT_TYPE_TRADITIONAL_IRA": AcctTypeEnum.TRADITIONAL_IRA,
        "ACCT_TYPE_ROTH_IRA": AcctTypeEnum.ROTH_IRA,
        "ACCT_TYPE_BROKERAGE": AcctTypeEnum.BROKERAGE,
        "ACCT_TYPE_529": AcctTypeEnum.PLAN_529,
    }),
    (ref_cache.recurrence_pattern_id, {
        "REC_EVERY_PERIOD": RecurrencePatternEnum.EVERY_PERIOD,
        "REC_EVERY_N_PERIODS": RecurrencePatternEnum.EVERY_N_PERIODS,
        "REC_MONTHLY": RecurrencePatternEnum.MONTHLY,
        "REC_MONTHLY_FIRST": RecurrencePatternEnum.MONTHLY_FIRST,
        "REC_QUARTERLY": RecurrencePatternEnum.QUARTERLY,
        "REC_SEMI_ANNUAL": RecurrencePatternEnum.SEMI_ANNUAL,
        "REC_ANNUAL": RecurrencePatternEnum.ANNUAL,
        "REC_ONCE": RecurrencePatternEnum.ONCE,
    }),
    (ref_cache.acct_category_id, {
        "ACCT_CAT_ASSET": AcctCategoryEnum.ASSET,
        "ACCT_CAT_LIABILITY": AcctCategoryEnum.LIABILITY,
        "ACCT_CAT_RETIREMENT": AcctCategoryEnum.RETIREMENT,
        "ACCT_CAT_INVESTMENT": AcctCategoryEnum.INVESTMENT,
    }),
    (ref_cache.deduction_timing_id, {
        "TIMING_PRE_TAX": DeductionTimingEnum.PRE_TAX,
        "TIMING_POST_TAX": DeductionTimingEnum.POST_TAX,
    }),
    (ref_cache.calc_method_id, {
        "CALC_PERCENTAGE": CalcMethodEnum.PERCENTAGE,
        "CALC_FLAT": CalcMethodEnum.FLAT,
    }),
    (ref_cache.goal_mode_id, {
        "GOAL_MODE_FIXED": GoalModeEnum.FIXED,
        "GOAL_MODE_INCOME_RELATIVE": GoalModeEnum.INCOME_RELATIVE,
    }),
    (ref_cache.income_unit_id, {
        "INCOME_UNIT_PAYCHECKS": IncomeUnitEnum.PAYCHECKS,
        "INCOME_UNIT_MONTHS": IncomeUnitEnum.MONTHS,
    }),
    (ref_cache.employer_contribution_type_id, {
        "EMPLOYER_TYPE_NONE": EmployerContributionTypeEnum.NONE,
        "EMPLOYER_TYPE_FLAT_PERCENTAGE": EmployerContributionTypeEnum.FLAT_PERCENTAGE,
        "EMPLOYER_TYPE_MATCH": EmployerContributionTypeEnum.MATCH,
    }),
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
    ``create_app``), it MUST skip this call -- the accessors in
    ``_REF_ID_GLOBALS`` would raise ``KeyError`` for missing enum
    members.

    Args:
        app: The Flask application instance whose
            ``jinja_env.globals`` map will be populated.
    """
    globals_map = app.jinja_env.globals
    for accessor, members in _REF_ID_GLOBALS:
        for name, member in members.items():
            globals_map[name] = accessor(member)
