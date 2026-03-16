Analyze my entire project and write a detailed implementation plan for Phase 8E: Multi-User Groundwork.

## Context

This is a personal finance app called Shekel. The stack is Flask, Jinja2, HTMX, Bootstrap 5, and PostgreSQL. The project uses Alembic for migrations, pytest for testing, and follows a service-layer architecture (routes call services, services call models). The app runs in Docker containers on a Proxmox host behind Nginx and Cloudflare Tunnel.

All of Phase 8 is complete except 8E:

- **8A (complete):** MFA/TOTP, password management, rate limiting, session management, custom error pages.
- **8B (complete):** PostgreSQL audit triggers on financial tables, structured JSON logging with request_id and duration, log event categories.
- **8C (complete):** Automated pg_dump backups, tiered retention, restore scripts, backup verification, data integrity checks.
- **8D (complete):** Docker finalization, Nginx reverse proxy, Gunicorn, Cloudflare Tunnel/Access/WAF, CI pipeline, deploy script, .env.example.

8E is independent of 8C and 8D (it depends only on 8A). It was deferred to last because the user_id audit is tedious and benefits from a stable codebase.

Read these files first to understand the scope and standards:

1. `phase_8_hardening_ops_plan.md` -- the master plan. Phase 8E is defined in "Sub-Phase 8E: Multi-User Groundwork" (items 1-7 and the test gate).
2. `phase_8a_implementation_plan.md` -- the completed implementation plan for Phase 8A. **Your output must match this document's structure, depth, and level of detail exactly.** This is your template.
3. `project_requirements_v2.md` -- sections 4.17 (Authentication), 5 (Data Model), and the schema tables. Pay special attention to the `user_id` column design notes and the statement "The database schema includes user_id columns on all relevant tables for future multi-user support."
4. `project_requirements_v3_addendum.md` -- sections 5.3-5.6 for the additional tables added in later phases (mortgage_params, auto_loan_params, investment_params, pension_profiles, hysa_params, escrow_components, mortgage_rate_history). These tables are scoped through their FK to `budget.accounts` (which has `user_id`), except `pension_profiles` which has a direct `user_id` column.

## What 8E Covers (master plan items)

### Registration Flow (items 1-4)

1. **Registration page** (`app/templates/auth/register.html`): fields for email, display name, password, confirm password. Validation: email format, email uniqueness, password minimum length (12 chars, per 8A standard), confirmation match. On success: create user, create default `user_settings` row, redirect to login. On failure: flash specific errors.
2. **Registration route** (`app/routes/auth.py`): GET for the form, POST for submission. Service layer handles user creation, bcrypt hashing, default settings. Log the registration event via the structured logging from 8B.
3. **Open vs. invite-only registration decision.** The master plan recommends open registration since Cloudflare Access (completed in 8D) already restricts who can reach the app. Only people added to the Cloudflare Access policy can see the registration page.
4. **Seed script update.** Make `scripts/seed_user.py` idempotent (skip if user already exists).

### user_id Query Audit (items 5-6)

5. **Audit every database query.** Systematically review every model query, service method, and route handler to confirm it filters by `user_id` where applicable.
   - Deliverable: a checklist document organized by blueprint/service, listing every query and whether it filters by `user_id`.
   - Scope: all `budget.*` tables, all `salary.*` tables, `auth.user_settings`, `auth.mfa_configs`.
   - Excluded: `ref.*` tables (shared lookup data), `system.audit_log` (already scoped by user_id column).
6. **Fix any queries that do not filter by `user_id`.** Common patterns to check:
   - Direct queries: `Account.query.filter_by(id=account_id)` must become `Account.query.filter_by(id=account_id, user_id=current_user.id)`.
   - Relationship traversals: loading a child record by ID (transaction, template, transfer, etc.) and accessing its parent without a `user_id` check. If the child was loaded without scoping, the traversal is also unscoped.
   - Template context: Jinja2 templates iterating over accounts, transactions, etc. The data source in the route/service must be user-scoped.

