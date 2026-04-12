"""
Shekel Budget App -- Charts Route (Redirect Stub)

The /charts page was replaced by /analytics in Section 8.
This redirect preserves old bookmarks.  All chart functionality
now lives in the analytics tabs.
"""

from flask import Blueprint, redirect, url_for
from flask_login import login_required

from app.utils.auth_helpers import require_owner

charts_bp = Blueprint("charts", __name__)


@charts_bp.route("/charts")
@login_required
@require_owner
def dashboard():
    """Redirect old /charts URL to /analytics (301 permanent)."""
    return redirect(url_for("analytics.page"), code=301)
