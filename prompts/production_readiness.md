You are analyzing the Shekel personal finance application for production readiness. Shekel is a Flask + HTMX + Jinja2 + PostgreSQL + Bootstrap 5 app deployed on a Proxmox LXC container with external access via Cloudflare Tunnel. The production target is daily single-user use accessible over the internet through Cloudflare Tunnel.

## Your Task

Perform a comprehensive production readiness audit of this codebase. Read the project documentation first, then systematically analyze the code. Produce a markdown report saved to `docs/production_readiness_report.md` with your findings.

## Context You Need to Know

The project has extensive planning docs. Read these files before you begin your code analysis:

1. `docs/project_requirements_v2.md` -- the core requirements document
2. `docs/project_requirements_v3_addendum.md` -- extended account types and revised phase roadmap
3. `docs/phase_8_hardening_ops_plan.md` -- the full Phase 8 plan (security, logging, backups, deployment, multi-user)
4. `docs/phase_8a_implementation_plan.md` -- the detailed 8A security implementation plan
5. `docs/progress.md` -- current build status for all phases

Use these docs as your source of truth for what is planned vs. what is built. Cross-reference the docs against the actual codebase to verify the progress claims.

## Production Definition

"Production" for this analysis means: **the app can be used daily by a single user, accessed externally over a Cloudflare Tunnel, with confidence that data will not be lost and the app is not trivially exploitable.** This does not require multi-user support, CI/CD automation, or features from phases 7, 9, or 10.

## Analysis Categories

Organize your findings into exactly these six sections:

### 1. Blockers: Things That Must Be Fixed Before Production

Items that would cause the app to fail, lose data, or be unacceptable for daily external use. For each blocker, explain the specific risk if it is not addressed. Examples of what might qualify: missing backup/restore capability, secrets hardcoded in source, database migrations that could fail silently, no way to recover from a crash, application errors that corrupt data.

### 2. Security Risks: Unaddressed Vulnerabilities

Analyze the codebase for security issues beyond what Phase 8A already addressed. Phase 8A is marked complete and covered CSRF, MFA, rate limiting, session management, security headers, and custom error pages. Look for anything 8A may have missed or that exists outside its scope. Specifically check for:

- Secrets management (are secrets in source control, environment variables, or a secrets manager?)
- SQL injection vectors (raw SQL without parameterization)
- Input validation gaps (routes accepting user input without schema validation)
- Session configuration (cookie flags, session lifetime, secure/httponly/samesite)
- Dependency vulnerabilities (run `pip audit` or check known CVEs against `requirements.txt`)
- File upload or path traversal risks
- Debug mode or verbose error exposure in production configuration
- CORS configuration
- Whether the Gunicorn/Docker configuration exposes unnecessary ports or runs as root
- Any route that is not behind `@login_required` but should be

### 3. Pain Points: Things That Work but Are Fragile or Risky

Items that function today but could cause problems under real daily use. These are not strict blockers but represent operational risk. Examples: no health check endpoint, no structured logging to diagnose issues, in-memory rate limiter state lost on restart, database connection handling under load, long-running requests without timeouts, no monitoring or alerting.

### 4. Missing for Production but Deferrable as Feature Updates

Items from the Phase 8 plan (8B through 8E) or elsewhere that are not strictly required for a single user on a Cloudflare Tunnel but would improve the app over time. For each item, briefly note why it is safe to defer and what triggers the need to revisit it. Examples: audit logging, multi-user groundwork, CI/CD pipeline, CSV export, mobile layout polish.

### 5. Codebase Health Check

Evaluate the overall quality and maintainability of the code. This is not about features but about whether the code itself is production-grade:

- Test coverage: are there obvious gaps? Are critical services (recurrence engine, balance calculator, credit workflow) well tested?
- Code organization: does the project structure match the documented layout? Are there orphaned files, dead code, or unused imports?
- Database migrations: is the Alembic migration chain clean? Any unapplied migrations? Any migrations that are not idempotent?
- Dependency management: are dependencies pinned? Are there unused packages in `requirements.txt`?
- Configuration: is there a clear separation between dev, test, and production configs?
- Error handling: do service functions handle edge cases, or do they let exceptions bubble up unhandled?

### 6. Recommended Pre-Production Checklist

Based on everything above, produce a prioritized checklist of actions to take before going live. Order them by risk (highest risk first). For each item, estimate the effort as small (less than 1 hour), medium (1 to 4 hours), or large (more than 4 hours). Mark each item as either BLOCKER (must do) or RECOMMENDED (should do). Keep the checklist actionable and specific. Do not pad it with generic advice.

## How to Conduct the Analysis

1. **Read the docs first.** Start with `docs/progress.md` to understand what is built, then read the requirements and Phase 8 plans.
2. **Verify claims against code.** If `progress.md` says Phase 8A is complete, spot-check the actual auth routes, MFA service, CSRF configuration, and rate limiter setup to confirm.
3. **Inspect the deployment stack.** Look at `Dockerfile`, `docker-compose.yml`, `entrypoint.sh` (or equivalent), and any Gunicorn config. Check for production anti-patterns.
4. **Scan for secrets.** Grep for hardcoded passwords, API keys, or secret keys in source files (not just `.env`). Check `.gitignore` to confirm sensitive files are excluded.
5. **Review the database layer.** Check Alembic migrations, model definitions, and whether there are any raw SQL queries that bypass the ORM without parameterization.
6. **Check route protection.** Verify every route blueprint to confirm `@login_required` is applied where needed.
7. **Examine error handling.** Look at how service functions handle failures. Check whether database transactions are properly committed/rolled back.
8. **Review the test suite.** Run `pytest --co -q` to get a count of collected tests. Note any service or route modules that lack test coverage.
9. **Check dependency security.** Review `requirements.txt` for pinned versions and known vulnerabilities.
10. **Look at configuration.** Review `app/config.py` for how dev vs. production settings are handled. Check whether `DEBUG`, `TESTING`, or `SECRET_KEY` defaults are safe for production.

## Output Format

Save the report to `docs/production_readiness_report.md`. Use the following structure:

```markdown
# Shekel Production Readiness Report

**Generated:** [date]
**Commit:** [current git short hash]
**Test count:** [number from pytest --co -q]
**Production target:** Single-user daily use via Cloudflare Tunnel

## Executive Summary

[2-3 paragraph summary of overall readiness. State clearly whether the app is ready, almost ready, or has significant work remaining. Call out the top 3 concerns.]

## 1. Blockers

[Each blocker as a subsection with: description, specific risk, affected files, and suggested fix]

## 2. Security Risks

[Each risk as a subsection with: description, severity (critical/high/medium/low), affected files, and suggested fix]

## 3. Pain Points

[Each pain point as a subsection with: description, likelihood of causing issues, and suggested mitigation]

## 4. Deferrable Items

[Each item with: description, why it is safe to defer, and what triggers the need to revisit]

## 5. Codebase Health Check

[Subsections for: tests, code organization, migrations, dependencies, configuration, error handling]

## 6. Pre-Production Checklist

[Ordered table with columns: priority number, item, category (BLOCKER or RECOMMENDED), estimated effort, and notes]
```

Do not include generic advice like "consider adding more tests" without specifying which modules need them. Every finding should reference specific files, line numbers, or configuration values where possible. If you find nothing wrong in a category, say so explicitly rather than padding the section with hypothetical concerns.

Begin by reading the documentation files listed above, then proceed with the codebase analysis.