### Data Isolation Tests (item 7)

7. **Integration tests for data isolation.** A test suite that:
   - Creates two users with separate data (accounts, transactions, templates, salary profiles, etc.).
   - Logs in as user A and verifies only user A's data is visible on every page and API endpoint.
   - Logs in as user B and verifies the same.
   - Attempts to access user A's resources by ID while logged in as user B (should return 403 or 404).
   - Covers: budget grid, accounts dashboard, transfers, salary, templates, mortgage, auto loan, investment, retirement, charts, settings.

## Critical: Comprehensive Codebase Audit

This phase requires the deepest codebase scan of any Phase 8 sub-phase. You must systematically read every file that touches the database.

### Step 1: Build the Complete Table Inventory

Read every model file in `app/models/` and build a table of ALL tables in the database with these columns:

| Schema | Table | Has user_id Column | Scoping Method | Notes |

Where "Scoping Method" is one of:

- **Direct:** Table has its own `user_id` column (e.g., `budget.accounts`, `salary.salary_profiles`, `salary.pension_profiles`).
- **Indirect via FK:** Table is scoped through a foreign key to a user-scoped parent (e.g., `budget.transactions` -> `budget.transaction_templates` -> `user_id`, or `budget.mortgage_params` -> `budget.accounts` -> `user_id`).
- **Shared:** Table is shared across all users (e.g., `ref.*` tables).
- **System:** Table is system-level (e.g., `system.audit_log`).

For indirectly scoped tables, document the full FK chain back to `user_id`. This determines whether a query needs a direct `user_id` filter or a join-based filter.

### Step 2: Build the Complete Query Inventory

Read every file in these directories and catalog every database query:

- `app/routes/` -- every route handler that queries the database.
- `app/services/` -- every service function that queries the database.
- `app/models/` -- any class methods or query helpers on models.

For each query, document:

- File path and line number.
- The table(s) being queried.
- Whether the query currently filters by `user_id` (directly or via a user-scoped join).
- Whether it SHOULD filter by `user_id` (based on the table inventory).
- If it is missing a filter: what the fix is.

Organize by blueprint/service module:

- `auth` (login, registration, settings, MFA)
- `grid` (budget grid, transactions)
- `accounts` (accounts dashboard, HYSA, mortgage, auto loan, investment)
- `templates` (transaction templates)
- `transfers` (transfer templates)
- `salary` (salary profiles, raises, deductions, tax config)
- `retirement` (pension profiles, retirement dashboard)
- `savings` (savings goals)
- `pay_periods` (pay period generation)
- `categories` (category management)
- `charts` (visualization endpoints)
- `settings` (user settings)
- `health` (excluded -- no user-scoped data)

### Step 3: Identify Route-Level Authorization Gaps

Beyond query filtering, check every route that takes an ID parameter (e.g., `/accounts/<id>`, `/templates/<id>/edit`, `/transfers/<id>`) for authorization:

- After loading the object by ID, does the route verify `object.user_id == current_user.id` (or equivalent)?
- If not, an attacker could guess IDs to access another user's data.
- For indirectly scoped objects (e.g., mortgage_params loaded by account_id), the authorization check must verify the parent object's ownership.

### Step 4: Audit Pre-Existing Infrastructure

Also check for:

- `scripts/seed_user.py`: read it fully. Is it already idempotent? What does it create (user, settings, sample data)?
- `app/services/auth_service.py`: what functions exist for user creation? Is there a `create_user()` function or does the seed script handle it directly?
- `app/routes/auth.py`: the existing login route structure. Understand where the registration routes will be added.
- `app/templates/auth/login.html`: the login page layout. The registration page should follow the same visual pattern and include a link to the login page (and vice versa).
- `tests/conftest.py`: the `seed_user` fixture. The isolation tests need a SECOND user with separate data. Understand how fixtures currently create test users and data so the new fixtures follow the same pattern.
- The `app/config.py` for any relevant settings (e.g., minimum password length if it is configurable).
- The 8A password change implementation for the 12-character minimum validation pattern -- registration must use the same validation.

