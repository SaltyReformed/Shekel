# 08 -- Runtime Audit of the Production Container

Audit session: S2, Section 1D. Branch: `audit/security-2026-04-15`.
Date: 2026-04-16. Auditor: Claude (Opus 4.6).

## Summary

- **Containers inspected:** `shekel-prod-app`, `shekel-prod-db`, `nginx`
  (standalone), plus stale containers `shekel-app`, `shekel-db`,
  `shekel-nginx`
- **Networks inspected:** `shekel-prod_backend`, `homelab`
- **Drift checks performed:** nginx.conf (repo vs running), gunicorn.conf.py
  (repo vs running), docker-compose.yml (repo vs /opt/docker/shekel/),
  docker-compose.override.yml (exists only in prod)
- **Image recorded for 1G:** `ghcr.io/saltyreformed/shekel:latest`
  (revision `91f2627`)
- **Finding count:** 0 Critical / 4 High / 4 Medium / 5 Low / 3 Info

## Architecture discovered

The production stack does NOT match the architecture described in the
repo's `docker-compose.yml` header comment. The repo describes:

```
[Client] --> [shekel-prod-nginx :80] --> [shekel-prod-app :8000] --> [shekel-prod-db :5432]
Networks: frontend (bridge) / backend (bridge, internal)
```

The actual production architecture is:

```
[WAN Client] --> [Cloudflare Tunnel] --> [cloudflared]
                                              |
                                         (homelab net)
                                              |
[LAN Client] --> [nginx :443 TLS] -----> [shekel-prod-app :8000]
                                              |
                                      (shekel-prod_backend, internal)
                                              |
                                       [shekel-prod-db :5432]
```

The difference exists because `/opt/docker/shekel/docker-compose.override.yml`
(not in the repo) disables the bundled nginx service and puts the app on
the shared `homelab` network where a standalone nginx reverse proxy serves
all homelab services.

### Container inventory

| Container | Image | Status | Networks | Notes |
|-----------|-------|--------|----------|-------|
| shekel-prod-app | ghcr.io/saltyreformed/shekel:latest | healthy | shekel-prod_backend, homelab | Current production app |
| shekel-prod-db | postgres:16-alpine (16.13) | healthy | shekel-prod_backend | Current production database |
| nginx | nginx:latest (1.29.8) | running | homelab | Shared homelab reverse proxy |
| cloudflared | cloudflare/cloudflared:latest | running | homelab | Cloudflare Tunnel |
| shekel-app | a637395b362b | **unhealthy** | shekel_backend | STALE -- old naming |
| shekel-db | 20edbde7749f | healthy | shekel_backend | STALE -- old naming |
| shekel-nginx | nginx:1.27-alpine | **Created** (never started) | -- | STALE -- disabled by override |

### Network inventory

| Network | Internal? | Members | Purpose |
|---------|-----------|---------|---------|
| shekel-prod_backend | **true** | shekel-prod-app, shekel-prod-db | Database isolation |
| homelab | **false** | nginx, shekel-prod-app, cloudflared, immich_server, jellyfin, unifi | Shared service mesh |
| shekel_backend | false | shekel-app (stale), shekel-db (stale) | Old/stale network |
| shekel_frontend | -- | empty | Old/stale, never used |
| shekel_default | -- | (not inspected) | Old/stale |

---

## Findings

### F-D-01: Flat shared network exposes app to co-tenant compromise

- **Severity:** High
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-653 (Improper Isolation or Compartmentalization)
- **Location:** `/opt/docker/shekel/docker-compose.override.yml:4-6`,
  `homelab` network
- **Evidence:** `shekel-prod-app` is on the `homelab` network
  (172.18.0.0/16) alongside `immich_server`, `jellyfin`, `unifi`,
  `cloudflared`, and `nginx`. The network is NOT internal. Any
  compromised co-tenant container can reach `shekel-prod-app` directly
  on port 8000, bypassing nginx entirely -- no TLS, no nginx security
  headers, no rate limiting.
- **Impact:** A vulnerability in jellyfin, immich, or unifi (all
  internet-facing services with their own attack surfaces) becomes a
  direct attack vector against Shekel's gunicorn on port 8000.
- **Recommendation:** Create a dedicated `shekel-frontend` bridge
  network containing only nginx and shekel-prod-app. Remove
  shekel-prod-app from the `homelab` network. Nginx proxies to the app
  via the dedicated frontend network; other services cannot reach the
  app at all.

### F-D-02: Production nginx config not version-controlled (repo nginx.conf is dead code)

- **Severity:** High
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-1188 (Initialization with Hard-Coded Network Resource Configuration)
- **Location:** Repo: `nginx/nginx.conf` (194 lines). Production:
  `/opt/docker/nginx/nginx.conf` + `/opt/docker/nginx/conf.d/shekel.conf`
