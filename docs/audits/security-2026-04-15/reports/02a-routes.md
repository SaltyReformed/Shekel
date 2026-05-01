# 02a -- Routes and Query Scoping Findings

## Summary

- **Blueprints reviewed:** 22
  - `app/routes/__init__.py` (6 lines; docstring-only)
  - `app/routes/analytics.py` (479 lines)
  - `app/routes/auth.py` (521 lines)
  - `app/routes/accounts.py` (996 lines)
  - `app/routes/categories.py` (209 lines)
  - `app/routes/charts.py` (22 lines)
  - `app/routes/companion.py` (161 lines)
  - `app/routes/dashboard.py` (185 lines)
  - `app/routes/debt_strategy.py` (448 lines)
  - `app/routes/entries.py` (256 lines)
  - `app/routes/grid.py` (467 lines)
  - `app/routes/health.py` (48 lines)
  - `app/routes/investment.py` (815 lines)
  - `app/routes/loan.py` (1256 lines)
  - `app/routes/obligations.py` (423 lines)
  - `app/routes/pay_periods.py` (54 lines)
  - `app/routes/retirement.py` (348 lines)
  - `app/routes/salary.py` (1113 lines)
  - `app/routes/savings.py` (236 lines)
  - `app/routes/settings.py` (548 lines)
  - `app/routes/templates.py` (584 lines)
  - `app/routes/transactions.py` (749 lines)
  - `app/routes/transfers.py` (882 lines)
- **Handlers reviewed:** 143
- **Finding count:** 0 Critical / 2 High / 8 Medium / 3 Low / 4 Info
- **Top concern:** `transactions.update_transaction` (PATCH) allows re-parenting a transaction to another user's pay period or category because the route never validates `pay_period_id` / `category_id` ownership before the unfiltered `setattr()` write -- a cross-user data-corruption IDOR (F-B1-01).

## Per-handler table

