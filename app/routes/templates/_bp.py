"""
Shekel Budget App -- Recurring route package: blueprint declaration.

Leaf module that declares ``templates_bp`` so the per-concern sub-modules
(:mod:`~app.routes.templates.surface`, :mod:`~app.routes.templates.crud`) can
import the blueprint without a circular dependency on the package ``__init__``
(which imports those sub-modules for their registration side effects).  Mirrors
the ``app/routes/accounts/_bp.py``, ``app/routes/salary/_bp.py``,
``app/routes/transactions/_bp.py`` and ``app/routes/transfers/_bp.py``
cycle-break pattern.
"""

from flask import Blueprint

templates_bp = Blueprint("templates", __name__)
