Analyze my entire project and write a detailed implementation plan for Phase 8D-3: Cloudflare Tunnel, Cloudflare Access, Cloudflare WAF Rate Limiting, and Runbook Finalization.

## Context

This is a personal finance app called Shekel. The stack is Flask, Jinja2, HTMX, Bootstrap 5, and PostgreSQL. The project uses Alembic for migrations, pytest for testing, and follows a service-layer architecture. The app runs in Docker containers on a Proxmox host.

Phases 8A through 8C are complete. Phase 8D has been split into three sub-phases:

- **8D-1 (complete):** Health endpoint, Dockerfile finalization, docker-compose production and dev files, Nginx reverse proxy, Gunicorn configuration. The app runs in production configuration with Gunicorn behind Nginx, accessible on the local network.
- **8D-2 (complete):** CI pipeline (GitHub Actions), deployment script, environment configuration (.env.example, secret management). CI runs on push to main. deploy.sh automates pull/build/migrate/restart/verify with rollback on failure.
- **8D-3 (this plan):** Cloudflare Tunnel, Cloudflare Access, Cloudflare WAF rate limiting, Promtail/Loki log shipping validation, and final runbook consolidation.

Read these files first to understand the scope and standards:

1. `phase_8_hardening_ops_plan.md` -- the master plan. Phase 8D items 7-9 (Cloudflare) are this plan's primary scope. The test gate items related to Cloudflare and log shipping are also in scope.
2. `phase_8a_implementation_plan.md` -- the completed implementation plan for Phase 8A. **Your output must match this document's structure, depth, and level of detail exactly.** This is your template.
3. `project_requirements_v2.md` and `project_requirements_v3_addendum.md` -- for overall project context.

## What 8D-3 Covers (master plan items)

### Cloudflare Tunnel (item 7)

- Install `cloudflared` on the Proxmox host or as a container in docker-compose.
- Create a tunnel and configure it to point to the Nginx service.
- DNS configuration for the chosen subdomain.
- Deliverables: documented steps in the runbook, plus a `cloudflared` config file template.

### Cloudflare Access / Zero-Trust (item 8)

- Add a Cloudflare Access policy restricting access to allowed email addresses.
- This is an additional authentication layer before requests reach the app.
- Deliverable: step-by-step Access configuration documented in the runbook.

### Cloudflare WAF Rate Limiting (item 9)

- Rate limit `/login` and `/auth/mfa-verify` at the Cloudflare level.
- This is the outer rate limiting layer; the app-level Flask-Limiter (8A) is the inner layer.
- Deliverable: WAF rule configuration documented in the runbook.

### Log Shipping Validation (master plan test gate)

- The 8D test gate includes: "Application logs appear in JSON format in container stdout" and "Promtail (or equivalent) scrapes logs and they appear in Grafana/Loki."
- The Promtail sample config was a deliverable from 8B. This plan validates it works end-to-end and documents any adjustments needed.

### Runbook Consolidation

- Phases 8A through 8D-2 each produced documentation fragments (MFA recovery, backup/restore procedures, NAS mount setup, CI workflow, deploy script usage, secret management). This plan consolidates all operational documentation into a single coherent runbook.

## Critical: Audit Pre-Existing Infrastructure First

Before writing any implementation steps, you MUST thoroughly scan the codebase. Specifically check:

**Cloudflare configuration:**

- Check if any `cloudflared` config file (e.g., `cloudflared/config.yml` or similar) already exists in the project.
- Check if `docker-compose.yml` (from 8D-1) already includes a `cloudflared` service.
- Check if there are any references to Cloudflare in existing documentation or config files.

**Nginx configuration (from 8D-1):**

- Read `nginx/nginx.conf` to understand what port Nginx listens on and how it is exposed. The `cloudflared` tunnel must point to this port.
- Check whether Nginx is configured to accept proxy protocol headers or trust `X-Forwarded-For` from Cloudflare. This matters for rate limiting and IP logging.

**Rate limiting (from 8A):**

- Read `app/routes/auth.py` to see the Flask-Limiter configuration on the login endpoint. Document the current limits (e.g., "5 per 15 minutes"). The Cloudflare WAF rate limit must be set to complement this (usually a higher threshold at the Cloudflare layer, since Cloudflare blocks before the request reaches the app).
- Read `app/routes/auth.py` for the MFA verify endpoint's rate limiting. Document the current limits.

**Promtail/Loki configuration:**

- Check if a `promtail-config.yml` or similar file was created in 8B. Read it.
- Check if there are any Loki or Grafana references in docker-compose or documentation.
- Read the 8B structured logging configuration to understand the JSON log format that Promtail needs to parse.

**Existing documentation:**

- Check if a `docs/` directory exists. List every file in it.
- Check if a runbook file already exists (may have been started in 8C for backup/restore procedures).
- Scan all `scripts/` files for usage documentation in their `--help` output or header comments.
- Check if the 8A plan's MFA recovery script (`scripts/reset_mfa.py`) has documentation beyond its own `--help`.
- Check the `.env.example` (from 8D-2) for inline documentation.