| Blueprint | Handler | Method(s) | Path | user_id filter? | is_deleted filter? | Marshmallow on mutation? | Uses auth_helpers? | 404 on not-yours? | Raw SQL? | joinedload? | Finding ref |
|-----------|---------|-----------|------|-----------------|-------------------|--------------------------|---------------------|-------------------|----------|-------------|-------------|
| health.py | health_check | GET | /health | n/a public | n/a | n/a | n/a | n/a | yes (safe) | n/a | F-B1-11 |
| charts.py | dashboard | GET | /charts | n/a redirect | n/a | n/a | require_owner | n/a | no | n/a | |
| pay_periods.py | generate_form | GET | /pay-periods/generate | n/a redirect | n/a | n/a | require_owner | n/a | no | n/a | |
| pay_periods.py | generate | POST | /pay-periods/generate | yes | n/a | yes | require_owner | n/a | no | n/a | |
| auth.py | login | GET POST | /login | n/a public | n/a | no (manual) | n/a | n/a | no | n/a | F-B1-04 |
| auth.py | register_form | GET | /register | n/a public | n/a | n/a | n/a | n/a | no | n/a | |
| auth.py | register | POST | /register | n/a public | n/a | no (manual) | n/a | n/a | no | n/a | F-B1-04 |
| auth.py | logout | POST | /logout | n/a self | n/a | n/a no-input | n/a | n/a | no | n/a | |
| auth.py | change_password | POST | /change-password | yes self | n/a | no (manual) | n/a | n/a | no | n/a | F-B1-04 |
| auth.py | invalidate_sessions | POST | /invalidate-sessions | yes self | n/a | n/a no-input | n/a | n/a | no | n/a | |
| auth.py | mfa_verify | GET POST | /mfa/verify | yes session | n/a | no (manual) | n/a | n/a | no | n/a | F-B1-04 |
| auth.py | mfa_setup | GET | /mfa/setup | yes self | n/a | n/a | n/a | n/a | no | n/a | |
| auth.py | mfa_confirm | POST | /mfa/confirm | yes self | n/a | no (manual) | n/a | n/a | no | n/a | F-B1-04 |
| auth.py | regenerate_backup_codes | POST | /mfa/regenerate-backup-codes | yes self | n/a | n/a no-input | n/a | n/a | no | n/a | |
| auth.py | mfa_disable | GET | /mfa/disable | yes self | n/a | n/a | n/a | n/a | no | n/a | |
| auth.py | mfa_disable_confirm | POST | /mfa/disable | yes self | n/a | no (manual) | n/a | n/a | no | n/a | F-B1-04 |
| dashboard.py | page | GET | / /dashboard | yes | yes (service) | n/a | require_owner | n/a | no | n/a (service) | |
| dashboard.py | mark_paid | POST | /dashboard/mark-paid/<int:txn_id> | yes helper | yes | no (manual) | require_owner | yes | no | n/a | F-B1-05 |
| dashboard.py | bills_section | GET | /dashboard/bills | yes | yes (service) | n/a | require_owner | n/a | no | n/a (service) | |
| dashboard.py | balance_section | GET | /dashboard/balance | yes | yes (service) | n/a | require_owner | n/a | no | n/a (service) | |
| companion.py | index | GET | /companion/ | yes service | yes (service) | n/a | n/a companion | n/a | no | n/a (service) | |
| companion.py | period_view | GET | /companion/period/<int:period_id> | yes service | yes (service) | n/a | n/a companion | yes | no | n/a (service) | |
| categories.py | list_categories | GET | /categories | n/a redirect | n/a | n/a | require_owner | n/a | no | n/a | |
| categories.py | create_category | POST | /categories | yes | n/a (cat) | yes | require_owner | n/a | no | n/a | |
| categories.py | edit_category | POST | /categories/<int:category_id>/edit | yes | n/a (cat) | yes | require_owner | yes | no | n/a | |
| categories.py | archive_category | POST | /categories/<int:category_id>/archive | yes | n/a (cat) | n/a no-input | require_owner | yes | no | n/a | |
| categories.py | unarchive_category | POST | /categories/<int:category_id>/unarchive | yes | n/a (cat) | n/a no-input | require_owner | yes | no | n/a | |
| categories.py | delete_category | POST | /categories/<int:category_id>/delete | yes | n/a (cat) | n/a no-input | require_owner | yes | no | n/a | |
| grid.py | index | GET | /grid | yes | yes | n/a | require_owner | n/a | no | yes selectinload | |
| grid.py | create_baseline | POST | /create-baseline | yes | n/a | n/a no-input | require_owner | n/a | no | n/a | |
| grid.py | balance_row | GET | /grid/balance-row | yes | yes | n/a | require_owner | n/a | no | yes selectinload | F-B1-09 |
| transactions.py | get_cell | GET | /transactions/<int:txn_id>/cell | yes helper | yes helper | n/a | require_owner | yes | no | n/a | |
| transactions.py | get_quick_edit | GET | /transactions/<int:txn_id>/quick-edit | yes helper | yes helper | n/a | require_owner | yes | no | n/a | |
| transactions.py | get_full_edit | GET | /transactions/<int:txn_id>/full-edit | yes helper | yes helper | n/a | require_owner | yes | no | n/a | |
| transactions.py | update_transaction | PATCH | /transactions/<int:txn_id> | yes helper | yes helper | yes (partial bypass) | require_owner | yes | no | n/a | F-B1-01 |
| transactions.py | mark_done | POST | /transactions/<int:txn_id>/mark-done | yes helper | yes helper | no (manual) | n/a companion-ok | yes | no | n/a | F-B1-05 |
| transactions.py | mark_credit | POST | /transactions/<int:txn_id>/mark-credit | yes helper | yes helper | n/a no-input | require_owner | yes | no | n/a | |
| transactions.py | unmark_credit | DELETE | /transactions/<int:txn_id>/unmark-credit | yes helper | yes helper | n/a no-input | require_owner | yes | no | n/a | |
| transactions.py | cancel_transaction | POST | /transactions/<int:txn_id>/cancel | yes helper | yes helper | n/a no-input | require_owner | yes | no | n/a | |
| transactions.py | get_quick_create | GET | /transactions/new/quick | yes | n/a | n/a | require_owner | yes | no | n/a | |
| transactions.py | get_full_create | GET | /transactions/new/full | yes | n/a | n/a | require_owner | yes | no | n/a | |
| transactions.py | get_empty_cell | GET | /transactions/empty-cell | yes | n/a | n/a | require_owner | yes | no | n/a | |
| transactions.py | create_inline | POST | /transactions/inline | yes | n/a | yes | require_owner | yes | no | n/a | |
| transactions.py | create_transaction | POST | /transactions | yes | n/a | yes | require_owner | yes | no | n/a | |
| transactions.py | delete_transaction | DELETE | /transactions/<int:txn_id> | yes helper | yes helper | n/a no-input | require_owner | yes | no | n/a | |
| transactions.py | carry_forward | POST | /pay-periods/<int:period_id>/carry-forward | yes | yes (service) | n/a no-input | require_owner | yes | no | n/a | |
| transfers.py | list_transfer_templates | GET | /transfers | yes | n/a (template) | n/a | require_owner | n/a | no | n/a | |
| transfers.py | new_transfer_template | GET | /transfers/new | yes | n/a | n/a | require_owner | n/a | no | n/a | |
| transfers.py | create_transfer_template | POST | /transfers | yes | n/a | yes | require_owner | yes | no | n/a | F-B1-06 |
| transfers.py | edit_transfer_template | GET | /transfers/<int:template_id>/edit | yes | n/a | n/a | require_owner | yes | no | n/a | |
| transfers.py | update_transfer_template | POST | /transfers/<int:template_id> | yes | n/a | yes | require_owner | yes | no | n/a | |
| transfers.py | archive_transfer_template | POST | /transfers/<int:template_id>/archive | yes | yes | n/a no-input | require_owner | yes | no | n/a | |
| transfers.py | unarchive_transfer_template | POST | /transfers/<int:template_id>/unarchive | yes | yes | n/a no-input | require_owner | yes | no | n/a | |
| transfers.py | hard_delete_transfer_template | POST | /transfers/<int:template_id>/hard-delete | yes | yes (helper) | n/a no-input | require_owner | yes | no | n/a | |
| transfers.py | get_cell (xfer) | GET | /transfers/cell/<int:xfer_id> | yes helper | n/a | n/a | require_owner | yes | no | n/a | |
| transfers.py | get_quick_edit (xfer) | GET | /transfers/quick-edit/<int:xfer_id> | yes helper | n/a | n/a | require_owner | yes | no | n/a | |
| transfers.py | get_full_edit (xfer) | GET | /transfers/<int:xfer_id>/full-edit | yes helper | n/a | n/a | require_owner | yes | no | n/a | |
| transfers.py | update_transfer | PATCH | /transfers/instance/<int:xfer_id> | yes helper | n/a | yes | require_owner | yes | no | n/a | F-B1-06 |
| transfers.py | create_ad_hoc | POST | /transfers/ad-hoc | yes (via service) | n/a | yes | require_owner | yes | no | n/a | F-B1-06 |
| transfers.py | delete_transfer (instance) | DELETE | /transfers/instance/<int:xfer_id> | yes helper | n/a | n/a no-input | require_owner | yes | no | n/a | |
| transfers.py | mark_done (xfer) | POST | /transfers/instance/<int:xfer_id>/mark-done | yes helper | n/a | n/a no-input | require_owner | yes | no | n/a | |
| transfers.py | cancel_transfer | POST | /transfers/instance/<int:xfer_id>/cancel | yes helper | n/a | n/a no-input | require_owner | yes | no | n/a | |
| templates.py | list_templates | GET | /templates | yes | n/a | n/a | require_owner | n/a | no | n/a | |
| templates.py | new_template | GET | /templates/new | yes | n/a | n/a | require_owner | n/a | no | n/a | |
| templates.py | create_template | POST | /templates | yes | n/a | yes | require_owner | yes | no | n/a | |
| templates.py | edit_template | GET | /templates/<int:template_id>/edit | yes | n/a | n/a | require_owner | yes | no | n/a | |
| templates.py | update_template | POST | /templates/<int:template_id> | yes | yes | yes | require_owner | yes | no | n/a | |
| templates.py | archive_template | POST | /templates/<int:template_id>/archive | yes | yes | n/a no-input | require_owner | yes | no | n/a | |
| templates.py | unarchive_template | POST | /templates/<int:template_id>/unarchive | yes | yes | n/a no-input | require_owner | yes | no | n/a | |
| templates.py | hard_delete_template | POST | /templates/<int:template_id>/hard-delete | yes | yes | n/a no-input | require_owner | yes | no | n/a | |
| templates.py | preview_recurrence | GET | /templates/preview-recurrence | yes | n/a | n/a | require_owner | yes | no | n/a | |
| entries.py | list_entries | GET | /transactions/<int:txn_id>/entries | yes helper | yes helper | n/a | n/a companion-ok | yes | no | n/a (service) | |
| entries.py | create_entry | POST | /transactions/<int:txn_id>/entries | yes helper | yes helper | yes | n/a companion-ok | yes | no | n/a (service) | |
| entries.py | update_entry | PATCH | /transactions/<int:txn_id>/entries/<int:entry_id> | yes helper | yes helper | yes | n/a companion-ok | yes | no | n/a (service) | |
| entries.py | toggle_cleared | PATCH | /transactions/<int:txn_id>/entries/<int:entry_id>/cleared | yes helper | yes helper | n/a no-input | n/a companion-ok | yes | no | n/a (service) | |
| entries.py | delete_entry | DELETE | /transactions/<int:txn_id>/entries/<int:entry_id> | yes helper | yes helper | n/a no-input | n/a companion-ok | yes | no | n/a (service) | |
| accounts.py | list_accounts | GET | /accounts | yes | n/a | n/a | require_owner | n/a | no | n/a | |
| accounts.py | new_account | GET | /accounts/new | n/a form | n/a | n/a | require_owner | n/a | no | n/a | |
| accounts.py | create_account | POST | /accounts | yes | n/a | yes | require_owner | n/a | no | n/a | |
| accounts.py | edit_account | GET | /accounts/<int:account_id>/edit | yes | n/a | n/a | require_owner | yes | no | n/a | |
| accounts.py | update_account | POST | /accounts/<int:account_id> | yes | n/a | yes | require_owner | yes | no | n/a | |
| accounts.py | archive_account | POST | /accounts/<int:account_id>/archive | yes | n/a | n/a no-input | require_owner | yes | no | n/a | |
| accounts.py | unarchive_account | POST | /accounts/<int:account_id>/unarchive | yes | n/a | n/a no-input | require_owner | yes | no | n/a | |
| accounts.py | hard_delete_account | POST | /accounts/<int:account_id>/hard-delete | yes | n/a | n/a no-input | require_owner | yes | no | n/a | |
| accounts.py | inline_anchor_update | PATCH | /accounts/<int:account_id>/inline-anchor | yes | n/a | yes | require_owner | yes | no | n/a | |
| accounts.py | inline_anchor_form | GET | /accounts/<int:account_id>/inline-anchor-form | yes | n/a | n/a | require_owner | yes | no | n/a | |
| accounts.py | inline_anchor_display | GET | /accounts/<int:account_id>/inline-anchor-display | yes | n/a | n/a | require_owner | yes | no | n/a | |
| accounts.py | create_account_type | POST | /accounts/types | n/a global | n/a | yes | require_owner | n/a | no | n/a | F-B1-07 |
| accounts.py | update_account_type | POST | /accounts/types/<int:type_id> | n/a global | n/a | yes | require_owner | n/a | no | n/a | F-B1-07 |
| accounts.py | delete_account_type | POST | /accounts/types/<int:type_id>/delete | n/a global | n/a | n/a no-input | require_owner | n/a | no | n/a | F-B1-07 |
| accounts.py | true_up | PATCH | /accounts/<int:account_id>/true-up | yes | n/a | yes | require_owner | yes | no | n/a | |
| accounts.py | anchor_form | GET | /accounts/<int:account_id>/anchor-form | yes | n/a | n/a | require_owner | yes | no | n/a | |
| accounts.py | anchor_display | GET | /accounts/<int:account_id>/anchor-display | yes | n/a | n/a | require_owner | yes | no | n/a | |
| accounts.py | interest_detail | GET | /accounts/<int:account_id>/interest | yes | yes | n/a | require_owner | partial (302) | no | n/a | F-B1-10 |
| accounts.py | update_interest_params | POST | /accounts/<int:account_id>/interest/params | yes | n/a | yes | require_owner | yes | no | n/a | |
| accounts.py | checking_detail | GET | /accounts/<int:account_id>/checking | yes | yes | n/a | require_owner | yes | no | n/a | |
| loan.py | dashboard | GET | /accounts/<int:account_id>/loan | yes (helper) | n/a (service) | n/a | require_owner | yes | no | n/a (service) | |
| loan.py | create_params | POST | /accounts/<int:account_id>/loan/setup | yes | n/a | yes | require_owner | yes | no | n/a | |
| loan.py | update_params | POST | /accounts/<int:account_id>/loan/params | yes (helper) | n/a | yes | require_owner | yes | no | n/a | |
| loan.py | add_rate_change | POST | /accounts/<int:account_id>/loan/rate | yes (helper) | n/a | yes | require_owner | yes | no | n/a | |
| loan.py | add_escrow | POST | /accounts/<int:account_id>/loan/escrow | yes (helper) | n/a | yes | require_owner | yes | no | n/a | |
| loan.py | delete_escrow | POST | /accounts/<int:account_id>/loan/escrow/<int:component_id>/delete | yes (helper+fk) | n/a | n/a no-input | require_owner | yes | no | n/a | |
| loan.py | payoff_calculate | POST | /accounts/<int:account_id>/loan/payoff | yes (helper) | n/a | yes | require_owner | yes | no | n/a | |
| loan.py | refinance_calculate | POST | /accounts/<int:account_id>/loan/refinance | yes (helper) | n/a | yes | require_owner | yes | no | n/a | |
| loan.py | create_payment_transfer | POST | /accounts/<int:account_id>/loan/create-transfer | yes (helper) | n/a | yes | require_owner | yes | no | n/a | |
| investment.py | dashboard (inv) | GET | /accounts/<int:account_id>/investment | yes | yes | n/a | require_owner | partial (302) | no | yes joinedload | F-B1-10 |
| investment.py | growth_chart | GET | /accounts/<int:account_id>/investment/growth-chart | yes | yes | n/a | require_owner | yes | no | yes joinedload | |
| investment.py | create_contribution_transfer | POST | /accounts/<int:account_id>/investment/create-contribution-transfer | yes | n/a | yes | require_owner | yes | no | n/a | |
| investment.py | update_params (inv) | POST | /accounts/<int:account_id>/investment/params | yes | n/a | yes | require_owner | partial (302) | no | n/a | F-B1-10 |
| debt_strategy.py | dashboard | GET | /debt-strategy | yes | n/a (loans) | n/a | require_owner | n/a | no | n/a | |
| debt_strategy.py | calculate | POST | /debt-strategy/calculate | yes | n/a (no-write) | no (manual) | require_owner | yes | no | n/a | F-B1-03 |
| analytics.py | page | GET | /analytics | n/a static | n/a | n/a | require_owner | n/a | no | n/a | |
| analytics.py | calendar_tab | GET | /analytics/calendar | yes (service) | yes (service) | n/a | require_owner | partial (trust svc) | no | n/a (service) | F-B1-02 |
| analytics.py | year_end_tab | GET | /analytics/year-end | yes (service) | yes (service) | n/a | require_owner | n/a | no | n/a (service) | |
| analytics.py | variance_tab | GET | /analytics/variance | yes (service) | yes (service) | n/a | require_owner | partial (trust svc) | no | n/a (service) | F-B1-08 |
| analytics.py | trends_tab | GET | /analytics/trends | yes (service) | yes (service) | n/a | require_owner | n/a | no | n/a (service) | |
| savings.py | dashboard | GET | /savings | yes (service) | yes (service) | n/a | require_owner | n/a | no | n/a (service) | |
| savings.py | new_goal | GET | /savings/goals/new | yes | n/a | n/a | require_owner | n/a | no | n/a | |
| savings.py | create_goal | POST | /savings/goals | yes | n/a | yes | require_owner | yes | no | n/a | |
| savings.py | edit_goal | GET | /savings/goals/<int:goal_id>/edit | yes | n/a | n/a | require_owner | yes | no | n/a | |
| savings.py | update_goal | POST | /savings/goals/<int:goal_id> | yes | n/a | yes | require_owner | yes | no | n/a | |
| savings.py | delete_goal | POST | /savings/goals/<int:goal_id>/delete | yes | n/a | n/a no-input | require_owner | yes | no | n/a | |
| retirement.py | dashboard | GET | /retirement | yes (service) | n/a (service) | n/a | require_owner | n/a | no | n/a (service) | |
| retirement.py | pension_list | GET | /retirement/pension | yes | n/a | n/a | require_owner | n/a | no | n/a | |
| retirement.py | create_pension | POST | /retirement/pension | yes | n/a | yes | require_owner | n/a | no | n/a | |
| retirement.py | edit_pension | GET | /retirement/pension/<int:pension_id>/edit | yes | n/a | n/a | require_owner | yes | no | n/a | |
| retirement.py | update_pension | POST | /retirement/pension/<int:pension_id> | yes | n/a | yes | require_owner | yes | no | n/a | |
| retirement.py | delete_pension | POST | /retirement/pension/<int:pension_id>/delete | yes | n/a | n/a no-input | require_owner | yes | no | n/a | |
| retirement.py | gap_analysis | GET | /retirement/gap | yes (service) | n/a (service) | n/a | require_owner | n/a | no | n/a (service) | |
| retirement.py | update_settings | POST | /retirement/settings | yes | n/a | yes | require_owner | yes | no | n/a | F-B1-12 |
| salary.py | list_profiles | GET | /salary | yes | n/a | n/a | require_owner | n/a | no | n/a | |
| salary.py | new_profile | GET | /salary/new | n/a form | n/a | n/a | require_owner | n/a | no | n/a | |
| salary.py | create_profile | POST | /salary | yes | n/a | yes | require_owner | n/a | no | n/a | F-B1-13 |
| salary.py | edit_profile | GET | /salary/<int:profile_id>/edit | yes | n/a | n/a | require_owner | yes | no | n/a | |
| salary.py | update_profile | POST | /salary/<int:profile_id> | yes | n/a | yes | require_owner | yes | no | n/a | F-B1-13 |
| salary.py | delete_profile | POST | /salary/<int:profile_id>/delete | yes | n/a | n/a no-input | require_owner | yes | no | n/a | |
| salary.py | add_raise | POST | /salary/<int:profile_id>/raises | yes | n/a | yes | require_owner | yes | no | n/a | F-B1-13 |
| salary.py | delete_raise | POST | /salary/raises/<int:raise_id>/delete | yes (via parent) | n/a | n/a no-input | require_owner | yes | no | n/a | F-B1-13 |
| salary.py | update_raise | POST | /salary/raises/<int:raise_id>/edit | yes (via parent) | n/a | yes | require_owner | yes | no | n/a | F-B1-13 |
| salary.py | add_deduction | POST | /salary/<int:profile_id>/deductions | yes | n/a | yes | require_owner | yes | no | n/a | F-B1-13 |
| salary.py | delete_deduction | POST | /salary/deductions/<int:ded_id>/delete | yes (via parent) | n/a | n/a no-input | require_owner | yes | no | n/a | F-B1-13 |
| salary.py | update_deduction | POST | /salary/deductions/<int:ded_id>/edit | yes (via parent) | n/a | yes | require_owner | yes | no | n/a | F-B1-13 |
| salary.py | breakdown | GET | /salary/<int:profile_id>/breakdown/<int:period_id> | yes | n/a | n/a | require_owner | yes | no | n/a | |
| salary.py | breakdown_current | GET | /salary/<int:profile_id>/breakdown | n/a redirect | n/a | n/a | require_owner | n/a | no | n/a | |
| salary.py | projection | GET | /salary/<int:profile_id>/projection | yes | n/a | n/a | require_owner | yes | no | n/a | |
| salary.py | calibrate_form | GET | /salary/<int:profile_id>/calibrate | yes | n/a | n/a | require_owner | yes | no | n/a | |
| salary.py | calibrate_preview | POST | /salary/<int:profile_id>/calibrate | yes | n/a | yes | require_owner | yes | no | n/a | |
| salary.py | calibrate_confirm | POST | /salary/<int:profile_id>/calibrate/confirm | yes | n/a | yes | require_owner | yes | no | n/a | F-B1-13 |
| salary.py | calibrate_delete | POST | /salary/<int:profile_id>/calibrate/delete | yes | n/a | n/a no-input | require_owner | yes | no | n/a | F-B1-13 |
| salary.py | tax_config | GET | /salary/tax-config | n/a redirect | n/a | n/a | require_owner | n/a | no | n/a | |
| salary.py | update_tax_config | POST | /salary/tax-config | yes | n/a | yes | require_owner | n/a | no | n/a | |
| salary.py | update_fica_config | POST | /salary/fica-config | yes | n/a | yes | require_owner | n/a | no | n/a | |
| obligations.py | summary | GET | /obligations | yes | n/a (template) | n/a | require_owner | n/a | no | yes joinedload | |
| settings.py | show | GET | /settings | yes | n/a | n/a | require_owner | n/a | no | n/a | |
| settings.py | update | POST | /settings | yes | n/a | yes | require_owner | n/a | no | n/a | |
| settings.py | companion_create | POST | /settings/companions | yes (linked) | n/a | yes | require_owner | yes | no | n/a | |
| settings.py | companion_edit | POST | /settings/companions/<int:companion_id>/edit | yes helper | n/a | yes | require_owner | yes | no | n/a | |
| settings.py | companion_deactivate | POST | /settings/companions/<int:companion_id>/deactivate | yes helper | n/a | n/a no-input | require_owner | yes | no | n/a | |
| settings.py | companion_reactivate | POST | /settings/companions/<int:companion_id>/reactivate | yes helper | n/a | n/a no-input | require_owner | yes | no | n/a | |

