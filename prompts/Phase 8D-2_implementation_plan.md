Analyze my entire project and write a detailed implementation plan for Phase 8D-2: CI Pipeline, Deployment Script, and Environment Configuration.

## Context

This is a personal finance app called Shekel. The stack is Flask, Jinja2, HTMX, Bootstrap 5, and PostgreSQL. The project uses Alembic for migrations, pytest for testing, and follows a service-layer architecture (routes call services, services call models). The app runs in Docker containers on a Proxmox host.

Phases 8A through 8C are complete. Phase 8D has been split into three sub-phases:

- **8D-1 (complete):** Health endpoint, Dockerfile finalization, docker-compose production and dev files, Nginx reverse proxy, Gunicorn configuration. The app now runs in production configuration with Gunicorn behind Nginx, accessible on the local network, with a `/health` endpoint returning 200.
- **8D-2 (this plan):** CI pipeline (GitHub Actions), deployment script, environment configuration (.env.example, secret management documentation).
- **8D-3 (next):** Cloudflare Tunnel, Cloudflare Access, Cloudflare WAF rate limiting, runbook finalization.

Read these files first to understand the scope and standards:

1. `phase_8_hardening_ops_plan.md` -- the master plan. Phase 8D items 11-14 are this plan's scope. Also read item 10 (health endpoint, completed in 8D-1) since the deploy script depends on it for post-deploy verification.
2. `phase_8a_implementation_plan.md` -- the completed implementation plan for Phase 8A. **Your output must match this document's structure, depth, and level of detail exactly.** This is your template.
3. `project_requirements_v2.md` and `project_requirements_v3_addendum.md` -- for overall project context.

## What 8D-2 Covers (master plan items)

### CI Pipeline (item 11)

- `.github/workflows/ci.yml`: GitHub Actions workflow.
- Trigger on push to `main` and on pull requests.
- Steps: checkout, set up Python, install dependencies, run `pylint`, run `pytest` with a PostgreSQL service container.
- This is the user's first CI pipeline. Keep it simple and well-commented.

### Deployment Script (item 12)

- `scripts/deploy.sh`: shell script run on the Proxmox host.
- Pull latest code from GitHub.
- Build the Docker image.
- Run database migrations (`flask db upgrade` inside the container).
- Restart the app container (rolling restart if possible, otherwise stop/start).
- Run a health check to verify the deploy succeeded.
- Roll back (restart the previous image) if the health check fails.

### Environment Configuration (items 13-14)

- `.env.example`: documents ALL required environment variables across the entire project (8A through 8D).
- Secret management documentation: how secrets (database password, Flask secret key, TOTP encryption key) are stored, protected, and recovered after a disaster.

## Critical: Audit Pre-Existing Infrastructure First

Before writing any implementation steps, you MUST thoroughly scan the codebase. Specifically check:

**CI pipeline:**

- Check if `.github/` or `.github/workflows/` already exists with any workflow files.
- Read `requirements.txt` to understand all Python dependencies that CI must install.
- Read `pytest.ini` or `pyproject.toml` or `setup.cfg` for pytest configuration (test discovery, markers, flags).
- Check for a `pylintrc` or `.pylintrc` file, or pylint configuration in `pyproject.toml`/`setup.cfg`. CI must run pylint with the same configuration as local development.
- Read `tests/conftest.py` to understand how the test database is configured: what environment variables does it expect, how is the database created, how are schemas and migrations applied, what fixtures set up the test state. The CI PostgreSQL service container must provide the same setup.
- Check whether any tests require environment variables beyond `DATABASE_URL` (e.g., `TOTP_ENCRYPTION_KEY` from 8A, any logging config from 8B). These must be set in the CI workflow.
- Check the Python version used in `Dockerfile` (from 8D-1). The CI workflow must use the same Python version.
- Check if any tests are slow or have known issues. Look for pytest markers like `@pytest.mark.slow` or skip decorators.

**Deployment:**