- **Evidence:** The repo's `nginx/nginx.conf` is a Shekel-specific config
  with Cloudflare real-IP handling, JSON logging, gzip compression, static
  file serving, and security headers. The running nginx uses a completely
  different generic homelab config from `/opt/docker/nginx/`. The Shekel
  server block lives at `/opt/docker/nginx/conf.d/shekel.conf`. None of
  these production files are in the repo.

  Key differences:

  | Feature | Repo | Production | Impact |
  |---------|------|------------|--------|
  | TLS termination | none (Cloudflare handles it) | Let's Encrypt, TLS 1.2/1.3 | Prod is better |
  | HTTP to HTTPS redirect | none | yes (301) | Prod is better |
  | CF-Connecting-IP real IP | configured | absent | Real IP from tunnel not extracted |
  | Static file serving | `location /static/` with cache + X-Content-Type-Options | absent (all to gunicorn) | Feature gap |
  | Gzip | configured | absent | Performance gap |
  | Proxy timeouts | 120s read/send | nginx defaults (60s) | May cause 504s on slow ops |
  | HSTS | absent | absent | Both missing |
  | server_tokens | not set | off | Prod is better |

- **Impact:** Changes to the repo's nginx config will have no effect in
  production. The production config cannot be rebuilt from the repo alone.
  This is an unreproducible deployment -- a disaster recovery or new
  deployment would produce a different configuration.
- **Recommendation:** Move the production nginx configuration into the
  repo (either as a separate `nginx-homelab/` directory or by updating
  the existing `nginx/` to match production). Version-control the
  override file.

### F-D-03: docker-compose.override.yml not in the repo

- **Severity:** High
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-1188
- **Location:** `/opt/docker/shekel/docker-compose.override.yml`
- **Evidence:** This file disables the bundled nginx service and adds
  the `homelab` external network to the app:
  ```yaml
  services:
    app:
      networks:
        - backend
        - homelab
    nginx:
      profiles: ["disabled"]
  networks:
    homelab:
      external: true
  ```
  It is not committed to the repository. Without it, the
  `docker-compose.yml` would start the bundled nginx on a dedicated
  frontend network -- a more secure architecture.
- **Impact:** The production deployment cannot be reproduced from the
  repo. The override silently changes the security architecture.
- **Recommendation:** Commit the override file to the repo (possibly
  as `docker-compose.prod.yml`), or refactor the base
  `docker-compose.yml` to match the actual production architecture.

### F-D-04: SEED_USER_PASSWORD persists in running container environment

- **Severity:** High
- **OWASP:** A07:2021 Identification and Authentication Failures
- **CWE:** CWE-798 (Use of Hard-Coded Credentials)
- **Location:** Container env: `SEED_USER_PASSWORD=<REDACTED>`
  (`docker exec shekel-prod-app env`)
- **Evidence:** The production user's seed password is visible in the
  container's environment variables. It is accessible to:
  - Any process inside the container (via `/proc/1/environ`)
  - Any host user who can run `docker inspect` or `docker exec`
  - Docker's JSON log driver if env vars are logged
  - Container orchestration tools that display env vars

  If this is still the user's active password (unchanged since
  seeding), it is a live credential leak.
- **Impact:** An attacker who gains read access to the container's
  environment (via a compromised co-tenant on the homelab network,
  a Docker API exposure, or host access) obtains the production
  user's password.
- **Recommendation:** (1) Change the production user's password
  immediately if it matches the seed password. (2) Remove
  SEED_USER_PASSWORD and SEED_USER_EMAIL from docker-compose.yml
  environment after initial setup -- the seed script should only run
  once and does not need these values persisted in the container env.
  (3) Consider using Docker secrets (for Swarm) or a mounted file
  instead of env vars for credentials.

### F-D-05: Stale containers from old naming scheme still running

- **Severity:** Medium
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-1295 (Debug Messages Revealing Unnecessary Information)
- **Location:** Containers `shekel-app` (unhealthy), `shekel-db`
  (running), `shekel-nginx` (Created). Networks `shekel_backend`,
  `shekel_frontend`, `shekel_default`.
- **Evidence:** Three containers and three networks from the old naming
  scheme remain on the host. `shekel-app` is actively running (though
  unhealthy), consuming resources. `shekel-db` is healthy and running
  a PostgreSQL instance.
- **Impact:** Stale running containers expand the attack surface:
  `shekel-app` (unhealthy) may be running an older, unpatched version
  of the application. `shekel-db` is a running PostgreSQL instance
  that may contain old data. Even if isolated on `shekel_backend`,
  they are unnecessary attack surface.