## Grep results

### request.form.get / request.json.get bypass checks

`request.json.get(` -- **no hits** (the project uses form submissions throughout).

`request.form.get(` hits (file:line -- schema context):

- `app/routes/transfers.py:850` -- inside `_resolve_shadow_context`; reads `source_txn_id` as auxiliary field outside the Marshmallow schema. Ownership is re-verified inside the helper. Acceptable single auxiliary field.
- `app/routes/transactions.py:321` -- inside `mark_done` (transfer branch); reads optional `actual_amount` with manual Decimal parse. No Marshmallow schema on this handler (see **F-B1-05**).
- `app/routes/transactions.py:355` -- inside `mark_done` (regular branch); same pattern, same finding (**F-B1-05**).
- `app/routes/settings.py:382-383`, `443-444` -- inside `companion_create`/`companion_edit` error re-render paths; only used to rebuild the form, schema still runs on primary code path. Acceptable.
- `app/routes/salary.py:377`, `452`, `506`, `583` -- manual checkbox reads (`is_recurring`, `inflation_enabled`) applied AFTER `schema.load()` because Marshmallow cannot natively fold HTML-form checkbox semantics. This is a schema-hole pattern -- the boolean is not validated by Marshmallow and the checkbox value is trusted. Acceptable because the field is coerced to strict bool via `== "on"`, but it is a pattern that could easily regress. Recorded as **F-B1-13** (Info).
- `app/routes/debt_strategy.py:234`, `251`, `261` -- `calculate` route parses `extra_monthly`, `strategy`, `custom_order` directly without a Marshmallow schema. This is POST-but-read-only (no DB writes). Recorded as **F-B1-03** (Medium).
- `app/routes/auth.py:85-87`, `167-170`, `205-207`, `267-268`, `391`, `479-480` -- login, register, change_password, mfa_verify, mfa_confirm, mfa_disable_confirm all parse credentials directly via `request.form.get` with no Marshmallow schema. Rate-limiting and service-layer validation are in place, but the route bypasses the project-mandated "Marshmallow schema for every state-changing route" rule. Recorded as **F-B1-04** (Medium; reduced from the default High because all of these are password/credential inputs where type-length validation adds marginal defence compared to the existing bcrypt + rate-limit stack).
- `app/routes/dashboard.py:162` -- inside `mark_paid` helper `_parse_actual_amount`; auxiliary field outside any Marshmallow schema on the handler. Recorded under **F-B1-05**.

