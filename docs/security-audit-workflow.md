# Security Audit Workflow for Shekel

## Context

Shekel is a solo-maintained personal budget app that manages real financial
data, runs in a Docker container on a bare-metal Arch Linux machine, and is
exposed via Nginx (LAN) and Cloudflare Tunnel (WAN). There is no QA team, no
external code reviewer, and no CI security stage beyond pylint. The developer
wants to use Claude Code to perform an in-depth security audit and asked for a
recommended workflow: local vs. web, production access, three-phase structure,
and the best overall approach.

The existing codebase already has substantial security infrastructure (bcrypt,
TOTP/MFA, Flask-WTF CSRF, Marshmallow validation, `auth_helpers.py` ownership
pattern, `test_access_control.py` IDOR coverage, `test_adversarial/`), so an
audit is not starting from zero -- it needs to verify what exists works, find
gaps, and check areas that are hard to spot by writing new code (dependency
CVEs, config drift, crypto correctness, edge-case auth flows, infra hardening).

## Recommended Approach at a Glance

1. **Run the audit locally with Claude Code CLI** -- not on the web. This
   project's deployment (Docker on the same machine as dev) needs filesystem,
   bash, and Docker access that only the local CLI has.
2. **Use plan mode (read-only) for Phase 1 and Phase 2.** Plan mode is exactly
   the tool for audit phases: Claude explores, reasons, and writes a findings
   document -- but cannot modify code. Enter plan mode with `Shift+Tab` twice.
3. **Three phases, three (or more) sessions.** Audit -> Remediation plan ->
   Implement. Each phase is a separate Claude Code session on a dedicated
   branch. Phase 1 is split across 2-3 sessions for the comprehensive scope so
   each session starts with fresh context; rolling everything into one session
   anchors the auditor on whatever it looked at first.
4. **Parallel subagents by domain** -- map the attack surface in parallel, not
   sequentially, to keep the main context window clean.
5. **Dedicated audit branch:** `audit/security-2026-04-13` off of `main`. Do
   not conduct the audit on `dev` or `main`. The audit commits (findings.md,
   raw scan outputs, threat model) belong on the audit branch; Phase 3 fixes
   are PR'd to `dev` in the normal workflow.
6. **Production access is read-only and passive.** For this setup (Docker on
   the same machine), "production" is a container on localhost. Read container
   state with `docker inspect`, `docker logs`, and read-only `docker exec`.
   Never give Claude Code the ability to mutate container state, edit mounted
   config, or issue `docker compose` commands against prod without the
   developer driving.
7. **Comprehensive scope:** beyond the standard SAST + manual review, include
   full supply-chain audit, git-history secrets scan, container image
   vulnerability scan, host hardening benchmark, external attack-surface
   mapping, and a STRIDE threat model. Estimated total: 1-2 days of
   focused Claude Code work across the Phase 1 sessions.

## Why Local CLI (Not Web)

| Need                                   | Local CLI | Web (claude.ai/code) |
|----------------------------------------|-----------|----------------------|
| Read `app/` source                     | yes       | yes                  |
| Read `nginx/nginx.conf`, `cloudflared/config.yml`, `Dockerfile` | yes | yes |
| Run `pip-audit`, `bandit`, `semgrep`   | yes       | no (no local tools)  |
| Inspect running Docker container       | yes       | no                   |
| Run `docker-bench-security`            | yes       | no                   |
| Read `.env` to check real secrets      | yes       | no                   |
| Spawn parallel Explore subagents       | yes       | limited              |
| 1M context window (Opus 4.6)           | yes       | yes                  |