- Check if `scripts/deploy.sh` or any deployment script already exists.
- Read all existing scripts in `scripts/` to understand conventions (shebang, `set -euo pipefail`, error handling, argument parsing, logging format). The deploy script must follow the same conventions.
- Read the `docker-compose.yml` (finalized in 8D-1) to understand: the app service name, how to exec into the container for migrations, the image name/tag convention, and how to restart individual services.
- Read the `Dockerfile` (finalized in 8D-1) to understand the image build process and how to tag images for rollback.
- Read `app/routes/health.py` (created in 8D-1) to understand the health endpoint's response format. The deploy script's health check must parse this response correctly.
- Check if Alembic migrations have ever been used to downgrade. Read `migrations/env.py` and a sample migration file to understand whether downgrade functions are implemented. This affects the rollback strategy (can the script automatically reverse a migration, or is manual intervention required?).

**Environment configuration:**

- Read `app/config.py` thoroughly. Document every `os.getenv()`, `os.environ.get()`, or `os.environ[]` call. This is the primary source for the .env.example.
- Scan every file in `scripts/` for environment variable reads. The 8C backup/restore/retention/verify scripts may read `BACKUP_LOCAL_DIR`, `BACKUP_NAS_DIR`, `DATABASE_URL`, etc.
- Scan `app/services/mfa_service.py` (from 8A) for `TOTP_ENCRYPTION_KEY`.
- Scan the 8B logging configuration for any environment-driven settings (e.g., `LOG_LEVEL`, `LOG_FORMAT`).
- Scan `docker-compose.yml` for any `${VARIABLE}` references.
- Check if `.env.example` or `.env.template` already exists.
- Check `.gitignore` for `.env` exclusion.

Document ALL findings in a "## Pre-Existing Infrastructure" section at the top of the plan.

## Required Output Structure (match the 8A plan exactly)

### 1. Overview

Brief summary, pre-existing infrastructure highlights, and key decisions.

### 2. Pre-Existing Infrastructure

Detailed audit results with file paths, line numbers, and impact on 8D-2 implementation.

### 3. Decision/Recommendation Sections

8D-2 has three decisions that must be documented:

- **CI test database configuration:** The GitHub Actions workflow needs a PostgreSQL service container. Document how the test database will be configured to match the local pytest setup. Specifically: what `DATABASE_URL` does the test suite expect? Does `conftest.py` create the database/schemas, or does it expect them to exist? Does it run Alembic migrations, or create tables directly from models? Show the exact `services` and `env` blocks for the workflow YAML.
- **Deploy script rollback strategy:** The master plan says "roll back (restart the previous image) if the health check fails." This requires preserving the previous image. Document the strategy: (a) Does the script tag the current image as `shekel:previous` before building the new one? (b) What if the new build itself fails (before deployment)? (c) What if the migration fails partway through? (d) Are Alembic downgrade functions implemented in the project's migrations? If not, what is the manual recovery procedure? (e) What if the health check times out vs. returns 500? Document each failure mode and the recovery path.
- **Pylint in CI:** Document the pylint configuration. Does a `.pylintrc` file exist? What score threshold should CI enforce? Should CI fail on any pylint error, or only below a score threshold? What about existing pylint warnings -- will they block the first CI run? Recommend a pragmatic approach (e.g., start with `--fail-under=9.0` and tighten over time, or fail only on errors not warnings).

### 4. Work Units

Organize into sequential work units. I recommend this ordering:

- **WU-1: .env.example and secret management documentation.** This comes first because the CI workflow and deploy script both need to know every environment variable. Produce the complete `.env.example` by scanning the full codebase. Write the secret management section of the runbook.
- **WU-2: CI Pipeline.** Create `.github/workflows/ci.yml`. This is independent of the deploy script and can be tested immediately by pushing to a branch.
- **WU-3: Deployment Script.** Create `scripts/deploy.sh` with build, migrate, restart, health check, and rollback. This depends on WU-1 (needs to know which env vars to validate) and benefits from WU-2 (CI proves the tests pass before deploy).

Each work unit must include:

- **Goal** statement.
- **Depends on** list.
- **Files to Create** with complete file contents:
  - For `.env.example`: the complete file with every variable, grouped by category, with comments explaining each variable, default values where applicable, and which are required vs. optional.
  - For `ci.yml`: the complete workflow YAML with comments on every step.
  - For `deploy.sh`: the complete script with functions, error handling, `set -euo pipefail`, configurable variables, help message, logging, rollback logic.
  - For runbook sections: section outline with key content for each section.
- **Files to Modify** with exact line numbers, current code, new code, and rationale.
- **Test Gate** checklist.
- **Testing/Verification:** Manual verification procedures for CI (push a commit, check Actions tab) and deploy (run deploy.sh, verify results). Document exact commands and expected outcomes, including failure mode testing (e.g., intentionally break a test to confirm CI catches it, intentionally fail a health check to confirm rollback works).

### 5. Work Unit Dependency Graph

ASCII diagram.

### 6. Complete Test Plan

- **Manual verification runbook:** numbered checklist for CI validation (push, check workflow, verify pylint runs, verify pytest runs with Postgres, verify PR checks) and deploy script validation (deploy, verify health, simulate failure, verify rollback).

### 7. Phase 8D-2 Test Gate Checklist

These are the master plan test gate items that 8D-2 is responsible for:

- [ ] GitHub Actions CI runs tests on push to main
- [ ] `deploy.sh` successfully deploys a new version with zero manual steps after invocation
- [ ] `deploy.sh` rolls back on health check failure

Map each to the specific verification step(s).

### 8. File Summary

New files and modified files tables.

## Code Standards

- Shell scripts must use `bash`, include `set -euo pipefail`, use functions for logical sections, and include a usage/help message.
- Shell scripts must have configurable variables at the top with environment variable overrides.
- Shell scripts must use consistent logging format: `[YYYY-MM-DD HH:MM:SS] [LEVEL] message`.
- GitHub Actions YAML must have comments on every step.
- The `.env.example` must be organized by category with explanatory comments.
- All Python must conform to Pylint standards with docstrings and inline comments.
- Use snake_case for all naming.

## Important Constraints

- The `.env.example` must be exhaustive. Scan EVERY file in the project that reads an environment variable: `app/config.py`, every file in `scripts/`, `docker-compose.yml`, `app/services/mfa_service.py`, `app/utils/logging_config.py`, and any other file. A missing variable is a production outage. Group variables by category: core app, database, authentication/security, logging, backup, and deployment.
- The CI workflow must use the same Python version as the production Dockerfile and the same PostgreSQL version as docker-compose.yml. Read both files and match the versions exactly.
- The CI workflow must run Alembic migrations (or however the test suite sets up its schema) before running pytest. Read `tests/conftest.py` to understand the exact setup sequence.
- The deploy script must tag the current running image before building the new one so rollback can restore the previous version. Use a naming convention like `shekel-app:previous` or `shekel-app:rollback`.
- The deploy script must handle five failure modes: (1) git pull fails (network issue), (2) Docker build fails (code error), (3) migration fails (schema error), (4) health check returns unhealthy, (5) health check times out. Each failure mode must log a clear error message and either exit cleanly or trigger rollback as appropriate. Rollback is only needed for modes 3-5 (the old container was already stopped).
- The deploy script must wait for the health endpoint to become healthy with a configurable timeout and retry interval, not just check once. Containers take time to start.
- The secret management documentation must cover disaster recovery: if the Proxmox host is lost, how does the user reconstruct the `.env` file? Should `.env` be included in the 8C NAS backup? Document the recommendation.

## What NOT to Include

- Do not implement Cloudflare Tunnel, Access, or WAF rules (those are 8D-3).
- Do not install or configure Loki, Grafana, or Promtail.
- Do not add Docker Swarm, Kubernetes, or any orchestration.
- Do not add a container registry or CI/CD deployment (CD is manual via deploy.sh).
- Do not modify the Dockerfile, docker-compose.yml, Nginx config, or Gunicorn config (those were finalized in 8D-1). If you discover issues with them during the audit, document them as findings but do not change them in this plan.
