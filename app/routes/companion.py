"""
Shekel Budget App -- Companion View Routes

Minimal stub for Commit 9: provides the ``companion.index`` endpoint
so login routing can redirect companion users.  Full implementation
with period navigation, transaction cards, and entry management is
in Commit 10.
"""

from flask import Blueprint, redirect, render_template, url_for
from flask_login import current_user, login_required

from app import ref_cache
from app.enums import RoleEnum

companion_bp = Blueprint("companion", __name__, url_prefix="/companion")


@companion_bp.route("/")
@login_required
def index():
    """Companion landing page.

    Redirects owner users to the grid -- companions only.
    Renders a minimal placeholder page until the full companion
    view is implemented in Commit 10.
    """
    companion_role_id = ref_cache.role_id(RoleEnum.COMPANION)
    if current_user.role_id != companion_role_id:
        return redirect(url_for("grid.index"))
    return render_template("companion/index.html")
