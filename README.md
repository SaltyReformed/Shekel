# Shekel -- Personal Budget App

A paycheck-centric budget application that replaces a biweekly-paycheck-based spreadsheet. Organizes finances around **pay periods** rather than calendar months, mapping every expense to a specific paycheck and projecting balances forward over a ~2-year horizon.

**Stack:** Flask - Jinja2 - HTMX - Bootstrap 5 - PostgreSQL

**Two ways to run Shekel:**
- **Docker (recommended):** Download two files, create a volume, run `docker compose up`. No Python or PostgreSQL install needed. See [Quick Start (Docker)](#quick-start-docker).
- **From source:** Clone the repo, set up Python, use Docker for databases. See [Developer Setup (from source)](#developer-setup-from-source).

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
| `REGISTRATION_ENABLED` | No | Set to `false` to disable public registration. Default: `true`. See [Security](#security). |
| `SEED_USER_EMAIL` | No | Login email. Default: `admin@shekel.local` |
| `SEED_USER_PASSWORD` | No | Login password (min 12 characters). Default: `ChangeMe!2026` |

### 4. Create the Database Volume and Start

```bash
# One-time setup: create the database volume.
# This volume is external -- "docker compose down -v" will NOT delete it.
# Your financial data is safe from accidental cleanup commands.
docker volume create shekel-prod-pgdata

docker compose up -d
```

First startup takes 1-2 minutes while the database is initialized. Check progress with:

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

### Stopping and Removing

```bash
# Stop containers (data is preserved in the external volume):
docker compose down

# Stop containers and remove logs/static volumes (database is safe):
docker compose down -v

# To permanently delete ALL data including the database:
docker compose down -v
docker volume rm shekel-prod-pgdata
```

### Troubleshooting

| Symptom | Fix |
|---|---|
| `POSTGRES_PASSWORD` error on startup | Set `POSTGRES_PASSWORD` in your `.env` file. |
| `SECRET_KEY` error on startup | Set `SECRET_KEY` in your `.env` file. Run `openssl rand -hex 32` to generate one. |
| `shekel-prod-pgdata ... not found` on first run | Run `docker volume create shekel-prod-pgdata` before `docker compose up`. |
| MFA enable fails with "TOTP_ENCRYPTION_KEY" message | Set `TOTP_ENCRYPTION_KEY` in `.env`. See `.env.example` for generation instructions. |
| `/register` returns 404 | `REGISTRATION_ENABLED` is set to `false` in `.env`. Set to `true` or remove the line to re-enable. |
| App does not start or shows blank page | Run `docker compose logs app` and check for error messages. |
| Container keeps restarting | Run `docker compose logs app` -- a missing required variable or database connection issue is the most common cause. |
| Container marked unhealthy during first startup | First-time initialization (schema creation, migrations, seeding) can take over 60 seconds. The healthcheck `start_period` allows 120 seconds before failures count. Wait and check `docker compose logs -f app`. |
| `curl` not found inside the container | The slim image does not include curl. Use Python instead: `docker compose exec app python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health').read().decode())"` |
| `Can't locate revision identified by ...` | The GHCR image may be older than your database. Rebuild from source (`docker compose build`) or pull the latest image (`docker compose pull`). |

### Deploying Behind an Existing Reverse Proxy

If you already run a central Nginx (or Traefik/Caddy) on your Docker host, you do not need the bundled Nginx service. Instead, put the app container on your shared Docker network so the central proxy can reach it.

**1. Create an override file** (`docker-compose.override.yml`) next to `docker-compose.yml`:

```yaml
# docker-compose.override.yml -- use a central reverse proxy instead
# of the bundled Nginx service.
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
        proxy_pass         http://shekel-prod-app:8000;
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
        proxy_pass http://shekel-prod-app:8000;
    }
}
```

**3. Start and verify:**

```bash
docker compose up -d

# Confirm the app container joined the shared network:
docker network inspect homelab --format '{{range .Containers}}{{.Name}} {{end}}'
# Should include "shekel-prod-app"

# Test the health endpoint from inside the container:
docker compose exec app python -c \
  "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health').read().decode())"
```

### Post-Deploy Verification Checklist

After any deployment or update, verify:

```bash
# 1. All containers are healthy
docker compose ps

# 2. Health endpoint responds
docker compose exec app python -c \
  "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health').read().decode())"
```

---

## Backups

Shekel stores all financial data in a PostgreSQL Docker volume. **If this volume is lost, corrupted, or the host fails, your data is gone.** Set up automated backups before entering real financial data.

See [docs/backup_runbook.md](docs/backup_runbook.md) for complete instructions covering:

- Automated daily backups via `pg_dump` with configurable retention
- Off-site backup to NAS or remote storage
- Backup encryption with GPG
- Restore procedures and verification

### Quick Backup Setup

```bash
# Run a manual backup now
./scripts/backup.sh

# Add to crontab for daily automated backups (2:00 AM)
crontab -e
# Add: 0 2 * * * /path/to/shekel/scripts/backup.sh >> /var/log/shekel_backup.log 2>&1
```

See the runbook for retention policies, NAS configuration, and encryption setup.

---

## Developer Setup (from source)

For contributing to Shekel or running from source. Uses Docker for databases and the host for the Python application.

### 1. Prerequisites

- **Docker Engine** -- provides the dev and test PostgreSQL databases
- **Python 3.12+** and **pip**

```bash
docker compose version
python --version
```

### 2. Clone & Set Up Python Environment

```bash
cd ~/projects  # or wherever you keep code
git clone https://github.com/SaltyReformed/Shekel.git
cd Shekel

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies (dev file includes production deps + test/lint tools)
pip install -r requirements-dev.txt
```

### 3. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` and set at minimum:

```
SECRET_KEY=<any random string for dev>
```

The default `DATABASE_URL` and `TEST_DATABASE_URL` in `.env.example` point to `localhost:5432` and `localhost:5433`, which match the dev Docker databases started in the next step.

### 4. Start the Dev Databases

```bash
docker compose -f docker-compose.dev.yml up -d db test-db
```

This starts two PostgreSQL containers:
- `shekel-dev-db` on port 5432 (development database)
- `shekel-dev-test-db` on port 5433 (test database)

**Important:** These containers use project name `shekel-dev` and are fully isolated from production. Running `docker compose down -v` from the production directory cannot affect them, and vice versa.

### 5. Initialize the Database

```bash
# Apply all migrations
flask db upgrade

# Seed reference data and the initial user
python scripts/seed_ref_tables.py
python scripts/seed_user.py
python scripts/seed_tax_brackets.py
```

### 6. Run the App

```bash
flask run
# or
python run.py
```

Open http://localhost:5000 (development server) and log in with the seed user credentials, or register a new account at http://localhost:5000/register.
- **Default Email:** `admin@shekel.local`
- **Default Password:** `ChangeMe!2026`

### 7. First-Time Setup in the App

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

Last evaluated: 2026-03-23

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
├── tests/                       # pytest test suite
├── docs/                        # Plans, progress tracking, runbooks
├── docker-compose.yml           # Production Docker Compose (app + PG + Nginx)
├── docker-compose.dev.yml       # Development Docker Compose (dev DB + test DB)
├── Dockerfile                   # Multi-stage production container
├── gunicorn.conf.py             # Gunicorn WSGI server configuration
├── entrypoint.sh                # Container startup (DB init, migrate, seed)
├── requirements.txt             # Production Python dependencies
├── requirements-dev.txt         # Dev/test/lint dependencies (includes requirements.txt)
└── run.py                       # Entry point
```
