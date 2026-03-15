# Shekel Budget App -- Progress Report

**Evaluated on:** YYYY-MM-DD
**Evaluated by:** Claude Code (`/update-docs` skill)

---

## Summary

| Metric           | Count |
| ---------------- | ----- |
| Phases evaluated | 12    |
| Complete         | 0     |
| In progress      | 0     |
| Not started      | 0     |
| Deferred         | 0     |

---

## Recent Development Activity

<!-- Paste the output of `git log --oneline -30` or a summary of recent
     commits here. Highlight which phase the recent work relates to. -->

---

## Phase-by-Phase Assessment

### Phase 1 -- Replace the Spreadsheet

**Status:** (Complete | In Progress | Not Started)

**Expected deliverables:**

- Models: user, account, pay_period, transaction, category,
  recurrence_rule, transaction_template, ref tables
- Routes: auth, grid, transactions, templates, categories, pay_periods,
  accounts, settings
- Services: auth_service, balance_calculator, recurrence_engine,
  credit_workflow, carry_forward
- Templates: base layout, grid view, template management, category
  management, auth pages, settings, pay period generation
- Tests: balance calculator, recurrence engine, credit workflow,
  carry forward, auth routes, grid routes, transaction routes
- Scripts: seed_user, seed_ref_tables

**Found:**

<!-- List which files exist and which are missing. -->

**Notes:**

<!-- Any discrepancies, partial implementations, or deviations from the
     requirements. -->

---

### Phase 2 -- Paycheck Calculator

**Status:** (Complete | In Progress | Not Started)

**Expected deliverables:**

- Models: salary_profile, paycheck_deduction, tax_bracket, fica_config,
  state_tax_config, salary_raise
- Routes: salary
- Services: paycheck_calculator, tax_calculator
- Templates: salary breakdown, salary projection, salary form, salary list
- Tests: paycheck calculator, tax calculator
- Scripts: seed_tax_brackets

**Found:**

<!-- List which files exist and which are missing. -->

**Notes:**

---

### Savings & Accounts (v2 Phase 4, already built)

**Status:** (Complete | In Progress | Not Started)

**Expected deliverables:**

- Models: savings_goal, transfer (or transfer fields)
- Routes: accounts (with savings features), transfers
- Services: transfer_service, savings_goal_service (or similar)
- Templates: accounts dashboard (savings version)
- Tests: savings and transfer tests

**Found:**

**Notes:**

---

### Phase 3 -- HYSA & Accounts Reorganization

**Status:** (Complete | In Progress | Not Started)

**Expected deliverables:**

- Models: hysa_params
- Services: interest_projection
- Templates: unified accounts dashboard (reorganized by category)
- Tests: test_interest_projection
- Schema change: category column on ref.account_types

**Found:**

**Notes:**

---

### Phase 4 -- Debt Accounts

**Status:** (Complete | In Progress | Not Started)

**Expected deliverables:**

- Models: mortgage_params, auto_loan_params, rate_history, escrow
- Routes: mortgage, auto_loan
- Services: amortization_engine, escrow_calculator
- Schemas: mortgage, auto_loan (Marshmallow)
- Templates: mortgage dashboard (with payoff, escrow, rate history
  fragments), auto loan dashboard
- Tests: amortization engine, escrow calculator, mortgage routes,
  auto loan routes

**Found:**

**Notes:**

---

### Phase 5 -- Investments & Retirement

**Status:** (Complete | In Progress | Not Started)

**Expected deliverables:**

- Models: investment_params, pension_profile
- Routes: investment, retirement
- Services: growth_engine, pension_calculator, retirement_gap_calculator
- Schemas: investment, pension (Marshmallow)
- Templates: investment dashboard, retirement dashboard, pension form,
  gap analysis fragment
- Tests: growth engine, pension calculator, retirement gap calculator,
  investment routes, retirement routes
- Schema change: target_account_id on salary.paycheck_deductions

**Found:**

**Notes:**

---

### Phase 6 -- Visualization

**Status:** (Complete | In Progress | Not Started)

**Expected deliverables:**

- Chart.js integration
- Balance-over-time line chart
- Category spending breakdown (bar chart)
- Budget vs. actuals comparison chart
- Net pay trajectory chart
- Scenario comparison overlay chart (may depend on Phase 7)

**Found:**

**Notes:**

---

### Phase 7 -- Scenarios

**Status:** Deferred (per v3 addendum, moved from v2 Phase 3)

**Expected deliverables (when built):**

- Models: scenario (or scenario fields on existing models)
- Routes: scenarios
- Services: scenario_service (clone, diff)
- Templates: scenario comparison view

**Found:**

**Notes:**

---

### Phase 8A -- Security Hardening

**Status:** (Complete | In Progress | Not Started)

**Expected deliverables:**

- Password change: route + auth_service.change_password()
- CSRF audit: all POST forms have csrf_token, HTMX header injection
- Session management: session_invalidated_at column, load_user check
- Rate limiting: Flask-Limiter on auth routes
- MFA/TOTP: mfa_service.py, mfa routes, mfa templates, pyotp +
  qrcode + cryptography in requirements.txt
- Custom error pages: 404, 500, 429, 403
- Tests: test_mfa_service, test_errors, test_auth (password change,
  session management, MFA setup/login/disable)

**Found:**

**Notes:**

---

### Phase 8B -- Audit & Structured Logging

**Status:** (Complete | In Progress | Not Started)

**Depends on:** 8A

**Expected deliverables:**

- system.audit_log table (migration)
- Audit trigger function on budget.transactions (and other tables)
- scripts/audit_cleanup.py
- Structured JSON logging (production config)
- Request ID correlation in logs
- Auth event logging

**Found:**

**Notes:**

---

### Phase 8C -- Backups & Disaster Recovery

**Status:** (Complete | In Progress | Not Started)

**Depends on:** 8B

**Expected deliverables:**

- scripts/backup.sh
- scripts/backup_retention.sh (tiered retention)
- scripts/restore.sh
- scripts/verify_backup.sh
- Cron configuration documentation
- NAS mount documentation
- Restore procedure in runbook

**Found:**

**Notes:**

---

### Phase 8D -- Production Deployment

**Status:** (Complete | In Progress | Not Started)

**Depends on:** 8A, 8B, 8C

**Expected deliverables:**

- Dockerfile (finalized for production)
- docker-compose.yml (production config with Nginx)
- Nginx reverse proxy configuration
- Cloudflare Tunnel setup or documentation
- Cloudflare rate limiting configuration
- Health check endpoint (/health or similar)
- CI pipeline (.github/workflows/ or similar)
- scripts/deploy.sh
- Deployment runbook

**Found:**

**Notes:**

---

### Phase 8E -- Multi-User Groundwork

**Status:** (Complete | In Progress | Not Started)

**Depends on:** Can proceed in parallel with 8C

**Expected deliverables:**

- Registration route and template
- user_id filtering audit (all queries reviewed)
- Data isolation integration tests (two users, verify separation)
- Direct object access returns 403/404 for unauthorized users

**Found:**

**Notes:**

---

## Requirements Drift

<!-- List any areas where the implementation has diverged from the
     requirements documents. Examples:
     - Files that exist but are not described in requirements
     - Requirements that specify one approach but implementation uses another
     - Features partially built outside their designated phase -->

---

## Suggested Next Steps

<!-- Based on the dependency chain and current progress, recommend the
     logical next phase or sub-phase to work on. Reference the dependency
     graph from phase_8_hardening_ops_plan.md if the user is in Phase 8
     territory. -->

1. (First priority)
2. (Second priority)
3. (Third priority)
