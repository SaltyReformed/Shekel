"""
Shekel Budget App -- Card route package: blueprint declaration.

Leaf module that declares ``card_bp`` so the per-concern sub-modules
(:mod:`~app.routes.card.terms`, and the APR and cockpit modules plan steps
CC-3 and CC-11 add) and the shared :mod:`~app.routes.card._helpers` can import
the blueprint without a circular dependency on the package ``__init__`` (which
imports those sub-modules for their registration side effects).  Mirrors the
``app/routes/loan/_bp.py`` cycle-break pattern.
"""

from flask import Blueprint

card_bp = Blueprint("card", __name__)
