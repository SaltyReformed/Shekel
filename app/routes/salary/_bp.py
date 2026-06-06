"""
Shekel Budget App -- Salary route package: blueprint declaration.

Leaf module that declares ``salary_bp`` so the per-domain sub-modules
(:mod:`~app.routes.salary.profiles`, ``items`` (raises + deductions),
``views``, ``calibration``, ``tax_config``) and the shared
:mod:`~app.routes.salary._helpers` can import the blueprint without a
circular dependency on the package ``__init__`` (which imports those
sub-modules for their registration side effects).  Mirrors the
``app/routes/accounts/_bp.py`` cycle-break pattern.
"""

from flask import Blueprint

salary_bp = Blueprint("salary", __name__)
