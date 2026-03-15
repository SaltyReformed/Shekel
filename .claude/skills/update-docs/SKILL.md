---
name: update-docs
description: >
  Evaluate the Shekel budget app project against its requirements documents,
  assess completion status of every phase and sub-phase, then update README.md
  and docs/progress.md to reflect the current state. Use when the user asks to
  update documentation, check project progress, evaluate the codebase against
  requirements, or refresh the README.
---

# Update Project Documentation Skill

This skill evaluates the Shekel budget app codebase against the project
requirements, builds a progress assessment, and updates the project
documentation to reflect the current state of development.

## Conventions

- Use snake_case for all identifiers and file names.
- Do not use em dashes or en dashes anywhere in the output.
- Write docstrings and inline comments in any Python scripts you create.
- Format dates as YYYY-MM-DD.
- Wrap lines in Markdown files at a reasonable length for readability.

---

## Step 1: Load the Requirements Documents

Read the following files in order. These define the full project scope:

1. `project_requirements_v2.md` -- the original requirements (v2).
2. `project_requirements_v3_addendum.md` -- the v3 addendum that restructured
   the phase roadmap and added new account types.
3. `phase_8_hardening_ops_plan.md` -- the Phase 8 sub-phase plan (8A through 8E).
4. `phase_8a_implementation_plan.md` -- the detailed implementation plan for
   Phase 8A (security hardening).

Pay close attention to the **build status table** in the v3 addendum:

| Phase                              | Status   |
| ---------------------------------- | -------- |
| Phase 1 -- Replace the Spreadsheet | Built    |
| Phase 2 -- Paycheck Calculator     | Built    |
| Phase 4 -- Savings & Accounts (v2) | Built    |
| Phase 3 -- Scenarios (v2)          | Deferred |

The v3 addendum renumbered phases as follows:

- v3 Phase 3 = HYSA & Accounts Reorganization (extends built v2 Phase 4)
- v3 Phase 4 = Debt Accounts (mortgage, auto loan)
- v3 Phase 5 = Investments & Retirement
- v3 Phase 6 = Visualization
- v3 Phase 7 = Scenarios (deferred from v2 Phase 3)
- v3 Phase 8 = Hardening & Ops (sub-phases 8A through 8E)

---

## Step 2: Scan the Codebase

Use the following strategy to determine what has been built. Do not guess.
Verify each item by checking for the existence and content of actual files.

### 2a. Directory inventory

List all directories and files under each of these paths:

- `app/models/`
- `app/routes/`
- `app/services/`
- `app/schemas/`
- `app/templates/`
- `app/static/`
- `tests/`
- `scripts/`
- `migrations/versions/`

### 2b. Phase-specific file checks

For each phase, look for the specific files and artifacts listed below. A
phase is **complete** only if all of its expected deliverables exist and appear
functional. A phase is **in progress** if some deliverables exist. A phase is
**not started** if no evidence of implementation exists.

**Phase 1 (Replace the Spreadsheet):**

- `app/models/`: user.py, account.py, pay_period.py, transaction.py,
  category.py, recurrence_rule.py, transaction_template.py, ref.py
- `app/routes/`: auth.py, grid.py, transactions.py, templates.py,
  categories.py, pay_periods.py, accounts.py, settings.py
- `app/services/`: auth_service.py, balance_calculator.py,
  recurrence_engine.py, credit_workflow.py, carry_forward.py
- `app/templates/`: base.html, grid/, templates/, categories/, auth/,
  settings/, pay_periods/, accounts/
- `tests/test_services/`: test_balance_calculator.py,
  test_recurrence_engine.py, test_credit_workflow.py,
  test_carry_forward.py
- `tests/test_routes/`: test_auth.py, test_grid.py, test_transactions.py
- `scripts/`: seed_user.py, seed_ref_tables.py

**Phase 2 (Paycheck Calculator):**

- `app/models/`: salary_profile.py (or salary.py), paycheck_deduction.py,
  tax_bracket.py (or similar)
- `app/routes/`: salary.py
- `app/services/`: paycheck_calculator.py, tax_calculator.py
- `app/templates/salary/`: breakdown.html, projection.html, form.html,
  list.html
- `tests/test_services/`: test_paycheck_calculator.py (or
  test_tax_calculator.py)
- `scripts/`: seed_tax_brackets.py

**v2 Phase 4 / Savings & Accounts (already built per v3 addendum):**

- `app/models/`: savings_goal.py (or within account.py), transfer.py
- `app/routes/`: accounts.py (with savings features), transfers.py
- `app/services/`: transfer_service.py, savings_goal_service.py (or similar)
- `app/templates/accounts/`: dashboard.html (savings dashboard)
- `tests/`: related savings/transfer tests

**v3 Phase 3 (HYSA & Accounts Reorganization):**

- `app/models/`: hysa_params.py
- `app/services/`: interest_projection.py
- `app/templates/accounts/`: dashboard.html (reorganized by category)
- `tests/test_services/`: test_interest_projection.py
- `ref.account_types` should have a `category` column

**v3 Phase 4 (Debt Accounts):**

