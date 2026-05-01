# 13 -- External Attack-Surface Map (Section 1I)

Session S3 of the Shekel security audit (branch `audit/security-2026-04-15`).
This report catalogs every network entry point into Shekel from four zones:

1. **Public (WAN)** -- reachable from the internet via Cloudflare Tunnel.
2. **LAN** -- reachable from any device on the home LAN that resolves the
   Shekel hostname or the host IP.
3. **Container internal** -- reachable only from inside Docker networks the
   app container is on.
4. **Host loopback** -- bound only to `127.0.0.1` / `[::1]`; unreachable from
   anywhere except the host.

For each entry the four standard questions are answered: **auth gate**, **rate
limit**, **data exposed if the auth gate fails**, and **blast radius**. Where
a value cannot be verified from available evidence, the answer is **UNKNOWN**
and the unknown itself is recorded as a finding -- an auth gate that cannot be
verified from the repo is an auth gate that cannot be trusted.

## 0. Method and Evidence

### Commands executed (all read-only)

- `ss -tulpn` --> `scans/host-listening-ports.txt`
- `docker network ls` --> `scans/docker-networks.txt`
- `docker network inspect $(docker network ls -q)` --> `scans/docker-networks-detail.json`
- `nmap -sV -p 80,443,5432,5433,8000,8080,5000 127.0.0.1` --> `scans/nmap-localhost.txt`
- `docker exec shekel-prod-app sh -c 'cat /proc/net/tcp'`
- `docker exec shekel-prod-db sh -c 'cat /proc/net/tcp'`
- `docker inspect` on shekel-prod-app, shekel-prod-db, shekel-app (orphan),
  shekel-db (orphan), shekel-dev-db, shekel-dev-test-db, nginx, cloudflared.

### Configurations consulted

- `/opt/docker/docker-compose.yml` (homelab base stack, captured in
  `scans/homelab-compose.txt`)
- `/opt/docker/shekel/docker-compose.yml` (byte-identical to repo
  `docker-compose.yml`)
- `/opt/docker/shekel/docker-compose.override.yml` (captured in
  `scans/prod-compose-override.txt`)
- `/opt/docker/cloudflared/config.yml` (captured in
  `scans/cloudflared-ingress.txt`, redactions noted there)
- `/opt/docker/nginx/nginx.conf` (captured in `scans/shared-nginx.conf.txt`)
- `/opt/docker/nginx/conf.d/shekel.conf` (captured in
  `scans/shared-nginx-shekel-vhost.conf.txt`)
- `/opt/docker/nginx/conf.d/{jellyfin,immich,unifi}.conf` (read, not copied --
  they are not in the Shekel attack path but inform blast-radius analysis)
- Repo `nginx/nginx.conf`, `Dockerfile`, `gunicorn.conf.py`,
  `entrypoint.sh`, `docker-compose.yml`, `docker-compose.dev.yml`

### Critical architectural findings up front (context for everything below)

1. **The WAN path bypasses BOTH Nginx instances.**
   `cloudflared/config.yml` routes `shekel.saltyreformed.com` directly to
   `http://shekel-prod-app:8000`. Neither the repo's bundled
   `shekel-prod-nginx` container (disabled via
   `docker-compose.override.yml` profile) nor the shared
   `/opt/docker/nginx` container (used only for LAN access) is in the WAN
   path.
2. **The committed `nginx/nginx.conf` is documentation of dead
   architecture.** It is mounted by `shekel-prod-nginx`, which is
   disabled in production. All its limits, timeouts, header logic, and
   `set_real_ip_from` directives are inert for the live deployment.
