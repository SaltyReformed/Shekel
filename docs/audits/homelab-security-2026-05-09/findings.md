# Homelab Security Audit -- 2026-05-09

## Scope

This document inventories the operator's homelab Docker stack at
`/opt/docker/` and assesses each container's security posture against
the same OWASP Top 10 / ASVS L2 controls used in the Shekel-specific
audit at `docs/audits/security-2026-04-15/`. The Shekel app itself was
the focus of the April audit; this companion audit covers the
**other** services that share the maintainer's homelab host so the
operator has a single triage queue for all infrastructure.

Audited surfaces, in order:

1. **Network topology** -- which Docker bridges exist and which
   containers attach to each.
2. **Cloudflare Tunnel ingress** -- where each public hostname
   terminates.
3. **Shared Nginx** -- the per-vhost reverse proxy enforcement
   (security headers, real-client-IP resolution, TLS, ACLs).
4. **Per-container hardening** -- `cap_drop`, `no-new-privileges`,
   `read_only` filesystem, image version pinning.

Scoping note: this audit does not cover the OPNsense firewall, the
host-side sshd configuration (covered separately by F-066 / F-130 in
the Shekel audit), the LAN allow/deny chain that fronts cloudflared,
or the off-host backup destinations. The recommendations focus on the
class of fixes that mirror Shekel's Commit C-33 work.

## Methodology

Read-only inspection of the following files, all on the audit host:

- `/opt/docker/docker-compose.yml` -- jellyfin, unifi, calibre-web,
  nginx, cloudflared.
- `/opt/docker/immich/docker-compose.yml` -- immich-server,
  immich-machine-learning, immich_redis, immich_postgres.
- `/opt/docker/cloudflared/config.yml` -- public ingress map.
- `/opt/docker/nginx/nginx.conf` -- shared http block + resolver +
  server_tokens.
- `/opt/docker/nginx/conf.d/jellyfin.conf`,
  `immich.conf`, `unifi.conf`, `calibre-web.conf`, `ntfy.conf`,
  `grafana.conf`, `shekel.conf`, `00-stub-status.conf` -- each per-
  service vhost.

The monitoring stack (`/opt/docker/monitoring/docker-compose.yml`)
was not enumerated in detail because the audit tooling's read
permission was deliberately scoped away from that path; the only
hardening signal recorded for the monitoring containers comes from
the inline mention in the existing `/opt/docker/AUDIT.md` that
"security_opt: no-new-privileges:true (where compatible with
privileged: true) and cap_drop: ALL with minimal cap_add" is the
stated convention. Confirming the observed state in the running
containers is recommended as a follow-up.

The Shekel app's own files at `/opt/docker/shekel/` were already
audited in `docs/audits/security-2026-04-15/`. Findings F-015,
F-020, F-063, F-064, F-129, and F-156 (closed by Commit C-33 on
2026-05-09) are referenced from this document where they have
parity equivalents on the homelab side.

## Container Inventory

### `homelab` Docker bridge (172.18.0.0/16)

| Container | Image | Network membership | Public ingress | Hardening |
|---|---|---|---|---|
| jellyfin | jellyfin/jellyfin:latest | homelab | nginx + cloudflared | cap_drop ALL + DAC_OVERRIDE; no-new-privileges; not read_only |
| unifi | jacobalberty/unifi:latest | homelab | nginx (LAN-only) + host:8080 | none (operator comment justifies omission via gosu / MongoDB) |
| calibre-web | crocodilestick/calibre-web-automated:v4.0.6 | homelab | nginx + cloudflared | cap_drop ALL + 5 caps; no-new-privileges; not read_only |
| ntfy | (from sibling compose; not enumerated) | homelab | nginx (LAN-only) + cloudflared | not inspected at this audit |
| nginx | nginx:latest | homelab | host:80 + host:443 (LAN reverse proxy) | cap_drop ALL + 5 caps; no-new-privileges; not read_only |
| cloudflared | cloudflare/cloudflared:latest | homelab | host process (no port mappings; outbound tunnel only) | cap_drop ALL; no-new-privileges; not read_only |
| immich-server | ghcr.io/immich-app/immich-server:${IMMICH_VERSION:-release} | immich_default + homelab | nginx + cloudflared (also 127.0.0.1:2283 host bind) | none |
| immich-machine-learning | ghcr.io/immich-app/immich-machine-learning:${IMMICH_VERSION:-release}-openvino | immich_default | none | none |
| immich_redis (valkey) | docker.io/valkey/valkey:9@sha256:546304... | immich_default | none | none |
| immich_postgres | ghcr.io/immich-app/postgres:14-vectorchord0.4.3-pgvectors0.2.0@sha256:bcf63357... | immich_default | none | none |
| **shekel-prod-app** | **ghcr.io/saltyreformed/shekel:latest** | **shekel-frontend + backend (post-C-33)** | nginx + cloudflared via shekel-frontend | cap_drop ALL + minimal caps; no-new-privileges; not read_only |
| shekel-prod-db | postgres:16-alpine | shekel-prod_backend | none | not separately inspected here |
| shekel-prod-redis | redis:7.4-alpine | shekel-prod_backend | none | cap_drop ALL; no-new-privileges; read_only + tmpfs |

