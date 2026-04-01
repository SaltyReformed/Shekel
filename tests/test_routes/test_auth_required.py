"""
Shekel Budget App -- Unauthenticated Access Tests

Verifies that every protected endpoint in the application redirects
unauthenticated requests to /login. This is the centralized auth gate
test that replaces the need for individual auth tests in each route
test file.

Generated from a complete audit of all @login_required endpoints across
all 16 route blueprints. If a new route is added to the application,
it must be added to PROTECTED_ENDPOINTS in this file.

Addresses: Test Audit Report Cross-Cutting Issue 3.
"""

import pytest


PROTECTED_ENDPOINTS = [
    # -- auth blueprint --
    ("POST", "/logout"),  # auth.logout
    ("POST", "/change-password"),  # auth.change_password
    ("POST", "/invalidate-sessions"),  # auth.invalidate_sessions
    ("GET", "/mfa/setup"),  # auth.mfa_setup
    ("POST", "/mfa/confirm"),  # auth.mfa_confirm
    # auth.regenerate_backup_codes
    ("POST", "/mfa/regenerate-backup-codes"),
    ("GET", "/mfa/disable"),  # auth.mfa_disable
    ("POST", "/mfa/disable"),  # auth.mfa_disable_confirm

    # -- grid blueprint --
    ("GET", "/"),  # grid.index
    ("GET", "/grid/balance-row"),  # grid.balance_row

    # -- transactions blueprint --
    # transactions.get_cell
    ("GET", "/transactions/99999/cell"),
    # transactions.get_quick_edit
    ("GET", "/transactions/99999/quick-edit"),
    # transactions.get_full_edit
    ("GET", "/transactions/99999/full-edit"),
    # transactions.update_transaction
    ("PATCH", "/transactions/99999"),
    # transactions.mark_done
    ("POST", "/transactions/99999/mark-done"),
    # transactions.mark_credit
    ("POST", "/transactions/99999/mark-credit"),
    # transactions.unmark_credit
    ("DELETE", "/transactions/99999/unmark-credit"),
    # transactions.cancel_transaction
    ("POST", "/transactions/99999/cancel"),
    # transactions.get_quick_create
    ("GET", "/transactions/new/quick"),
    # transactions.get_full_create
    ("GET", "/transactions/new/full"),
    # transactions.get_empty_cell
    ("GET", "/transactions/empty-cell"),
    ("POST", "/transactions/inline"),  # transactions.create_inline
    ("POST", "/transactions"),  # transactions.create_transaction
    # transactions.delete_transaction
    ("DELETE", "/transactions/99999"),
    # transactions.carry_forward
    ("POST", "/pay-periods/99999/carry-forward"),

    # -- templates blueprint --
    ("GET", "/templates"),  # templates.list_templates
    ("GET", "/templates/new"),  # templates.new_template
    ("POST", "/templates"),  # templates.create_template
    # templates.edit_template
    ("GET", "/templates/99999/edit"),
    # templates.update_template
    ("POST", "/templates/99999"),
    # templates.archive_template
    ("POST", "/templates/99999/archive"),
    # templates.unarchive_template
    ("POST", "/templates/99999/unarchive"),
    # templates.preview_recurrence
    ("GET", "/templates/preview-recurrence"),

    # -- pay_periods blueprint --
    # pay_periods.generate_form
    ("GET", "/pay-periods/generate"),
    ("POST", "/pay-periods/generate"),  # pay_periods.generate

    # -- accounts blueprint --
    ("GET", "/accounts"),  # accounts.list_accounts
    ("GET", "/accounts/new"),  # accounts.new_account
    ("POST", "/accounts"),  # accounts.create_account
    # accounts.edit_account
    ("GET", "/accounts/99999/edit"),
    # accounts.update_account
    ("POST", "/accounts/99999"),
    # accounts.archive_account
    ("POST", "/accounts/99999/archive"),
    # accounts.unarchive_account
    ("POST", "/accounts/99999/unarchive"),
    # accounts.inline_anchor_update
    ("PATCH", "/accounts/99999/inline-anchor"),
    # accounts.inline_anchor_form
    ("GET", "/accounts/99999/inline-anchor-form"),
    # accounts.inline_anchor_display
    ("GET", "/accounts/99999/inline-anchor-display"),
    # accounts.create_account_type
    ("POST", "/accounts/types"),
    # accounts.update_account_type
    ("POST", "/accounts/types/99999"),
    # accounts.delete_account_type
    ("POST", "/accounts/types/99999/delete"),
    # accounts.true_up
    ("PATCH", "/accounts/99999/true-up"),
    # accounts.anchor_form
    ("GET", "/accounts/99999/anchor-form"),
    # accounts.anchor_display
    ("GET", "/accounts/99999/anchor-display"),
    ("GET", "/accounts/99999/interest"),  # accounts.interest_detail
    # accounts.update_interest_params
    ("POST", "/accounts/99999/interest/params"),

    # -- categories blueprint --
    ("GET", "/categories"),  # categories.list_categories
    ("POST", "/categories"),  # categories.create_category
    # categories.delete_category
    ("POST", "/categories/99999/delete"),

    # -- settings blueprint --
    ("GET", "/settings"),  # settings.show
    ("POST", "/settings"),  # settings.update

    # -- salary blueprint --
    ("GET", "/salary"),  # salary.list_profiles
    ("GET", "/salary/new"),  # salary.new_profile
    ("POST", "/salary"),  # salary.create_profile
    # salary.edit_profile
    ("GET", "/salary/99999/edit"),
    ("POST", "/salary/99999"),  # salary.update_profile
    # salary.delete_profile
    ("POST", "/salary/99999/delete"),
    # salary.add_raise
    ("POST", "/salary/99999/raises"),
    # salary.delete_raise
    ("POST", "/salary/raises/99999/delete"),
    # salary.add_deduction
    ("POST", "/salary/99999/deductions"),
    # salary.delete_deduction
    ("POST", "/salary/deductions/99999/delete"),
    # salary.breakdown
    ("GET", "/salary/99999/breakdown/99999"),
    # salary.breakdown_current
    ("GET", "/salary/99999/breakdown"),
    # salary.projection
    ("GET", "/salary/99999/projection"),
    ("GET", "/salary/tax-config"),  # salary.tax_config
    # salary.update_tax_config
    ("POST", "/salary/tax-config"),
    # salary.update_fica_config
    ("POST", "/salary/fica-config"),

    # -- transfers blueprint --
    # transfers.list_transfer_templates
    ("GET", "/transfers"),
    # transfers.new_transfer_template
    ("GET", "/transfers/new"),
    # transfers.create_transfer_template
    ("POST", "/transfers"),
    # transfers.edit_transfer_template
    ("GET", "/transfers/99999/edit"),
    # transfers.update_transfer_template
    ("POST", "/transfers/99999"),
    # transfers.archive_transfer_template
    ("POST", "/transfers/99999/archive"),
    # transfers.unarchive_transfer_template
    ("POST", "/transfers/99999/unarchive"),
    # transfers.get_cell
    ("GET", "/transfers/cell/99999"),
    # transfers.get_quick_edit
    ("GET", "/transfers/quick-edit/99999"),
    # transfers.get_full_edit
    ("GET", "/transfers/99999/full-edit"),
    # transfers.update_transfer
    ("PATCH", "/transfers/instance/99999"),
    # transfers.create_ad_hoc
    ("POST", "/transfers/ad-hoc"),
    # transfers.delete_transfer
    ("DELETE", "/transfers/instance/99999"),
    # transfers.mark_done
    ("POST", "/transfers/instance/99999/mark-done"),
    # transfers.cancel_transfer
    ("POST", "/transfers/instance/99999/cancel"),

    # -- savings blueprint --
    ("GET", "/savings"),  # savings.dashboard
    ("GET", "/savings/goals/new"),  # savings.new_goal
    ("POST", "/savings/goals"),  # savings.create_goal
    # savings.edit_goal
    ("GET", "/savings/goals/99999/edit"),
    # savings.update_goal
    ("POST", "/savings/goals/99999"),
    # savings.delete_goal
    ("POST", "/savings/goals/99999/delete"),

    # -- loan blueprint --
    # loan.dashboard
    ("GET", "/accounts/99999/loan"),
    # loan.create_params
    ("POST", "/accounts/99999/loan/setup"),
    # loan.update_params
    ("POST", "/accounts/99999/loan/params"),
    # loan.add_rate_change
    ("POST", "/accounts/99999/loan/rate"),
    # loan.add_escrow
    ("POST", "/accounts/99999/loan/escrow"),
    # loan.delete_escrow
    ("POST", "/accounts/99999/loan/escrow/99999/delete"),
    # loan.payoff_calculate
    ("POST", "/accounts/99999/loan/payoff"),

    # -- investment blueprint --
    # investment.dashboard
    ("GET", "/accounts/99999/investment"),
    # investment.growth_chart
    ("GET", "/accounts/99999/investment/growth-chart"),
    # investment.update_params
    ("POST", "/accounts/99999/investment/params"),

    # -- retirement blueprint --
    ("GET", "/retirement"),  # retirement.dashboard
    # retirement.pension_list
    ("GET", "/retirement/pension"),
    # retirement.create_pension
    ("POST", "/retirement/pension"),
    # retirement.edit_pension
    ("GET", "/retirement/pension/99999/edit"),
    # retirement.update_pension
    ("POST", "/retirement/pension/99999"),
    # retirement.delete_pension
    ("POST", "/retirement/pension/99999/delete"),
    # retirement.gap_analysis
    ("GET", "/retirement/gap"),
    # retirement.update_settings
    ("POST", "/retirement/settings"),

    # -- charts blueprint --
    ("GET", "/charts"),  # charts.dashboard
    # charts.balance_over_time
    ("GET", "/charts/balance-over-time"),
    # charts.spending_by_category
    ("GET", "/charts/spending-by-category"),
    # charts.budget_vs_actuals
    ("GET", "/charts/budget-vs-actuals"),
    ("GET", "/charts/amortization"),  # charts.amortization
    ("GET", "/charts/net-worth"),  # charts.net_worth
    ("GET", "/charts/net-pay"),  # charts.net_pay
]


@pytest.mark.parametrize("method,path", PROTECTED_ENDPOINTS)
def test_unauthenticated_redirects_to_login(client, method, path):
    """Every protected endpoint must redirect unauthenticated users."""
    dispatch = {
        "GET": client.get,
        "POST": lambda p: client.post(p, data={}),
        "PUT": lambda p: client.put(p, data={}),
        "PATCH": lambda p: client.patch(p, data={}),
        "DELETE": client.delete,
    }
    resp = dispatch[method](path)

    assert resp.status_code in (302, 303), (
        f"{method} {path} returned {resp.status_code}, "
        f"expected redirect"
    )

    location = resp.headers.get("Location", "")
    assert "/login" in location, (
        f"{method} {path} redirected to {location}, "
        f"expected /login"
    )