3. **Shekel is ALSO reachable from the LAN** via the shared
   `/opt/docker/nginx` at `https://shekel.saltyreformed.com` (OPNsense DNS
   override resolves the hostname to the host's LAN IP).
4. **There is NO Cloudflare Access (Zero Trust) policy.** Confirmed by the
   developer. Flask's `login_required` is the only authentication gate
   between the public internet and any state-changing operation.
5. **Two orphan containers are live** on an orphan internal network
   (`shekel_backend` / `172.22.0.0/16`): `shekel-app` (unhealthy, 2673
   consecutive failures) and `shekel-db` (healthy, bound to the
   `shekel_pgdata` volume -- which likely contains obsolete real data
   from before the 2026-03-23 rename to `shekel-prod`).

---

## 1. Public (WAN via Cloudflare Tunnel)

### 1.1 `https://shekel.saltyreformed.com` --> `http://shekel-prod-app:8000`

- **Evidence:** `scans/cloudflared-ingress.txt` (ingress rule 2),
  `docker network inspect homelab` shows `cloudflared` (172.18.0.6) and
  `shekel-prod-app` (172.18.0.5) share the `homelab` network.
- **Path:** Public internet -> Cloudflare edge (TLS terminated) ->
  Cloudflare Tunnel (outbound-only) -> cloudflared container
  (172.18.0.6) -> plain HTTP on `homelab` network ->
  shekel-prod-app:8000 (Gunicorn).
- **Auth gate:** Flask `login_required` / `require_owner` decorators on
  each route. **No edge auth.** No Cloudflare Access policy
  (confirmed with developer). No basic auth. No IP allow-list.
- **Rate limit:** Flask-Limiter on login/MFA routes only. Memory-backed
  (`storage_uri="memory://"`, `app/extensions.py:31`), so per-worker
  counters -- effective rate = `limit x GUNICORN_WORKERS` (default 2).
  Cloudflare's free-tier WAF does have basic DDoS protection at the
  edge, but no custom rate rules.
- **Data exposed if gate fails:** everything Flask serves -- the owner
  user's full budget (every transaction, account balance, paycheck,
  debt, scenario, audit log), TOTP secret ciphertext if re-exposed on
  MFA endpoints, and any companion user's data scoped to that
  companion's permissions. Account takeover via successful login.
- **Blast radius:** Full app-level compromise. Cannot pivot directly to
  the host (app runs as non-root `shekel` user per
  `Dockerfile:31,47`), but **can reach the database on the backend
  network** using `DB_PASSWORD` from the container environment. A
  working Python RCE inside the app container = owner-level DB
  access.
- **Severity -- current deployment:** **Medium**. Shekel's own auth is
  strong (bcrypt + TOTP + Flask-Limiter + Flask-WTF CSRF, per S2
  findings). The Medium rating is because the rate-limit bypass (S1
  finding) applies here and because there is no defense-in-depth at
  the edge.
- **Severity -- if the app goes public-multi-user:** **High**. Zero
  edge auth means every `pip-audit` CVE in Flask or a dependency
  becomes remotely exploitable at scale.

### 1.2 `https://<jellyfin-hostname>` --> `http://jellyfin:8096`

- **Evidence:** `scans/cloudflared-ingress.txt` (ingress rule 1).
- **Included only because it shares the homelab network with
  shekel-prod-app.** If Jellyfin is compromised (e.g., a Jellyfin
  CVE), the attacker gets network access to `shekel-prod-app:8000`
  with no auth gate at the network boundary -- Shekel only sees a
  request from an IP on the homelab network (172.18.0.0/16), which
  is already inside Gunicorn's `forwarded_allow_ips` trust list.
- **Blast radius for Shekel:** Same as 1.1 (attacker speaks to
  Gunicorn with trusted proxy status), but the attacker must still
  defeat Flask's login.
- **Severity -- current deployment:** **Low** (requires a Jellyfin
  CVE AND successful Shekel auth bypass). Called out because it
  documents lateral movement, not because it is immediately
  exploitable.

### 1.3 `https://immich.saltyreformed.com` --> `http://immich_server:2283`

- **Evidence:** `scans/cloudflared-ingress.txt` (ingress rule 3).
- Same lateral-movement story as 1.2. immich_server is on the
  homelab network (172.18.0.7) and can reach `shekel-prod-app:8000`
  directly.
- **Severity -- current deployment:** **Low**, same reasoning as 1.2.

### 1.4 UNKNOWN -- Cloudflare Access policy verification

- **Claim:** developer says no Zero Trust policies.
- **Why still recorded as Low/Info:** the source of truth is the
  Cloudflare dashboard, not the repo. A future change in the
  dashboard could silently remove the only defense, or add one that
  a future auditor would miss.
- **Recommendation:** export Cloudflare account config (or
  screenshot-diff the Zero Trust page) into `scans/` on every
  audit, so the state is reproducible.

### WAN summary

| # | Entry | Auth gate | Rate limit | Data exposed | Blast radius |
|---|-------|-----------|------------|--------------|--------------|
| 1.1 | shekel.saltyreformed.com | Flask login only | Flask-Limiter (memory-backed, per-worker) | Full owner budget; companion data scoped | App-level, can reach DB via env |
| 1.2 | jellyfin.* (lateral) | Jellyfin auth, then Flask | Jellyfin's own, then Flask-Limiter | Same as 1.1 after defeating Jellyfin | Pivot from jellyfin CVE to Shekel |
| 1.3 | immich.saltyreformed.com (lateral) | Immich auth, then Flask | Immich's own | Same as 1.1 after defeating Immich | Pivot from immich CVE to Shekel |
| 1.4 | CF Access status | UNKNOWN | UNKNOWN | N/A (meta) | N/A (meta) |