The web version has a legitimate secondary role (see "Optional: web for second
opinion"), but for a deep audit of an app + its local deployment, the CLI has
strictly more capability.

## Environment Preparation (before Phase 1)

Run these on the local machine, not in Claude Code, before starting the audit
session:

1. `git checkout main && git pull && git checkout -b audit/security-2026-04-13`
2. `mkdir -p docs/audits/security-2026-04-13/{scans,reports,sbom}`
3. Add `.audit-venv/` to `.gitignore` so the scratch venv is not committed.
4. Install Python scanners into a scratch venv (do NOT pollute
   `requirements.txt`):
   ```
   python -m venv .audit-venv
   source .audit-venv/bin/activate
   pip install pip-audit bandit semgrep cyclonedx-bom detect-secrets
   ```
5. Install host-level scanners (one-time, via pacman on Arch; if any are not
   in repos, use the AUR or run via Docker as noted in each phase):
   - `gitleaks` -- secret scanning over git history
   - `trivy` -- container image and filesystem vulnerability scanner
   - `lynis` -- Linux host hardening audit
   - (`docker-bench-security` is run as a container, no install needed)
6. Verify scanners can read the project:
   - `pip-audit --requirement requirements.txt --format json > /dev/null`
   - `gitleaks detect --no-banner --no-git --source /home/josh/projects/Shekel --report-format json --report-path /dev/null`
7. Confirm the prod Docker container is up (`docker ps | grep shekel`) so
   runtime checks work in Phase 1D.
8. Confirm Claude Code is set to Opus 4.6 1M context (`/model` should show
   the 1M variant) so a single audit session can hold the entire codebase
   plus scan outputs without compaction.

## Phase 1 -- Static + Runtime + Supply Chain + Threat Model (read-only, plan mode)

Phase 1 is split across two or three Claude Code sessions to keep each one
focused. Plan mode is on for all of them. None of these sessions write source
code -- they write `findings.md`, raw scan outputs, and the threat model
document.

**Session 1A:** subagent exploration + SAST (Phases 1A, 1B, 1C).
**Session 1B:** runtime audit + supply chain + git history + container image
+ host hardening (Phases 1D, 1E, 1F, 1G, 1H).
**Session 1C:** attack-surface map + threat model + consolidated findings
(Phases 1I, 1J, 1K).

It is fine to merge 1B and 1C into one session if context stays under ~60% --
the split exists so the consolidator in 1K starts fresh and is not biased by
whichever scanner turned up the most noise.

### 1A. Parallel OWASP-domain exploration (3 Explore subagents)

Run all three in parallel in one message:

- **Subagent A -- Identity & Access (OWASP A01, A07):** `app/routes/auth.py`,
  `app/services/auth_service.py`, `app/services/mfa_service.py`,
  `app/models/user.py`, `app/utils/auth_helpers.py`,
  `tests/test_integration/test_access_control.py`. Check: login/register flows,
  session invalidation, MFA bypass paths, backup-code exhaustion, "remember
  me" lifetime, `require_owner` fallback (note: `getattr(current_user,
  "role_id", owner_id)` defaults to OWNER when `role_id` is missing -- verify
  this cannot trigger in prod with a real user row).
- **Subagent B -- Data Layer & Injection (OWASP A03, A04):** every blueprint
  in `app/routes/`, `auth_helpers.py` callers, balance calculator, transfer
  service, recurrence engine. Check: every query filters by `user_id`; every
  soft-deletable query filters `is_deleted`; no raw SQL; joinedload usage on
  high-traffic paths; Marshmallow coverage on POST/PATCH/DELETE; Decimal
  construction from strings; transfer invariants hold under edge cases.
- **Subagent C -- Config, Secrets, Deploy (OWASP A02, A05, A08):** `config.py`,
  `Dockerfile`, `docker-compose.yml`, `docker-compose.dev.yml`,
  `nginx/nginx.conf`, `cloudflared/config.yml`, `gunicorn.conf.py`,
  `entrypoint.sh`, `.env.example`, `.env.dev`, `.gitignore`. Check: secret
  defaults, TOTP Fernet key handling, cookie flags, security headers (CSP,
  HSTS, X-Frame-Options -- currently the comment in nginx.conf says "Flask
  owns all other headers", verify Flask actually sets them), Nginx trusted
  proxy ranges, Cloudflare Tunnel ingress rules, Gunicorn
  `FORWARDED_ALLOW_IPS`, Docker non-root user.

Each subagent writes to its own file under `docs/audits/security-2026-04-13/reports/`
(e.g. `01-identity.md`, `02-data-layer.md`, `03-config-deploy.md`). Use
severity scale: **Critical / High / Medium / Low / Info**, mapped to OWASP ID
and the CWE if applicable.

### 1B. Automated SAST (main session drives bash)

Run these from the main Claude session (plan mode allows read-only bash --
these tools don't write code):

```
# Python SAST
bandit -r app/ -f json -o docs/audits/security-2026-04-13/scans/bandit.json
bandit -r app/ -f txt  -o docs/audits/security-2026-04-13/scans/bandit.txt