**Real IP propagation (critical for rate limiting and logging):**

- When traffic flows through Cloudflare Tunnel to Nginx to Gunicorn to Flask, the client's real IP must be available to Flask for rate limiting and logging. Check:
  - Does Nginx set `X-Real-IP` or `X-Forwarded-For` when proxying to Gunicorn?
  - Does Flask or Flask-Limiter read these headers?
  - Does `cloudflared` set `X-Forwarded-For` when forwarding to Nginx?
  - If Nginx is configured to trust only specific upstream IPs (e.g., `set_real_ip_from`), it needs to trust the `cloudflared` source IP.
  - Document the full IP propagation chain: client -> Cloudflare edge -> cloudflared -> Nginx -> Gunicorn -> Flask. At each hop, which headers carry the real client IP?

Document ALL findings in a "## Pre-Existing Infrastructure" section at the top of the plan.

## Required Output Structure (match the 8A plan exactly)

### 1. Overview

Brief summary, pre-existing infrastructure highlights, key decisions.

### 2. Pre-Existing Infrastructure

Detailed audit results with file paths, line numbers, and impact on 8D-3 implementation.

### 3. Decision/Recommendation Sections

8D-3 has three decisions that must be documented:

- **Cloudflared deployment model:** Run `cloudflared` directly on the Proxmox host vs. as a Docker container in docker-compose. Pros/cons of each:
  - **On host:** Survives docker-compose restarts, simpler networking (connects to Nginx via localhost/host port), managed by systemd. But requires installing software directly on the Proxmox host.
  - **In docker-compose:** Everything is in one stack, easier to version control, follows the existing containerized pattern. But adds coupling (if docker-compose goes down, the tunnel goes down), and networking requires the `cloudflared` container to reach the Nginx container.
  - Recommend one and justify. Consider: the user already runs backup scripts on the host via cron (from 8C), so host-level tooling has precedent.

- **Real IP propagation strategy:** Document the full chain (client -> Cloudflare -> cloudflared -> Nginx -> Gunicorn -> Flask) and recommend the configuration at each hop to ensure the real client IP reaches Flask. This affects Flask-Limiter (rate limits by IP), structured logging (logs include `remote_addr`), and audit trails. If Nginx needs `set_real_ip_from` and `real_ip_header` directives, document them. If Flask-Limiter needs `key_func` configuration, document it.

- **Cloudflare rate limit thresholds:** The app-level limits (8A) are "5 per 15 minutes" on login. The Cloudflare WAF layer should have different thresholds. Recommend thresholds for both `/login` and `/auth/mfa-verify` at the Cloudflare level. The Cloudflare limits should be higher (e.g., 10-20 per minute) since they are a blunt outer layer catching volumetric abuse, while the app limits are the fine-grained inner layer. Document the rationale for the specific numbers.

### 4. Work Units

Organize into sequential work units. I recommend this ordering:

- **WU-1: Real IP Propagation.** Before any Cloudflare work, ensure the IP propagation chain is correct. This may require modifying `nginx/nginx.conf` (add `set_real_ip_from`, `real_ip_header`), verifying Flask-Limiter's `key_func`, and verifying the structured logging captures the correct IP. This is foundational -- Cloudflare rate limiting and Access policies are meaningless if Flask sees `127.0.0.1` for every request.
- **WU-2: Cloudflared Installation and Tunnel Configuration.** Install `cloudflared`, create the tunnel, create the config file template, configure DNS, test basic connectivity. Deliverables: `cloudflared/config.yml` template file, runbook section with step-by-step setup instructions.
- **WU-3: Cloudflare Access Policy.** Configure zero-trust Access policy, test that unauthorized users are blocked before requests reach the app. Deliverable: runbook section with step-by-step Cloudflare dashboard instructions.
- **WU-4: Cloudflare WAF Rate Limiting.** Configure WAF rules for `/login` and `/auth/mfa-verify`. Deliverable: runbook section documenting the rule configuration.
- **WU-5: Log Shipping Validation.** Verify that Promtail scrapes container logs and they appear in Grafana/Loki. If the Promtail sample config from 8B needs adjustments, document them. Deliverable: validated Promtail config and runbook section for Grafana/Loki setup.
- **WU-6: Runbook Consolidation.** Gather all operational documentation produced across 8A-8D into a single `docs/runbook.md`. Organize by topic, cross-reference scripts, ensure consistency.

Each work unit must include:

- **Goal** statement.
- **Depends on** list.
- **Files to Create** with complete content:
  - For `cloudflared/config.yml`: a template file with placeholder values and comments. Use `<PLACEHOLDER>` markers for values the user fills in (tunnel ID, domain name, etc.).
  - For runbook sections: complete prose with step-by-step instructions, not just outlines. Where Cloudflare dashboard steps are needed, describe each screen, field, and value to configure. Include screenshot-equivalent descriptions (e.g., "Navigate to Zero Trust > Access > Applications > Add an application > Self-hosted").
  - For Nginx modifications (if needed): exact directive additions with line numbers and comments.
- **Files to Modify** with exact line numbers, current code, new code, rationale.
- **Test Gate** checklist.
- **Testing/Verification:** Manual verification procedures with exact commands and expected results. For Cloudflare, this includes:
  - How to test the tunnel is routing traffic correctly.
  - How to test Access blocks an unauthorized email.
  - How to test rate limiting triggers at the Cloudflare layer.
  - How to verify real client IP appears in Flask's structured logs.
  - The master plan recommends testing with a staging subdomain first (Risk R6). Document the staging test procedure.

### 5. Work Unit Dependency Graph

ASCII diagram.

### 6. Complete Test Plan

- **Manual verification runbook:** numbered checklist covering every Cloudflare configuration step, the log shipping validation, and the end-to-end smoke test.
- **End-to-end smoke test sequence:** an ordered list of checks that exercises the full production stack after 8D-3 is complete. This covers ALL remaining 8D master plan test gate items:
  - External access through Cloudflare Tunnel.
  - Access policy blocks unauthorized users.
  - WAF rate limiting triggers on rapid login attempts.
  - Application logs in JSON format in container stdout.
  - Promtail scrapes logs into Grafana/Loki.
  - Real client IP (not 127.0.0.1) appears in Flask logs.
  - Health endpoint returns 200 through the full chain (Cloudflare -> Nginx -> Flask).

### 7. Phase 8D-3 Test Gate Checklist

These are the master plan test gate items that 8D-3 is responsible for:

- [ ] Cloudflare Tunnel routes traffic to the app
- [ ] Cloudflare Access blocks unauthenticated requests
- [ ] Application logs appear in JSON format in container stdout
- [ ] Promtail (or equivalent) scrapes logs and they appear in Grafana/Loki

Map each to the specific verification step(s).

### 8. File Summary

New files and modified files tables.

## Code Standards

- Shell scripts (if any) must use `bash`, `set -euo pipefail`, and follow the conventions established in 8C.
- Nginx configuration changes must have every directive commented.
- YAML configuration (cloudflared) must have comments explaining each field.
- Runbook documentation must be clear enough for someone unfamiliar with the project to follow. Write for the user's future self who has forgotten the setup details.
- Use snake_case for all naming.

## Important Constraints

- Cloudflare Tunnel handles TLS termination. Nginx listens on HTTP only. Do NOT add SSL certificates or HTTPS listeners to Nginx.
- The `cloudflared` config file is a TEMPLATE with placeholder values, not a hardcoded configuration. The user fills in their tunnel ID, domain, etc. Use `<TUNNEL_ID>`, `<DOMAIN>`, etc. as placeholders.
- Cloudflare Access policies and WAF rules are configured in the Cloudflare dashboard, not via API calls or Terraform. The deliverables for items 8 and 9 are runbook documentation, not automation scripts. Do not overengineer this with Cloudflare API automation.
- Risk R6 from the master plan: recommend testing with a staging subdomain (e.g., `staging.example.com`) before pointing the production subdomain. Document the staging test procedure.
- The Promtail configuration was a deliverable from 8B. If it exists, validate it works. If it does not exist or is incomplete, create it in this plan. Loki and Grafana are assumed to already be running as separate containers on the Proxmox host. Do NOT install or configure Loki or Grafana in this plan. Only configure Promtail to scrape the Shekel container logs.
- The runbook consolidation (WU-6) is a significant documentation effort. It must cover: deployment (how to deploy, how to roll back), backup/restore (how to run backup, how to restore, how to verify), security operations (how to reset MFA, how to change secrets, how to review audit logs), monitoring (how to check logs in Grafana, what to look for), Cloudflare management (how to add a new authorized user, how to update WAF rules), and troubleshooting (common issues and their resolutions). Each section should reference the relevant scripts with usage examples.
- The real IP propagation chain MUST be verified end-to-end. A common failure mode is Flask logging `127.0.0.1` or the Docker bridge IP for every request because `X-Forwarded-For` is not being read correctly. The plan must include a concrete verification step: make a request through the tunnel and confirm the logs show the actual client IP.

## What NOT to Include

- Do not modify the Dockerfile, Gunicorn config, or core docker-compose files (finalized in 8D-1) unless the real IP propagation analysis requires Nginx changes.
- Do not modify the CI pipeline or deploy script (finalized in 8D-2).
- Do not install Loki or Grafana. Only configure Promtail (the log scraper).
- Do not implement anything from 8E (Multi-User Groundwork).
- Do not build a monitoring dashboard or alerting system.
- Do not automate Cloudflare configuration via API or Terraform.
