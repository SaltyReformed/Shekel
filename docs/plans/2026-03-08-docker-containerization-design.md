# Docker Containerization Design

**Date:** 2026-03-08
**Status:** Approved

## Goal

Package Shekel as a production-ready Docker image published to GitHub Container Registry (GHCR). End users pull a pre-built image and run it with `docker compose up` -- no source code, no building, no manual setup. First boot automatically runs migrations, seeds reference data, and creates an initial user from environment variables.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Registry | GHCR | Free for public repos, integrated with GitHub Actions, no extra accounts |
| Dockerfile | Multi-stage build | Smaller image (~150MB vs ~250MB), no gcc/build tools in production |
| Python version | 3.14-slim | Matches development environment |
| Database init | Entrypoint script | Fully automatic first boot, idempotent on restarts |
| Reverse proxy | None (Gunicorn only) | Personal budgeting app, users can add their own proxy |
| User creation | Environment variables | No app changes needed, works with existing seed scripts |
| Product scope | Shareable | Others can pull and run with their own credentials |

## Architecture

```
GitHub Actions (CI/CD)
    |
    v
GHCR (ghcr.io/<username>/shekel:latest)
    |
    v  docker compose pull
+-----------------------+
| User's machine        |
|                       |
|  +--------+  +-----+ |
|  | app    |->| db  | |
|  | :5000  |  |:5432| |
|  +--------+  +-----+ |
+-----------------------+
```

## Multi-Stage Dockerfile

### Stage 1 -- Builder
- Base: `python:3.14-slim`
- Installs gcc + libpq-dev (compile psycopg2)
- Creates virtualenv, installs all Python dependencies + gunicorn
- Purpose: compile C extensions in an isolated stage

### Stage 2 -- Runtime
- Base: `python:3.14-slim`
- Installs only `libpq5` (runtime PostgreSQL client library)
- Creates non-root `shekel` user
- Copies virtualenv from Stage 1 (no gcc, no headers, no pip cache)
- Copies application code
- Copies `entrypoint.sh`
- Runs as `shekel` user
- Exposes port 5000

Final image contains only: Python runtime, compiled dependencies, app code, entrypoint script.

## Entrypoint Script

Runs on every container start, before Gunicorn. Must be idempotent.

### Execution order

1. **Wait for PostgreSQL** -- Loop `pg_isready` until the database is reachable
2. **Create schemas** -- `CREATE SCHEMA IF NOT EXISTS` for ref, auth, budget, salary, system
3. **Run migrations** -- `flask db upgrade` (Alembic skips already-applied migrations)
4. **Seed reference data** -- `seed_ref_tables.py` and `seed_tax_brackets.py` (check before insert)
5. **Create seed user** -- If no users exist AND `SEED_USER_EMAIL` is set, run `seed_user.py`
6. **Start Gunicorn** -- `exec gunicorn ...` (replaces shell, becomes PID 1)

### Idempotency guarantees
- Schemas: `IF NOT EXISTS`
- Migrations: Alembic tracks applied migrations in `alembic_version` table
- Reference data: Scripts check for existing rows before inserting
- Seed user: Only created when user table is empty

## Docker Compose (Production)

Two services:

### db
- Image: `postgres:16-alpine`
- Volume: `pgdata` for data persistence
- Healthcheck: `pg_isready`
- Environment: `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`

### app
- Image: `ghcr.io/<username>/shekel:latest` (pulled from GHCR)
- Depends on: `db` (healthy)
- Environment from `.env` file
- Port: 5000

### User environment variables

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `SECRET_KEY` | Yes | -- | Flask session signing |
| `POSTGRES_PASSWORD` | Yes | -- | Database password |
| `SEED_USER_EMAIL` | First run | -- | Initial user email |
| `SEED_USER_PASSWORD` | First run | -- | Initial user password |
| `SEED_USER_DISPLAY_NAME` | No | `Budget Admin` | Display name |

## GitHub Actions CI/CD

Workflow: `.github/workflows/docker-publish.yml`

### Triggers
- Push to `main` -- tags image as `latest`
- Git tags `v*` -- tags image as version number + `latest`

### Steps
1. Checkout code
2. Set up Docker Buildx (multi-stage caching)
3. Log in to GHCR with `GITHUB_TOKEN` (automatic, no secrets to configure)
4. Extract metadata (tags, labels)
5. Build and push image

### Image tags
- `ghcr.io/<username>/shekel:latest` -- most recent main build
- `ghcr.io/<username>/shekel:v1.0.0` -- pinned release version

## .dockerignore

Excludes from build context:
- `.git`, `.env`, `.env.*` (except `.env.example`)
- `__pycache__`, `*.pyc`, `*.pyo`, `.pytest_cache`
- `tests/`, `logs/`, `pgdata/`, `docs/`
- `*.md`, `.github/`, IDE files
- `docker-compose*.yml`, `Dockerfile`, `.dockerignore`

## Development Workflow

A separate `docker-compose.dev.yml` preserves the current development workflow:
- Uses `build: .` instead of `image:` from GHCR
- Mounts `init_db.sql` for test database creation
- Exposes PostgreSQL port for local development
- Day-to-day development is unchanged

## End User Experience

### First run
```bash
mkdir shekel && cd shekel
# Download docker-compose.yml and .env.example from GitHub
cp .env.example .env
# Edit .env: set SECRET_KEY, POSTGRES_PASSWORD, SEED_USER_*
docker compose up -d
# App is ready at http://localhost:5000
```

### Updates
```bash
docker compose pull && docker compose up -d
```

## Files Changed

| File | Action | Purpose |
|------|--------|---------|
| `Dockerfile` | Rewrite | Multi-stage build with python:3.14-slim |
| `entrypoint.sh` | Create | Migrations, seeding, idempotent first-boot |
| `docker-compose.yml` | Rewrite | Production compose pulling from GHCR |
| `docker-compose.dev.yml` | Create | Development compose with local build |
| `.dockerignore` | Create | Exclude unnecessary files from image |
| `.github/workflows/docker-publish.yml` | Create | CI/CD to build and push to GHCR |
| `.env.example` | Update | Add POSTGRES_PASSWORD, clarify seed vars |
| `scripts/init_db.sql` | Create | Schema creation SQL |
| `scripts/seed_ref_tables.py` | Modify | Make idempotent |
| `scripts/seed_tax_brackets.py` | Modify | Make idempotent |
| `scripts/seed_user.py` | Modify | Make idempotent |

No changes to application code (models, routes, services, templates, static files).
