"""
Shekel Budget App -- Salary route package: the old tax-configuration URL.

``GET /salary/tax-config`` redirects to the Settings tax section, so a link or
bookmark to the retired page still lands on the law.  The two POST doors that
wrote the per-user state-tax and FICA rows were deleted at plan step
salary:X-at-1 (ruling **R-SAL74**): the tax law has one home,
:mod:`app.tax_law`, and a release edits it.  One of them wrote a single state
standard deduction to all four filing statuses (finding **SAL-574**).
"""

from flask import redirect, url_for

from app.utils.auth_helpers import require_owner
from app.routes.salary._bp import salary_bp


@salary_bp.route("/salary/tax-config")
@require_owner
def tax_config():
    """Redirect to the Settings dashboard's Tax Rates section (read-only)."""
    return redirect(url_for("settings.show", section="tax"))
