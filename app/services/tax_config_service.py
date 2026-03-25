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