### `monitoring` Docker bridge

Loki, Prometheus, Grafana, Alloy, exporters. Not enumerated in this
audit. The shared nginx serves grafana.saltyreformed.com via the
LAN-only ACL `allow 10.10.101.0/24; deny all;` in
`/opt/docker/nginx/conf.d/grafana.conf`. Monitoring traffic enters
the Shekel-side observability pipeline as documented elsewhere.

## Findings

The numbering scheme is `H-NNN` for "homelab" to avoid overlap with
the Shekel audit's `F-NNN` series. `Status: Open` is the default;
`Recommendation` is the proposed remediation. Severity follows the
same scale as the Shekel audit (Critical / High / Medium / Low /
Info). Each finding cross-references the Shekel-side equivalent
where one exists.

---

### H-001: Cloudflare Tunnel bypasses Nginx for every WAN-exposed service

- **Severity:** Medium (per-service); High when aggregated
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-653 (Improper Compartmentalization)
- **ASVS:** V14.4.1
- **Cross-reference:** Identical pattern to Shekel finding F-063
  (Fixed in C-33 for `shekel.saltyreformed.com` only).
- **Location:** `/opt/docker/cloudflared/config.yml:11-39`
- **Description:** Five public hostnames are routed straight at the
  origin container's HTTP port, bypassing the shared
  `/opt/docker/nginx` reverse proxy. The C-33 commit fixed this gap
  for Shekel's hostname but only Shekel's; the same pattern persists
  for jellyfin, immich, calibre-web, and ntfy. WAN traffic for those
  hostnames does not benefit from any of the controls Nginx already
  enforces on the LAN path: `client_max_body_size`, slowloris-style
  request / body / send timeouts, `set_real_ip_from` /
  `real_ip_header` (see H-002), the per-vhost security headers (see
  H-003), per-vhost ACLs (currently scoped to LAN-only services
  like grafana and ntfy, but the precedent matters), and JSON
  per-request access logs.
- **Evidence:**
  ```yaml
  # /opt/docker/cloudflared/config.yml:12-27
  ingress:
    - hostname: jellyfin.saltyreformed.com
      service: http://jellyfin:8096
      ...
    - hostname: shekel.saltyreformed.com
      service: http://shekel-prod-app:8000      # F-063, fixed in C-33
    - hostname: immich.saltyreformed.com
      service: http://immich_server:2283
    - hostname: books.saltyreformed.com
      service: http://calibre-web:8083
      ...
    - hostname: ntfy.saltyreformed.com
      service: http://ntfy:80
      ...
  ```
- **Impact:** A WAN client of any hostname above can submit a
  request body of arbitrary size, hold connections open against the
  origin's default timeouts, and bypass any Nginx-layer header
  enforcement. The aggregate exposure is highest for immich
  (`client_max_body_size 50000M` on the LAN vhost is intentional
  for 4K video uploads, but absent on the WAN path Nginx never
  sees) and calibre-web (Kobo store-API shim has its own
  `client_max_body_size 200M` on the `/kobo/` LAN location).
- **Recommendation:** Mirror the C-33 pattern for the four
  remaining hostnames. Either:
  - (a) per-hostname: `service: http://nginx:80` with the existing
    LAN vhosts preserved as-is. Cloudflared resolves `nginx` via
    Docker DNS on the `homelab` bridge.
  - (b) bulk fold: a single `service: http://nginx:80` rule
    matching all `*.saltyreformed.com` hostnames, with the
    catch-all rule kept last. Simpler ingress block at the cost of
    losing per-hostname `originRequest` tuning (jellyfin's
    `keepAliveTimeout: 90s` and ntfy's 5-minute keepalive in
    particular have functional reasons to stay).
  Option (a) is the lower-risk choice; replicate the existing
  `originRequest` tuning verbatim under each hostname.
  Restart cloudflared with `cd /opt/docker && docker compose up -d
  cloudflared`. Verify with `docker exec cloudflared cloudflared
  tunnel info`.
