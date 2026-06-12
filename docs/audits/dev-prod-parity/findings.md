# Dev-vs-Prod Container Parity Audit -- 2026-06-12

Comparison of the production container stack (the `shekel-prod` compose
project at `/opt/docker/shekel`, fronted by the shared homelab nginx +
cloudflared) against the development setup (the `shekel-dev` compose
project from `docker-compose.dev.yml` plus the primary host `flask run`
workflow). Goal: make dev match prod as closely as possible now that the
app is stable, while keeping the divergences that exist for good reasons.

**Method.** 32-agent orchestrated audit: four parallel inventory readers
(prod compose + override, repo compose files + Dockerfile + entrypoint +
nginx configs + app config, live `docker inspect` state of all seven
shekel containers, repo-vs-host drift + dev workflow scripts), a synthesis
pass producing a difference matrix, then one adversarial verifier per
claimed difference plus a completeness critic. Result: 26 claimed
differences (20 confirmed, 4 adjusted, 2 refuted) + 8 critic additions.
Finding IDs below (`D__` matrix rows, `M__` critic additions) are stable
references to that run.

## Decision log

| Date | Decision |
|---|---|
| 2026-06-12 | **M01 timezone: approved.** Pin `TZ: America/New_York` on both app services (prod override + dev compose) rather than app-level timezone config or staying UTC. Rationale: single-operator app; one env line; `tzdata` verified present in the image (setting `TZ` flips `datetime.now()` from UTC to Eastern in the running container). This also explains a long-observed quirk: with the app on UTC, "today" flipped to tomorrow at 8pm Eastern (7pm in winter), skewing date-defaulted entries and staleness flags in that window. |
| 2026-06-12 | Parity plan steps 1-3 (below) reviewed by operator; implementation order TBD. |
| 2026-06-12 | **Containerized dev app is the PRIMARY workflow** (operator decision): full prod parity (entrypoint pipeline, `shekel_app` role, redis limits, hardening) with live reload; host `flask run` demoted to documented fallback. CLAUDE.md and the dev compose header updated. |
| 2026-06-12 | **D12 approved and implemented**: prod redis ACLs moved to a file-backed docker secret holding only a SHA-256 password hash; `SHEKEL_REDIS_PASSWORD` rotated in the same recreate. Residual accepted channel: the app container's env still carries the plaintext inside `RATELIMIT_STORAGE_URI` (closing it needs an entrypoint `_load_secret` extension = image change; revisit if/when wanted). |
| 2026-06-12 | **D10 declined** (operator decision): no memory cap on the dev app container. |
| 2026-06-12 | **M09 mechanism chosen**: systemd timer matching the `docker-backup.timer` convention, daily 03:30 (after the backup window so pruned rows are always in that night's snapshots), invoking the cleanup via `docker compose run` (a `docker exec` invocation gets placeholder env post-C-38 and cannot work -- verified). Requires the next image deploy to carry the `audit_cleanup.py` owner-role fix; `Persistent=true` self-heals until then. |

## Effective architecture

- **Prod** (`shekel-prod`): Postgres 18 (digest-pinned, TLS `ssl=on`,
  hardened), Redis 7.4 (rate-limit storage, ACL user scoped `~LIMITS*`,
  fail-closed), app (GHCR image digest-pinned + cosign-verified, Gunicorn,
  entrypoint pipeline: schemas -> role provisioning -> migrations -> seeds ->
  audit-trigger gate). No published ports; ingress is shared nginx +
  cloudflared over the external `shekel-frontend` bridge (172.32.0.0/24).
  Nightly `pg_dump` (retain 3) + restic. WUD watches images; Alloy ships
  logs to Loki.
- **Dev** (`shekel-dev`): dev Postgres (`127.0.0.1:5432`), test Postgres
  (`127.0.0.1:5433`, btrfs-reflink PGDATA), optional app container
  (`build: .`, same entrypoint as prod, Flask dev server on :5000, source
  bind-mounted for live reload). Primary daily workflow is host
  `flask run` against the containerized dev DB.

## A. Parity gaps -- close these

| ID | Sev | Finding | Status |
|---|---|---|---|
| D07 | high | Dev app container permanently `unhealthy`: inherits the image `HEALTHCHECK` probing `:8000` (Dockerfile:128-129) while the dev command runs Flask on `:5000`; no healthcheck override in `docker-compose.dev.yml`. | [x] fixed 2026-06-12: healthcheck override probes `:5000/health`; container reports healthy for the first time |
| D09 | high | Dev app publishes `"5000:5000"` = all interfaces, with `FLASK_DEBUG=1`: the Werkzeug debugger answers from any LAN device, bypassing the LAN allowlist the operator-private `shekel-dev` vhost enforces at nginx. Fix: publish `127.0.0.1:5000` + `172.32.0.1:5000` (the second keeps the dev vhost path working). | [x] fixed 2026-06-12: scoped binds live; LAN probe refused; container-visible client IP unchanged (172.24.0.1), so saved Playwright sessions survive strong session protection |
| D04 | high | No Redis in dev: `RATELIMIT_STORAGE_URI` falls back to `memory://` (app/config.py:229), so the prod rate-limit path (fail-closed Redis, ACL user, `~LIMITS*` key pattern, outage = 500s) is never executed before prod. Fix: add a redis service to `docker-compose.dev.yml` mirroring prod's command/ACL shape with a committed dev password, loopback-bound. | [x] fixed 2026-06-12: `shekel-dev-redis` mirrors prod ACL/flags; container app uses a hardcoded compose URI; host `flask run` gets its URI from the gitignored `.flaskenv` (NOT `.env` -- see the note below the table); LIMITS keys confirmed landing in Redis |
| D03 | high | Host `flask run` connects as the **owner** role, while prod runs as least-privilege `shekel_app` (entrypoint.sh:373; app/config.py:362-373 prefers `DATABASE_URL_APP`). A new table missing GRANTs works in dev, 500s in prod (seen once post-clone). Containerized dev app does NOT have this gap. | [x] resolved 2026-06-12 with a REVISED design: host `flask run` intentionally STAYS owner-role (documented divergence) because any dotenv `DATABASE_URL_APP` is also loaded by the Flask CLI and would make `flask db upgrade` run DDL as the DML-only role; the least-privilege parity path is the containerized dev app. Plus a real defect fixed: `scripts/init_database.py` / `scripts/build_test_template.py` popped `DATABASE_URL_APP` to force the owner role, but `config.py`'s `load_dotenv()` re-inserts a `.env` value into the popped key, silently defeating the override -- both now set the key to `""` (documented empty-as-unset, survives dotenv) |
| M01 | high | Prod app computed dates in UTC (no `TZ` env) vs Eastern on the dev host; 78 naive `date.today()`/`datetime.now()` call sites. See decision log. | [x] **fixed 2026-06-12**: `TZ: America/New_York` pinned in `deploy/docker-compose.prod.yml` + host override + `docker-compose.dev.yml`, containers recreated |
| D08 | med | Zero container hardening on dev vs prod's `cap_drop: [ALL]`, `no-new-privileges`, `read_only` rootfs + tmpfs, pinned non-root users. `read_only` is the bug-hiding one: code writing outside `/tmp`/state works in dev, crashes prod. Fix: harden the dev **app** service only (source bind mount stays rw, so live reload survives); leave dev/test DBs alone. | [x] fixed 2026-06-12: app service runs read_only + cap_drop ALL + no-new-privileges + /tmp tmpfs; boots healthy under it |
| D17 | med | Host `flask run` skips the entrypoint pipeline, including the audit-trigger count gate (entrypoint.sh:338-344) that refuses to boot when triggers are missing -- a trigger-dropping migration passes host dev, then blocks prod boot. Mitigation: boot the containerized dev app once after migration work (it runs the full pipeline), or a dev preflight script. | [ ] open |
| D01 | med | Gunicorn (multi-worker, request limits, 120s timeouts, `FORWARDED_ALLOW_IPS` trust) only ever runs in prod; both dev workflows use the Flask dev server. Habit: `docker compose -f docker-compose.yml -f docker-compose.build.yml up` before PRs touching request handling. | [ ] open (habit, not config) |
| D05 | med | No forwarded-header processing in dev (no gunicorn config, no ProxyFix by design): `remote_addr` is the proxy hop, not the client, for anything keyed on it (rate-limiter key, audit IP logging). Documented caveat; keep tunnel/nginx out of dev. | [ ] documented here, no config change planned |
| D15 | low | Dev compose pins postgres by tag only vs prod's tag+digest. Same digest today by pull timing only. Fix: pin the prod digest in dev, bump both in the same commit. | [x] fixed 2026-06-12: both dev DB services pin the prod digest (96d56f7f) |
| D18 | low | Dev `pgdata` volume is compose-managed: `docker compose -p shekel-dev down -v` would delete it, and it frequently holds a prod clone. Fix: `external: true` like prod's. Backups stay prod-only (dev data is a disposable clone by policy). | [x] fixed 2026-06-12: `external: true` + `name: shekel-dev_pgdata`; existing data reused (855 txns verified post-recreate) |
| D10 | low | No resource limits on dev containers (prod caps every service). Mostly keep divergent (test-db + `pytest -n 12` need headroom); optionally mirror the 1G memory cap on the dev app only so runaway memory surfaces locally. | [ ] optional, not taken |

## B. Repo-vs-host drift -- restore repo as canon

| ID | Sev | Finding | Status |
|---|---|---|---|
| D14 | med | Host `/opt/docker/shekel/docker-compose.yml` carried 5 hunks the repo lacked: newer postgres digest `96d56f7f...` (what actually runs) + WUD `wud.tag.include` labels on db/redis/app/nginx (redis label deliberately fences off Redis 8). | [x] closed 2026-06-12: repo backport + host sync done; all three host copies byte-identical to repo; compose config hashes confirmed unchanged vs running containers (no recreates) |
| M07 | low | `deploy/nginx-shared/nginx.conf` differed from host in 3 comment-only hunks; `conf.d/shekel.conf` byte-identical. Repo copy was the newer one. | [x] closed 2026-06-12: repo->host sync done; `nginx -t` clean |
| -- | -- | `deploy/docker-compose.prod.yml` vs host override must-match rule. | [x] byte-identical as of 2026-06-12 (incl. the D12 redis ACL-secret override) |

## C. Housekeeping (repo + host)

| ID | Sev | Finding | Status |
|---|---|---|---|
| M08/D12/D13 | med | Host-side secrets hygiene items at `/opt/docker/shekel` (residue files from the May secrets migration, one permissions/docs mismatch, and the redis password's visibility surface). Specifics deliberately kept out of this public doc: see the local ops note `/opt/docker/shekel/PARITY-NOTES.md`. | [x] closed 2026-06-12: residue deleted (after verifying the live restic passphrase lives outside the stack dir and the bak-only keys were legacy or empty), `SHEKEL_REDIS_PASSWORD` rotated, redis ACLs moved to a hash-only file-backed docker secret (see decision log), reconcile script hardened at the root |
| D26 | low | Orphaned pre-rename volumes `shekel_pgdata`, `shekel_applogs`, `shekel_static_files` (project `shekel`, March 2026) mounted by nothing. | [x] closed 2026-06-12: contents verified stale (pre-PG18 cluster superseded by the upgrade-day dump + a month of PG18 operation; one May 9 log file; empty static volume), all three removed |
| D25 | low | Stale docs: base compose db comment says `postgres:16-alpine` (image is 18); `deploy/README.md` layout table says the override joins "the homelab network" (it joins `shekel-frontend`). | [x] fixed 2026-06-12 (both, plus the matching 16-alpine comments in the prod override) |
| M06 | low | Stale repo artifacts describing the dead pre-container pipeline: `cloudflared/config.yml` (systemd-install template), `monitoring/` promtail docs (prod uses Alloy), `docker-compose.build.yml`'s monitoring-network coupling. Refresh or delete. | [x] fixed 2026-06-12: cloudflared header rewritten for the containerized topology, monitoring/README.md rewritten for the Alloy pipeline (promtail-config.yml deleted), build override reduced to the build key |
| M09 | med | **Discovered during M06 rework:** `scripts/audit_cleanup.py` (the `system.audit_log` retention job) was scheduled NOWHERE on the deployment host; prod's audit log grew unbounded. Two further defects surfaced implementing the fix: (a) the documented `docker exec` invocation has been broken since C-38 (exec'd processes get placeholder env, not entrypoint-loaded secrets); (b) the script lacked the owner-role override, and `shekel_app` deliberately has no DELETE on the audit log -- fixed with the same empty-string pattern as the other deployment scripts, verified end-to-end via `docker compose run` in dev (real DELETE as owner). | [x] script + units done 2026-06-12 (timer 03:30, after the backup window; reference units in `/opt/docker/shekel/`); [ ] operator must install: `sudo cp /opt/docker/shekel/shekel-audit-cleanup.{service,timer} /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now shekel-audit-cleanup.timer`; first successful prod run needs the next image deploy (the running image bakes the old script) |
| D22 | info | Surprise but harmless: **Flask serves `/static/` in prod too** -- shared nginx has no static location (proxies everything to the app), the bundled nginx is profile-disabled, so the entrypoint's static-volume copy (entrypoint.sh:347-352) is dead code in shared mode. Cache headers are correct either way (content-hash `v=` + 1-year immutable from the app's own hook). Candidate cleanup: remove the dead copy + volume in shared mode. | [ ] candidate |