Document ALL findings in a "## Pre-Existing Infrastructure" section.

## Required Output Structure (match the 8A plan exactly)

### 1. Overview

Brief summary, pre-existing infrastructure highlights, new dependencies (if any), key decisions.

### 2. Pre-Existing Infrastructure

Detailed audit results with file paths, line numbers, and impact on 8E implementation.

### 3. Table Inventory

The complete table inventory from Step 1 above. This is a KEY deliverable of the plan and must be 100% complete.

### 4. Query Audit Results

The complete query inventory from Step 2 above, organized by blueprint/service. For each query that is MISSING a `user_id` filter, include:

- The current code (exact lines).
- The required fix (new code).
- The risk level if unfixed (data leak severity).

This section will be long. That is expected and necessary. Do not abbreviate it.

### 5. Route Authorization Audit

The results from Step 3 above. List every route that takes an ID parameter and whether it verifies ownership.

### 6. Decision/Recommendation Sections

- **Open vs. invite-only registration:** The master plan recommends open registration because Cloudflare Access restricts who can reach the app. Confirm this is the correct approach given the completed 8D infrastructure. If open registration is recommended, document the security layers: Cloudflare Access (outer) -> app authentication (inner). If invite-only is recommended, document the invite token generation and validation mechanism.
- **403 vs. 404 for unauthorized access:** When user B tries to access user A's resource by ID, should the app return 403 Forbidden or 404 Not Found? 404 is more secure (does not confirm the resource exists), but 403 is more informative for debugging. Recommend one approach and apply it consistently across all routes.
- **Default data for new users:** When a new user registers, what default data should be created? At minimum: a `user_settings` row. Should the app also create default categories, a default checking account, or initial pay periods? Or should the new user see a completely empty state with onboarding prompts? Examine what the seed script currently creates to inform this decision.
- **Test data factory pattern:** The isolation tests need two users with full separate datasets (accounts, transactions, templates, categories, pay periods, salary profiles, etc.). This is a substantial test fixture. Recommend whether to: (a) create a factory function in `conftest.py` that builds a complete user dataset, (b) use a dedicated test data builder module, or (c) build minimal data per test. Consider the existing test patterns.

### 7. Work Units

Organize into sequential work units. I recommend this ordering:

- **WU-1: Registration service and route.** Add `create_user()` to the auth service (or verify it exists). Create the registration template, route (GET + POST), validation, default user_settings creation. Update seed script for idempotency. Add a link between login and registration pages.
- **WU-2: user_id query fixes (budget domain).** Apply all fixes identified in the query audit for the `budget.*` tables: accounts, transactions, transaction_templates, transfers, savings_goals, pay_periods, categories, recurrence_rules, and all account-type-specific parameter tables (hysa_params, mortgage_params, auto_loan_params, investment_params, escrow_components, mortgage_rate_history, account_anchor_history).
- **WU-3: user_id query fixes (salary domain).** Apply all fixes for `salary.*` tables: salary_profiles, salary_raises, paycheck_deductions, pension_profiles. Also `auth.user_settings` and `auth.mfa_configs`.
- **WU-4: Route authorization hardening.** Add ownership verification to every route that takes an ID parameter. Implement the 403-vs-404 decision consistently.
- **WU-5: Data isolation integration tests.** Create the test data factory (two users with full separate datasets). Write isolation tests covering every blueprint: budget grid, accounts, transfers, salary, templates, mortgage, auto loan, investment, retirement, charts, settings. Write direct-object-access-by-ID tests for every route that takes an ID.

Each work unit must include:

- **Goal** statement.
- **Depends on** list.
- **Files to Create** with complete content (templates, test files with class/method signatures).
- **Files to Modify** with exact line numbers, current code, new code, rationale. For WU-2 and WU-3, this will be extensive -- list EVERY file and EVERY query change. Do not summarize or abbreviate.
- **Test Gate** checklist.
- **New Tests** with file path, test class name, every test method signature, and a description of what each test verifies.
- **Impact on Existing Tests** analysis. This is critical for WU-2 and WU-3: adding `user_id` filters to queries may break existing tests if the test fixtures do not set up `current_user` correctly or if the test database has data without proper `user_id` values. Analyze every existing test file and document which tests will be affected and how.