- **Status:** Open

---

### H-002: Shared Nginx has no `set_real_ip_from` / `real_ip_header` for cloudflared

- **Severity:** Medium
- **OWASP:** A05:2021 Security Misconfiguration; A09:2021 Logging
  Failures
- **CWE:** CWE-348 (Use of Less Trusted Source); CWE-778
  (Insufficient Logging)
- **ASVS:** V14.4.1; V7.1.1
- **Cross-reference:** Equivalent of Shekel finding F-015 (Fixed in
  C-33 with `set_real_ip_from 172.32.0.0/24` for the
  `shekel-frontend` bridge only). The shared nginx side of F-015 is
  unaddressed for non-Shekel vhosts.
- **Location:** `/opt/docker/nginx/nginx.conf:15-52` -- no
  `set_real_ip_from`, no `real_ip_header`, no `real_ip_recursive`
  directive in the entire `http` block.
- **Description:** With H-001's WAN bypass closed, all WAN traffic
  for jellyfin / immich / calibre-web / ntfy will arrive at Nginx
  via cloudflared. cloudflared connects to the `nginx` container
  from the `homelab` bridge (172.18.0.0/16); from Nginx's
  perspective, every WAN request's `$remote_addr` is the
  cloudflared container IP, not the actual client. The
  `CF-Connecting-IP` header carries the real client IP, but Nginx
  is not configured to honour it.
- **Evidence:** `grep -E 'set_real_ip_from|real_ip_header|
  real_ip_recursive' /opt/docker/nginx/nginx.conf` returns zero
  matches. The Shekel C-33 commit added the directives only inside
  the C-33 patch to `deploy/nginx-shared/nginx.conf` (gated to
  `172.32.0.0/24`).
- **Impact:** Per-request access logs (`/var/log/nginx/access.log`)
  attribute every WAN request to the cloudflared container IP, not
  the real client. Forensic queries against the access log cannot
  distinguish a single attacker IP from organic traffic. The
  Jellyfin and Immich application logs also see the cloudflared IP
  if they trust `X-Real-IP` from Nginx. Per-IP rate limiting (where
  it exists in any of these services) is defeated -- every WAN
  request appears to come from one IP.