# Semgrep with OWASP rulepack
semgrep --config p/python --config p/owasp-top-ten --config p/flask \
    --json --output docs/audits/security-2026-04-13/scans/semgrep.json app/
semgrep --config p/python --config p/owasp-top-ten --config p/flask \
    app/ > docs/audits/security-2026-04-13/scans/semgrep.txt

# Dependency CVEs (quick first pass; deeper supply-chain audit in 1E)
pip-audit --requirement requirements.txt \
    --format json --output docs/audits/security-2026-04-13/scans/pip-audit.json
pip-audit --requirement requirements.txt \
    > docs/audits/security-2026-04-13/scans/pip-audit.txt
```

Claude reads the outputs and normalizes every finding into the same severity
schema as the subagent reports. Duplicate findings across tools are merged.
Low-confidence findings are flagged for human review.

### 1C. Manual deep dives (main session)

The subagents give breadth; these reads give depth on the highest-risk areas
that tools miss:

1. **Crypto correctness** -- read `mfa_service.py` end-to-end. Verify Fernet
   key rotation story, TOTP time-window, backup code entropy, constant-time
   comparison for secrets.
2. **Transfer invariants** -- read `app/services/transfer_service.py` and
   trace every code path that can mutate a transfer or shadow. CLAUDE.md
   lists five invariants -- verify each is enforced by a code path, not by
   convention.
3. **Balance calculator** -- read `app/services/balance_calculator.py`.
   Confirm it queries only `budget.transactions`, never `budget.transfers`.
4. **Open-redirect helper** -- read `_is_safe_redirect()` in auth routes.
   Common miss: newline/tab injection, IDN homograph.
5. **Rate limiting** -- confirm Flask-Limiter decorators on login, register,
   password-reset, MFA verification. Check the storage backend isn't memory
   (lost on restart, bypassable).
6. **Audit log completeness** -- verify every state-changing route emits a
   `log_event()` call.

### 1D. Runtime audit of the production container

Still read-only. From the main Claude session:

```
docker inspect shekel-app --format '{{json .Config}}' > docs/audits/security-2026-04-13/scans/container-config.json
docker inspect shekel-app --format '{{json .HostConfig}}' > docs/audits/security-2026-04-13/scans/container-hostconfig.json
docker exec shekel-app id                               # confirm non-root
docker exec shekel-app env                              # confirm env drift
docker exec shekel-app ls -la /home/shekel/app
docker logs shekel-app --tail 500 > docs/audits/security-2026-04-13/scans/container-logs.txt
docker network inspect shekel_backend shekel_frontend > docs/audits/security-2026-04-13/scans/networks.json
```

Then do a **drift check**: compare `nginx/nginx.conf` in the repo to what the
Nginx container is actually serving (`docker exec shekel-nginx cat /etc/nginx/nginx.conf`).
If they differ, that is itself a finding. Repeat the comparison for
`gunicorn.conf.py` and any other mounted config.

### 1E. Supply chain audit

Beyond the quick `pip-audit` from 1B, do a deeper supply-chain pass:

```
# Generate a CycloneDX SBOM for the prod requirements
cyclonedx-py requirements --of JSON \
    -o docs/audits/security-2026-04-13/sbom/sbom.json requirements.txt
cyclonedx-py requirements --of XML  \
    -o docs/audits/security-2026-04-13/sbom/sbom.xml  requirements.txt

# Resolved transitive tree (catches indirect deps that pip-audit may underweight)
pip install --dry-run --report \
    docs/audits/security-2026-04-13/sbom/resolved-tree.json -r requirements.txt

# Re-scan the SBOM with trivy for a second opinion (different DB than pip-audit)
trivy sbom docs/audits/security-2026-04-13/sbom/sbom.json \
    --format json --output docs/audits/security-2026-04-13/scans/trivy-sbom.json
trivy sbom docs/audits/security-2026-04-13/sbom/sbom.json \
    > docs/audits/security-2026-04-13/scans/trivy-sbom.txt