---

## 2. LAN (host-bound ports reachable from any device on the home LAN)

### 2.1 Host TCP :80 (shared nginx, redirects to 443)

- **Evidence:** `scans/host-listening-ports.txt` line for
  `0.0.0.0:80`; `/opt/docker/nginx/conf.d/shekel.conf:7-11` shows
  the HTTP block does an unconditional 301 to HTTPS. Same for
  jellyfin, immich, unifi.
- **Auth gate:** none (redirect only).
- **Data exposed if gate fails:** none at HTTP; the Host header may
  leak which vhosts are configured, which is already public DNS
  information.
- **Blast radius:** negligible.
- **Severity:** **Info**.

### 2.2 Host TCP :443 --> `/opt/docker/nginx` --> `shekel-prod-app:8000`

- **Evidence:** `scans/shared-nginx-shekel-vhost.conf.txt`
  (`listen 443 ssl; http2 on; server_name shekel.saltyreformed.com;`).
- **Path:** LAN client -> OPNsense DNS override for
  `shekel.saltyreformed.com` returns the host LAN IP -> shared
  nginx :443 terminates TLS (Let's Encrypt certs) -> proxy_pass
  to `shekel-prod-app:8000` over the `homelab` docker network.
- **Auth gate:** Flask `login_required` (same as WAN). **No basic
  auth at nginx**, no IP allow-list, no CF Access (CF is not in
  this path at all).
- **Rate limit:** none at nginx (the shared `nginx.conf` has no
  `limit_req_zone`). Flask-Limiter applies downstream same as
  WAN.
- **Data exposed:** same as 1.1 -- full owner budget.
- **Blast radius:** same as 1.1, because the proxy_pass destination
  is the same Gunicorn port.
- **Security headers added at this layer for Shekel:** **none.** The
  jellyfin vhost adds `X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`, `Permissions-Policy` at lines 29-32, but
  `shared-nginx-shekel-vhost.conf.txt` does not. Shekel relies
  solely on Flask's headers (`app/__init__.py:409-428` per S2
  finding). Adding header-consistency here is a hardening win.
- **HSTS:** not set at nginx or Flask (confirmed S1 finding).
- **TLS config:** `ssl_protocols TLSv1.2 TLSv1.3;` and
  `ssl_ciphers HIGH:!aNULL:!MD5;`. Acceptable but weak compared to
  the Jellyfin vhost's explicit Mozilla Intermediate cipher suite
  (line 22 of `jellyfin.conf`). Not a finding on its own, but a
  consistency issue.
- **`client_max_body_size 5M`** at this layer. Mirrors the
  disabled bundled Nginx. Protects against oversized uploads
  arriving from the LAN.
- **Severity -- current deployment:** **Low** (LAN trust model is
  tight -- your devices only; IoT on a separate VLAN). Becomes
  **Medium** if untrusted devices ever join the LAN (guest, work,
  compromised IoT pivot).

### 2.3 Host TCP :5432 --> `shekel-dev-db` (Postgres)

- **Evidence:** `scans/host-listening-ports.txt` line for
  `0.0.0.0:5432`; `scans/nmap-localhost.txt` fingerprint
  `PostgreSQL DB 16.11 - 16.12`; `docker inspect shekel-dev-db`
  shows `ports=map[5432/tcp:[{invalid IP 5432}]]` and
  `docker-compose.dev.yml:36` declares `- "5432:5432"`.
- **Auth gate:** Postgres `md5`/`scram-sha-256` password auth.
  Credentials are **hardcoded in the committed
  `docker-compose.dev.yml:32-33`**: `shekel_user` /
  `shekel_pass`. These values are in the public repo already.
- **Rate limit:** none.
- **Data exposed if gate fails:** everything in the dev Shekel
  database -- whatever the developer has been testing with, which
  may include copies or restores of real data, test data with
  PII-shaped values, or seed data. A single `psql -h <host> -U
  shekel_user -d shekel` from any LAN device is a full dump.
- **Blast radius:** dev database. Not prod directly. **But:**
  - If dev shares volume snapshots with prod, or if the dev DB
    was ever seeded from a prod backup, PII leakage is direct.
  - The attacker can also write to the dev DB, which is not
    dangerous to prod but is a pivot for test-environment
    poisoning (e.g., plant an evil seed that the developer will
    run against prod by mistake).
- **Severity -- current deployment:** **Medium**. LAN trust model
  mitigates. If the LAN ever has an untrusted device, **High**.