- **Recommendation:** Extend the Shekel-side fix to cover all
  cloudflared-originated traffic. Add to
  `/opt/docker/nginx/nginx.conf` inside the `http` block:
  ```nginx
  # Trust cloudflared (on the homelab bridge) for the
  # CF-Connecting-IP header that carries the real client IP.
  set_real_ip_from <cloudflared-container-ip-or-pinned-cidr>;
  real_ip_header CF-Connecting-IP;
  real_ip_recursive off;
  ```
  Three options for the trusted source:
  - (a) Pin cloudflared's IP via `docker network connect --ip
    <ip>` or `ipv4_address:` in the homelab compose. Most
    restrictive.
  - (b) Pin a `homelab`-replacement bridge subnet (e.g.
    `172.50.0.0/24`) and trust the entire subnet -- requires
    re-creating the network, but lets Shekel's `shekel-frontend`
    pattern repeat for every Cloudflare-fronted service.
  - (c) Trust `172.18.0.0/16` (the current `homelab` subnet).
    Easiest, but reintroduces the F-015 spoofing surface for any
    co-tenant on `homelab`.

  Option (a) or (b) are the audit-grade fixes; option (c) is the
  minimum viable improvement and still beats the current "trust
  nothing, log nothing useful" posture.
- **Status:** Open

---

### H-003: Three vhosts (immich, unifi, ntfy) emit no defense-in-depth headers; calibre-web and ntfy are missing Permissions-Policy

- **Severity:** Medium (immich, unifi, ntfy); Low (calibre-web)
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-1188 (Initialization with Hardcoded Network
  Resource Configuration)
- **ASVS:** V14.4.3
- **Cross-reference:** Equivalent of Shekel finding F-064 (Fixed in
  C-33 for `shekel.conf` only). The audit-window evidence
  (`scans/shared-nginx-shekel-vhost.conf.txt`) was specifically
  about Shekel; this finding catalogs the same gap on every other
  vhost.
- **Location:** `/opt/docker/nginx/conf.d/immich.conf`,
  `unifi.conf`, `ntfy.conf`, `calibre-web.conf`.
- **Description:** Each vhost should emit, with the `always` flag,
  the four defense-in-depth headers Jellyfin and Grafana already
  set:
  ```
  add_header X-Content-Type-Options "nosniff" always;
  add_header X-Frame-Options "SAMEORIGIN" always;     (or DENY for non-iframed services)
  add_header Referrer-Policy "no-referrer-when-downgrade" always;
  add_header Permissions-Policy "<sensor-deny list>" always;
  ```
  Current state per vhost:
  - `immich.conf`: **all four missing**.
  - `unifi.conf`: **all four missing**.
  - `ntfy.conf`: missing **Permissions-Policy** (other three
    present).
  - `calibre-web.conf`: missing **Permissions-Policy** (other
    three present).
- **Evidence:** Grepping `add_header X-Content-Type-Options`
  returns 5 of 7 vhosts (jellyfin, grafana, ntfy, calibre-web,
  shekel after C-33). Grepping `add_header Permissions-Policy`
  returns 3 of 7 vhosts (jellyfin, grafana, shekel after C-33).
  The `immich.conf` and `unifi.conf` files have no `add_header`
  directives at all (nor any `always` tokens).
- **Impact:** A 502 from Nginx (origin restart, immich-server
  panic, unifi reboot) returns a default Nginx error page that is
  framable, MIME-sniffable, and leaks referrer to outbound clicks.
  An attacker hosting a malicious page can iframe immich's login
  form or unifi's controller UI for credential / token theft via
  CSS overlay. This is a classic clickjacking surface.
- **Recommendation:** Mirror the Jellyfin pattern verbatim. Add to
  the `server { listen 443 ssl; ... }` block in each affected
  vhost (above the first `location` block), with the `always`
  flag so the header survives 4xx/5xx responses where Nginx's
  default add_header is silently suppressed:
  ```nginx
  add_header X-Content-Type-Options "nosniff" always;
  add_header X-Frame-Options "SAMEORIGIN" always;
  add_header Referrer-Policy "no-referrer-when-downgrade" always;
  add_header Permissions-Policy "accelerometer=(), ambient-light-sensor=(), battery=(), camera=(), display-capture=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), usb=()" always;
  ```
  For `immich.conf` and `unifi.conf` use `X-Frame-Options
  SAMEORIGIN` (matching the existing pattern). Validate with
  `docker exec nginx nginx -t` and reload with `docker exec nginx
  nginx -s reload`. Lowest-risk change in this audit; ~5 minutes
  per vhost; zero compose / restart impact.
- **Status:** Open

---

### H-004: Shared `homelab` bridge enables unrestricted lateral movement among co-tenants

- **Severity:** Medium (per-service); High (aggregate with H-005)
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-653 (Improper Compartmentalization)
- **ASVS:** V14.4.1
- **Cross-reference:** Systemic equivalent of Shekel findings F-020
  / F-129 (Fixed in C-33 by moving Shekel onto the dedicated
  `shekel-frontend` bridge). The same isolation has not been
  applied to other services.
- **Location:** `/opt/docker/docker-compose.yml:223-227` -- the
  `homelab` bridge is the sole network for jellyfin, unifi,
  calibre-web, nginx, cloudflared. `/opt/docker/immich/docker-
  compose.yml:35-37` adds immich-server to the same bridge.
  Sibling ntfy (not in either compose I read) is also reported on
  homelab via the `/opt/docker/nginx/conf.d/ntfy.conf` upstream
  reference `ntfy:80`.
- **Description:** Every container on the `homelab` bridge can
  reach every other container's listening ports directly,
  bypassing the shared Nginx and any per-vhost ACL. The shared
  Nginx at `/opt/docker/nginx/conf.d/grafana.conf` and `ntfy.conf`
  has `allow 10.10.101.0/24; deny all;` directives that defend the
  LAN path, but a compromised co-tenant on `homelab` can hit the
  origin port directly without going through Nginx (e.g.,
  `http://immich_server:2283/api/...`,
  `http://unifi:8443/manage/`,
  `http://jellyfin:8096/Users/<id>`).
- **Evidence:** Per the Shekel audit's
  `scans/docker-networks-detail.json`, the `homelab` bridge has at
  least 6 attached containers and is `Internal: false`. The actual
  port maps (jellyfin 8096, immich-server 2283 with extra
  127.0.0.1:2283 host bind, unifi 8443/8080, calibre-web 8083,
  ntfy 80) are all reachable container-to-container over the
  bridge.