- **Recommendation:** Stop and remove `shekel-app`, `shekel-db`, and
  `shekel-nginx`. Remove the stale networks. Verify no data in
  `shekel-db` is needed before removal.

### F-D-06: REGISTRATION_ENABLED=true in production

- **Severity:** Medium
- **OWASP:** A07:2021 Identification and Authentication Failures
- **CWE:** CWE-284 (Improper Access Control)
- **Location:** Container env: `REGISTRATION_ENABLED=true`
- **Evidence:** The production container has open registration. Any
  client that can reach the application can create a new account.
  The app is reachable from:
  - The public internet via Cloudflare Tunnel
  - The LAN via nginx on ports 80/443
- **Impact:** Unauthorized users can create accounts and access the
  budgeting application. For a personal finance app intended for
  single-user use, this is an unnecessary exposure.
- **Recommendation:** Set `REGISTRATION_ENABLED=false` in the
  production .env after the intended users are created. Or gate
  registration behind an invite code.

### F-D-07: No `--security-opt=no-new-privileges` on any container

- **Severity:** Medium
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-250 (Execution with Unnecessary Privileges)
- **Location:** `SecurityOpt: null` in shekel-prod-app, nginx, and
  shekel-prod-db HostConfig
- **Evidence:** None of the three production containers set
  `--security-opt=no-new-privileges`. This flag prevents processes
  inside the container from gaining additional privileges via setuid
  binaries or other mechanisms.
- **Impact:** If an attacker gains code execution inside a container,
  they can potentially escalate privileges via setuid binaries in the
  base image.
- **Recommendation:** Add `security_opt: ["no-new-privileges:true"]`
  to all services in docker-compose.yml.

### F-D-08: No capability dropping on any container

- **Severity:** Medium
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-250 (Execution with Unnecessary Privileges)
- **Location:** `CapAdd: null, CapDrop: null` in all three containers
- **Evidence:** Docker's default capability set includes capabilities
  the application does not need (e.g., NET_RAW, SYS_CHROOT,
  MKNOD, AUDIT_WRITE). Best practice is `cap_drop: [ALL]` with
  selective add-back of only what's needed.
- **Impact:** A broader capability set gives an attacker more options
  if they achieve code execution inside the container.
- **Recommendation:** Add `cap_drop: [ALL]` and `cap_add:` with only
  required capabilities to each service.

### F-D-09: No resource limits on any container

- **Severity:** Low
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-770 (Allocation of Resources Without Limits or Throttling)
- **Location:** `Memory: 0, PidsLimit: null` in all three containers
- **Evidence:** No memory limits, no PID limits, no CPU limits on any
  container. A runaway process or fork bomb could consume all host
  resources, affecting all other services on the machine.
- **Impact:** Denial of service against the host and all co-located
  services (immich, jellyfin, unifi).
- **Recommendation:** Set `mem_limit` and `pids_limit` on each
  service. Suggested: `mem_limit: 512m` for app, `mem_limit: 256m`
  for db (adjust based on observed usage).

### F-D-10: No Docker log rotation configured

- **Severity:** Low
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-400 (Uncontrolled Resource Consumption)
- **Location:** `LogConfig: {"Type": "json-file", "Config": {}}` on
  all containers
- **Evidence:** Docker's json-file log driver is configured with no
  `max-size` or `max-file` limits. Container logs can grow without
  bound, potentially filling the host disk.
- **Recommendation:** Add to docker-compose.yml per service:
  ```yaml
  logging:
    driver: json-file
    options:
      max-size: "10m"
      max-file: "3"
  ```

### F-D-11: Container root filesystem is writable

- **Severity:** Low
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-732 (Incorrect Permission Assignment for Critical Resource)
- **Location:** `ReadonlyRootfs: false` on shekel-prod-app
- **Evidence:** The app container's root filesystem is writable.
  An attacker who gains code execution could modify application
  files.
- **Impact:** Low because the container is ephemeral (rebuilt on
  deploy), but a read-only rootfs with writable tmpfs for /tmp
  and named volumes for logs/static would reduce the window.
- **Recommendation:** Add `read_only: true` to the app service
  with `tmpfs: ["/tmp"]`.

### F-D-12: Unnecessary files in production image