- `app/models/`: mortgage_params.py, auto_loan_params.py
- `app/routes/`: mortgage.py, auto_loan.py
- `app/services/`: amortization_engine.py, escrow_calculator.py
- `app/schemas/`: mortgage.py, auto_loan.py
- `app/templates/mortgage/`: dashboard.html, \_payoff_results.html,
  \_escrow_list.html, \_rate_history.html
- `app/templates/auto_loan/`: dashboard.html
- `tests/test_services/`: test_amortization_engine.py,
  test_escrow_calculator.py
- `tests/test_routes/`: test_mortgage.py, test_auto_loan.py

**v3 Phase 5 (Investments & Retirement):**

- `app/models/`: investment_params.py, pension_profile.py
- `app/routes/`: investment.py, retirement.py
- `app/services/`: growth_engine.py, pension_calculator.py,
  retirement_gap_calculator.py
- `app/schemas/`: investment.py, pension.py
- `app/templates/investment/`: dashboard.html
- `app/templates/retirement/`: dashboard.html, pension_form.html,
  \_gap_analysis.html
- `tests/test_services/`: test_growth_engine.py,
  test_pension_calculator.py, test_retirement_gap_calculator.py
- `tests/test_routes/`: test_investment.py, test_retirement.py
- `salary.paycheck_deductions` should have `target_account_id` column

**v3 Phase 6 (Visualization):**

- Chart.js integration in templates
- Balance-over-time chart, category breakdown chart, budget vs. actuals
  chart, net pay trajectory chart
- Look for chart-related templates or partials under `app/templates/`

**v3 Phase 7 (Scenarios):**

- `app/models/`: scenario.py (or scenario fields on existing models)
- `app/routes/`: scenarios.py
- `app/services/`: scenario_service.py
- `app/templates/scenarios/`: compare.html
- `tests/`: scenario-related tests

**Phase 8A (Security Hardening):**

- Password change route and service method in auth_service.py
- CSRF audit completed (check for csrf_token in all POST forms)
- Session invalidation (session_invalidated_at column on auth.users)
- Rate limiting (Flask-Limiter configuration)
- MFA/TOTP: mfa_service.py, mfa routes, mfa templates, pyotp and
  qrcode in requirements.txt
- Custom error pages (404, 500, 429, 403)
- tests/test_services/test_mfa_service.py
- tests/test_routes/test_errors.py

**Phase 8B (Audit & Structured Logging):**

- Audit trigger function in a migration
- system.audit_log table
- Structured JSON logging configuration
- scripts/audit_cleanup.py

**Phase 8C (Backups & DR):**

- scripts/backup.sh
- scripts/backup_retention.sh
- scripts/restore.sh
- scripts/verify_backup.sh

**Phase 8D (Production Deployment):**

- Dockerfile (finalized)
- docker-compose.yml (production configuration)
- Nginx configuration
- Cloudflare Tunnel configuration or documentation
- Health check endpoint
- CI pipeline configuration (.github/workflows/ or similar)
- scripts/deploy.sh

**Phase 8E (Multi-User Groundwork):**

- Registration route and template
- user_id filtering audit across all queries
- Data isolation integration tests

### 2c. Recent activity

Run `git log --oneline -30` to see the most recent commits and understand
what area of the codebase has been actively developed.

### 2d. Test health

If possible, note whether `pytest` is configured and how many test files
exist. Do not run the full test suite unless the user explicitly asks --
just report on test file presence and coverage areas.

---

## Step 3: Build the Progress Assessment

Create a structured assessment with the following information for each phase:

- **Phase name and number** (use the v3 numbering)
- **Status**: Complete, In Progress, Not Started, or Deferred
- **Evidence**: Which key files exist or are missing
- **Notes**: Any discrepancies, partial implementations, or drift from
  the requirements
- **Blockers or dependencies**: Note if a phase depends on another
  incomplete phase

Refer to `templates/progress_report_template.md` in this skill directory
for the expected output format.

---

## Step 4: Update README.md

Open the existing `README.md` and update it as follows:

1. **Preserve** any existing content that is still accurate (project
   description, setup instructions, tech stack, etc.).

2. **Add or update** a "Build Status" section with a Markdown table
   showing each phase and its completion status. Use the template in
   `templates/readme_progress_table.md` as a guide.

3. **Add or update** a "Last Evaluated" line showing today's date.

4. **Add or update** a "Project Structure" section if the existing one
   is out of date, based on the actual directory listing from Step 2.

5. **Do not remove** setup instructions, contribution guidelines, or
   other operational content.

6. If the README.md does not exist, create it from scratch with:
   - Project name and one-line description
   - Tech stack summary
   - Setup instructions (reference existing docker-compose.yml, .env, etc.)
   - Build status table
   - Project structure
   - Last evaluated date

---

## Step 5: Update or Create docs/progress.md

Write a detailed progress report to `docs/progress.md`. This file is the
full assessment with evidence, not just a summary table.

Use the template in `templates/progress_report_template.md` for structure.

If the `docs/` directory does not exist, create it.

---

## Step 6: Summarize to the User

After updating the files, print a concise summary to the terminal:

- Total phases evaluated
- Count by status (complete, in progress, not started, deferred)
- The most recently active area of development (from git log)
- Suggested next steps based on the dependency chain in the Phase 8 plan
  and the v3 roadmap
- List of files that were created or modified
