# Shekel — Personal Budget App

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

# Install dependencies
pip install -r requirements.txt
```

### 3. Configure Environment

```bash
# Copy the example and fill in your values
cp .env.example .env

# Edit .env — at minimum, update:
#   DATABASE_URL — your local PostgreSQL connection string
#   SECRET_KEY   — a random string (use: python -c "import secrets; print(secrets.token_hex(32))")
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

Open http://localhost:5000 and log in with:
- **Email:** `admin@shekel.local`
- **Password:** `changeme`

### 6. First-Time Setup in the App

1. **Generate Pay Periods:** Navigate to Pay Periods → enter your next payday → Generate.
2. **Set Anchor Balance:** On the grid, click the balance display and enter your current checking balance.
3. **Create Templates:** Go to Templates → create recurring income/expenses with their recurrence rules.
4. **Start Using:** The grid will populate with auto-generated transactions. Mark items done/received as they clear your bank.

---

## Development

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=app --cov-report=term-missing

# Run specific test files
pytest tests/test_services/test_balance_calculator.py -v
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

# SQL (SQLFluff) — for raw SQL files
sqlfluff lint scripts/
```

---

## Project Structure

```
shekel/
├── app/
│   ├── __init__.py              # Application factory (create_app)
│   ├── config.py                # Dev / Test / Prod configuration
│   ├── extensions.py            # SQLAlchemy, Migrate, LoginManager
│   ├── exceptions.py            # Domain-specific exceptions
│   ├── models/                  # SQLAlchemy models (mirror DB schemas)
│   ├── routes/                  # Flask Blueprints (HTTP layer)
│   ├── services/                # Business logic (no Flask imports)
│   ├── schemas/                 # Marshmallow validation
│   ├── templates/               # Jinja2 HTML templates
│   └── static/                  # CSS and JS
├── migrations/                  # Alembic database migrations
├── scripts/                     # Seed scripts and utilities
├── tests/                       # pytest test suite
├── docker-compose.yml           # PostgreSQL + app services
├── Dockerfile                   # Production container
├── requirements.txt             # Python dependencies
└── run.py                       # Entry point
```

---

## Payday Workflow

The core interaction loop the app supports:

1. **Open the app** — grid loads with current period as leftmost column.
2. **True-up balance** — click the anchor balance, enter real checking balance.
3. **Mark paycheck received** — click income row, mark as received, enter actual.
4. **Carry forward unpaid** — click "Carry Fwd" on past periods with unpaid items.
5. **Mark cleared expenses** — set done with actual amounts as they post.
6. **Mark credit card** — expenses charged to CC become payback items next period.
7. **Check projections** — scan future balances for any danger zones.

---

## Phase Roadmap

| Phase | Status | Description |
|-------|--------|-------------|
| 1 | ✅ Complete | Core budget grid — templates, recurrence, balance calc, status workflow |
| 2 | ✅ Complete | Paycheck calculator — salary, raises, deductions, federal/state/FICA taxes |
| 3 | Deferred | Scenarios — clone, compare, what-if analysis |
| 4 | ✅ Complete | Accounts & transfers — HYSA, mortgage, auto loan, savings goals, transfers |
| 5 | ✅ Complete | Investments & retirement — 401(k), IRA, pensions, gap analysis |
| 6 | ✅ Complete | Visualization — charts dashboard, interactive sliders, Chart.js theming |
| 7 | Not Started | Smart features — rolling averages, inflation adjustment, scenario overlays |
| 8A | ✅ Complete | Security hardening — MFA/TOTP, rate limiting, session mgmt, error pages |
| 8B | Not Started | Audit & structured logging |
| 8C | Not Started | Backups & disaster recovery |
| 8D | Partial | Production deployment — Docker done, Nginx/Cloudflare/CI remaining |
| 8E | Not Started | Multi-user groundwork |
| UI/UX | ✅ Complete | Remediation — nav restructure, settings consolidation, visual polish |

See [docs/progress.md](docs/progress.md) for detailed feature-level tracking.