- **Severity:** Low
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-1295
- **Location:** `docker exec shekel-prod-app ls -la /home/shekel/app`
- **Evidence:** The production image contains files not needed at
  runtime: `.claude/` (IDE config), `amortization-fix.patch` (32KB
  patch), `cloudflared/`, `nginx/` (other services' config),
  `requirements-dev.txt`, `pytest.ini`, `diagnostics/`,
  `monitoring/`, `scripts/`.
- **Impact:** Increases image size and provides an attacker with
  additional information about the project structure, development
  tooling, and other services' configuration.
- **Recommendation:** Add a `.dockerignore` that excludes non-runtime
  files, or use a multi-stage build that copies only the app directory.

### F-D-13: User email addresses logged on every container start

- **Severity:** Low
- **OWASP:** A09:2021 Security Logging and Monitoring Failures
- **CWE:** CWE-532 (Insertion of Sensitive Information into Log File)
- **Location:** Container logs, lines 24 and 43
- **Evidence:** Every container start logs user email addresses
  during the seed/migration phase:
  `User 'josh@REDACTED' already exists (id=1).  Skipping.`
  `Seeding tax data for user: josh@REDACTED (id=1)`
  `Seeding tax data for user: klgrubb@REDACTED (id=2)`
  These are PII that persist in Docker logs.
- **Recommendation:** Redact email addresses in seed script output,
  or suppress "already exists" messages after initial setup.

---

## Info findings

### F-D-I1: TLS is terminated at nginx (contradicts repo documentation)

The repo's `nginx/nginx.conf` line 9 states "TLS is NOT terminated here.
Cloudflare Tunnel handles TLS." In practice, the production nginx
terminates TLS with Let's Encrypt certificates
(`/opt/docker/nginx/conf.d/shekel.conf`), provides HTTP-to-HTTPS
redirect, and serves HTTP/2. This is BETTER than what the repo
describes, but the documentation is wrong.

### F-D-I2: Deployment secrets passed via environment variables

SECRET_KEY, TOTP_ENCRYPTION_KEY, DATABASE_URL (with password),
DB_PASSWORD, and SEED_USER_PASSWORD are all passed as Docker
environment variables. This is standard Docker practice for a
self-hosted deployment. Docker secrets (Swarm-only) or mounted files
are more secure alternatives, but for a single-machine compose
deployment, env vars are accepted practice. Noted for completeness.

### F-D-I3: Gunicorn confirms Flask-Limiter rate limit multiplication

`GUNICORN_WORKERS=2` with `storage_uri="memory://"` (confirmed in S1
report 01-identity.md, F-A-05) means each worker maintains independent
rate-limit counters. The documented rate limit of 5/15min on `/login`
is effectively 10/15min worst case. This was already a finding in S1;
the runtime confirms the worker count.

---

## Drift check summary

| Config file | Repo path | Container path | Drift? | Severity |
|-------------|-----------|----------------|--------|----------|
| nginx.conf | `nginx/nginx.conf` | `/etc/nginx/nginx.conf` (via `/opt/docker/nginx/nginx.conf`) | **TOTAL** -- completely different files | High (F-D-02) |
| shekel server block | n/a (does not exist in repo) | `/etc/nginx/conf.d/shekel.conf` | N/A -- prod-only file | High (F-D-02) |
| gunicorn.conf.py | `gunicorn.conf.py` | `/home/shekel/app/gunicorn.conf.py` | **None** -- identical | -- |
| docker-compose.yml | `docker-compose.yml` | `/opt/docker/shekel/docker-compose.yml` | **None** -- identical | -- |
| docker-compose.override.yml | n/a (does not exist in repo) | `/opt/docker/shekel/docker-compose.override.yml` | N/A -- prod-only file | High (F-D-03) |

---

## Preliminary finding cross-references

| PF# | Status in 1D | Notes |
|-----|-------------|-------|
| #3 (HSTS missing) | **Reinforced** -- HSTS is absent in both the repo nginx.conf AND the production shekel.conf. However, production DOES terminate TLS (Let's Encrypt), making HSTS relevant and actionable. |
| #4 (Flask-Limiter memory) | **Confirmed** at runtime -- GUNICORN_WORKERS=2 means effective 2x rate limits (F-D-I3). |
| #1 (.env.dev stale) | Not in scope for 1D (no config drift check against .env.dev). |
| #2 (role_id fallback) | Not in scope for 1D. |
| #5 (dependency freshness) | Deferred to 1E/1G. |
| #6 (Fernet rotation) | Not in scope for 1D. |

---

## Scan artifacts produced

| File | Size | Contents |
|------|------|----------|
| `scans/container-config.json` | Container Config JSON for shekel-prod-app |
| `scans/container-hostconfig.json` | HostConfig JSON for shekel-prod-app |
| `scans/container-logs.txt` | Last 500 lines of shekel-prod-app logs |
| `scans/networks.json` | Network inspect for shekel-prod_backend + homelab |
| `scans/nginx-config.json` | Container Config JSON for nginx |
| `scans/nginx-hostconfig.json` | HostConfig JSON for nginx |
| `scans/db-config.json` | Container Config JSON for shekel-prod-db |
| `scans/db-hostconfig.json` | HostConfig JSON for shekel-prod-db |
