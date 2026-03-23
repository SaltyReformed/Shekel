# Shekel -- Personal Budget App

A paycheck-centric budget application that replaces a biweekly-paycheck-based spreadsheet. Organizes finances around **pay periods** rather than calendar months, mapping every expense to a specific paycheck and projecting balances forward over a ~2-year horizon.

**Stack:** Flask · Jinja2 · HTMX · Bootstrap 5 · PostgreSQL

**Two ways to run Shekel:**
- **Docker (recommended):** Download two files, run `docker compose up`. No Python or PostgreSQL install needed. See [Quick Start (Docker)](#quick-start-docker).
- **From source:** Clone the repo, set up Python and PostgreSQL locally. See [Developer Setup (from source)](#developer-setup-from-source).

---

## Quick Start (Docker)

### 1. Prerequisites

Install [Docker Engine](https://docs.docker.com/engine/install/) (Linux) or Docker Desktop (macOS/Windows). Verify the installation:

```bash
docker compose version
```

### 2. Download Configuration Files

```bash
mkdir shekel && cd shekel
curl -O https://raw.githubusercontent.com/SaltyReformed/Shekel/main/docker-compose.yml
curl -O https://raw.githubusercontent.com/SaltyReformed/Shekel/main/.env.example
cp .env.example .env
```

### 3. Configure Environment

Edit `.env` and set these values:

| Variable | Required | Description |
|---|---|---|
| `POSTGRES_PASSWORD` | Yes | Choose a strong database password. |
| `SECRET_KEY` | Yes | Run `openssl rand -hex 32` and paste the output. |
| `TOTP_ENCRYPTION_KEY` | No | Only needed before enabling MFA/TOTP. See `.env.example` for generation instructions. |
| `SEED_USER_EMAIL` | No | Login email. Default: `admin@shekel.local` |
| `SEED_USER_PASSWORD` | No | Login password (min 12 characters). Default: `ChangeMe!2026` |

### 4. Start the Application

```bash
docker compose up -d
```

First startup takes 1--2 minutes while the database is initialized. Check progress with:

```bash
docker compose logs -f app
```

Look for `=== Starting Application ===` to confirm the app is running.

### 5. Log In

Open http://localhost and log in with the credentials from your `.env` file (or the defaults above).

### 6. First-Time Setup in the App

After logging in you will see a **Welcome to Shekel!** banner with a setup checklist. Your account and budget categories are already created. Complete the remaining steps:

1. **Generate Pay Periods** -- Navigate to Pay Periods and enter your next payday, then generate. This creates ~2 years of biweekly periods.
2. **Set Up a Salary Profile** -- Go to Salary and create your income profile with deductions and tax info.
3. **Create Recurring Transactions** -- Go to Templates and add your regular income and expenses with their recurrence patterns.

Once all three steps are done, the welcome banner dismisses and the budget grid populates with your projected transactions. You can then set your **anchor balance** (click the balance display on the grid) to calibrate projections against your real checking account balance.

### Updating

```bash
docker compose pull && docker compose up -d
```

Database migrations run automatically on startup.

### Troubleshooting

| Symptom | Fix |
|---|---|
| `POSTGRES_PASSWORD` error on startup | Set `POSTGRES_PASSWORD` in your `.env` file. |
| `SECRET_KEY` error on startup | Set `SECRET_KEY` in your `.env` file. Run `openssl rand -hex 32` to generate one. |
| MFA enable fails with "TOTP_ENCRYPTION_KEY" message | Set `TOTP_ENCRYPTION_KEY` in `.env`. See `.env.example` for generation instructions. |
| App does not start or shows blank page | Run `docker compose logs app` and check for error messages. |
| Container keeps restarting | Run `docker compose logs app` -- a missing required variable or database connection issue is the most common cause. |
| Container marked unhealthy during first startup | First-time initialization (schema creation, migrations, seeding) can take over 60 seconds. The healthcheck `start_period` allows 120 seconds before failures count. Wait and check `docker compose logs -f app`. |
| `curl` not found inside the container | The slim image does not include curl. Use Python instead: `docker compose exec app python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health').read().decode())"` |
| `Can't locate revision identified by ...` | The GHCR image may be older than your database. Rebuild from source (`docker compose build`) or pull the latest image (`docker compose pull`). |

### Deploying Behind an Existing Reverse Proxy

If you already run a central Nginx (or Traefik/Caddy) on your Docker host, you do not need the bundled `shekel-nginx` service. Instead, put `shekel-app` on your shared Docker network so the central proxy can reach it.

**1. Create an override file** (`docker-compose.override.yml`) next to `docker-compose.yml`:

```yaml
# docker-compose.override.yml -- use a central reverse proxy instead
# of the bundled shekel-nginx service.
services:
  app:
    networks:
      - backend
      - homelab        # your shared proxy network

  nginx:
    # Disable the bundled Nginx.
    profiles: ["disabled"]

networks:
  homelab:
    external: true
```

Replace `homelab` with whatever your shared Docker network is named.

**2. Add a server block to your central Nginx** (e.g., `/etc/nginx/conf.d/shekel.conf`):

```nginx
server {
    listen 80;
    server_name shekel.example.com;   # your domain or LAN hostname

    location / {
        proxy_pass         http://shekel-app:8000;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 120;
    }

    location /static/ {
        # Optional: serve static files directly if you mount the
        # static_files volume into your Nginx container.
        # Otherwise, Gunicorn serves them (slightly slower but simpler).
        proxy_pass http://shekel-app:8000;
    }
}
```

**3. Start and verify:**

```bash
docker compose up -d

# Confirm shekel-app joined the shared network:
docker network inspect homelab --format '{{range .Containers}}{{.Name}} {{end}}'
# Should include "shekel-app"

# Test the health endpoint from inside the container:
docker compose exec app python -c \
  "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health').read().decode())"
```

### Post-Deploy Verification Checklist

After any deployment or update, verify:

```bash
# 1. All containers are healthy
docker compose ps

# 2. App is on the expected network(s)
docker network inspect homelab --format '{{range .Containers}}{{.Name}} {{end}}'

# 3. Health endpoint responds
docker compose exec app python -c \
  "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health').read().decode())"

# 4. Alembic head matches the running code
docker compose exec app python -c \
  "from app import create_app; app = create_app('production'); \
   from flask_migrate import current as _c; \
   print('OK') if app else print('FAIL')"
```

---

## Developer Setup (from source)

For contributing to Shekel or running from source.

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

Open http://localhost:5000 (development server) and log in with the seed user credentials, or
register a new account at http://localhost:5000/register.
- **Default Email:** `admin@shekel.local`
- **Default Password:** `ChangeMe!2026`

### 6. First-Time Setup in the App

See [Quick Start (Docker) > First-Time Setup in the App](#6-first-time-setup-in-the-app) for initial configuration steps.

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

**Test suite:** 1780 test functions across 63 test files (+ 3 performance benchmarks run separately)

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
│   ├── templates/               # Jinja2 HTML templates (80 files, 16 directories)
│   └── static/                  # CSS, JS (16 chart/grid/form scripts), images
├── migrations/                  # Alembic database migrations (19 versions)
├── monitoring/                  # Promtail config and Grafana/Loki runbook
├── nginx/                       # Nginx reverse proxy configuration
├── cloudflared/                 # Cloudflare Tunnel configuration
├── .github/workflows/           # CI (lint + test) and Docker image publishing
├── scripts/                     # Seed, backup/restore, integrity check, ops scripts
├── tests/                       # pytest test suite (1780 test functions, 63 test files)
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