```

Then Claude reads:
1. Every direct dependency in `requirements.txt` -- last release date, GitHub
   stars, maintenance signal, security advisory history. Flag any unmaintained
   or single-maintainer packages.
2. Pin discipline -- every direct dep must be pinned to an exact version
   (`==`, not `>=` or `~=`). A loose pin in `requirements.txt` is itself a
   finding because it makes builds non-reproducible and dependency confusion
   attacks easier.
3. License audit -- flag any GPL/AGPL-licensed deps if the project does not
   intend to be GPL.
4. Cross-check `pip-audit` and `trivy` outputs -- discrepancies are findings
   in their own right.

### 1F. Git history secrets scan

The repo currently has `.env` gitignored, but a secret may have been committed
historically and never rotated. Scan the entire git history:

```
gitleaks detect --no-banner --source /home/josh/projects/Shekel \
    --report-format json \
    --report-path docs/audits/security-2026-04-13/scans/gitleaks.json
gitleaks detect --no-banner --source /home/josh/projects/Shekel \
    --report-format sarif \
    --report-path docs/audits/security-2026-04-13/scans/gitleaks.sarif

# Second-opinion scan with detect-secrets (different ruleset)
detect-secrets scan --all-files --baseline \
    docs/audits/security-2026-04-13/scans/detect-secrets-baseline.json
```

Any detected secret is a **Critical finding** even if the secret looks fake or
old -- the remediation is to rotate AND remove from history (`git filter-repo`
or BFG). Note clearly that this requires force-pushing rewritten history,
which is destructive and must be done by the developer.

### 1G. Container image vulnerability scan

Scan the actual built Docker image, not just `requirements.txt`. The image
includes the Python runtime, Debian/Alpine packages, and any layers added by
the multi-stage build:

```
# Find the running image tag
IMAGE=$(docker inspect shekel-app --format '{{.Config.Image}}')

# OS + language vuln scan
trivy image "$IMAGE" \
    --format json \
    --output docs/audits/security-2026-04-13/scans/trivy-image.json
trivy image "$IMAGE" \
    --severity CRITICAL,HIGH,MEDIUM \
    > docs/audits/security-2026-04-13/scans/trivy-image.txt

# Misconfiguration scan of the Dockerfile + compose files
trivy config /home/josh/projects/Shekel \
    --format json \
    --output docs/audits/security-2026-04-13/scans/trivy-config.json
trivy config /home/josh/projects/Shekel \
    > docs/audits/security-2026-04-13/scans/trivy-config.txt
```

Findings include OS-level CVEs in the base image (Python 3.14 slim ->
Debian), Python wheels with CVEs that pip-audit missed, and Dockerfile
anti-patterns (root user, no HEALTHCHECK, etc. -- this project already
addresses these but the scan confirms).

### 1H. Host hardening + Docker daemon benchmark

The container runs on a bare-metal Arch Linux host that is also the dev
machine. Hardening here matters because if the host is compromised, the
Cloudflare Tunnel becomes a pivot.

```
# Linux host hardening audit
sudo lynis audit system --quick \
    --logfile docs/audits/security-2026-04-13/scans/lynis.log \
    --report-file docs/audits/security-2026-04-13/scans/lynis-report.dat

# Docker daemon CIS benchmark
docker run --rm --net host --pid host --userns host --cap-add audit_control \
    -v /var/lib:/var/lib:ro -v /var/run/docker.sock:/var/run/docker.sock:ro \
    -v /usr/lib/systemd:/usr/lib/systemd:ro -v /etc:/etc:ro \
    docker/docker-bench-security \
    > docs/audits/security-2026-04-13/scans/docker-bench.txt
```

Lynis findings to look at first:
- Kernel hardening flags (`kernel.kptr_restrict`, `kernel.dmesg_restrict`,
  `kernel.unprivileged_userns_clone`)
- File permissions on `/home/josh/projects/Shekel/.env` (must be `600`)
- SSH config if SSH is exposed to LAN
- Firewall state (nftables/iptables rules)
- Automatic security updates -- Arch is rolling, so the hardening question is
  whether the host is updated regularly

Docker-bench findings to look at first: containers running as root, missing
`--security-opt no-new-privileges`, missing seccomp profile, mounted Docker
socket, exposed Docker daemon TCP socket.

### 1I. External attack-surface mapping

Document everything that is reachable from the public internet, the LAN, or
inside the Docker network. This is read-only and Claude does most of it from
config files plus a few `nmap`/`ss` calls on the host.

```
# What ports are listening on the host?
ss -tulpn > docs/audits/security-2026-04-13/scans/host-listening-ports.txt

