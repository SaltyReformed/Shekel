"""
Shekel Budget App -- Transaction route package: blueprint declaration.

Leaf module that declares ``transactions_bp`` so the per-concern
sub-modules (:mod:`~app.routes.transactions.forms`, ``create``,
``mutations``, ``carry_forward``) and the shared
:mod:`~app.routes.transactions._helpers` can import the blueprint without a
circular dependency on the package ``__init__`` (which imports those
sub-modules for their registration side effects).  Mirrors the
``app/routes/accounts/_bp.py`` and ``app/routes/salary/_bp.py`` cycle-break
pattern.
"""

from flask import Blueprint

transactions_bp = Blueprint("transactions", __name__)