### db.session.execute / text() usage

- `app/routes/health.py:39` -- `db.session.execute(db.text("SELECT 1"))`. Hardcoded, no user input, single round-trip for connectivity probe. **Safe.** Recorded as **F-B1-11** (Info: intentional exception documented in the docstring; the route is public and has `except Exception` but explicitly never echoes the exception to the response body).

No other blueprint contains `db.session.execute(` or `db.text(`. All other DB access goes through the SQLAlchemy ORM.

### get_or_404 without ownership check

**No hits.** The project does not use `Model.query.get_or_404(...)` anywhere in `app/routes/`. Every single lookup goes through `db.session.get(Model, pk)` followed by an explicit `record.user_id != current_user.id` check (or `_get_owned_transaction` / `_get_accessible_transaction` / `_load_companion_or_404` / `_get_owned_transfer` / `_load_loan_account` helper). This is the correct pattern per `app/utils/auth_helpers.py:78-83`, which is the only `get_or_404` definition and returns `None` (not 404) on missing or unowned rows so callers can uniformly 404 both cases.

### Routes without @login_required

| Route | Path | Justification |
|-------|------|---------------|
| `health.health_check` | `GET /health` | Intentionally public -- Docker HEALTHCHECK / load-balancer probe. Database-error branch never leaks exception details to the response (see F-B1-11). |
| `auth.login` | `GET POST /login` | Required to sign in. Rate-limited `5 per 15 minutes` on POST. |
| `auth.register_form` | `GET /register` | Registration landing page; gated by `REGISTRATION_ENABLED` flag (404 otherwise). Rate-limited `10 per hour`. |
| `auth.register` | `POST /register` | Registration submission; same gate + rate limit `3 per hour`. |
| `auth.mfa_verify` | `GET POST /mfa/verify` | Runs between password check and session creation; validates the Flask-session `_mfa_pending_user_id` manually. Intentional. |

All five are correctly excluded from `@login_required`. No other handlers in any blueprint are missing `@login_required` where required.

### |safe / Markup usage in render context

Zero `|safe` hits anywhere under `app/routes/`.

Four `Markup(...)` hits:
- `app/routes/templates.py:584` -- `preview_recurrence` returns a Markup string built from `p.start_date.strftime(...)` and `p.end_date.strftime(...)`, i.e. formatted dates from DB rows. Dates are not user-controlled strings. **Safe.**
- `app/routes/salary.py:146, 179, 662` -- three `flash(Markup(...))` calls that embed fixed HTML `<a href="...">` links built from `url_for(...)`. No user input interpolated. **Safe.**

## Findings

### F-B1-01: Cross-user re-parenting IDOR in `update_transaction` PATCH

- **Severity:** High
- **OWASP:** A01 (Broken Access Control)
- **CWE:** CWE-639 (Authorization Bypass Through User-Controlled Key)
- **Location:** `app/routes/transactions.py:183-285`
- **Evidence:**
  ```python
  @transactions_bp.route("/transactions/<int:txn_id>", methods=["PATCH"])
  @login_required
  @require_owner
  def update_transaction(txn_id):
      txn = _get_owned_transaction(txn_id)
      if txn is None:
          return "Not found", 404

      errors = _update_schema.validate(request.form)
      if errors:
          return jsonify(errors=errors), 400

      data = _update_schema.load(request.form)
      ...
      # Apply updates (regular transactions only).
      for field, value in data.items():
          setattr(txn, field, value)
      ...
      if txn.template_id and ("estimated_amount" in data or "pay_period_id" in data):
          txn.is_override = True

      try:
          db.session.commit()
      except IntegrityError:
          db.session.rollback()
          return "Invalid reference. Check that all referenced records exist.", 400
  ```
  `TransactionUpdateSchema` (`app/schemas/validation.py:20-36`) exposes both `pay_period_id` and `category_id` as plain `fields.Integer()`. The route never validates that the submitted `pay_period_id` or `category_id` belongs to `current_user`. Because the FK is valid (the row exists -- just under another user), PostgreSQL does not raise `IntegrityError`, and the unfiltered `for field, value in data.items(): setattr(txn, field, value)` happily writes the cross-user FK.

