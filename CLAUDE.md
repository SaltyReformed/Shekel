# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Shekel is a personal budget app that organizes finances around **pay periods** (biweekly paychecks) rather than calendar months. Every transaction maps to a specific paycheck with ~2-year forward projections.

**Stack:** Python 3.12+ · Flask 3.1 · SQLAlchemy 2.0 · PostgreSQL (multi-schema) · Jinja2 · HTMX · Bootstrap 5

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

**Services are isolated from Flask** -- they take plain data, return plain data, and never import Flask or touch `request`/`session`. This is intentional for testability.

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
- **Paycheck Calculator** (`app/services/paycheck_calculator.py`) -- Computes net pay: salary + raises − federal/state taxes − FICA − deductions. Supports multi-state tax brackets.
- **Transaction Status Workflow** -- `projected → done|credit|cancelled`, `done|received → settled`

### Frontend Pattern

HTMX with server-rendered partials -- no SPA. The budget grid uses HTMX for inline editing, creating/deleting transactions, and carry-forward without full page reloads. Templates are in `app/templates/` organized by domain.

### Application Factory

`create_app()` in `app/__init__.py` with configs in `app/config.py` (DevConfig, TestConfig, ProdConfig). Extensions initialized in `app/extensions.py`.

## Testing

- Tests use a real PostgreSQL database (configured via `TEST_DATABASE_URL` or defaults in TestConfig)
- `conftest.py` uses session-scoped app/db setup, truncates tables between tests
- Test categories: `test_routes/`, `test_services/`, `test_models/`, `test_integration/`, `test_adversarial/`, `test_scripts/`

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

## Environment

Copy `.env.example` to `.env` and configure. Key vars: `DATABASE_URL`, `SECRET_KEY`, `TOTP_ENCRYPTION_KEY`. Default login: `admin@shekel.local` / `ChangeMe!2026`. Alternatively, use `/register` to create an account.

## Style

- **No Unicode dashes.** Never use em dashes (U+2014) or en dashes (U+2013). Use `--` (double hyphen) for sentence breaks/separators and `-` (single hyphen) for ranges/short separators.

## Development Status

Multi-phase project. Phases 1-7 complete (core budgeting, salary, accounts, transfers, savings, charts, UI/UX). Phase 8 (hardening/ops) in progress. See `docs/` for detailed plans.
