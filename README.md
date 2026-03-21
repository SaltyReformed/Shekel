# Shekel -- Personal Budget App

A paycheck-centric budget application that replaces a biweekly-paycheck-based spreadsheet. Organizes finances around **pay periods** rather than calendar months, mapping every expense to a specific paycheck and projecting balances forward over a ~2-year horizon.

**Stack:** Flask · Jinja2 · HTMX · Bootstrap 5 · PostgreSQL

---

## Quick Start (Arch Linux)

### 1. Prerequisites

```bash
# PostgreSQL (should already be installed and running)
sudo systemctl status postgresql

# Python 3.12+ and pip
python --version
```

### 2. Clone & Set Up Python Environment

```bash
cd ~/projects  # or wherever you keep code
git init shekel && cd shekel  # or clone from your repo

# Create a virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies (dev file includes production deps + test/lint tools)
pip install -r requirements-dev.txt
```

### 3. Configure Environment

```bash
# Copy the example and fill in your values
cp .env.example .env

# Edit .env -- at minimum, update:
#   DATABASE_URL -- your local PostgreSQL connection string
#   SECRET_KEY   -- a random string (use: python -c "import secrets; print(secrets.token_hex(32))")
```

If you created the databases without a password (peer auth), your DATABASE_URL would be:
```
DATABASE_URL=postgresql:///shekel
TEST_DATABASE_URL=postgresql:///shekel_test
```

### 4. Initialize the Database

```bash
# Create the PostgreSQL schemas (ref, auth, budget, salary, system)
# and run the initial migration
flask db init        # Only needed the first time (creates migrations/)
flask db migrate -m "initial schema"
flask db upgrade

# Seed reference data and the initial user
python scripts/seed_ref_tables.py
python scripts/seed_user.py
```

### 5. Run the App

```bash
flask run
# or
python run.py
```

Open http://localhost:5000 and log in with the seed user credentials, or
register a new account at http://localhost:5000/register.
- **Default Email:** `admin@shekel.local`
- **Default Password:** `ChangeMe!2026`

### 6. First-Time Setup in the App

1. **Generate Pay Periods:** Navigate to Pay Periods and enter your next payday, then Generate.
2. **Set Anchor Balance:** On the grid, click the balance display and enter your current checking balance.
3. **Create Templates:** Go to Templates and create recurring income/expenses with their recurrence rules.
4. **Start Using:** The grid will populate with auto-generated transactions. Mark items done/received as they clear your bank.

---

## Development

### Running Tests

```bash
# Run all tests (use timeout -- full suite takes ~9 minutes)
timeout 660 pytest -v --tb=short

# Run with coverage
timeout 660 pytest --cov=app --cov-report=term-missing

# Run specific test files (fast feedback during development)
pytest tests/test_services/test_balance_calculator.py -v
pytest tests/test_routes/test_grid.py -v
```

### Database Migrations

```bash
# After changing models, generate a new migration
flask db migrate -m "describe what changed"

# Apply migrations
flask db upgrade

# Rollback one migration
flask db downgrade
```

### Linting

```bash
# Python (Pylint)
pylint app/
```

---

## Payday Workflow

The core interaction loop the app supports:

1. **Open the app** -- grid loads with current period as leftmost column.
2. **True-up balance** -- click the anchor balance, enter real checking balance.
3. **Mark paycheck received** -- click income row, mark as received, enter actual.
4. **Carry forward unpaid** -- click "Carry Fwd" on past periods with unpaid items.
5. **Mark cleared expenses** -- set done with actual amounts as they post.
6. **Mark credit card** -- expenses charged to CC become payback items next period.
7. **Check projections** -- scan future balances for any danger zones.

---

## Build Status

Last evaluated: 2026-03-21

| Phase | Name                           | Status      | Notes                                            |
| ----- | ------------------------------ | ----------- | ------------------------------------------------ |
| 1     | Replace the Spreadsheet        | Complete    | Grid, templates, recurrence, balance, status     |
| 2     | Paycheck Calculator            | Complete    | Salary, raises, deductions, federal/state/FICA   |
| 3     | HYSA & Accounts Reorganization | Complete    | HYSA interest, category grouping, account dashboard |
| 4     | Debt Accounts                  | Complete    | Mortgage (fixed+ARM), auto loan, escrow, payoff  |
| 5     | Investments & Retirement       | Complete    | 401(k), IRA, pension, growth engine, gap analysis |
| 6     | Visualization                  | Complete    | Charts dashboard, interactive sliders, theming   |
| 7     | Scenarios                      | Deferred    | Model stub exists; clone/compare not built       |
| 8A    | Security Hardening             | Complete    | MFA/TOTP, rate limiting, session mgmt, CSP       |
| 8B    | Audit & Structured Logging     | Complete    | PG triggers on 22 tables, JSON logs, Promtail    |
| 8C    | Backups & Disaster Recovery    | Complete    | pg_dump, retention, restore, integrity checks    |
| 8D    | Production Deployment          | Complete    | Docker, Nginx, Cloudflare Tunnel, CI, deploy.sh  |
| 8E    | Multi-User Groundwork          | Complete    | Registration, user_id audit, data isolation tests |
| UI/UX | Remediation                    | Complete    | Nav restructure, settings consolidation, polish  |

**Status key:** Complete | In Progress | Not Started | Deferred

**Test suite:** 1533 test functions across 61 test files (+ 3 performance benchmarks run separately)

See [docs/progress.md](docs/progress.md) for detailed feature-level tracking.

---

## Project Structure

```
shekel/
├── app/
│   ├── __init__.py              # Application factory (create_app)
│   ├── config.py                # Dev / Test / Prod configuration
│   ├── extensions.py            # SQLAlchemy, Migrate, LoginManager, Limiter
│   ├── exceptions.py            # Domain-specific exceptions
│   ├── models/                  # SQLAlchemy models (21 files, 5 PG schemas)
│   ├── routes/                  # Flask Blueprints (17 route modules)
│   ├── services/                # Business logic (21 service modules)
│   ├── schemas/                 # Marshmallow validation (consolidated schema)
│   ├── utils/                   # Logging config, structured log events, auth helpers
│   ├── templates/               # Jinja2 HTML templates (78 files, 17 directories)
│   └── static/                  # CSS, JS (16 chart/grid/form scripts), images
├── migrations/                  # Alembic database migrations (19 versions)
├── monitoring/                  # Promtail config and Grafana/Loki runbook
├── nginx/                       # Nginx reverse proxy configuration
├── cloudflared/                 # Cloudflare Tunnel configuration
├── .github/workflows/           # CI (lint + test) and Docker image publishing
├── scripts/                     # Seed, backup/restore, integrity check, ops scripts
├── tests/                       # pytest test suite (1533 test functions, 61 test files)
├── docs/                        # Plans, progress tracking, runbooks
├── docker-compose.yml           # Production Docker Compose (app + PG + Nginx)
├── docker-compose.dev.yml       # Development Docker Compose (with test DB)
├── Dockerfile                   # Multi-stage production container
├── gunicorn.conf.py             # Gunicorn WSGI server configuration
├── entrypoint.sh                # Container startup (DB init, migrate, seed)
├── requirements.txt             # Production Python dependencies
├── requirements-dev.txt         # Dev/test/lint dependencies (includes requirements.txt)
└── run.py                       # Entry point
```
