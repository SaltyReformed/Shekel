"""
Shekel Budget App -- Transfer route package: blueprint declaration.

Leaf module that declares ``transfers_bp`` so the per-concern sub-modules
(:mod:`~app.routes.transfers.templates`, ``forms``, ``mutations``) and the
shared :mod:`~app.routes.transfers._helpers` can import the blueprint without
a circular dependency on the package ``__init__`` (which imports those
sub-modules for their registration side effects).  Mirrors the
``app/routes/accounts/_bp.py``, ``app/routes/salary/_bp.py``, and
``app/routes/transactions/_bp.py`` cycle-break pattern.
"""

from flask import Blueprint

transfers_bp = Blueprint("transfers", __name__)