# What does the local Docker network look like?
docker network ls > docs/audits/security-2026-04-13/scans/docker-networks.txt
docker network inspect $(docker network ls -q) \
    > docs/audits/security-2026-04-13/scans/docker-networks-detail.json

# Local-host scan of the running app (LAN side)
nmap -sV -p 80,443,5432,8000 127.0.0.1 \
    > docs/audits/security-2026-04-13/scans/nmap-localhost.txt

# Cloudflare Tunnel ingress -- which hostnames map to which services
cat /home/josh/projects/Shekel/cloudflared/config.yml \
    > docs/audits/security-2026-04-13/scans/cloudflared-ingress.txt
```

Claude then writes `docs/audits/security-2026-04-13/reports/04-attack-surface.md`
with sections:

- **Public (WAN via Cloudflare Tunnel):** every hostname, the service it maps
  to, the auth gate at the edge (Cloudflare Access? none?), and the OWASP
  attack categories that apply.
- **LAN (Nginx on the host):** every port reachable from the LAN, the auth
  gate at Nginx, and what an attacker on the LAN could touch.
- **Container internal (Docker backend network):** services that are *not*
  externally reachable but are reachable from a compromised app container
  (the database, mainly).
- **Host (loopback only):** services bound to 127.0.0.1 that should not be
  reachable from outside the host.

For each entry, list a) the auth gate, b) the rate limit (or "none"), c) the
data exposed if the auth gate fails, d) the blast radius.

### 1J. Threat model (STRIDE)

Write `docs/audits/security-2026-04-13/reports/05-threat-model.md`. STRIDE
covers six categories per asset: Spoofing, Tampering, Repudiation, Information
disclosure, Denial of service, Elevation of privilege.

The assets to model:

1. **User account** -- email, password hash, TOTP secret, backup codes,
   session cookie
2. **Financial data** -- transactions, balances, transfers, paychecks, debt
   accounts
3. **Anchor balance** -- the single source of truth for projections; if
   tampered the entire app is wrong
4. **Audit log** -- if tampered, repudiation becomes possible
5. **Cloudflare Tunnel credentials** -- pivot to taking over the public
   endpoint
6. **Docker socket / host shell** -- pivot to entire host

For each asset, the model answers: what can an external attacker do? what
can a logged-in attacker (companion role) do? what can a compromised
dependency do? what can a compromised host do?

The threat model is the deliverable that tells the developer what they
should be most worried about and where the remaining audit effort should
focus -- it is also what stays useful long after this audit is done.

### 1K. Consolidate into `findings.md`

The final deliverable of Phase 1 is one file:
`docs/audits/security-2026-04-13/findings.md`. Done in a fresh Claude Code
session (Session 1C) so the consolidator is not anchored on whichever scanner
or subagent ran last.

The session loads:
- All five subagent / domain reports from `reports/`: `01-identity.md`,
  `02-data-layer.md`, `03-config-deploy.md`, `04-attack-surface.md`,
  `05-threat-model.md`
- All scanner outputs from `scans/` (bandit, semgrep, pip-audit, trivy
  image, trivy SBOM, trivy config, gitleaks, detect-secrets, lynis,
  docker-bench, container inspect, network inspect, nmap)
- The SBOM and resolved-tree from `sbom/`

Then it normalizes everything into one report with this structure:

```
# Shekel Security Audit -- 2026-04-13

## Summary
- Scope: Shekel commit <sha>, audit branch audit/security-2026-04-13
- Tool versions: bandit X.Y, semgrep X.Y, pip-audit X.Y, trivy X.Y,
  gitleaks X.Y, lynis X.Y, docker-bench (date)
- Duration: total wall-clock, per-phase breakdown
- Counts: N critical, N high, N medium, N low, N info
- Top three risks (one paragraph each)

## Threat Model Summary
- Brief asset/attacker matrix from 1J, with pointers into Findings

## Findings
### F-001: <short title>
- Severity: High
- OWASP: A01:2021 -- Broken Access Control
- CWE: CWE-284
- Phase / source: 1A subagent A | 1B bandit | 1F gitleaks | etc.
- Location: app/routes/transactions.py:142
- Description: ...
- Evidence: <code snippet or tool output>
- Impact: ...
- Recommendation: ...
- Status: Open

### F-002: ...