- **Impact:** Any authenticated owner can submit a PATCH with another user's `pay_period_id` (and/or `category_id`) to silently re-parent their own transaction into the victim's pay period. The transaction then appears on the victim's grid (because the victim's `balance_row` / `grid.index` scope queries by `Transaction.pay_period_id.in_(victim_period_ids)`) and is included in the victim's balance projection -- forbidden data mingling and projected-balance corruption. The attacker's own grid loses the row. In a multi-owner deployment this is a direct cross-tenant data-corruption vulnerability; in the current single-owner-plus-companion deployment the blast radius is reduced but the pattern still violates the "every query touching user data must filter by user_id" invariant.
- **Recommendation:** Mirror the ownership validation that `transactions.create_inline`, `transactions.create_transaction`, and `templates.update_template` already implement -- after `_update_schema.load`, and before the `setattr` loop, check:
  ```python
  if "pay_period_id" in data:
      period = db.session.get(PayPeriod, data["pay_period_id"])
      if not period or period.user_id != current_user.id:
          return "Pay period not found", 404
  if "category_id" in data:
      cat = db.session.get(Category, data["category_id"])
      if not cat or cat.user_id != current_user.id:
          return "Category not found", 404
  ```
  Alternatively, drop `pay_period_id` and `category_id` from `TransactionUpdateSchema` entirely and require separate dedicated endpoints for moves and re-categorizations. The same fix is required on `transfers.update_transfer` (`app/routes/transfers.py:617-667`) for `category_id` -- see **F-B1-06**.

---

### F-B1-02: `analytics.calendar_tab` passes raw `account_id` query param to service without ownership validation

- **Severity:** Medium
- **OWASP:** A01 (Broken Access Control)
- **CWE:** CWE-639
- **Location:** `app/routes/analytics.py:49-102`
- **Evidence:**
  ```python
  @analytics_bp.route("/analytics/calendar")
  @login_required
  @require_owner
  def calendar_tab():
      ...
      account_id = request.args.get("account_id", None, type=int)
      ...
      data = calendar_service.get_year_overview(
          user_id=current_user.id, year=year,
          account_id=account_id, large_threshold=threshold,
      )
      ...
      data = calendar_service.get_month_detail(
          user_id=current_user.id, year=year, month=month,
          account_id=account_id, large_threshold=threshold,
      )
  ```
  The route never verifies that `account_id` belongs to `current_user.id`. It passes both `user_id` and `account_id` to `calendar_service`, relying on the service to intersect them. If the service filters only by `account_id` (without re-joining `user_id`), this becomes a cross-user data read via query-string tampering. Services are explicitly out of scope for this subagent, so I can only verify the route-side contract. The route-side contract is "unvalidated input passed to service with both user_id and account_id" -- which per CLAUDE.md rule "Verify referenced rows exist and belong to the user" and "Validate FK existence before commit" must be enforced at the route boundary.