- **Public deployment of Shekel itself does not change this** --
  dev is never meant to be public -- but the *existence* of
  `"5432:5432"` in `docker-compose.dev.yml` is a foot-gun because
  a future change in deployment architecture (e.g. running dev
  on a VPS for convenience) inherits the host bind silently.

### 2.4 Host TCP :5433 --> `shekel-dev-test-db` (Postgres)

- **Evidence:** `scans/host-listening-ports.txt` line for
  `0.0.0.0:5433`; `nmap-localhost.txt` shows PostgreSQL 16
  fingerprint; `docker-compose.dev.yml:55` declares `- "5433:5432"`.
- **Auth gate:** same as 2.3 -- hardcoded `shekel_user` /
  `shekel_pass`, same credentials.
- **Rate limit:** none.
- **Data exposed:** test DB is wiped between test runs and should
  contain no real data. Fixture data only. Low exposure, BUT the
  same credentials as the dev DB means if the attacker pivots to
  either one, they have access to both.
- **Blast radius:** same as 2.3.
- **Severity -- current deployment:** **Low** (test data), but
  rated **Medium** jointly with 2.3 because of credential reuse.

### 2.5 Host TCP :22 (sshd)

- **Evidence:** `scans/host-listening-ports.txt` line for
  `0.0.0.0:22` with `backlog 128` (distinct from the Docker
  bindings which have `backlog 4096`). Not Docker-provided.
- **Auth gate:** whatever `/etc/ssh/sshd_config` allows (UNKNOWN
  from the audit data -- Session S2's Lynis run may have
  reported this; see `scans/lynis.log`).
- **Rate limit:** `sshd`'s `MaxStartups` default or `fail2ban` if
  installed. UNKNOWN from data captured in S3.
- **Data exposed if gate fails:** full host shell access -->
  everything. This is the highest-blast-radius entry in the
  entire map.
- **Blast radius:** if the attacker has a shell as `josh`, they
  are in the `docker` group (based on `/opt/docker/nginx`
  directory listing showing `josh docker` ownership), which means
  they can `docker exec` into any container including
  `shekel-prod-db`, read all data, modify all data, and deploy
  new containers. Root not strictly required.
- **Severity -- current deployment:** depends entirely on sshd
  config, which is out of S3 scope. **Should be cross-referenced
  against `scans/lynis.log`** from Session S2 (host hardening).
  If SSH allows password auth and has no fail2ban, this is the
  single highest-risk item on the LAN. If key-only with strong
  keys and fail2ban, **Info**.
- **Public deployment note:** the developer's stated architecture
  says "Cloudflare Tunnel is the only WAN path, no router port
  forwarding." If that claim holds, :22 is not WAN-reachable.
  Recommend confirming the router firewall rule that drops
  inbound :22 from WAN.

### 2.6 Host TCP :8080 --> UniFi Network Controller UI

- **Evidence:** `scans/host-listening-ports.txt`; `nmap` reports
  `Apache Tomcat`, which is the UniFi controller's embedded web
  server. `/opt/docker/docker-compose.yml` unifi service declares
  `- "8080:8080"`.
- **Auth gate:** UniFi controller's own login.
- **Blast radius for Shekel:** none directly. But a compromised
  UniFi controller gives the attacker control of the home
  network, which in turn means they can ARP-spoof or DNS-spoof
  their way to MitM any LAN client of Shekel. Called out for
  threat-model purposes, not as a Shekel finding.
