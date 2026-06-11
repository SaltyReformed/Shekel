"""
Shekel Budget App -- Auth route package: blueprint declaration.

Leaf module that declares ``auth_bp`` so the per-domain sub-modules
(:mod:`~app.routes.auth.credentials`, :mod:`~app.routes.auth.mfa`,
:mod:`~app.routes.auth.session_security`) and the shared
:mod:`~app.routes.auth._helpers` can import the blueprint without a
circular dependency on the package ``__init__`` (which imports those
sub-modules for their registration side effects).  Mirrors the
``app/routes/salary/_bp.py`` / ``app/routes/accounts/_bp.py``
cycle-break pattern.
"""

from flask import Blueprint

auth_bp = Blueprint("auth", __name__)
