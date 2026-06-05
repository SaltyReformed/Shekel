"""
Shekel Budget App -- Loan route package: blueprint declaration.

Leaf module that declares ``loan_bp`` so the per-concern sub-modules
(:mod:`~app.routes.loan.dashboard`, ``params``, ``escrow_rates``,
``calculators``, ``payment_transfer``) and the shared
:mod:`~app.routes.loan._helpers` can import the blueprint without a circular
dependency on the package ``__init__`` (which imports those sub-modules for
their registration side effects).  Mirrors the ``app/routes/accounts/_bp.py``,
``app/routes/salary/_bp.py``, ``app/routes/transactions/_bp.py``, and
``app/routes/transfers/_bp.py`` cycle-break pattern.
"""

from flask import Blueprint

loan_bp = Blueprint("loan", __name__)
