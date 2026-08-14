"""
Shekel Budget App -- Grid route package: blueprint declaration.

Leaf module that declares ``grid_bp`` so the per-concern sub-modules
(:mod:`~app.routes.grid.page`, :mod:`~app.routes.grid.partials`) and the
shared :mod:`~app.routes.grid._shared` can import the blueprint without a
circular dependency on the package ``__init__`` (which imports those
sub-modules for their registration side effects).  Mirrors the
``app/routes/transactions/_bp.py``, ``app/routes/accounts/_bp.py`` and
``app/routes/salary/_bp.py`` cycle-break pattern.
"""

from flask import Blueprint

grid_bp = Blueprint("grid", __name__)
