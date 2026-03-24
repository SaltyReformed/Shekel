# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Shekel is a personal budget app that organizes finances around **pay periods** (biweekly paychecks) rather than calendar months. Every transaction maps to a specific paycheck with ~2-year forward projections.

**Stack:** Python 3.12+ · Flask 3.1 · SQLAlchemy 2.0 · PostgreSQL (multi-schema) · Jinja2 · HTMX · Bootstrap 5

**Critical context:** This project is built and maintained by a solo developer with no QA team and no safety net. There is no one to catch mistakes after the fact. You (Claude Code) are the only safeguard between this code and production. A miscalculation, a missed edge case, or a sloppy shortcut in a budgeting application means real money is mismanaged -- bills get missed, projections are wrong, and financial decisions are made on bad data. If this project becomes public, those consequences extend to every user who trusts it. Treat every line of code as if someone's rent payment depends on it being correct, because it might.

## Non-Negotiable Rules

These rules apply to every task, every file, every commit. No exceptions. They are not guidelines or suggestions. They are requirements. Violating them is never acceptable, even if following them is slower, harder, or more verbose.

1. **Do it right, not fast.** Never take shortcuts, use workarounds, or choose an easier path over the correct one. If the right approach takes longer or requires more effort, that is the approach to use. Do not stub out implementations with `pass` or `TODO` and call it done. Do not hardcode values to avoid writing proper logic. Do not skip validation because "it probably won't happen." Do not use broad `except Exception` blocks to sweep problems under the rug. Do not write a "quick fix" that avoids the root cause. Do not simplify a solution in ways that sacrifice correctness, security, or maintainability. If best practice exists for a problem, follow it -- even if a shortcut would "work for now." The correct solution is the only acceptable solution.

2. **Read before you write.** Before changing ANY file, read the ENTIRE file first. Do not rely on memory, assumptions, or line number references from planning documents. Line numbers shift between commits. Find the actual code by reading the file.

3. **Verify before you fix.** Before implementing any fix, confirm the problem still exists in the current code. If a fix has already been applied, skip it and document that you skipped it and why.

4. **No guessing, no assuming.** If you are uncertain about how something works, read the code. If you are uncertain about what the user wants, ask. Do not fill gaps with assumptions and do not fabricate information. Do not assume what a function returns, what columns a table has, or what state an object is in -- open the file and verify. Wrong assumptions that slip through become bugs that are harder to find than if the code had never been written. When uncertain about financial logic (rounding, truncation, tax rules, etc.), research the standard best practice first. If there is a definitive industry-standard answer, follow it. If the situation is ambiguous, ask for the developer's preference before proceeding.

5. **Match existing patterns.** Study the codebase before writing new code. The project has established patterns for ownership checks, test fixtures, service isolation, error handling, and configuration. Use them. Do not invent new patterns unless there is a documented reason to do so.

6. **One concern per commit.** Each commit should be atomic and individually revertable. Do not batch unrelated changes. Each fix, feature, or refactor gets its own commit.

7. **Test everything.** Every code change must have corresponding tests. Run the relevant tests before committing. Run the full suite before reporting done. Tests are not optional, not "nice to have," and not something to circle back to later.

8. **No silent failures.** Do not suppress errors, swallow exceptions, or ignore edge cases to make something "work." Handle errors explicitly and visibly.

9. **Think about edge cases.** Before writing any logic, consider: what if the input is zero? Negative? None? An empty list? A boundary date? A user with no data yet? This is a financial application -- edge cases are where money gets lost. Write defensive code and test the boundaries, not just the happy path.

10. **Finish what you start.** Do not leave partial implementations, placeholder logic, or "temporary" solutions that become permanent. Every piece of code you write should be production-ready when you commit it. If a task is too large to complete properly in one pass, break it into smaller tasks that are each individually complete and correct.

## Common Commands