## D. Intentional divergences -- keep these

- **D02** DevConfig cookie posture (no `Secure`/`__Host-` on plain-HTTP dev; audit F-112).
- **D11** Committed public dev credentials + hard-coded dev `APP_ROLE_PASSWORD` (F-081), loopback-only DB binds (F-057).
- **D05/M03** No tunnel/nginx in dev; the operator-private `shekel-dev` nginx vhost (LAN-allowlisted, TLS) exists for mobile testing and is deliberately un-mirrored to the repo.
- **D20** Dev app `restart: no` (a self-resurrecting debug server would be worse); dev DBs `unless-stopped` (correct -- test.sh and host flask depend on them).
- **D21** No subnet pinning in dev (no forwarded-header trust literals depend on dev subnets).
- **M02** `REGISTRATION_ENABLED` defaults true in dev, false+404 in prod (F-053). Test auth-route changes once with it false.
- **M05** test-db (non-durable knobs + btrfs reflink clone path) is dev-only by design. Note the host dependency: a fresh machine needs `sudo btrfs subvolume create /var/lib/shekel-test-pgdata && sudo chown 70:70 ...` before `./scripts/test.sh` works.
- **D24** `TOTP_ENCRYPTION_KEY` soft-defaults empty in dev; cloned users' 2FA needs the prod key or a reset (known clone caveat).
- **D06** No Postgres TLS in dev (loopback/bridge-local traffic; prod-only cert mount).

