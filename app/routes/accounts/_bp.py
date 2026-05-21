"""
Shekel Budget App -- Accounts Package Blueprint Declaration

Holds the ``accounts_bp`` :class:`flask.Blueprint` instance in a leaf
module so the per-sub-domain modules (``crud``, ``anchor``, ``types``,
``detail``) can import it without going back through
``app.routes.accounts.__init__``.  Pre-F-25 the blueprint lived in the
package init, which meant the package <-> submodule import round-trip
surfaced as four pylint ``R0401 Cyclic import`` warnings rooted at
``app/utils/account_validation.py:1``.  Splitting the declaration out
breaks the cycle without changing any registered URL, blueprint
attribute, or runtime behaviour.

No ``url_prefix`` is set: every route decorator in the sibling modules
carries the ``/accounts`` prefix explicitly (preserved verbatim from
the pre-F-1 monolithic file).  Adding ``url_prefix="/accounts"`` here
would require stripping every decorator's prefix in lockstep -- a
behavioural change the F-1 acceptance criteria explicitly forbid.
"""
from flask import Blueprint

accounts_bp = Blueprint("accounts", __name__)