- **Impact:** A vulnerability in any one homelab container (the
  Jellyfin / immich / UniFi / calibre-web CVE histories are non-
  empty) yields a reach to every other co-tenant's internal
  endpoint. UniFi's controller exposes credential reset and SSH
  configuration; immich-server stores photo libraries; the cluster
  effectively shares its blast radius. The Shekel C-33 fix removed
  Shekel from this bridge; the other services remain exposed to
  each other.
- **Recommendation:** This is the most architecturally significant
  finding in this audit and the largest scope of work. Three
  options, in increasing isolation strength:
  - (a) **Per-service frontend bridge** (mirror C-33's Shekel
    pattern). Each service gets its own
    `<service>-frontend` bridge containing only that service +
    nginx + cloudflared. Eliminates lateral movement between
    services entirely. ~1 hour per service.
  - (b) **Tier-based bridges**. Group services into trust tiers
    (e.g., a `media` tier with jellyfin + calibre-web that
    legitimately share a media volume; an `infra` tier with
    grafana + alloy + prometheus; a `tools` tier with unifi +
    ntfy). Reduces lateral surface without per-service overhead.
  - (c) **Keep current homelab bridge but add nginx ACLs to every
    backend port**. Doesn't actually prevent lateral movement
    (containers can still talk on docker network) but at least
    constrains the WAN-via-cloudflared path. Lowest-effort, lowest
    isolation gain.

  Recommend (a) for any service with stored credentials or PII
  (immich photo library, unifi controller passwords) and (b) for
  the rest. Defer until after H-001 / H-002 / H-003 to keep change
  windows small.
- **Status:** Open

---

### H-005: Immich stack runs without `cap_drop` / `no-new-privileges` / `read_only`

- **Severity:** Medium
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-250 (Execution with Unnecessary Privileges)
- **ASVS:** V10.3.2
- **Cross-reference:** Adjacent to F-094 / F-095 in the Shekel
  audit (container hardening). No Shekel-side equivalent because
  Shekel's containers all set the recommended pattern.
- **Location:** `/opt/docker/immich/docker-compose.yml:11-80` --
  none of `immich-server`, `immich-machine-learning`,
  `immich_redis` (valkey), or `immich_postgres` declare
  `cap_drop`, `security_opt`, or `read_only`. The compose file
  is the upstream-published immich template with no operator
  hardening overlay.
- **Description:** Each Immich container starts with the full
  default Linux capability set, can call `setuid` / `setgid`,
  has a writable root filesystem, and inherits any new
  capabilities the kernel might surface. The shared `homelab`
  bridge (H-004) means a compromise of any one of these escalates
  laterally to the rest of the homelab.
- **Evidence:**
  ```yaml
  # /opt/docker/immich/docker-compose.yml -- no security_opt,
  # cap_drop, or read_only on any of the four services.
  immich-server:
    container_name: immich_server
    devices:
      - /dev/dri:/dev/dri    # GPU passthrough
    ...
  ```
  Compare with `/opt/docker/docker-compose.yml`'s jellyfin entry
  (cap_drop ALL + DAC_OVERRIDE + no-new-privileges) -- the
  hardening pattern exists for similar workloads (media server with
  GPU passthrough), so the Immich stack diverges from the operator's
  documented convention.
- **Impact:** A remote-code-execution chain in any Immich service
  (immich-server is internet-facing via cloudflared / H-001;
  immich_postgres is reachable from any homelab co-tenant per
  H-004) yields full container privileges immediately, including
  CAP_NET_ADMIN, CAP_SYS_ADMIN if the kernel enables them, and
  arbitrary writes to the container root. Container escape via a
  kernel-side capability flaw (CVE-2022-0185, CVE-2022-2588 class)
  goes from "needs a privileged container" to "every container".
- **Recommendation:** Add the operator's standard pattern to each
  Immich service. The minimum viable overlay (does not require
  pinning specific caps Immich needs):
  ```yaml
  immich-server:
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    cap_add:
      - DAC_OVERRIDE          # postgres / redis dirs are typically
                              # owned by uid 70 / 999; container
                              # init may need to chown
      # ... add caps as needed if the container fails to start;
      # err on the side of cap_drop ALL and only add back caps
      # that Immich actually requires.
  ```
  For `immich_postgres` and `immich_redis`, consider also adding
  `read_only: true` with explicit tmpfs mounts for `/tmp`,
  `/var/run/postgresql`, etc., matching the Shekel-side
  `shekel-prod-redis` configuration in
  `/opt/docker/shekel/docker-compose.yml`. This requires more
  careful tuning per image.

  Test in stages: add `no-new-privileges` first (almost always
  safe), then `cap_drop ALL` (test image starts), then re-add caps
  one at a time if startup fails. The operator's existing
  `/opt/docker/AUDIT.md` (2026-04-16) documents the pattern as the
  convention; the immich subdir was just never updated.