## E. Non-differences and verifier corrections -- do not re-flag

- **D16/M04** (refuted) Postgres page checksums are ON in both clusters (PG 18 initdb default; prod's explicit `--data-checksums` is redundant belt-and-braces).
- **D19** (adjusted) Dev container logs ARE rotation-bounded (host `/etc/docker/daemon.json`: json-file 10m x3) despite no compose logging keys -- the residual difference is only explicit per-service config vs the inherited daemon default. WUD also watches dev images (watch-by-default is on host-wide).
- **D22** (refuted) Static serving + cache headers are identical in dev and prod (see C above); the "nginx serves static with 7d expires" model only applies to the unused bundled mode.
- (D05 correction) Security headers come from Flask's unconditional `after_request` hook (app/__init__.py:171, :785-851) in ALL environments -- dev is not missing them; only nginx's duplicates on pre-Flask error pages differ.
- **D23** (confirmed, low) Image/runtime provenance is near-parity already: containerized dev builds the same Dockerfile from the working tree; venv pins matched `requirements.txt` exactly on spot-check; only drift is one Python patch level (venv 3.14.5 vs image 3.14.4 -- bump the Dockerfile `FROM` digests at next maintenance).

## Parity plan

1. [x] **Dev compose changes** (2026-06-12): D07 healthcheck, D09 scoped
   port binds, D04 redis service, D08 app-service hardening, D15 postgres
   digest pin, D18 `external: true` pgdata -- applied and verified live
   (all four dev containers healthy, LAN probe refused, LIMITS keys in
   Redis, client IP unchanged at 172.24.0.1 so saved sessions survive,
   855-transaction clone intact).  D03 took the revised owner-role design
   (see its register row).
2. [x] **Repo backport + host sync** (D14, M07): done 2026-06-12 via
   targeted `cp` + `cmp` (NOT the full reconcile script, whose
   .env-rewrite and snapshot steps are for the original migration and
   would recreate residue).
3. [x] **Host hygiene** (M08 residue + rotation, D26 volumes): done
   2026-06-12, folded together with the D12 ACL-secret migration into a
   single redis+app recreate.
4. [x] **Timezone** (M01): done 2026-06-12, see decision log.
5. [x] **Doc fixes** (D25, M06): done 2026-06-12.

New since the original plan: M09 (see register row -- units staged,
operator install command pending) and the decision-log entries of
2026-06-12 (containerized-primary workflow, D12 implemented, D10
declined).

The only remaining open items: the M09 `sudo` install one-liner, the
first post-deploy verification of the timer, and the accepted-residual
review hooks recorded in the decision log.

## Dev workflow notes (post-parity)

- `docker compose -f docker-compose.dev.yml up -d` now also starts
  `shekel-dev-redis`; host `flask run` reads its rate-limit URI from the
  gitignored `.flaskenv` (Flask-CLI-only -- pytest and the deployment
  scripts never see it; putting it in `.env` breaks
  tests/test_config.py's documented env assumptions via config.py's
  `load_dotenv()`).
- Rate limits in dev are now real: Redis counters survive the reloader,
  so the global default (`200/hour; 30/min` per client IP, with all
  host-originated traffic sharing the bridge-gateway IP bucket) can 429
  an aggressive automated crawl.  `/health` and `/static/` are exempt.
  That is prod behavior; throttle the crawl, not the limit.
- The dev app rootfs is read-only: app code that writes outside `/tmp`,
  the source tree, or its mounts now fails in dev exactly as it would
  in prod.