```bash
# Run dev server
flask run                    # or: python run.py (http://localhost:5000)

# Tests -- IMPORTANT: full suite takes ~9 minutes (526s+)
# Always use an explicit timeout so long runs are not mistakenly killed.

# Full suite (use 660s timeout -- 11 min with buffer)
timeout 660 pytest -v --tb=short

# Targeted runs for development iteration (fast feedback):
pytest tests/test_routes/test_grid.py -v           # ~20s
pytest tests/test_services/ -v                     # ~120s
pytest tests/path/test_file.py -v                  # single file
pytest tests/path/test_file.py::TestClass -v       # single class
pytest tests/path/test_file.py::test_name -v       # single test

# With coverage (also needs the long timeout)
timeout 660 pytest --cov=app --cov-report=term-missing

# Lint
pylint app/

# Database migrations
flask db migrate -m "description"
flask db upgrade

# Seed (first-time setup, in order)
python scripts/seed_ref_tables.py
python scripts/seed_user.py
python scripts/seed_tax_brackets.py
```

## Architecture

### Layered Design

```
Routes (Blueprints)  →  Services (pure logic, no Flask imports)  →  Models (SQLAlchemy ORM)
                                                                  →  Schemas (Marshmallow validation)
```

**Services are isolated from Flask** -- they take plain data, return plain data, and never import Flask or touch `request`/`session`. This is intentional for testability. Do not violate this boundary.

### PostgreSQL Schemas

Five database schemas separate concerns:

- **ref** -- Lookup/reference tables (AccountType, Status, TransactionType, FilingStatus, etc.)
- **auth** -- Users, sessions, MFA, user settings
- **budget** -- Pay periods, transactions, categories, scenarios, accounts, templates
- **salary** -- Salary profiles, deductions, tax configs, raises, pensions
- **system** -- Reserved for audit/system metadata

### Core Domain Concepts

- **Anchor Balance** -- A real checking account balance at a reference pay period. All projections flow forward from this point.
- **Balance Calculator** (`app/services/balance_calculator.py`) -- Computes end-of-period balances by anchoring to a real balance, then adding income and subtracting expenses period-by-period. Excludes "done/received" (already settled) and "credit" (on credit card) transactions.
- **Recurrence Engine** (`app/services/recurrence_engine.py`) -- Generates transactions from templates using 8 patterns: `every_period`, `every_n_periods`, `monthly`, `monthly_first`, `quarterly`, `semi_annual`, `annual`, `once`. Handles overrides and deletions.
- **Paycheck Calculator** (`app/services/paycheck_calculator.py`) -- Computes net pay: salary + raises - federal/state taxes - FICA - deductions. Supports multi-state tax brackets.
- **Transaction Status Workflow** -- `projected -> done|credit|cancelled`, `done|received -> settled`

### Frontend Pattern

HTMX with server-rendered partials -- no SPA. The budget grid uses HTMX for inline editing, creating/deleting transactions, and carry-forward without full page reloads. Templates are in `app/templates/` organized by domain.

### Application Factory

`create_app()` in `app/__init__.py` with configs in `app/config.py` (DevConfig, TestConfig, ProdConfig). Extensions initialized in `app/extensions.py`.

### Established Patterns (Use These -- Do Not Reinvent)

**Ownership verification:** Use the helpers in `app/utils/auth_helpers.py`. `get_or_404(model, pk)` for models with a direct `user_id` column. `get_owned_via_parent(model, pk, parent_attr)` for models scoped through a FK parent (e.g., Transaction via PayPeriod). Never write ad-hoc ownership checks inline when these helpers exist.

**Security response rule:** When a resource is not found OR belongs to another user, the response MUST be identical -- return 404 with the same body in both cases. Never return different status codes or messages that would let an attacker distinguish "does not exist" from "exists but is not yours."

**Structured logging:** Use `log_event()` from `app/utils/logging_helpers.py` with the established event categories (`AUTH`, `BUSINESS`, `ERROR`, `PERFORMANCE`). Do not use bare `logger.info()` calls with ad-hoc message formats. Do not invent new event categories without documenting them.

**Dependencies:** Only use packages already in `requirements.txt`. All packages are pinned to exact versions. Do not add new dependencies without explicit approval. Do not suggest switching to alternative libraries for functionality that already works.

## Code Quality Standards

### Python

- **Pylint compliance is mandatory.** Run `pylint app/ --fail-on=E,F` after every change. Do not decrease the current score.
- **Docstrings on every module, class, and function.** Explain what it does, not how the syntax works.
- **Inline comments on non-obvious logic.** If a line would make a future reader pause, comment it.
- **Use `Decimal`, never `float`**, for all monetary amounts. Financial precision is non-negotiable.
- **snake_case** for all variables, functions, modules, and database columns.
- **No unused imports.** Pylint catches these. Fix them immediately.