- **Impact:** Depending on `calendar_service` internals (Subagent B2's scope), this could be a direct read IDOR that exposes another user's calendar entries via a URL like `/analytics/calendar?account_id=<victim_id>&format=csv`. The `_csv_response` path returns the service output verbatim as a download, bypassing any template-level scoping.
- **Recommendation:** Validate the account before calling the service:
  ```python
  if account_id is not None:
      acct = db.session.get(Account, account_id)
      if not acct or acct.user_id != current_user.id:
          return "", 404
  ```
  The same fix pattern applies to `variance_tab` for `period_id` -- see **F-B1-08**.

---

### F-B1-03: `debt_strategy.calculate` parses POST form without Marshmallow schema

- **Severity:** Medium
- **OWASP:** A03 (Injection / untrusted input)
- **CWE:** CWE-20 (Improper Input Validation)
- **Location:** `app/routes/debt_strategy.py:222-356`
- **Evidence:**
  ```python
  @debt_strategy_bp.route("/debt-strategy/calculate", methods=["POST"])
  @login_required
  @require_owner
  def calculate():
      ...
      extra_raw = request.form.get("extra_monthly", "0").strip()
      try:
          extra_monthly = Decimal(extra_raw)
      except InvalidOperation:
          return render_template(...)
      ...
      strategy = request.form.get("strategy", STRATEGY_AVALANCHE).strip()
      if strategy not in _VALID_STRATEGIES:
          return render_template(...)
      ...
      custom_raw = request.form.get("custom_order", "").strip()
      ...
      try:
          custom_order = [int(x.strip()) for x in custom_raw.split(",")]
      except ValueError:
          return render_template(...)
  ```
- **Impact:** The route is a POST/state-changing method per project convention, but is read-only in practice (no DB writes). Three separate hand-rolled parsers bypass the "Marshmallow schema for every state-changing route" rule from `docs/coding-standards.md`. Missing: range validation on `extra_monthly` (the Decimal is only checked for negativity after parse), upper-bound check for the list length of `custom_order` (no cap -- a long comma-separated string forces the route through `calculate_strategy` with a potentially huge list), and no cap on `extra_monthly` magnitude. Low direct impact because no row is written, but the pattern bypasses the linter's project-wide schema rule and can regress if the handler grows side-effects later.
- **Recommendation:** Add a small `DebtStrategyCalculateSchema` with `extra_monthly = fields.Decimal(required=True, places=2, as_string=True, validate=Range(min=0, max=Decimal("1000000")))`, `strategy = fields.String(validate=OneOf(list(_VALID_STRATEGIES)))`, and `custom_order = fields.String(allow_none=True, validate=Length(max=500))`, then parse the custom order inside the route with the schema-validated string.

---

### F-B1-04: Auth blueprint parses credentials without Marshmallow schemas

- **Severity:** Medium
- **OWASP:** A01/A03 (Broken Access Control via missing input validation on credential flows)
- **CWE:** CWE-20
- **Location:** `app/routes/auth.py:73-132`, `151-188`, `201-228`, `250-344`, `377-427`, `472-521`
- **Evidence:**
  ```python
  # login
  email = request.form.get("email", "").strip()
  password = request.form.get("password", "")
  remember = request.form.get("remember") == "on"
  ...
  # register
  email = request.form.get("email", "")
  display_name = request.form.get("display_name", "")
  password = request.form.get("password", "")
  confirm_password = request.form.get("confirm_password", "")
  ...
  # change_password
  current_password = request.form.get("current_password", "")
  new_password = request.form.get("new_password", "")
  confirm_password = request.form.get("confirm_password", "")
  ...
  # mfa_verify
  totp_code = request.form.get("totp_code", "").strip()
  backup_code = request.form.get("backup_code", "").strip()
  ...
  # mfa_disable_confirm
  current_password = request.form.get("current_password", "")
  totp_code = request.form.get("totp_code", "").strip()
  ```
  None of these POST handlers call a Marshmallow schema. Project coding standards are explicit: "Marshmallow schema for every state-changing route. Every POST/PUT/PATCH/DELETE that accepts input must validate through Marshmallow before any database operations." CLAUDE.md reinforces with "No manual `request.form.get()` with inline `try/except`."
- **Impact:** The missing schemas let the route accept arbitrarily long `email`, `password`, `display_name`, etc. before they reach `auth_service.register_user` / `authenticate` / `change_password` / `hash_password`. The service layer does enforce bcrypt's 72-byte password limit (confirmed by `CompanionCreateSchema.validate_password_bytes`), but there is no corresponding schema for the owner's own registration / password-change path -- owner passwords flow straight into `bcrypt.hashpw()` with no byte-limit check. An email over 255 chars bypasses the `validate.Length(max=255)` constraint that `CompanionCreateSchema` applies. The inconsistency means a companion's credentials are validated more strictly than an owner's.
- **Recommendation:** Define `LoginSchema`, `RegisterSchema`, `ChangePasswordSchema`, `MfaVerifySchema`, `MfaConfirmSchema`, and `MfaDisableSchema` in `app/schemas/validation.py` with the same 12-char minimum, 72-byte maximum, and email-regex rules as `CompanionCreateSchema` (or extract the shared rules into a mixin). Thread them through each auth handler.

---

### F-B1-05: `mark_done` / `mark_paid` accept `actual_amount` via raw `request.form.get` with no Marshmallow schema

- **Severity:** Medium
- **OWASP:** A03 (Input validation)
- **CWE:** CWE-20
- **Location:** `app/routes/transactions.py:288-370`, `app/routes/dashboard.py:44-99`, `app/routes/dashboard.py:156-168`
- **Evidence:**
  ```python
  # transactions.mark_done (transfer branch)
  actual = request.form.get("actual_amount")
  if actual:
      try:
          svc_kwargs["actual_amount"] = Decimal(actual)
      except (InvalidOperation, ValueError, ArithmeticError):
          return "Invalid actual amount", 400
  ```
  ```python
  # transactions.mark_done (regular branch)
  actual = request.form.get("actual_amount")
  if actual:
      try:
          txn.actual_amount = Decimal(actual)
      except (InvalidOperation, ValueError, ArithmeticError):
          return "Invalid actual amount", 400
  ```
  ```python
  # dashboard._parse_actual_amount
  def _parse_actual_amount():
      actual = request.form.get("actual_amount")
      if not actual:
          return None
      try:
          return Decimal(actual)
      except (InvalidOperation, ValueError, ArithmeticError):
          return False
  ```
- **Impact:** Duplicated hand-rolled Decimal parsing in three places for a financial-amount field. None of them enforces a range constraint; `Decimal("-9999999999")` or `Decimal("1E+100")` would sail straight through and either (a) make it to the DB (where the CHECK constraint catches it only if `Numeric(12,2)` rounds to fit, otherwise an `InvalidOperation` leak can show up as a 500 not a 400), or (b) corrupt the user's actual_amount field with a negative or out-of-range value. The project has `TransactionUpdateSchema.actual_amount = fields.Decimal(places=2, as_string=True, allow_none=True, validate=validate.Range(min=0))` -- exactly the schema that should have been reused.
- **Recommendation:** Define a `MarkDoneSchema` with `actual_amount = fields.Decimal(places=2, as_string=True, allow_none=True, validate=validate.Range(min=0))`, or simply reuse `TransactionUpdateSchema` with partial loading in these handlers. Remove `dashboard._parse_actual_amount` and thread the schema output through both `mark_paid` and `mark_done`.

---

### F-B1-06: `transfers.create_ad_hoc` and `transfers.update_transfer` trust raw FK ids after Marshmallow without per-route ownership verification

- **Severity:** Medium
- **OWASP:** A01 (Broken Access Control)
- **CWE:** CWE-639
- **Location:**
  - `app/routes/transfers.py:670-712` (`create_ad_hoc`)
  - `app/routes/transfers.py:617-667` (`update_transfer`)
  - `app/routes/transfers.py:117-244` (`create_transfer_template`) -- partial coverage
- **Evidence:**
  ```python
  # create_ad_hoc
  data = _xfer_create_schema.load(request.form)
  ...
  xfer = transfer_service.create_transfer(
      user_id=current_user.id,
      from_account_id=data["from_account_id"],
      to_account_id=data["to_account_id"],
      pay_period_id=data["pay_period_id"],
      scenario_id=data["scenario_id"],
      amount=data["amount"],
      status_id=projected_id,
      category_id=data["category_id"],
      name=data.get("name"),
      notes=data.get("notes"),
  )
  ```
  ```python
  # update_transfer -- only the _xfer_update_schema.load dict is forwarded
  transfer_service.update_transfer(xfer.id, current_user.id, **data)
  ```
  Unlike `transactions.create_inline` (`transactions.py:585-640`) and `transactions.create_transaction` (`transactions.py:643-683`) -- which explicitly verify `account_id`, `pay_period_id`, `scenario_id`, and `category_id` all belong to the current user -- these two transfer handlers trust the ids outright and rely on the service to block cross-user writes. `create_transfer_template` does validate `from_account_id`, `to_account_id`, and `start_period_id` explicitly but never `category_id`.
- **Impact:** The route hands raw FKs to the service. If `transfer_service.create_transfer` / `update_transfer` do not re-check ownership on every FK field (Subagent B2's scope), this is a cross-tenant transfer-injection IDOR. Even under a permissive single-owner deployment, the inconsistency with `transactions.create_inline` is a latent defect: any future refactor of the service that drops a user-scoping assumption would immediately open the gap, because the route no longer serves as a second line of defence.
- **Recommendation:** Mirror the pattern in `transactions.create_inline`. In `create_ad_hoc`, validate all four FKs (`from_account_id`, `to_account_id`, `pay_period_id`, `scenario_id`) and `category_id` against `current_user.id` before calling the service. In `update_transfer`, validate `category_id` if present. In `create_transfer_template`, validate `category_id` as well.

---

### F-B1-07: Account-type mutation routes operate on a global (non-user-scoped) table

- **Severity:** Medium
- **OWASP:** A01 (Broken Access Control)
- **CWE:** CWE-732 (Incorrect Permission Assignment)
- **Location:** `app/routes/accounts.py:543-642`
- **Evidence:**
  ```python
  @accounts_bp.route("/accounts/types", methods=["POST"])
  @login_required
  @require_owner
  def create_account_type():
      errors = _type_create_schema.validate(request.form)
      ...
      account_type = AccountType(**data)
      db.session.add(account_type)
      db.session.commit()
  ```
  ```python
  @accounts_bp.route("/accounts/types/<int:type_id>", methods=["POST"])
  @login_required
  @require_owner
  def update_account_type(type_id):
      account_type = db.session.get(AccountType, type_id)
      if account_type is None:
          flash("Account type not found.", "danger")
          return redirect(...)
      ...
      for field in ("name", "category_id", "has_parameters", "has_amortization",
                    "has_interest", "is_pretax", "is_liquid", "icon_class",
                    "max_term_months"):
          if field in data:
              setattr(account_type, field, data[field])
  ```
- **Impact:** `ref.account_types` is a **global** ref table with no `user_id` column, so any owner can rename, mutate, or delete account types used by every other owner. In a multi-owner deployment this is a trivial cross-tenant disruption vector: owner A deletes "HYSA" and owner B's accounts now have a dangling FK. Even in today's single-owner deployment it is still a landmine for the next feature that opens the owner role to more than one user, and it breaks the documented boundary that routes should only mutate user-scoped data. `@require_owner` alone is not enough because it allows *any* owner to mutate the global table.
- **Recommendation:** Either (a) enforce `role_id == OWNER and user_id == <seeded_admin_id>` on these three handlers, explicitly noting that the single-owner model depends on this, or (b) add a `user_id` column to `ref.account_types` and migrate the seed rows to NULL (meaning "shared built-in"). Option (b) is cleaner; option (a) is the minimal fix.

---

### F-B1-08: `variance_tab` forwards raw `period_id` query param to `compute_variance` and to `_variance_csv_filename`

- **Severity:** Low
- **OWASP:** A01
- **CWE:** CWE-639
- **Location:** `app/routes/analytics.py:139-185`, `410-431`
- **Evidence:**
  ```python
  @analytics_bp.route("/analytics/variance")
  @login_required
  @require_owner
  def variance_tab():
      today = date.today()
      window_type, period_id, month, year = _resolve_variance_params(today)

      report = budget_variance_service.compute_variance(
          user_id=current_user.id,
          window_type=window_type,
          period_id=period_id,
          ...
      )
      ...
      fname = _variance_csv_filename(window_type, period_id, month, year)
  ```
  ```python
  def _variance_csv_filename(window_type, period_id, month, year):
      if window_type == "pay_period" and period_id is not None:
          period = db.session.get(PayPeriod, period_id)
          if period:
              return f"variance_period_{period.start_date.isoformat()}.csv"
          return "variance_period.csv"
  ```
  The filename helper fetches `PayPeriod` by ID without verifying ownership and leaks its `start_date` into the CSV `Content-Disposition` filename header.
- **Impact:** A cross-user attacker with any valid authenticated session (owner role) can guess `period_id` values and harvest the victim's pay-period `start_date` by reading the download filename on any variance export. Low direct value -- only a date is exposed -- but it crosses a tenant boundary. The primary report data itself is scoped via `compute_variance(user_id=current_user.id, period_id=period_id)`; Subagent B2 must verify that the service intersects `period_id` with `user_id` (otherwise this escalates).
- **Recommendation:** Validate `period_id` ownership inside `_resolve_variance_params` before returning:
  ```python
  if period_id is not None:
      period = db.session.get(PayPeriod, period_id)
      if not period or period.user_id != current_user.id:
          period_id = None
  ```
  Then `_variance_csv_filename` can trust its input. Alternatively, drop the period metadata from the filename and use `variance_period_<id>.csv`.

---

### F-B1-09: `grid.balance_row` dereferences `scenario.id` without None-checking `scenario`

- **Severity:** Low
- **OWASP:** n/a (availability / robustness)
- **CWE:** CWE-476 (NULL Pointer Dereference equivalent)
- **Location:** `app/routes/grid.py:400-441`
- **Evidence:**
  ```python
  scenario = (
      db.session.query(Scenario)
      .filter_by(user_id=user_id, is_baseline=True)
      .first()
  )
  ...
  current_period = pay_period_service.get_current_period(user_id)
  if not current_period:
      return "", 204

  start_index = current_period.period_index + start_offset
  periods = pay_period_service.get_periods_in_range(user_id, start_index, num_periods)
  all_periods = pay_period_service.get_all_periods(user_id)

  period_ids = [p.id for p in all_periods]
  txn_filters = [
      Transaction.pay_period_id.in_(period_ids),
      Transaction.scenario_id == scenario.id,   # ← AttributeError if scenario is None
      Transaction.is_deleted.is_(False),
  ]
  ```
  `grid.index` (same file, lines 163-364) handles the no-baseline case by redirecting to `grid/no_setup.html`, but `grid.balance_row` does not. If a user somehow ends up without a baseline scenario (deletion race, partial seed failure) the HTMX refresh of the balance row produces a 500 instead of graceful empty state.
- **Impact:** No security impact. Operational bug that surfaces a 500 to the front-end during an HTMX refresh.
- **Recommendation:** Guard the scenario lookup and return `"", 204` (same as the "no current_period" branch) when `scenario is None`.

---

### F-B1-10: Inconsistent error responses for not-owned accounts in detail/params routes

- **Severity:** Low
- **OWASP:** A01
- **CWE:** CWE-209 (Information Exposure Through an Error Message)
- **Location:**
  - `app/routes/accounts.py:752-765` (`interest_detail`) -- `redirect(url_for("accounts.list_accounts"))` on missing/unowned
  - `app/routes/investment.py:63-72` (`investment.dashboard`) -- `flash` + `redirect(url_for("savings.dashboard"))` on missing/unowned
  - `app/routes/investment.py:749-758` (`investment.update_params`) -- `flash` + `redirect(url_for("savings.dashboard"))` on missing/unowned
  - `app/routes/loan.py:401-409` (`loan.dashboard`) -- `flash` + `redirect(url_for("savings.dashboard"))` on missing/unowned
- **Evidence:**
  ```python
  @accounts_bp.route("/accounts/<int:account_id>/interest")
  @login_required
  @require_owner
  def interest_detail(account_id):
      account = db.session.get(Account, account_id)
      if account is None or account.user_id != current_user.id:
          return redirect(url_for("accounts.list_accounts"))
  ```
  ```python
  @investment_bp.route("/accounts/<int:account_id>/investment")
  @login_required
  @require_owner
  def dashboard(account_id):
      account = db.session.get(Account, account_id)
      if account is None or account.user_id != current_user.id:
          flash("Account not found.", "danger")
          return redirect(url_for("savings.dashboard"))
  ```
- **Impact:** The project security rule is "404 for both 'not found' and 'not yours.'" These handlers return 302 redirects instead, which is observable to an attacker and inconsistent with the sister routes (`accounts.checking_detail` correctly returns `"Not found", 404`). An attacker probing for account IDs can still tell "exists but not mine" from "does not exist" if the redirect Location header differs from a real navigation. Low direct value but a policy violation the rest of the blueprint has already stamped out.
- **Recommendation:** Replace the redirect with a plain `abort(404)` (or `return "Not found", 404`) in all four handlers. This matches the pattern used in `accounts.checking_detail`, `entries._get_accessible_transaction`, and every transactions/transfers helper.

---

### F-B1-11: `health_check` uses broad `except Exception:` without pylint tag narrowing

- **Severity:** Info
- **OWASP:** n/a (observability)
- **CWE:** CWE-755 (Improper Handling of Exceptional Conditions) -- very weak
- **Location:** `app/routes/health.py:37-48`
- **Evidence:**
  ```python
  try:
      db.session.execute(db.text("SELECT 1"))
      return jsonify({"status": "healthy", "database": "connected"}), 200
  except Exception as exc:  # pylint: disable=broad-except
      logger.error("Health check failed: %s", exc)
      return jsonify({
          "status": "unhealthy",
          "database": "error",
      }), 500
  ```
- **Impact:** Health endpoints that catch any exception are a legitimate exception to the project's "no broad except" rule (the goal is "return 500 no matter what"). The `# pylint: disable=broad-except` tag is correctly scoped to one line. This is acceptable, and the route does correctly avoid echoing `str(exc)` to the response body (the commit message cites "audit M5"). Info-only, recorded so the auditor can confirm continued intent.
- **Recommendation:** Keep the broad except. Consider tightening to `except (SQLAlchemyError, OperationalError)` if the project is willing to let other RuntimeErrors propagate as 500 -- but the current behaviour is defensible.

---

### F-B1-12: `retirement.update_settings` catches `except Exception: pass` during percentage parsing

- **Severity:** Info
- **OWASP:** n/a (error handling)
- **CWE:** CWE-396 (Declaration of Catch for Generic Exception)
- **Location:** `app/routes/retirement.py:292-297`
- **Evidence:**
  ```python
  form_data = dict(request.form)
  for field in ("safe_withdrawal_rate", "estimated_retirement_tax_rate"):
      if field in form_data and form_data[field]:
          try:
              form_data[field] = str(Decimal(form_data[field]) / Decimal("100"))
          except Exception:
              pass
  ```
  CLAUDE.md rule 1: "Do not use broad `except Exception`." A silent `pass` on a percentage-parse failure means the unconverted raw string is passed to Marshmallow, which will then produce a confusing validation error downstream (or, if the value happens to parse as Decimal on the second attempt inside the schema, store the unconverted value).
- **Impact:** Silently swallowing arithmetic errors in a financial-rate path is a coding-standards violation (CLAUDE.md rule 1). No security impact; an info-severity quality issue.
- **Recommendation:** Narrow to `except (InvalidOperation, ValueError, ArithmeticError):` and, on failure, leave the field untouched while letting Marshmallow reject it. A similar `except Exception: pass` sits at `app/routes/investment.py:813`, `_convert_percentage_inputs`, for the same reason and should be narrowed in the same fix.

---

### F-B1-13: `salary.py` uses bare `except Exception:` in a dozen mutation routes and a bare `request.form.get("is_recurring") == "on"` post-schema

- **Severity:** Info
- **OWASP:** n/a (code quality)
- **CWE:** CWE-396, CWE-20
- **Location:**
  - Broad excepts: `app/routes/salary.py:249`, `:326`, `:390`, `:420`, `:470`, `:521`, `:551`, `:604`, `:836`, `:875`, `:1041`
  - Checkbox post-load bypasses: `app/routes/salary.py:377`, `:452`, `:506`, `:583`
- **Evidence:**
  ```python
  # app/routes/salary.py:248-253 -- create_profile
  try:
      ...
      db.session.commit()
  except Exception:
      db.session.rollback()
      logger.exception("user_id=%d failed to create salary profile", current_user.id)
      flash("Failed to create salary profile. Please try again.", "danger")
      return redirect(url_for("salary.new_profile"))
  ```
  ```python
  # app/routes/salary.py:375-378 -- add_raise
  data = _raise_schema.load(request.form)
  # Handle checkbox -- form sends "on" or nothing
  data["is_recurring"] = request.form.get("is_recurring") == "on"
  ```
- **Impact:** Two patterns, both coding-standards violations:
  1. Eleven `except Exception:` blocks in salary.py's mutating handlers. `logger.exception(...)` is called, which is helpful, but the catch is too broad -- a `KeyError` or `AttributeError` from unrelated code (e.g. a template accessing a missing attribute while rendering an error path) is swallowed just as easily as the `SQLAlchemyError` / `IntegrityError` the block is presumably intended to handle. Per CLAUDE.md rule 1 and coding-standards "Catch specific exceptions": these must be narrowed to `(IntegrityError, SQLAlchemyError, RecurrenceConflict)` or whatever the actual raising surface is.
  2. Four checkbox-override lines that assign a boolean to `data` *after* `schema.load()` returns, bypassing any Marshmallow validation. Marshmallow does not natively round-trip HTML-form checkbox semantics (an unchecked checkbox sends no key at all), so the current fix is a local workaround. The correct fix is either a `@pre_load` hook in the schema that normalises `is_recurring`/`inflation_enabled` to `"true"`/`"false"` before validation, or a dedicated `fields.Boolean(load_default=False)` with the same pre-load pattern used by `AccountTypeUpdateSchema.strip_empty_strings`.
- **Recommendation:** Narrow every `except Exception:` in `salary.py` to the specific exceptions each `try` block can actually raise. Move the checkbox coercion into `RaiseCreateSchema` and `DeductionCreateSchema` via a `@pre_load` hook that folds missing-checkbox keys into `"false"`.

---

## What was checked and found clean

- **`charts.py:20-22`** -- single redirect handler correctly guarded by `@login_required + @require_owner`; no queries.
- **`health.py:25-48`** -- correctly public, no user data touched, only `SELECT 1` via hardcoded `db.text`.
- **`pay_periods.py:34-54`** -- validates through `PayPeriodGenerateSchema`, scopes all writes via `pay_period_service.generate_pay_periods(user_id=current_user.id, ...)`.
- **`dashboard.py:30-41`** -- delegates entirely to `dashboard_service.compute_dashboard_data(current_user.id)`; route-level user scoping present.
- **`companion.py:80-121, 124-161`** -- both handlers run `_companion_or_redirect()` and then delegate to `companion_service.get_visible_transactions(current_user.id, ...)` which per design filters by companion's `linked_owner_id`.
- **`categories.py:27-209`** -- every handler filters by `user_id=current_user.id` on both read and write; `edit/archive/unarchive/delete` all do `if category is None or category.user_id != current_user.id` before mutating.
- **`grid.py:163-364`** -- every Transaction query filters by `Transaction.pay_period_id.in_(period_ids)` where `period_ids` come from `pay_period_service.get_all_periods(user_id)` and by `Transaction.scenario_id == scenario.id` where scenario is user-scoped. `selectinload(Transaction.entries), selectinload(Transaction.template)` avoid N+1 on the high-traffic grid path.
- **`grid.create_baseline:367-397`** -- idempotent, writes only `user_id=current_user.id`.
- **`transactions._get_owned_transaction:72-86`** and **`_get_accessible_transaction_for_status:89-120`** -- both correctly check `txn.pay_period.user_id` against `current_user.id` (and `current_user.linked_owner_id` for companions).
- **`transactions.create_inline:585-640`** and **`create_transaction:643-683`** -- exemplary pattern: Marshmallow schema + explicit ownership checks on `account_id`, `pay_period_id`, `scenario_id`, `category_id` before write.
- **`transactions.get_quick_create/get_full_create/get_empty_cell:451-582`** -- all three correctly verify `category_id`, `period_id`, `account_id` ownership (fix H1 from an earlier audit cycle is present).
- **`transactions.carry_forward:715-749`** -- source period ownership verified, scenario scope verified, service called with `current_user.id`.
- **`entries.py:33-64`** -- `_get_accessible_transaction` is the canonical companion-aware helper and handles both the owner and companion paths correctly; the guard at `entries.py:172, :214, :246` additionally checks `entry.transaction_id == txn.id` to block parameter-confusion attacks.
- **`accounts.hard_delete_account:339-460`** -- non-trivial delete path correctly walks all dependent tables with explicit ownership + cascade handling, and uses `transfer_service.delete_transfer` to preserve transfer invariants.
- **`accounts.interest_detail:752-851`** and **`checking_detail:900-996`** -- Transaction queries filter `account_id`, `pay_period_id.in_(period_ids)`, `scenario_id`, `is_deleted.is_(False)`. Good.
- **`loan.py` helpers `_load_loan_account` and `_load_loan_context`** -- both enforce ownership via `account.user_id != current_user.id` and `has_amortization` type gate.
- **`loan.create_payment_transfer:1128-1256`** -- explicit source-account ownership validation, template name derivation, single-transaction commit.
- **`templates.preview_recurrence:518-584`** -- explicitly validates `start_period_id` ownership (noted as "audit finding H3" fix) before using it.
- **`templates.update_template:256-384`** -- validates both `account_id` and `category_id` ownership after schema load; bulk rename of linked transactions is scoped by `template.id`, which was verified owner-scoped.
- **`transfers.update_transfer_template:282-389`** -- validates both `from_account_id` and `to_account_id` ownership after schema load.
- **`settings.show`, `settings.update`, `settings._load_companion_or_404`** -- all ownership checks correct; `_load_companion_or_404` triple-guards (`id` lookup + `role_id == COMPANION` + `linked_owner_id == current_user.id`).
- **`auth._is_safe_redirect:29-70`** -- sound implementation: rejects schemes, netlocs, protocol-relative URLs, backslash prefixes, and whitespace/newlines. Validated at both storage and redirect time.
- **`auth.mfa_verify:250-344`** -- clears pending MFA state on all failure paths, including key-rotation errors, avoiding lockout loops.

---

## Open questions for the developer

1. **F-B1-02 / F-B1-08 / F-B1-07**: these are route-boundary findings that escalate or de-escalate based on how the paired service enforces ownership. Please confirm with Subagent B2's report whether `calendar_service.get_year_overview`, `budget_variance_service.compute_variance`, and `transfer_service.create_transfer` / `update_transfer` re-validate every FK against `user_id`. If they do, F-B1-02, F-B1-06, and F-B1-08 can be downgraded to Info (double-defence regressions) but F-B1-01 and F-B1-07 remain because they cannot be rescued by the service.
2. **F-B1-07**: Is the deployment model permanently single-owner, or is multi-owner on the roadmap? If multi-owner is planned, `ref.account_types` needs a `user_id` column (or an `is_builtin` flag + ownership column) before the routes can be trusted.
3. **F-B1-04**: Do you want a full `LoginSchema` + `RegisterSchema` split, or is a single `CredentialsSchema` with `email` + `password` fields + a separate `RegisterSchema(CredentialsSchema)` mixin acceptable? The schemas should be defined by whoever owns auth policy, not me.
4. **F-B1-01**: `TransactionUpdateSchema` currently exposes `pay_period_id` as a legitimate update target. Is the move-to-another-period operation a real UI feature, or is the field exposed purely because the PATCH handler was written generically? If the operation is not a real feature, removing the field from the schema is the cleanest fix. If it is, the validator must be added.
5. **F-B1-05**: The `mark_done` POST endpoint is intentionally usable by companions (`@login_required` without `@require_owner`, owner/companion path inside `_get_accessible_transaction_for_status`). Is the ability to set `actual_amount` from this endpoint also intended for companions, or should companion POSTs be rejected when `actual_amount` is present?