## Accepted Risks
Findings the developer is intentionally NOT fixing, with rationale. (Empty
at the end of Phase 1 -- populated during Phase 2 triage.)

## Scan Inventory
A table mapping every scan output file to which findings it generated,
proving nothing was discarded.
```

Every finding carries file + line. Evidence is quoted. Every scan output
file is referenced in at least one finding or in the Scan Inventory -- if a
scanner produced output but no findings, the inventory says so explicitly,
which proves the scan was actually read.

No vague "consider reviewing X" entries -- each finding is either a concrete
defect with a fix or is demoted to Info.

## Phase 2 -- Remediation Plan (fresh session, plan mode)

**Session 2: brand new Claude Code session.** Load only `findings.md` plus the
critical files it references -- do NOT reuse Phase 1 context. The fresh
context prevents the planner from anchoring on whatever the auditor was
focused on last.

Produce `docs/audits/security-2026-04-13/remediation-plan.md`:

1. Triage every finding: **Fix now / Fix this week / Fix this month / Accept**.
2. Group fixes by domain so a single PR doesn't touch ten unrelated files.
3. For each fix: the change sketch, tests to add, regression risk, rollback
   plan, estimated effort.
4. Order by severity + blast radius. Criticals jump to the front even if the
   fix is harder.
5. Dependencies between fixes are called out -- e.g. "F-007 depends on F-003
   because both touch `auth_service.login`".

The remediation plan also notes which findings are intentionally deferred and
why, so the deferred list is not forgotten.

## Phase 3 -- Implementation (normal workflow)

**Session 3 (and onward): normal Claude Code sessions, NOT plan mode.** Work
through the remediation plan in order. For each group:

1. Create a feature branch off `dev`: `fix/security-F003-F005-rate-limit`.
2. Implement the fix with tests (regression tests assert the vulnerable path
   now fails).
3. Re-run the Phase 1 scanners on the touched area:
   `bandit -r app/routes/auth.py` etc. The finding should disappear.
4. Run targeted test suites for the modified files, not the full suite.
5. Full suite runs once per batch of fixes, before the PR, per
   `docs/testing-standards.md`.
6. Update `findings.md` to mark the finding as Fixed with the commit SHA and
   PR number.
7. Merge into `dev`, then into `main` on the normal cadence.

Never batch unrelated criticals into one PR -- if one fix has to be reverted,
the unrelated fixes revert with it.

## Optional: web (claude.ai/code) for Second Opinion

The web version cannot replace the local CLI for this project, but it can
usefully provide an independent second pass on `findings.md`. After Phase 1:

1. Push `audit/security-2026-04-13` to GitHub (private repo).
2. Open a web Claude session pointed at the branch.
3. Ask it to *only review* `findings.md` against the source files the findings
   reference, and flag (a) false positives, (b) missed high-severity items in
   the same files, (c) over-severe ratings.
4. Its comments feed back into Phase 2 triage.

Do not use the web version for the primary audit. Do not give it write access.

## Production Access -- Guardrails

For this deployment (Docker on the same Arch machine):

- **Allowed in Phase 1:** `docker inspect`, `docker logs`, `docker exec <ctr>
  <read-only command>`, reading mounted configs, reading the running Nginx
  config.
- **Allowed in Phase 3:** deployment via the existing `scripts/deploy.sh`
  *only when the developer runs it*. Claude Code should not be the one
  executing the deploy script -- it should write the fix, run tests, and hand
  off.
- **Forbidden always:** `docker exec` with write side effects, `docker compose
  down/up` on prod, editing mounted config files, touching `.env`, restarting
  the container, modifying Cloudflare Tunnel config, running `flask db
  upgrade` against the prod database. These require the developer to drive.

If the developer wants a remote pair of hands for the production machine,
SSH-only access via a scoped user (no sudo, no docker group, no write on
`/home/josh/projects/Shekel`) is the safer pattern -- but given the container
is local, there is no need to set that up for this audit.

## Preliminary Findings (to Verify in Phase 1)

These surfaced during the planning exploration and deserve priority in Phase 1
so they aren't forgotten:

1. **`.env.dev` is tracked and stale.** `.env` is correctly gitignored (line
   18), but `.env.dev` is committed. Its current contents are safe placeholder
   values (`dev_password_change_me`, `dev-secret-key-not-for-production`) so
   this is not a leak -- but the file references `FLASK_APP=src/flask_app/app.py`,
   a path that does not exist in this project. The file is either dead code
   or out of sync. Resolve: delete or bring in sync.
2. **`require_owner` fallback.** `app/utils/auth_helpers.py:52` uses
   `getattr(current_user, "role_id", owner_id)` which *defaults to owner role*
   when `role_id` is absent. Documented as "safe behavior for test fixtures."
   Verify in Phase 1 that a real production user row cannot have
   `role_id IS NULL` (NOT NULL constraint on the column), otherwise a row
   with a null role silently becomes an owner.
3. **Nginx owns almost no security headers.** `nginx.conf` sets only
   `X-Content-Type-Options: nosniff` on static files and comments that "Flask
   owns all other headers." Verify Flask actually sets CSP, HSTS,
   X-Frame-Options, Referrer-Policy, Permissions-Policy on every response --
   and does so with correct values (`HSTS max-age` >= 6 months, CSP without
   `unsafe-inline`).
4. **Dependency freshness.** `requirements.txt` looks current but has not been
   audited against CVE feeds in this session. `pip-audit` in Phase 1B will
   catch this, so no action needed now -- just flagged as a likely source of
   findings.

## Critical Files Any Auditor Must Read

- `app/config.py` -- secret and cookie handling
- `app/__init__.py` -- session/login-manager wiring, security headers if any
- `app/routes/auth.py` -- auth flows including open-redirect helper
- `app/services/auth_service.py` and `app/services/mfa_service.py` -- crypto
- `app/utils/auth_helpers.py` -- ownership pattern (every route's safety net)
- `app/services/transfer_service.py` -- financial invariants
- `app/services/balance_calculator.py` -- query scoping
- `nginx/nginx.conf`, `gunicorn.conf.py`, `Dockerfile`, `docker-compose.yml`,
  `cloudflared/config.yml`, `entrypoint.sh` -- deploy surface
- `.env.example`, `.env.dev`, `.gitignore` -- secret hygiene
- `requirements.txt` -- dependency CVEs
- `tests/test_integration/test_access_control.py`,
  `tests/test_adversarial/test_hostile_qa.py` -- what's already protected

## Verification the Audit Was Thorough

Phase 1 is complete when ALL of these hold:

- [ ] `findings.md` has entries (or an explicit "nothing found") for every
      OWASP A01-A10 category
- [ ] All five domain reports exist under `reports/`: `01-identity.md`,
      `02-data-layer.md`, `03-config-deploy.md`, `04-attack-surface.md`,
      `05-threat-model.md`
- [ ] All scanner outputs exist under `scans/`: `bandit.json`, `semgrep.json`,
      `pip-audit.json`, `trivy-image.json`, `trivy-sbom.json`,
      `trivy-config.json`, `gitleaks.json`, `detect-secrets-baseline.json`,
      `lynis-report.dat`, `docker-bench.txt`, `container-config.json`,
      `container-hostconfig.json`, `container-logs.txt`, `networks.json`,
      `host-listening-ports.txt`, `nmap-localhost.txt`
- [ ] SBOM exists under `sbom/sbom.json` and has been read by Claude (not
      just generated and ignored)
- [ ] Every medium-or-higher finding has a file:line reference and quoted
      evidence
- [ ] The four preliminary findings listed above are each either confirmed
      or explicitly resolved
- [ ] Runtime drift check (repo configs vs container configs) has been run
      for both Nginx and Gunicorn configs
- [ ] STRIDE threat model in `reports/05-threat-model.md` covers all six
      assets (account, financial data, anchor balance, audit log, Cloudflare
      Tunnel creds, host shell)
- [ ] `findings.md` Scan Inventory section accounts for every file in
      `scans/`, proving each scan was actually consumed
- [ ] Discrepancies between `pip-audit` and `trivy sbom` are explicitly
      reconciled (either both are right and the finding is real, or one is
      a false positive with reasoning)

Phase 2 is complete when `remediation-plan.md` exists with a prioritized,
triaged entry for every finding -- including a rationale for any finding
moved to "Accept."

Phase 3 is complete when `findings.md` shows every non-accepted finding as
Fixed with a commit SHA, the latest SAST scans on `main` no longer match the
previously-open finding patterns, and a regression test exists for every
Critical or High finding.
