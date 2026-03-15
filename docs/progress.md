# Shekel -- Progress Tracker

Last updated: 2026-03-15

## Phase 1: Core Budget Grid -- COMPLETE

- [x] Biweekly pay period grid (spreadsheet-style)
- [x] Transaction CRUD (inline via HTMX + full form)
- [x] Recurring transaction templates (8 recurrence patterns)
- [x] Balance calculation with anchor balance propagation
- [x] Status workflow (projected -> done/credit/cancelled -> settled)
- [x] Category management (group + item)
- [x] Pay period generation
- [x] Carry forward unpaid items

## Phase 2: Paycheck Calculator -- COMPLETE

- [x] Salary profiles with annual salary
- [x] Raises (flat + percentage, one-time + recurring)
- [x] Paycheck deductions (flat/%, target account linking)
- [x] Federal income tax (bracket-based, IRS tables)
- [x] State income tax (flat rate)
- [x] FICA (Social Security + Medicare)
- [x] W-4 fields integration
- [x] Net pay breakdown view
- [x] Tax bracket seed script

## Phase 3: Scenarios -- DEFERRED

- [ ] Clone budget for what-if analysis
- [ ] Compare scenarios side-by-side
- [ ] Scenario switching UI

The `budget.scenarios` model exists with `is_baseline` flag, but no
clone/compare logic is implemented. UI spec is in
`docs/scenario_ui_requirements.md`.

## Phase 4: Accounts & Transfers -- COMPLETE

- [x] Multi-account tracking (checking, savings, HYSA, debt, investment)
- [x] Account CRUD with anchor balance true-ups
- [x] Anchor balance history audit trail
- [x] Account-to-account transfers with recurrence
- [x] HYSA with compound interest projection
- [x] Mortgage (fixed + ARM, escrow components, payoff calculator)
- [x] Auto loan with amortization
- [x] Savings goals (target amount/date, progress tracking)
- [x] Emergency fund metrics (months of expenses)

## Phase 5: Investments & Retirement -- COMPLETE

- [x] Investment accounts (401k, Roth 401k, Traditional IRA, Roth IRA, brokerage)
- [x] Employer contributions (flat %, matching)
- [x] Compound growth projection with contributions
- [x] Annual contribution limits
- [x] Pension modeling (benefit multiplier, consecutive high years)
- [x] Retirement income gap analysis
- [x] Safe withdrawal rate (SWR) modeling
- [x] Retirement settings (target date, tax rate, SWR)

## Phase 6: Visualization & Interactive Upgrades -- COMPLETE

- [x] Centralized Charts dashboard page
- [x] Balance Over Time chart (multi-account, date range filtering)
- [x] Spending by Category chart (horizontal bar, multiple timeframes)
- [x] Budget vs. Actuals chart (grouped bar)
- [x] Amortization chart (debt payoff schedule)
- [x] Net Worth projection chart (assets vs. liabilities)
- [x] Net Pay projection chart
- [x] Growth projection chart (investment compound growth)
- [x] Mortgage payoff comparison chart
- [x] Retirement gap chart
- [x] Chart.js theming (chart_theme.js)
- [x] Interactive sliders for SWR/return rate (chart_slider.js)

## Phase 7: Smart Features -- NOT STARTED

- [ ] Rolling averages for expense tracking
- [ ] Inflation adjustment on long-term projections
- [ ] Scenario comparison overlay (depends on Phase 3)

Note: Inflation field exists on paycheck deductions but global
projection adjustment is not implemented.

## Phase 8: Hardening & Ops

### Phase 8A: Security Hardening -- COMPLETE

- [x] CSRF audit & protection (all 70 forms covered)
- [x] MFA/TOTP setup with QR codes
- [x] Backup codes (10 x 8-char, bcrypt hashed)
- [x] Two-step login (password -> TOTP verification)
- [x] MFA disable flow (requires password + TOTP)
- [x] MFA recovery script (reset_mfa.py)
- [x] Password change with session invalidation
- [x] Rate limiting on login/MFA (5 attempts per 15 min)
- [x] "Log out all sessions" feature (session_invalidated_at)
- [x] Custom error pages (404, 429, 500)
- [x] Security headers (CSP, X-Frame-Options, Referrer-Policy, etc.)
- [x] Encrypted MFA secrets (Fernet via TOTP_ENCRYPTION_KEY)