- **Status:** Open

---

### H-006: UniFi container runs unhardened by design; document or revisit

- **Severity:** Low (with Note)
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-250
- **ASVS:** V10.3.2
- **Cross-reference:** Same control as H-005, different
  resolution.
- **Location:** `/opt/docker/docker-compose.yml:62-91`
- **Description:** UniFi declares no `cap_drop`,
  `security_opt: no-new-privileges`, or `read_only`. An inline
  comment justifies the omission:
  > cap_drop/no-new-privileges omitted: UniFi uses gosu (needs
  > setuid) and embedded MongoDB/WiredTiger (needs capabilities for
  > mmap, file locking, etc.) that are incompatible with aggressive
  > capability dropping.
  This is a known-risk acceptance, not an oversight. The
  recommendation here is therefore documentation-grade rather than
  code-grade.
- **Evidence:** Direct read of the compose file; comment at lines
  67-69.
- **Impact:** Same as H-005 in kind; smaller blast radius because
  UniFi is LAN-only (no `cloudflared` ingress -- not in
  `/opt/docker/cloudflared/config.yml`'s ingress list). The
  network-controller workload still has CAP_NET_ADMIN-equivalent
  reach inside the container.
- **Recommendation:**
  - (a) Re-evaluate per UniFi minor version. The
    `jacobalberty/unifi:latest` image's MongoDB / gosu requirements
    have evolved across major UniFi releases; what was
    incompatible in 2024 may not be in 2026. The next time the
    UniFi image is updated, retry `cap_drop ALL` +
    `no-new-privileges` and remove the omission comment if the
    container starts.
  - (b) Consider migrating away from `jacobalberty/unifi:latest`
    to the official `linuxserver/unifi-network-application` image,
    which ships with a tighter default profile.
  - (c) If staying as-is, harden the LAN ACL on the UniFi vhost
    (`/opt/docker/nginx/conf.d/unifi.conf`) to a single-IP allow
    (the operator's workstation) rather than the entire
    10.10.101.0/24 LAN -- compensating control for the
    capability surface.
- **Status:** Open (accepted with documented rationale)

---

### H-007: `:latest` image tags on jellyfin, unifi, nginx, cloudflared

- **Severity:** Low
- **OWASP:** A06:2021 Vulnerable and Outdated Components;
  A08:2021 Software and Data Integrity Failures
- **CWE:** CWE-1357 (Use of Components with Indeterminate Origin)
- **ASVS:** V14.2.4
- **Cross-reference:** No Shekel-side equivalent (Shekel pins via
  GHCR `ghcr.io/saltyreformed/shekel:latest` but the operator
  controls image generation; the homelab containers pull from
  third-party registries).
- **Location:** `/opt/docker/docker-compose.yml`:
  - `jellyfin: image: jellyfin/jellyfin:latest`
  - `unifi: image: jacobalberty/unifi:latest`
  - `nginx: image: nginx:latest`
  - `cloudflared: image: cloudflare/cloudflared:latest`
- **Description:** The existing `/opt/docker/AUDIT.md` (dated
  2026-04-16, "Pin image versions -- stop using `:latest`") flags
  exactly this. The recommendation has not yet been applied --
  every `docker compose pull` on any of these services can
  silently introduce a major-version upgrade. Jellyfin database
  migrations are irreversible per upstream's documentation.
  `nginx:latest` rolls major release lines without warning.
- **Evidence:** Direct read of the compose file. The audit doc's
  recommendation table:
  ```yaml
  image: jellyfin/jellyfin:10.11
  image: nginx:stable-alpine
  image: cloudflare/cloudflared:2026.3.0
  image: jacobalberty/unifi:8.6
  ```
- **Impact:** Surprise breaking changes on routine pulls. Reduced
  reproducibility (a `docker compose down && up -d` recreates
  containers with whatever image is current at the moment, not the
  one that was tested). Compatibility with the
  `/opt/docker/nginx/conf.d/*.conf` files becomes implicit rather
  than asserted.
- **Recommendation:** Implement the existing AUDIT.md
  recommendation. Pin to minor (`jellyfin/jellyfin:10.11`) or
  patch versions per upstream's stability promise. The Shekel
  side already uses pinned digests for the bundled `nginx:1.27-
  alpine`, `redis:7.4-alpine`, and `postgres:16-alpine` -- the
  pattern is established.
- **Status:** Open (already flagged in the operator's existing
  AUDIT.md)

---

### H-008: Immich `127.0.0.1:2283` host port bind duplicates the LAN nginx route

- **Severity:** Info
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-1188
- **ASVS:** V14.1.3
- **Cross-reference:** No direct Shekel-side equivalent.
- **Location:** `/opt/docker/immich/docker-compose.yml:25-28`
- **Description:** `immich-server` publishes port 2283 to
  `127.0.0.1:2283` on the host:
  ```yaml
  ports:
    # Bound to localhost only. External access is through Nginx on the
    # homelab network. This port is for direct debugging from the host.
    - '127.0.0.1:2283:2283'
  ```
  The comment correctly states the operator's intent. The bind is
  scoped to the loopback interface, not 0.0.0.0, so the port is
  not exposed to the LAN. The risk surface is therefore only:
  (a) any other process running as the operator's UID on the host
  can connect to localhost:2283 and bypass the LAN ACL on
  `/opt/docker/nginx/conf.d/immich.conf` (which has none, but H-003
  notes immich.conf still misses the security headers); (b) if the
  operator ever runs an SSH local port-forward
  (`ssh -L 2283:localhost:2283 ...`) the loopback bind is
  forwarded to the remote endpoint.
- **Evidence:** Direct read of the compose file.
- **Impact:** Low. Localhost bind is the correct pattern for
  debugging. The risk only matters if a host-side process is
  compromised AND that process runs as the operator UID AND there
  is no other detection -- a chain that overlaps with much higher-
  severity findings (H-004 / H-005).
- **Recommendation:** Consider removing the `ports:` block
  entirely; `docker exec immich_server curl http://localhost:2283`
  serves the same debugging purpose without a host-side bind.
  Defer until H-001..H-005 are addressed.
- **Status:** Open

---

### H-009: Cloudflare Tunnel ingress lacks Cloudflare Access enforcement

- **Severity:** Medium
- **OWASP:** A07:2021 Identification and Authentication Failures
- **CWE:** CWE-306 (Missing Authentication for Critical Function)
- **ASVS:** V2.1.1
- **Cross-reference:** Shekel finding F-061 (Open). The same
  control gap applies to every WAN-exposed hostname; F-061 was
  scoped to Shekel's hostname only.
- **Location:** `/opt/docker/cloudflared/config.yml` -- no
  `originRequest.access:` block, no Cloudflare Access /
  Zero-Trust policy attached at the dashboard level (per the
  Shekel-side audit confirmation).
- **Description:** Cloudflare Tunnel terminates TLS at the edge
  but does not by default require user authentication. The only
  auth between the public internet and any service is the
  application-level login (jellyfin's Quick Connect, immich's
  user pool, calibre-web's login form, ntfy's per-topic auth,
  Shekel's session cookie + MFA). Cloudflare Access can layer a
  one-time-pin email check, GitHub / Google OIDC, or a service
  token in front of every service.
- **Evidence:** No `originRequest.access` blocks in
  `/opt/docker/cloudflared/config.yml`. The Shekel audit's S3
  Section 1I confirmed via the operator that no Cloudflare Access
  policies are attached at the dashboard.
- **Impact:** Public-internet attackers can probe each service's
  login form unbounded by a perimeter auth check. Brute-force
  rate limits on any individual service are the only defense.
  For low-volume audited services (Shekel) this is acceptable;
  for services with weaker rate limits or known credential-stuffing
  surface (jellyfin Quick Connect, immich registration), it is a
  real gap.
- **Recommendation:** Apply Cloudflare Access policies at the
  dashboard level, scoped per hostname. Start with the
  highest-risk service (immich, which has photo PII) and a
  one-time-pin email policy keyed to the operator's address.
  Complementary to H-001: once cloudflared routes through Nginx,
  Access policies can be tuned per `originRequest` block.
  Documented in F-061 of the Shekel audit; this finding extends
  the scope.
- **Status:** Open (cross-references F-061)

---

### H-010: `/opt/docker/nginx/nginx.conf` `server_tokens` already off; bundle wins

- **Severity:** N/A (positive finding)
- **OWASP:** -
- **CWE:** -
- **ASVS:** V14.1.3
- **Cross-reference:** Shekel finding F-156 (Fixed in C-33).
- **Location:** `/opt/docker/nginx/nginx.conf:32`
- **Description:** The shared `nginx.conf` already sets
  `server_tokens off;` at the http level. This applies to every
  vhost loaded via `include /etc/nginx/conf.d/*.conf;` -- so
  jellyfin, immich, unifi, calibre-web, ntfy, grafana, shekel,
  and the stub-status server all benefit. No action required.
- **Evidence:**
  ```nginx
  # /opt/docker/nginx/nginx.conf:31-32
      # Do not reveal Nginx version in headers
      server_tokens off;
  ```
- **Status:** Already mitigated.

---

## Summary

| Finding | Severity | Scope | Cross-reference | Recommended order |
|---|---|---|---|---|
| H-010 | -- | informational | F-156 | n/a (already done) |
| H-003 | Medium | per-vhost headers | F-064 | **First** -- 5 minutes per vhost, zero restart impact |
| H-001 | Medium / High aggregate | cloudflared routing | F-063 | **Second** -- single config edit + cloudflared restart |
| H-002 | Medium | shared nginx real_ip | F-015 | **Third** -- depends on cloudflared topology choice |
| H-005 | Medium | Immich hardening | -- | **Fourth** -- per-service compose edits |
| H-009 | Medium | Cloudflare Access | F-061 | **Fifth** -- dashboard work; no compose edits |
| H-007 | Low | image pinning | -- | Existing AUDIT.md item; coordinate with next image upgrade |
| H-004 | Medium / High aggregate | network isolation | F-020 / F-129 | **Largest scope** -- defer until smaller fixes land |
| H-006 | Low | UniFi accepted risk | -- | Re-evaluate on next UniFi major version |
| H-008 | Info | Immich host bind | -- | Optional cleanup |

### Cross-reference matrix (audit findings vs. service)

A `Y` cell means the listed finding's recommendation applies to
the listed service; `N` means it does not; `-` means the service
is out of scope for the finding. Read horizontally to see which
fixes a service needs.

| Service       | H-001 | H-002 | H-003 | H-004 | H-005 | H-006 | H-007 | H-008 | H-009 |
|---------------|-------|-------|-------|-------|-------|-------|-------|-------|-------|
| jellyfin      | Y     | Y     | N     | Y     | -     | -     | Y     | -     | Y     |
| immich-server | Y     | Y     | Y     | Y     | Y     | -     | -     | Y     | Y     |
| unifi         | -     | -     | Y     | Y     | -     | Y     | Y     | -     | -     |
| calibre-web   | Y     | Y     | Y     | Y     | -     | -     | -     | -     | Y     |
| ntfy          | Y     | Y     | Y     | Y     | -     | -     | -     | -     | Y     |
| nginx         | -     | Y     | -     | -     | -     | -     | Y     | -     | -     |
| cloudflared   | Y     | -     | -     | -     | -     | -     | Y     | -     | -     |
| shekel        | -     | -     | -     | -     | -     | -     | -     | -     | Y     |

Shekel's row is mostly `-` because Commit C-33 closed F-015,
F-020, F-063, F-064, F-129, and F-156 for Shekel specifically.
F-061 (Cloudflare Access) is still Open and is the only
remaining audit-tracked WAN finding for Shekel.

## Recommended Next Steps (operator decision queue)

1. **Approve H-003 (per-vhost security headers).** Smallest blast
   radius; lowest deployment risk; closes the most vhosts in one
   change. Follow the Jellyfin pattern; ~5 minutes per vhost +
   `nginx -t && nginx -s reload`.
2. **Approve H-001 (cloudflared through nginx).** Single edit to
   `/opt/docker/cloudflared/config.yml`; restart cloudflared.
   Verify each WAN hostname with `curl -I` and confirm Nginx logs
   show the request landing on its vhost.
3. **Discuss H-002 topology.** Options (a) / (b) / (c) above
   differ in operational complexity. Option (b) (a single
   `homelab-front` bridge containing only nginx + cloudflared,
   with each backend service on its own per-service bridge) is
   the most C-33-consistent path. Option (a) is most restrictive.
4. **Defer H-004, H-005, H-006, H-009 to discrete commits per
   service.** None of them are quick wins.
5. **Pick up H-007 next time any of jellyfin / unifi / nginx /
   cloudflared is upgraded.** The pin is documentation-grade --
   add the explicit version at upgrade time.

This document is the read-only artifact; no homelab containers
or files have been modified. The Shekel side of the audit
(`docs/audits/security-2026-04-15/`) remains the canonical record
for Shekel-specific work.
