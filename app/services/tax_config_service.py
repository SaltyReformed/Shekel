"""
Shekel Budget App -- Tax Config Service

Loads tax configuration objects (bracket sets, state configs, FICA)
required by the paycheck calculator.  Extracted from the salary route
to eliminate a route-to-route import and a duplicate copy in
chart_data_service.py.
"""

from datetime import date

from app.extensions import db
from app.models.tax_config import FicaConfig, StateTaxConfig, TaxBracketSet


def load_tax_configs(user_id, profile, tax_year=None):
    """Load tax configuration objects for paycheck calculation.

    Queries TaxBracketSet, StateTaxConfig, and FicaConfig for a given
    tax year, matching the given salary profile's filing status and
    state code.

    Args:
        user_id (int): The owning user's ID -- all tax configs are
            per-user so the query is ownership-scoped.
        profile (SalaryProfile): Must have ``filing_status_id`` and
            ``state_code`` attributes.
        tax_year (int, optional): The tax year to load configs for.
            Defaults to the current calendar year when ``None``.

    Returns:
        dict: Keys ``bracket_set``, ``state_config``, ``fica_config``.
            Each value is the matching model instance or ``None`` if no
            configuration exists for the requested year.
    """
    if tax_year is None:
        tax_year = date.today().year

    bracket_set = (
        db.session.query(TaxBracketSet)
        .filter_by(
            user_id=user_id,
            filing_status_id=profile.filing_status_id,
            tax_year=tax_year,
        )
        .first()
    )

    state_config = (
        db.session.query(StateTaxConfig)
        .filter_by(
            user_id=user_id,
            state_code=profile.state_code,
            tax_year=tax_year,
        )
        .first()
    )

    fica_config = (
        db.session.query(FicaConfig)
        .filter_by(user_id=user_id, tax_year=tax_year)
        .first()
    )

    return {
        "bracket_set": bracket_set,
        "state_config": state_config,
        "fica_config": fica_config,
    }


def load_tax_configs_for_year(user_id, profile, tax_year, *, fallback_year=None):
    """Load tax configs for ``tax_year``, falling back to ``fallback_year``.

    Loads the bracket set, state config, and FICA config for ``tax_year``
    (via :func:`load_tax_configs`).  When that year has NO configs at all
    -- every value ``None`` -- and ``tax_year`` differs from the fallback,
    re-loads the fallback year's configs instead.

    This is the single owner of the rule "use the period's own tax year,
    but fall back to the current calendar year when a future year has not
    been configured."  Every surface that resolves per-year configs -- the
    recurrence engine (which GENERATES the stored grid net pay), the
    year-end summary, and the salary projection / breakdown / dashboard
    paths -- goes through here so the generated amount and the live
    recompute cannot diverge on which year's brackets and FICA wage
    base/cap apply (deep-hunt DH-#30).

    Args:
        user_id (int): The owning user's ID.
        profile (SalaryProfile): Supplies ``filing_status_id`` and
            ``state_code`` (passed straight through to
            :func:`load_tax_configs`).
        tax_year (int): The tax year to load configs for.
        fallback_year (int, optional): Year to fall back to when
            ``tax_year`` has no configs at all.  Defaults to the current
            calendar year.

    Returns:
        dict: Keys ``bracket_set``, ``state_config``, ``fica_config``.
    """
    configs = load_tax_configs(user_id, profile, tax_year=tax_year)
    if fallback_year is None:
        fallback_year = date.today().year
    if (tax_year != fallback_year
            and configs["bracket_set"] is None
            and configs["state_config"] is None
            and configs["fica_config"] is None):
        configs = load_tax_configs(user_id, profile, tax_year=fallback_year)
    return configs


def load_tax_configs_for_periods(user_id, profile, periods, *, fallback_year=None):
    """Resolve tax configs for every distinct tax year present in ``periods``.

    Returns a ``{tax_year: configs}`` mapping so a multi-year salary
    projection can apply each period's OWN year's brackets, state config,
    and FICA wage base/cap -- the per-year resolution the recurrence engine
    already performs when generating the stored grid amounts (DH-#30).
    Each distinct year is resolved ONCE via :func:`load_tax_configs_for_year`
    (not once per period), so a full ~2-year horizon costs a handful of
    queries rather than one batch per period.  The current-year fallback is
    fixed for the whole call so every year resolves against the same
    reference (a call that straddles New Year's cannot pick two different
    fallbacks).

    Args:
        user_id (int): The owning user's ID.
        profile (SalaryProfile): Supplies ``filing_status_id`` and
            ``state_code``.
        periods (list): PayPeriod objects; ``start_date.year`` selects the
            tax year for each.
        fallback_year (int, optional): Passed through to
            :func:`load_tax_configs_for_year`.  Defaults to the current
            calendar year.

    Returns:
        dict: ``{tax_year: {bracket_set, state_config, fica_config}}`` for
            each distinct year in ``periods`` (empty when ``periods`` is
            empty).
    """
    if fallback_year is None:
        fallback_year = date.today().year
    years = {period.start_date.year for period in periods}
    return {
        year: load_tax_configs_for_year(
            user_id, profile, year, fallback_year=fallback_year
        )
        for year in years
    }