### SQL / Database

- **All queries must be user-scoped.** Every query touching user data must filter by `user_id`. No exceptions. Missing ownership checks are IDOR vulnerabilities.
- **Use SQLAlchemy ORM** for all database access. No raw SQL strings in application code.
- **Always use Alembic migrations** for schema changes. Never modify the database schema by hand and never use `db.create_all()` outside of the test suite. Migrations provide traceability -- they have been critical for tracking down the source of bugs in this project. Every column, constraint, and index change must have a corresponding migration file with a descriptive message.
- **Migration messages must be descriptive.** Use `flask db migrate -m "add pension_type column to salary_profiles"`, not `flask db migrate -m "update"`.

### Commit Messages

Format: `<type>(<scope>): <what changed>`

Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`

Examples:

- `fix(transactions): add ownership check to quick-create GET endpoint`
- `feat(salary): add pension deduction support`
- `refactor(balance): extract anchor lookup into helper function`
- `test(recurrence): add coverage for quarterly pattern edge cases`

### Branch Workflow

All development work happens on the `dev` branch. Confirm you are on `dev` and the working tree is clean before starting any task.

## Testing

- Tests use a real PostgreSQL database (configured via `TEST_DATABASE_URL` or defaults in TestConfig)
- `conftest.py` uses session-scoped app/db setup, truncates tables between tests
- Test categories: `test_routes/`, `test_services/`, `test_models/`, `test_integration/`, `test_adversarial/`, `test_scripts/`
- **All tests need docstrings** explaining what is verified and why
- **Use existing fixtures** from `conftest.py` (`seed_user`, `seed_second_user`, `auth_client`, `second_auth_client`, etc.). Do not create ad-hoc user setup in test methods.
- **Before writing a new test**, check if equivalent coverage already exists. Duplicate tests waste CI time and create maintenance burden.

## Test Run Guidelines

- **Full suite runtime:** ~9 minutes (1258 tests). Always use
  `timeout 660` when invoking `pytest` without a file target.
- **During development:** Run only the test file(s) relevant to the code
  being changed. These typically complete in under 30 seconds.
- **Before reporting done:** Run the full suite once with
  `timeout 660 pytest -v --tb=short` as a final gate.
- **If a test appears stuck:** It is almost certainly still running (the
  slowest individual test is ~3 seconds). Wait for the full timeout before
  concluding there is a problem.
- **MFA/auth tests are slow** (~1-3s each) due to bcrypt hashing. This is
  expected.

## Deployment Context

**Target environment:** Bare-metal Arch Linux desktop running Docker. Shekel runs as a Docker container with Gunicorn as the WSGI server behind Nginx. Nginx and Cloudflare Tunnel also run as Docker containers on the same host. Multiple services share the same Nginx instance, each on its own subdomain (e.g., `shekel.mydomain.com`). External access is through Cloudflare Tunnel only -- no ports are exposed directly.

**What this means for code decisions:** Do not suggest Ubuntu-specific packages, systemd service files, or Proxmox-specific configurations. Do not suggest exposing ports directly to the internet. Docker, Gunicorn, Nginx, and Cloudflare Tunnel are the deployment stack -- work within it.

## Environment

Copy `.env.example` to `.env` and configure. Key vars: `DATABASE_URL`, `SECRET_KEY`, `TOTP_ENCRYPTION_KEY`. Default login: `admin@shekel.local` / `ChangeMe!2026`. Alternatively, use `/register` to create an account.

## Style

- **No Unicode dashes.** Never use em dashes (U+2014) or en dashes (U+2013). Use `--` (double hyphen) for sentence breaks/separators and `-` (single hyphen) for ranges/short separators.

## Development Status

Multi-phase project. Phases 1-8 complete (core budgeting, salary, accounts, transfers, savings, charts, UI/UX, hardening/ops). Currently preparing for production deployment. Remaining phases will be implemented as feature updates after production launch. See `docs/` for detailed plans.

Do not re-implement or modify completed phase work unless explicitly asked. Do not jump ahead to future phase work without being directed to do so.