- **Severity (for this audit's scope):** **Info**.

### 2.7 Host UDP :3478 and :10001 (UniFi STUN/discovery)

- **Evidence:** `scans/host-listening-ports.txt`. UDP, UniFi.
- **Severity (for Shekel):** **Info**. Not in the Shekel path.

### LAN summary

| # | Entry | Auth gate | Rate limit | Data exposed | Blast radius |
|---|-------|-----------|------------|--------------|--------------|
| 2.1 | :80 (redirect) | N/A | N/A | None | None |
| 2.2 | :443 Shekel vhost | Flask login only | None at nginx | Full owner budget | App + DB via env |
| 2.3 | :5432 dev DB | Postgres pw (public creds) | None | Dev DB contents | Dev data (potential PII shape) |
| 2.4 | :5433 dev test DB | Postgres pw (public creds) | None | Test fixtures | Test data |
| 2.5 | :22 sshd | UNKNOWN (SSH config) | UNKNOWN | Full host | Entire machine; docker group = all containers |
| 2.6 | :8080 UniFi | UniFi auth | UniFi's | UniFi data (not Shekel) | Network control -> MitM LAN Shekel clients |
| 2.7 | UDP 3478,10001 | UniFi | N/A | UniFi | Same as 2.6 |

---

## 3. Container Internal (Docker networks)

Docker networks present on the host (`scans/docker-networks.txt`):

| Network | Subnet | Internal | Members (Shekel-relevant) |
|---------|--------|---------|---------------------------|
| homelab | 172.18.0.0/16 | no | nginx, jellyfin, unifi, shekel-prod-app, cloudflared, immich_server |
| shekel-prod_backend | 172.25.0.0/16 | yes | shekel-prod-app, shekel-prod-db |
| shekel-dev_default | 172.24.0.0/16 | no | shekel-dev-db, shekel-dev-test-db |
| shekel_backend (orphan) | 172.22.0.0/16 | yes | shekel-app, shekel-db |
| shekel_default (orphan) | ? | ? | ? (empty on inspect -- likely historical) |
| shekel_frontend (orphan) | ? | ? | ? (empty on inspect -- likely historical) |
| immich_default | 172.19.0.0/16 | no | immich_server, immich_postgres, immich_redis, immich_machine_learning |
| monitoring | 172.21.0.0/16 | no | (empty: 0 containers) |
| bridge / host / none | (Docker defaults) | -- | (default) |

### 3.1 `shekel-prod_backend` (internal, 172.25.0.0/16)

- **Members:** shekel-prod-app (172.25.0.3), shekel-prod-db (172.25.0.2).
- **Internal: true** -- no external Docker NAT. The DB cannot be
  reached from the host network; only other containers on
  `shekel-prod_backend` can speak to it.
- **Services listening:**
  - shekel-prod-app: `0.0.0.0:8000` (Gunicorn). Confirmed by
    `/proc/net/tcp` LISTEN `00000000:1F40`.
  - shekel-prod-db: `0.0.0.0:5432` (Postgres). Confirmed by
    `/proc/net/tcp` LISTEN `00000000:1538`.
- **Auth gate:** none at the network layer (shared-network ->
  directly reachable). Postgres has password auth
  (`POSTGRES_PASSWORD` from `.env`). Gunicorn has no network
  auth -- Flask routes enforce it.
- **Rate limit:** none at network layer.
- **Data exposed if app container is compromised:** the app
  container already has `DB_PASSWORD` in its environment
  (`docker-compose.yml:66`), so it **already has owner-level
  access to the prod DB**. A compromised app pod = full DB
  compromise, no further movement needed.
- **Blast radius:** This is the single most important lateral
  boundary in the system. Internal:true is doing its job (no
  host binding), but everything inside the boundary is trusted.
  Standard for Postgres; called out as Info.
- **Severity:** **Info** (correctly configured).

### 3.2 `homelab` (shared, 172.18.0.0/16)

- **Members:** cloudflared (172.18.0.6), nginx (172.18.0.2),
  jellyfin (172.18.0.3), unifi (172.18.0.4),
  **shekel-prod-app** (172.18.0.5), immich_server (172.18.0.7).
- **Internal: false** -- the network has a gateway (172.18.0.1)
  and containers can reach out; however, from the host's LAN
  side they are not reachable without either a port publish or
  an address on the 172.18.0.0/16 range.
- **This is the lateral-movement surface.** Any compromise of
  any of the 6 containers on homelab can directly speak to
  `shekel-prod-app:8000` (Flask) from an IP that Gunicorn
  trusts as a proxy (172.16.0.0/12 includes 172.18.0.x, per
  `gunicorn.conf.py:82`).
- **Auth gate to reach Shekel:** Flask `login_required` --
  same as WAN. Because Gunicorn trusts the `X-Forwarded-*`
  headers from any 172.16.0.0/12 IP, a malicious peer on
  homelab can inject headers like `X-Forwarded-For` to spoof
  client IPs in Flask logs / rate limiting. Severity of that
  specific attack: **Low** (IP spoofing within logs, not auth
  bypass -- Flask-Login uses the session cookie).
- **Rate limit:** none at network. Flask-Limiter applies.
- **Data exposed if a homelab peer is compromised:** same as
  1.1 if they also defeat Flask auth.
- **Blast radius in the Shekel direction:** full Shekel app
  compromise on successful Flask auth bypass.
- **Blast radius in the other direction:** if shekel-prod-app
  is compromised, the app container can scan homelab (TCP 80
  on nginx, TCP 8443 on unifi-via-docker-DNS, TCP 8096 on
  jellyfin, TCP 2283 on immich_server, TCP 2000 on cloudflared
  metrics). Cloudflared metrics on 0.0.0.0:2000 is NOT
  host-bound but IS reachable from homelab.
- **Severity -- current deployment:** **Medium** (the attack
  surface is wider than a Shekel-only deployment would require;
  jellyfin is internet-exposed and handles media parsing which
  is a historical CVE hotspot).
- **Recommended architectural change:** put Shekel on its own
  dedicated network segment (e.g. `shekel-proxy`) that
  cloudflared AND the shared nginx join, but keep
  jellyfin/immich/unifi off it. Cloudflared can sit on multiple
  networks; the homelab blob does not need to share a network
  with financial data.

### 3.3 `shekel-dev_default` (172.24.0.0/16)

- **Members:** shekel-dev-db, shekel-dev-test-db. No app (either
  not started, or bound to host port 5000 which `nmap` showed as
  `closed` -- so no app container currently on this network
  despite compose file defining one).
- **Access from host:** via the host port bindings (2.3 and 2.4).
- **Access from other Docker networks:** not directly, since this
  is a separate Docker bridge network.
- **Severity:** covered by 2.3 / 2.4 at the LAN layer.

### 3.4 ORPHAN -- `shekel_backend` (internal:true, 172.22.0.0/16)

- **Members:** shekel-app (172.22.0.2, unhealthy, 2673 consecutive
  healthcheck failures), shekel-db (172.22.0.3, healthy, postgres
  16-alpine, volume `shekel_pgdata`).
- **Context:** These containers are from the pre-2026-03-23 layout
  when the project was named `shekel` (no -prod suffix). After
  rename, the new `shekel-prod-*` containers were created and the
  old ones were NOT removed. `restart: unless-stopped` keeps them
  running. Docker has auto-restarted `shekel-app` continuously --
  it cannot connect to its database (probably expecting a name
  that no longer exists), so Gunicorn never starts. 22 hours of
  log noise and CPU churn to no purpose.
- **Auth gate:** Postgres pw on shekel-db. Gunicorn never starts
  on shekel-app, so no HTTP surface.
- **Rate limit:** N/A.
- **Data exposed:** the `shekel_pgdata` Docker volume is bound to
  shekel-db:/var/lib/postgresql/data. **If this volume was the
  live production data before the rename, it contains obsolete
  real budget data.** The developer should confirm and decide
  whether to `docker volume rm shekel_pgdata` or keep it as an
  archive. Internal:true means the volume is not network-reachable
  except from shekel-app (which cannot connect) -- so exposure is
  low in practice, but the data is still on-disk under
  `/var/lib/docker/volumes/shekel_pgdata/_data` and readable by
  anyone with docker group or root.
- **Blast radius:** low currently (no working app container) but
  the orphan volume is a latent data-at-rest risk.
- **Severity:** **Medium**. Orphan containers + orphan internal
  network + potentially-stale-real-data volume. Recommend
  `docker compose -p shekel down -v`-equivalent cleanup AFTER
  backing up or confirming the volume is empty/test-data.

### 3.5 ORPHAN -- `shekel_default`, `shekel_frontend`

- **Members:** none observed in `docker network inspect`.
  These are leftover networks from the old project name. Empty
  networks are harmless -- just clutter.
- **Severity:** **Info**.

### 3.6 `monitoring` (172.21.0.0/16, 0 containers)

- **Members:** none.
- Not referenced by any compose file seen in this audit. Either
  a placeholder for a future monitoring stack or leftover from
  an experiment.
- **Severity:** **Info**.

### 3.7 `immich_default`

- **Members:** immich_server, immich_postgres, immich_redis,
  immich_machine_learning.
- Separate from Shekel's networks. immich_server bridges to
  homelab, so it touches Shekel's attack path only via homelab
  (covered in 3.2).

### Container-internal summary

| # | Network | Auth gate | Data exposed if inside | Blast radius |
|---|---------|-----------|------------------------|--------------|
| 3.1 | shekel-prod_backend (internal) | Postgres pw / Flask login | Prod DB + app | Full Shekel (expected; correctly isolated) |
| 3.2 | homelab | Flask login (no network-layer gate) | Prod Shekel if Flask bypassed | Lateral to/from jellyfin, immich, unifi, nginx, cloudflared |
| 3.3 | shekel-dev_default | Postgres pw (public creds) | Dev DBs | Dev only (no app running) |
| 3.4 | shekel_backend (orphan) | Postgres pw | Obsolete data? | Latent; needs cleanup |
| 3.5-3.7 | Other | Various | None Shekel-related | Info |

---

## 4. Host (loopback only, `127.0.0.1` / `[::1]`)

### 4.1 `127.0.0.1:631` (CUPS printing)

- **Auth gate:** CUPS local-auth.
- **Blast radius for Shekel:** none. Called out for completeness.
- **Severity:** **Info**.

### 4.2 `127.0.0.1:43877` (unknown ephemeral-ish port)

- Random high port, bound on 127.0.0.1 only. Likely a local
  service (PipeWire, CUPS browsed, systemd-resolved, etc.).
- **Blast radius for Shekel:** none directly. Recommend
  identifying it during the next Lynis review.
- **Severity:** **Info**.

### 4.3 `127.0.0.1:2283` (Immich, loopback bind)

- Immich binds to `127.0.0.1:2283` on the host AND is reachable on
  homelab at `172.18.0.7:2283`. The loopback bind is for something
  host-local on the Arch host (perhaps a systemd unit) -- not a
  Shekel concern.
- **Blast radius for Shekel:** none directly.
- **Severity:** **Info**.

### 4.4 `[::1]:631` (CUPS IPv6)

- Same as 4.1.
- **Severity:** **Info**.

### Loopback summary

No Shekel-relevant services on loopback. No findings.

---

## 5. Consolidated Entry-Point Count

Across all four zones:

| Zone | Entries that could materially reach Shekel | Notes |
|------|-------------------------------------------|-------|
| Public (WAN) | 3 (1 direct, 2 lateral) | + 1 UNKNOWN (CF Access policy) |
| LAN | 2 (port 443 via shared nginx; port 22 sshd as "own the host") | + dev DB ports as pivot-risk |
| Container internal | 1 direct (shekel-prod_backend) + 1 shared (homelab) | + 1 orphan network |
| Host loopback | 0 | -- |

### Verified vs UNKNOWN auth gates

- **Verified:** Flask `login_required` on all Shekel routes (S1/S2
  reports); Postgres password auth on all DBs; SSH auth on port 22
  (existence verified, config not verified in S3).
- **UNKNOWN:**
  - **Cloudflare Access policy state** (claimed absent but not
    verifiable from repo).
  - **SSH config details** (`PermitRootLogin`,
    `PasswordAuthentication`, `AllowUsers`, fail2ban presence).
    Cross-check against `scans/lynis.log` in Session S8.
  - **Firewall state** between LAN and WAN (`firewalld`,
    `iptables`, or router rules). Confirmed only by developer's
    assertion that no port-forwarding exists on the router.

### Scariest single entry point

**Host TCP :22 (SSH).** If SSH is password-auth-capable and not
protected by fail2ban, this is the one entry that does not route
through Flask, the tunnel, or the shared nginx. A successful
compromise gets the attacker `docker exec` into every container
(josh is in the `docker` group), which means the prod database
is directly reachable with `docker exec -it shekel-prod-db psql
-U shekel_user -d shekel`. This is the highest-blast-radius entry
by a wide margin and should be verified against the sshd_config
before S3 is considered closed.

A close second is **the LAN bind of 5432/5433 with public
credentials from the committed compose file** -- because the
exploitation path is trivial (one `psql` command from a phone)
and the credentials are already public on GitHub.

---

## 6. Findings Arising From This Report (for S8 consolidator)

The following are new or sharpened attack-surface findings from
Session S3. Each is ready for `findings.md` with F-NNN numbering
assigned by the consolidator.

1. **[HIGH] Dev Postgres databases host-bound with public
   credentials.** `docker-compose.dev.yml:32-36,51-55` binds
   `5432:5432` and `5433:5432` with hardcoded `shekel_user` /
   `shekel_pass` credentials that are already in the public
   repository on GitHub. Any device on the LAN can `psql` into
   these databases. Current severity is **Medium** because the
   developer's LAN is trusted, but the attack is trivial and the
   LAN trust model is a single compromised device (phone, laptop,
   IoT pivot) away from becoming a High finding. Recommendation:
   change the dev compose to `- "127.0.0.1:5432:5432"` and
   `- "127.0.0.1:5433:5432"` so the bindings are loopback-only.
   Requires `flask run` to use `localhost` which is the default.

2. **[MEDIUM] Cloudflare Tunnel bypasses both Nginx layers on the
   WAN path.** `cloudflared/config.yml` routes
   `shekel.saltyreformed.com` directly to
   `http://shekel-prod-app:8000`, skipping the shared
   `/opt/docker/nginx` (which the LAN path uses) AND the repo's
   bundled `shekel-prod-nginx` (disabled via override). This means
   the 5M body limit, the 30s header/body timeouts, the
   `set_real_ip_from` chain, and every other protective directive
   in `/opt/docker/nginx/nginx.conf` + `shekel.conf` are inert
   for WAN traffic. Recommendation: route the cloudflared ingress
   through the shared nginx
   (`service: https://nginx:443` with `originServerName:
   shekel.saltyreformed.com` and `caPool` for the internal cert,
   OR `service: http://nginx:80` to let nginx's HTTPS-redirect
   block it and then retry). This closes the WAN/LAN parity gap.

3. **[MEDIUM] Repo `nginx/nginx.conf` is dead architecture.**
   `docker-compose.override.yml` disables the bundled
   `shekel-prod-nginx` service via `profiles: ["disabled"]`. The
   repo's `nginx/nginx.conf` is therefore never loaded in
   production. The repo ships an aspirational architecture while
   prod runs a different one. Recommendation: either (a) remove
   the bundled nginx from the repo and rely solely on the shared
   nginx (and document that in README), OR (b) stop using the
   shared nginx for Shekel and re-enable the bundled one. Either
   way, one source of truth.

4. **[MEDIUM] No security headers added by the shared nginx for
   Shekel.** `/opt/docker/nginx/conf.d/shekel.conf` sets zero
   `add_header` directives, while the sibling `jellyfin.conf`
   adds `X-Content-Type-Options`, `X-Frame-Options`,
   `Referrer-Policy`, and `Permissions-Policy`. Flask sets its
   own headers on Shekel responses (`app/__init__.py:409-428`),
   so this is defense-in-depth; still worth adding to protect
   against any Flask bypass (e.g., nginx serving a debug page on
   a 502 without going through the Flask handler). Matches the
   jellyfin vhost's example.

5. **[MEDIUM] Orphan `shekel-app` + `shekel-db` containers are
   live on host.** The pre-rename containers were never removed.
   `shekel-app` is unhealthy with 2673 consecutive healthcheck
   failures (22+ hours), burning CPU on restart loops and
   littering docker logs. `shekel-db` is healthy and holds the
   `shekel_pgdata` volume, which may contain stale real data
   from before the rename. Recommendation: back up the
   `shekel_pgdata` volume if it might be real, then
   `docker compose -p shekel down -v` to remove the orphans,
   orphan networks, and -- if the volume is confirmed expendable
   -- the old volume.

6. **[LOW] UNKNOWN -- Cloudflare Access policy state not
   verifiable from the repo.** Developer states none exist.
   Recommendation: export the Zero Trust configuration via
   `cloudflared access` or an API call and commit the artifact
   to `scans/` on every audit cycle. Empty output is still
   evidence.

7. **[LOW] Cloudflared metrics endpoint reachable from homelab
   network.** `cloudflared` command-line sets
   `--metrics 0.0.0.0:2000`, which is not host-bound but IS
   reachable from every other homelab peer (including
   shekel-prod-app). An attacker with code execution inside
   shekel-prod-app can poll cloudflared metrics (tunnel health,
   request counts), which may leak operational data. Low impact
   but easy to tighten: `--metrics 127.0.0.1:2000` inside the
   container makes it only locally reachable and still accessible
   for cloudflared's own purposes.

8. **[LOW] UniFi and other shared-nginx vhosts grant
   cross-service lateral movement.** If any of jellyfin, immich,
   or unifi is compromised via a CVE, the attacker lands on
   homelab (172.18.0.0/16) and is one Flask-auth-bypass away
   from Shekel. Recommendation as in F-3.2: isolate Shekel's
   proxy path onto its own docker network.

9. **[LOW] SSH configuration not verified in this session.**
   Cross-check against Lynis output
   (`scans/lynis.log`); verify PubkeyAuthentication-only,
   PermitRootLogin no, AllowUsers josh, fail2ban installed.
   This finding is a deferral-and-verify rather than a direct
   issue.

---

## 7. Scan-file Inventory (Produced in Session S3)

Files written to `docs/audits/security-2026-04-15/scans/`:

- `host-listening-ports.txt` -- `ss -tulpn` output.
- `docker-networks.txt` -- `docker network ls` output.
- `docker-networks-detail.json` -- full `docker network inspect` dump.
- `nmap-localhost.txt` -- `nmap -sV` on 127.0.0.1 for the probed port list.
- `cloudflared-ingress.txt` -- developer-pasted
  `/opt/docker/cloudflared/config.yml`.
- `prod-compose-override.txt` -- developer-pasted
  `/opt/docker/shekel/docker-compose.override.yml`.
- `homelab-compose.txt` -- developer-pasted
  `/opt/docker/docker-compose.yml`.
- `shared-nginx.conf.txt` -- copy of `/opt/docker/nginx/nginx.conf`.
- `shared-nginx-shekel-vhost.conf.txt` -- copy of
  `/opt/docker/nginx/conf.d/shekel.conf`.

---

End of 13-attack-surface.md.