### Phase 8B: Audit & Structured Logging -- COMPLETE

- [x] system.audit_log table with row-level tracking (Alembic migration)
- [x] Generic PL/pgSQL audit trigger function
- [x] Triggers on 22 financial/auth tables (budget, salary, auth schemas)
- [x] Flask middleware: SET LOCAL app.current_user_id per request
- [x] Structured JSON logging with python-json-logger
- [x] Request ID generation (UUID4) and X-Request-Id response header
- [x] Request duration tracking with configurable slow threshold (WARNING >=500ms)
- [x] Log event standardization (log_event helper, AUTH/BUSINESS/ERROR/PERFORMANCE categories)
- [x] user_id and remote_addr as standard log fields on authenticated requests
- [x] Auth event refactoring (9 auth routes use structured log_event)
- [x] Business event logging (recurrence engine, carry forward service)
- [x] Audit retention cleanup script (scripts/audit_cleanup.py, --days, --dry-run)
- [x] Performance benchmarking: trigger overhead <5% on recurrence engine (under 20% threshold)
- [x] Promtail configuration for Grafana/Loki log shipping (monitoring/promtail-config.yml)
- [x] Monitoring stack runbook (monitoring/README.md)
- [x] Gunicorn access log disabled (Flask JSON logging handles request logging)
- [x] docker-compose.yml updated with LOG_LEVEL, SLOW_REQUEST_THRESHOLD_MS, AUDIT_RETENTION_DAYS
- [x] .env.example updated with logging and audit environment variables
- [x] 47 new tests (23 audit trigger, 9 log events, 9 logging config, 6 audit cleanup)
- [x] 3 performance benchmark tests (excluded from default pytest run)

889 tests passing after 8B completion.

### Phase 8C: Backups & Disaster Recovery -- NOT STARTED

- [ ] Automated pg_dump backup script (scripts/backup.sh)
- [ ] Tiered retention (7 days daily, 4 weeks weekly, 6 months monthly)
- [ ] Restore script with DB recreation and verification
- [ ] NAS backup copy
- [ ] Cron configuration (daily 2 AM)

### Phase 8D: Production Deployment -- PARTIAL

- [x] Dockerfile (multi-stage build, Python 3.14-slim, non-root user)
- [x] docker-compose.yml (app + PostgreSQL with health checks)
- [x] docker-compose.dev.yml (development with separate test DB)
- [x] Entrypoint with idempotent DB init (pg_isready -> schemas -> migrate -> seed)
- [x] Gunicorn configuration (JSON logging via Flask, error log to stdout)
- [x] .env.example with all production variables documented
- [x] GitHub Actions workflow for Docker image publishing (docker-publish.yml)
- [ ] Nginx reverse proxy config
- [ ] Cloudflare Tunnel setup
- [ ] Cloudflare Access (zero-trust authentication)
- [ ] CI/CD pipeline (GitHub Actions: test -> build -> deploy)
- [ ] Health endpoint (/health)
- [ ] Deployment script (scripts/deploy.sh)

### Phase 8E: Multi-User Groundwork -- NOT STARTED

- [ ] Registration page (open vs. invite-only)
- [ ] user_id query audit across all routes
- [ ] Data isolation verification tests
- [ ] Idempotent seed script updates

## UI/UX Remediation -- COMPLETE

- [x] Phase 1: Visual consistency (icons, active nav state, breadcrumbs, headings)
- [x] Phase 2: Nomenclature ("Templates" -> "Recurring Transactions", account type formatting)
- [x] Phase 3: Settings consolidation (Categories, Pay Periods, Tax Config into Settings)
- [x] Phase 4: Navigation restructure (dropdown groups, merged "Accounts & Savings")
- [x] Phase 5: Future-proofing (scenario selector slots for Phase 7)