### 8. Work Unit Dependency Graph

ASCII diagram.

### 9. Complete Test Plan Table

A table listing every new test organized by file, class, method, and work unit number. This will be a LARGE table -- 8E will likely have more new tests than any other Phase 8 sub-phase because the isolation tests must cover every endpoint.

### 10. Phase 8E Test Gate Checklist (Expanded)

Map each checkbox from the master plan's 8E test gate to the specific test(s) that verify it:

- [ ] `pytest` passes (all existing tests plus new isolation tests)
- [ ] Registration creates a new user with default settings
- [ ] New user can log in and sees an empty budget (no data from the seeded user)
- [ ] Data isolation tests pass: user A cannot see user B's data on any endpoint
- [ ] Direct object access by ID returns 403/404 for unauthorized users
- [ ] user_id audit checklist is complete with all queries reviewed

### 11. File Summary

New files and modified files tables.

## Code Standards

- All Python must conform to Pylint standards with docstrings and inline comments.
- Use snake_case for all naming.
- Registration validation must use the same 12-character minimum password length as the 8A password change flow.
- All new queries must use SQLAlchemy's `filter_by()` or `filter()` with `user_id=current_user.id`.
- Authorization checks should be extracted into a reusable helper (e.g., `get_or_404_for_user(Model, id, user_id)`) rather than copy-pasted in every route. If such a helper already exists, use it. If not, create it and apply it consistently.
- Tests should follow the existing patterns in `tests/conftest.py` for fixtures and test class organization.

## Important Constraints

- **Do not add a role system, permissions, or admin features.** 8E is strictly user_id isolation: every user sees only their own data. There is no admin user, no shared data (except `ref.*` tables), and no cross-user visibility.
- **Do not add kid account features.** The master plan explicitly defers this.
- **The query audit must be exhaustive.** Missing a single unscoped query is a data leak. The audit checklist document is a deliverable that must list every query in the application. Err on the side of over-documenting rather than under-documenting.
- **The existing seeded user must continue to work.** The seed script is used for development and testing. Registration is the production path for new users. Both must coexist.
- **The data isolation tests must cover EVERY blueprint.** The master plan lists: budget grid, accounts dashboard, transfers, salary, templates, mortgage, auto loan, investment, retirement, charts, settings. Each of these must have at least one test verifying user A cannot see user B's data, and at least one test verifying user B cannot access user A's resources by ID.
- **Account-type-specific parameter tables are scoped indirectly.** `budget.mortgage_params`, `budget.auto_loan_params`, `budget.investment_params`, `budget.hysa_params`, `budget.escrow_components`, and `budget.mortgage_rate_history` do not have their own `user_id` columns. They are scoped through their FK to `budget.accounts.id`, which has `user_id`. Every route that loads these records must either: (a) load via a join that includes the account's `user_id` filter, or (b) load the account first, verify ownership, then load the child record. Document which approach is used for each table.
- **salary.pension_profiles has a direct user_id column.** Unlike the other "parameter" tables, pension_profiles is directly user-scoped. The audit must confirm that all pension queries filter by `user_id`.
- **salary.tax_bracket_sets, salary.tax_brackets, salary.fica_configs, salary.state_tax_configs are reference data.** The master plan excludes these from the audit (they are shared, like `ref.*` tables). Confirm this is correct by checking whether they have `user_id` columns.

## What NOT to Include

- Do not add CSV export or mobile-responsive layout (deferred per the master plan).
- Do not build an admin panel or user management UI.
- Do not add role-based access control or permissions.
- Do not add email verification for registration (Cloudflare Access already controls who can reach the app).
- Do not add account deletion or user deactivation features.
