# Grafana Observability Stack for /opt/docker Homelab

## Context

You run five service stacks on an Arch (CachyOS) desktop — Jellyfin, Immich, Unifi, Calibre-Web-Automated, and your own Flask app **Shekel** — all under `/opt/docker/`. There is no centralized log aggregation today (each service writes Docker `json-file` logs in isolation) and no host/hardware metrics collection. Shekel was set up with structured (JSON) logging, but the upstream config has bugs that prevent the structure from being usable.

The goal is to deploy a single, coherent observability stack that:

1. Ingests **Docker container logs** from all five service stacks.
2. Ingests **Arch system logs** from journald.
3. Collects **host metrics** including CPU, RAM, disks, network, hwmon temperatures, NVMe + HDD SMART, and Intel Arc B580 GPU utilization.
4. Collects **per-container metrics** (CPU/RAM/IO/network).
5. Collects **application metrics** from cloudflared, jellyfin, immich, postgres (× 2), and nginx.
6. Surfaces all of this in **Grafana**, served LAN-only via your existing nginx with the wildcard cert, mirroring how Jellyfin/Immich/Shekel are reverse-proxied today.
7. Mirrors the security and operational conventions in `/opt/docker/AUDIT.md` (pinned image versions, `cap_drop: ALL`, `no-new-privileges:true`, healthchecks, resource limits, json-file rotation, `init: true` where appropriate, bind-mounts under `/opt/docker/<service>/`).

**User-confirmed decisions:** LAN-only Grafana via nginx + TLS · pre-flight prep baked into Phase 0 · Shekel logging fix done upstream · 30 days metrics / 30 days logs.

---

## Issues found in the existing setup that must be addressed

These were uncovered during exploration. They are flagged here, not silently worked around.

### Showstoppers — fixed in Phase 0 of this plan
1. **NTP is INACTIVE.** `timedatectl status` reports the clock is unsynchronized. Prometheus is highly sensitive to clock drift and Loki ingestion will reject samples too far out of band.
2. **`smartmontools` and `nvme-cli` are not installed.** Required on the host (the exporter container ships its own `smartctl`, but you'll want host-side parity and `smartd` warnings).
3. **Btrfs root filesystem.** `/var/lib/docker` and `/opt/docker` live on btrfs. Prometheus TSDB and Loki chunks are random-write workloads that fragment btrfs catastrophically without `chattr +C`. Must be applied to empty data dirs **before** any data is written.

### Bugs in your Shekel logging — fixed upstream as part of this plan
4. **`python-json-logger==4.1.0` removed the `timestamp=True` kwarg.** Your `app/utils/logging_config.py` still passes it; the formatter silently ignores it. That is why no `timestamp` field appears in emitted logs. The `rename_fields` map is still supported in v4 but appears broken-by-association because the formatter as-configured probably never produced what you intended.
5. The fix is a 7-line `RFC3339JsonFormatter` subclass that overrides `formatTime` — see §6.

### Non-blockers (mentioned for awareness; out of scope here)
6. `cloudflared` container is currently `unhealthy`. The monitoring stack will surface this and you should fix it separately (likely tunnel credentials).
7. `shekel-prod-app` is currently `unhealthy`. Worth investigating since you're about to ingest its logs, but does not block this plan.
8. Most existing services use `:latest` image tags. AUDIT.md already documents this; out of scope here. **All new monitoring images in this plan are pinned by version.**
9. Plaintext secrets in `/opt/docker/{immich,shekel}/.env` — same comment.

---

## Architecture summary

```
┌───────────── Arch host (10.10.101.101) ────────────────────────────────┐
│                                                                        │
│  systemd-journald ──► /var/log/journal ──► (bind RO into Alloy)        │
│  /proc, /sys, /, hwmon, /dev/dri, /dev/nvme0, /dev/sd[a-e]             │
│                                                                        │
│  /var/run/docker.sock ──► (bind RO into Alloy + cAdvisor)              │
│                                                                        │
│  ┌── Docker network: monitoring ─────────────────────────────────────┐ │
│  │                                                                  │ │
│  │    ┌───────────┐   ┌───────────┐   ┌───────────┐                 │ │
│  │    │ prometheus│◄──┤  alloy    │──►│   loki    │                 │ │
│  │    └─────▲─────┘   └────▲──────┘   └─────▲─────┘                 │ │
│  │          │              │                │                       │ │
│  │          └──────────────┴───────►┌───────────┐                   │ │
│  │                                  │  grafana  │                   │ │
│  │                                  └─────┬─────┘                   │ │
│  │                                        │                         │ │
│  │  Exporters (scraped by Alloy):         │                         │ │
│  │   cadvisor · smartctl-exporter         │                         │ │
│  │   intel-gpu-exporter                   │                         │ │
│  │   nginx-exporter · postgres ×2         │                         │ │
│  └────────────────────────────────────────│─────────────────────────┘ │
│                                           │                            │
│  ┌── Docker network: homelab ────────────▼────────────────────────┐   │
│  │  nginx (existing) ──► grafana:3000  [+ jellyfin/immich/shekel] │   │
│  │  cloudflared (existing, scraped by Alloy on this network)      │   │
│  └────────────────────────────────────────────────────────────────┘   │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘
            ▲ LAN clients (10.10.101.0/24) → https://grafana.saltyreformed.com (via nginx 443)
```

- **Alloy joins both `monitoring` and `homelab`** so it can scrape `cloudflared:2000`, `jellyfin:8096`, `immich_server:808[12]`, and `nginx:8088/stub_status` while writing to `loki:3100` and `prometheus:9090` on `monitoring`.
- **Grafana joins both `monitoring` and `homelab`** so nginx can `proxy_pass http://grafana:3000`. Mirrors the existing `immich-server` pattern.
- **Postgres exporters** join `monitoring` plus the relevant existing internal network (`backend` for Shekel, `immich_default` for Immich) as `external: true`.
- **No new published host ports.** Everything stays inside Docker; only nginx 80/443 (already published) faces the LAN.

---

## Pinned image versions (May 2026 — all verified against upstream releases)

| Component | Image | Pinned tag |
|---|---|---|
| Grafana | `grafana/grafana` | `13.0.1` |
| Loki (single-binary) | `grafana/loki` | `3.7.1` |
| Prometheus | `prom/prometheus` | `v3.11.3` |
| Grafana Alloy | `grafana/alloy` | `v1.16.1` |
| cAdvisor | `gcr.io/cadvisor/cadvisor` | `v0.56.2` |
| smartctl_exporter | `quay.io/prometheuscommunity/smartctl_exporter` | `v0.14.0` |
| postgres_exporter | `quay.io/prometheuscommunity/postgres-exporter` | `v0.19.1` |
| nginx-prometheus-exporter | `nginx/nginx-prometheus-exporter` | `1.5.1` |
| Intel GPU exporter | `ghcr.io/onedr0p/intel-gpu-exporter` | `rolling` — pin by `@sha256:` digest after pull |

**Important traps (not just preferences):**
- `prom/prometheus:latest` still resolves to v2.x — pinning v3.11.3 is mandatory, not optional. v3 default config has different scrape behaviors.
- The smartctl exporter image is on **quay.io** with an **underscore** in the repo path: `quay.io/prometheuscommunity/smartctl_exporter`. Easy to get wrong.
- `cAdvisor v0.56` requires Docker ≥ 25.0 (CachyOS rolling has 27.x — fine).

---

## Directory layout (all new under `/opt/docker/monitoring/`)

```
/opt/docker/monitoring/
├── docker-compose.yml
├── .env                          # chmod 600
├── secrets/                      # chmod 700
│   ├── grafana_admin_password
│   ├── shekel_pg_exporter_password
│   └── immich_pg_exporter_password
├── grafana/
│   ├── data/                     # +C, owned by grafana UID
│   └── provisioning/
│       ├── datasources/datasources.yaml
│       ├── dashboards/dashboards.yaml
│       ├── dashboards/<8 dashboard JSONs>
│       └── alerting/             # Phase 5
├── loki/
│   ├── data/                     # +C, owned by 10001:10001
│   └── config/loki.yaml
├── prometheus/
│   ├── data/                     # +C, owned by 65534:65534
│   └── config/prometheus.yml
└── alloy/
    ├── data/                     # +C
    └── config/config.alloy
```

---

## Phase 0 — host pre-flight (all manual, run before any Docker activity)

Order matters: `chattr +C` is no-op on directories that already contain files.

```bash
# 0a. NTP
sudo timedatectl set-ntp true
sudo systemctl enable --now systemd-timesyncd
timedatectl status     # must show "System clock synchronized: yes"

# 0b. SMART tooling
sudo pacman -S --needed smartmontools nvme-cli
sudo systemctl enable --now smartd
sudo smartctl -a /dev/nvme0n1   # smoke test

# 0c. Btrfs nodatacow on monitoring data dirs (BEFORE first write)
sudo mkdir -p /opt/docker/monitoring/{prometheus,loki,grafana,alloy}/data
sudo chattr +C /opt/docker/monitoring/{prometheus,loki,grafana,alloy}/data
lsattr -d /opt/docker/monitoring/{prometheus,loki,grafana,alloy}/data
# Each line must show "C" — capital C — confirming nodatacow took.

# 0d. journald retention drop-in
sudo install -d /etc/systemd/journald.conf.d
sudo tee /etc/systemd/journald.conf.d/00-retention.conf >/dev/null <<'EOF'
[Journal]
SystemMaxUse=2G
SystemMaxFileSize=200M
MaxRetentionSec=30day
ForwardToSyslog=no
EOF
sudo systemctl restart systemd-journald

# 0e. Ownership for data dirs (UIDs from each image — verify via docker inspect first)
sudo chown -R 472:472     /opt/docker/monitoring/grafana/data
sudo chown -R 10001:10001 /opt/docker/monitoring/loki/data
sudo chown -R 65534:65534 /opt/docker/monitoring/prometheus/data
sudo chown -R 1000:1000   /opt/docker/monitoring/alloy/data

# 0f. Firewall (LAN-only via nginx → no new rules required; verify)
sudo firewall-cmd --list-all

# 0g. Docker daemon log size cap (separate from compose-level logging blocks; belt-and-braces)
sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'
{ "log-driver": "json-file", "log-opts": { "max-size": "10m", "max-file": "3" } }
EOF
sudo systemctl restart docker

# 0h. Confirm monitoring network exists, empty
docker network inspect monitoring | grep -E '"Name"|"Driver"|"Containers"'
```

**Phase 0 gate:** NTP synchronized · `smartctl -a /dev/nvme0n1` returns SMART data · `lsattr` shows `+C` on all four data dirs · `getent group adm systemd-journal` returns both groups · `docker network inspect monitoring` returns an empty bridge.

---

## Phase 1 — storage backends (Loki, Prometheus, Grafana)

Deploy with collectors disabled. Verify all three start cleanly and are reachable on the `monitoring` network.

### Files
- `/opt/docker/monitoring/docker-compose.yml` (Loki/Prometheus/Grafana services only at this phase)
- `/opt/docker/monitoring/loki/config/loki.yaml`
- `/opt/docker/monitoring/prometheus/config/prometheus.yml`
- `/opt/docker/monitoring/.env` (Grafana root URL, etc.)
- `/opt/docker/monitoring/secrets/grafana_admin_password`

### Loki config (single-binary, TSDB v13, 30d retention)

`loki/config/loki.yaml`:
```yaml
auth_enabled: false
server:
  http_listen_port: 3100
  grpc_listen_port: 9096
  log_level: info
common:
  instance_addr: 127.0.0.1
  path_prefix: /loki
  storage:
    filesystem:
      chunks_directory: /loki/chunks
      rules_directory: /loki/rules
  replication_factor: 1
  ring: { kvstore: { store: inmemory } }
schema_config:
  configs:
    - from: 2026-05-01
      store: tsdb
      object_store: filesystem
      schema: v13
      index: { prefix: index_, period: 24h }
storage_config:
  tsdb_shipper:
    active_index_directory: /loki/tsdb-shipper-active
    cache_location: /loki/tsdb-shipper-cache
    cache_ttl: 24h
  filesystem: { directory: /loki/chunks }
compactor:
  working_directory: /loki/compactor
  compaction_interval: 10m
  retention_enabled: true
  retention_delete_delay: 2h
  retention_delete_worker_count: 150
  delete_request_store: filesystem        # required ≥ Loki 3.0 with retention
limits_config:
  reject_old_samples: true
  reject_old_samples_max_age: 168h
  retention_period: 744h                  # 30 days
  max_query_series: 5000
  ingestion_rate_mb: 16
  ingestion_burst_size_mb: 32
  allow_structured_metadata: true
  volume_enabled: true
ruler:
  storage: { type: local, local: { directory: /loki/rules } }
  rule_path: /loki/rules-temp
  alertmanager_url: ""
  ring: { kvstore: { store: inmemory } }
  enable_api: true
analytics:
  reporting_enabled: false
```
Notes baked into this config: `schema: v13` (NOT v11/v12) · `table_manager` deliberately absent (deprecated for TSDB; retention is on the `compactor`) · `delete_request_store: filesystem` is required when retention deletes are enabled with filesystem object store.

Container command: `-config.file=/etc/loki/loki.yaml -target=all`.

### Prometheus config (Alloy `remote_write`s into it)

`prometheus/config/prometheus.yml`:
```yaml
global:
  scrape_interval: 30s
  scrape_timeout: 10s
  evaluation_interval: 30s
  external_labels:
    site: homelab
    host: arch-desktop
storage:
  tsdb:
    retention.time: 30d
    retention.size: 80GB
scrape_configs:
  - job_name: prometheus
    static_configs:
      - targets: ["localhost:9090"]
```
Container args: `--web.enable-remote-write-receiver --storage.tsdb.retention.time=30d --storage.tsdb.retention.size=80GB --config.file=/etc/prometheus/prometheus.yml --storage.tsdb.path=/prometheus`.

### Grafana env (in compose, Docker secrets for the password)
```
GF_SECURITY_ADMIN_PASSWORD__FILE=/run/secrets/grafana_admin_password
GF_SERVER_ROOT_URL=https://grafana.saltyreformed.com
GF_SERVER_DOMAIN=grafana.saltyreformed.com
GF_SERVER_SERVE_FROM_SUB_PATH=false
GF_AUTH_ANONYMOUS_ENABLED=false
GF_USERS_ALLOW_SIGN_UP=false
GF_ANALYTICS_REPORTING_ENABLED=false
```

### Phase 1 verification
```bash
docker compose -f /opt/docker/monitoring/docker-compose.yml up -d loki prometheus grafana
docker exec grafana    wget -qO- localhost:3000/api/health    # {"database":"ok"}
docker exec prometheus wget -qO- localhost:9090/-/healthy     # Prometheus Server is Healthy
docker exec loki       wget -qO- localhost:3100/ready         # ready
du -sh /opt/docker/monitoring/{prometheus,loki,grafana}/data  # small but non-zero, owned by correct UIDs
```

---

## Phase 2 — collectors (Alloy, cAdvisor, exporters)

Add: `alloy`, `cadvisor`, `smartctl-exporter`, `intel-gpu-exporter`, `nginx-exporter`, `shekel-postgres-exporter`, `immich-postgres-exporter`.

### Files
- `/opt/docker/monitoring/alloy/config/config.alloy`
- `/opt/docker/monitoring/secrets/{shekel,immich}_pg_exporter_password`

### Alloy River config

`alloy/config/config.alloy`:
```alloy
// Host metrics
prometheus.exporter.unix "host" {
  procfs_path = "/host/proc"
  sysfs_path  = "/host/sys"
  rootfs_path = "/host/root"
  enable_collectors  = ["hwmon", "systemd"]
  disable_collectors = ["ipvs", "infiniband"]
}
prometheus.scrape "host" {
  targets    = prometheus.exporter.unix.host.targets
  forward_to = [prometheus.remote_write.local.receiver]
  job_name   = "node"
}

// journald
loki.relabel "journal" {
  forward_to = []
  rule { source_labels = ["__journal__systemd_unit"]    target_label = "unit" }
  rule { source_labels = ["__journal__hostname"]        target_label = "hostname" }
  rule { source_labels = ["__journal_priority_keyword"] target_label = "level" }
}
loki.source.journal "host" {
  path           = "/var/log/journal"
  max_age        = "12h"
  format_as_json = false
  labels         = { job = "systemd-journal" }
  relabel_rules  = loki.relabel.journal.rules
  forward_to     = [loki.write.local.receiver]
}

// Docker container logs
discovery.docker "containers" { host = "unix:///var/run/docker.sock" }
discovery.relabel "containers" {
  targets = discovery.docker.containers.targets
  rule { source_labels = ["__meta_docker_container_name"]                                regex = "/(.*)" target_label = "container" }
  rule { source_labels = ["__meta_docker_container_log_stream"]                          target_label = "stream" }
  rule { source_labels = ["__meta_docker_container_label_com_docker_compose_project"]    target_label = "compose_project" }
  rule { source_labels = ["__meta_docker_container_label_com_docker_compose_service"]    target_label = "compose_service" }
}
loki.source.docker "containers" {
  host             = "unix:///var/run/docker.sock"
  targets          = discovery.relabel.containers.output
  labels           = { job = "docker" }
  forward_to       = [loki.process.shekel.receiver]
  refresh_interval = "10s"
}

// Shekel JSON parser — activated AFTER upstream Shekel fix ships
loki.process "shekel" {
  forward_to = [loki.write.local.receiver]
  stage.match {
    selector = "{compose_service=\"shekel-prod-app\"}"
    stage.json {
      expressions = {
        ts         = "timestamp",
        level      = "level",
        logger     = "logger",
        message    = "message",
        request_id = "request_id",
        event      = "event",
      }
    }
    stage.timestamp { source = "ts" format = "RFC3339Nano" }
    stage.labels    { values = { level = "", logger = "", event = "" } }
  }
}

// Scrape exporters
prometheus.scrape "cadvisor"     { targets = [{ __address__ = "cadvisor:8080",                 job = "cadvisor"     }] forward_to = [prometheus.remote_write.local.receiver] scrape_interval = "30s" }
prometheus.scrape "smartctl"     { targets = [{ __address__ = "smartctl-exporter:9633",        job = "smartctl"     }] forward_to = [prometheus.remote_write.local.receiver] scrape_interval = "5m" }
prometheus.scrape "intel_gpu"    { targets = [{ __address__ = "intel-gpu-exporter:8080",       job = "intel_gpu"    }] forward_to = [prometheus.remote_write.local.receiver] }
prometheus.scrape "postgres" {
  targets = [
    { __address__ = "shekel-postgres-exporter:9187", job = "postgres", instance = "shekel" },
    { __address__ = "immich-postgres-exporter:9187", job = "postgres", instance = "immich" },
  ]
  forward_to = [prometheus.remote_write.local.receiver]
}
prometheus.scrape "nginx"        { targets = [{ __address__ = "nginx-exporter:9113",           job = "nginx"        }] forward_to = [prometheus.remote_write.local.receiver] }
prometheus.scrape "cloudflared"  { targets = [{ __address__ = "cloudflared:2000",              job = "cloudflared"  }] forward_to = [prometheus.remote_write.local.receiver] }
prometheus.scrape "jellyfin"     { targets = [{ __address__ = "jellyfin:8096",                 job = "jellyfin"     }] metrics_path = "/metrics" forward_to = [prometheus.remote_write.local.receiver] }
prometheus.scrape "immich" {
  targets = [
    { __address__ = "immich_server:8081", job = "immich", subsystem = "api"           },
    { __address__ = "immich_server:8082", job = "immich", subsystem = "microservices" },
  ]
  forward_to = [prometheus.remote_write.local.receiver]
}
prometheus.scrape "self_stack" {
  targets = [
    { __address__ = "prometheus:9090", job = "prometheus" },
    { __address__ = "loki:3100",       job = "loki"       },
    { __address__ = "grafana:3000",    job = "grafana"    },
    { __address__ = "alloy:12345",     job = "alloy"      },
  ]
  forward_to = [prometheus.remote_write.local.receiver]
}

// Sinks
prometheus.remote_write "local" { endpoint { url = "http://prometheus:9090/api/v1/write" } }
loki.write              "local" { endpoint { url = "http://loki:3100/loki/api/v1/push"   } }
```

### Alloy mounts (compose)
- `/var/run/docker.sock:/var/run/docker.sock:ro`
- `/var/log/journal:/var/log/journal:ro`
- `/run/log/journal:/run/log/journal:ro`
- `/etc/machine-id:/etc/machine-id:ro` (journald cursor identity — easy to forget)
- `/proc:/host/proc:ro`
- `/sys:/host/sys:ro`
- `/:/host/root:ro,rslave`
- `./alloy/config/config.alloy:/etc/alloy/config.alloy:ro`
- `./alloy/data:/var/lib/alloy/data`

`group_add: ["adm", "systemd-journal"]` so the unprivileged Alloy user can read journald. Verify GIDs first: `getent group adm systemd-journal`.

Command: `run /etc/alloy/config.alloy --server.http.listen-addr=0.0.0.0:12345 --storage.path=/var/lib/alloy/data --stability.level=generally-available`.

### Per-service privileges and mounts

| Container | Privileges | Mounts | Networks |
|---|---|---|---|
| grafana | `cap_drop: ALL`, `no-new-privileges:true` | `./grafana/data:/var/lib/grafana`, `./grafana/provisioning:/etc/grafana/provisioning:ro` | monitoring, homelab |
| loki | unprivileged | `./loki/data:/loki`, `./loki/config/loki.yaml:/etc/loki/loki.yaml:ro` | monitoring |
| prometheus | unprivileged | `./prometheus/data:/prometheus`, `./prometheus/config/prometheus.yml:/etc/prometheus/prometheus.yml:ro` | monitoring |
| alloy | `cap_drop: ALL`, `cap_add: [DAC_READ_SEARCH]`, `group_add: [adm, systemd-journal]` | as above | monitoring, homelab |
| cadvisor | **`privileged: true`** (`no-new-privileges` MUST be omitted) | `/:/rootfs:ro`, `/var/run:/var/run:ro`, `/sys:/sys:ro`, `/var/lib/docker/:/var/lib/docker:ro`, `/dev/disk/:/dev/disk:ro`, `/cgroup:/cgroup:ro` | monitoring |
| smartctl-exporter | **`privileged: true`** | `/dev:/dev` (or specifically `/dev/nvme0,/dev/sd[a-e]`) | monitoring |
| intel-gpu-exporter | **`privileged: true`**, `pid: host` | `/dev/dri:/dev/dri`, `/sys:/sys:ro` | monitoring |
| shekel-postgres-exporter | unprivileged, `cap_drop: ALL` | none | monitoring, **backend (external)** |
| immich-postgres-exporter | unprivileged, `cap_drop: ALL` | none | monitoring, **immich_default (external)** |
| nginx-exporter | unprivileged | none | monitoring, homelab |

**Privileged conflict** with `no-new-privileges:true`: cAdvisor, smartctl-exporter, and intel-gpu-exporter **cannot** combine these. This is an intentional, documented deviation from the AUDIT.md convention. Each service gets a comment in compose explaining why.

`smartctl-exporter` args: `--smartctl.device-include=/dev/nvme0,/dev/sda,/dev/sdb,/dev/sdc,/dev/sdd,/dev/sde --smartctl.interval=300s`.

`nginx-exporter` args: `--nginx.scrape-uri=http://nginx:8088/stub_status`.

### Postgres exporter prerequisite SQL (run once per instance, by superuser)
```sql
CREATE USER pg_exporter WITH PASSWORD '<from secrets file>';
GRANT pg_monitor TO pg_exporter;          -- built-in read-only role since PG10
ALTER USER pg_exporter SET search_path = pg_catalog, public;
```
Apply to **shekel-prod-db** and **immich_postgres** separately. Connection strings via `DATA_SOURCE_NAME_FILE=/run/secrets/<name>_pg_exporter_password` (postgres_exporter ≥ v0.15 supports `_FILE`).

### Native metrics endpoints — flips required
- **Jellyfin:** Edit `/opt/docker/jellyfin/config/system.xml`, set `<EnableMetrics>true</EnableMetrics>`. Restart `jellyfin`.
- **Immich:** Edit `/opt/docker/immich/.env`, add `IMMICH_TELEMETRY_INCLUDE=all`. Confirm metrics ports `8081` (API) and `8082` (microservices) are exposed on the `homelab` network — do NOT publish to host. Restart `immich-server`.
- **Cloudflared:** Already exposes `:2000/metrics` (existing `--metrics 0.0.0.0:2000` in main compose). No change.
- **Unifi, Calibre-Web:** No native metrics. Container-level metrics from cAdvisor + log aggregation are sufficient.

### Phase 2 verification
```bash
docker compose -f /opt/docker/monitoring/docker-compose.yml up -d
docker exec prometheus wget -qO- 'http://localhost:9090/api/v1/targets' | grep -oE '"health":"[^"]+"' | sort -u
# Expect: only "health":"up" — anything else means a target is failing.

docker exec loki wget -qO- 'http://localhost:3100/loki/api/v1/labels'
# Expect labels: compose_project, compose_service, container, hostname, job, level, stream, unit, ...

# Smoke-test journald and a container log made it through:
docker exec loki wget -qO- 'http://localhost:3100/loki/api/v1/query?query=%7Bjob%3D%22systemd-journal%22%7D' | head -c 300
docker exec loki wget -qO- 'http://localhost:3100/loki/api/v1/query?query=%7Bcompose_service%3D%22jellyfin%22%7D' | head -c 300
```

---

## Phase 3 — reverse proxy integration (nginx)

### Files
- `/opt/docker/nginx/conf.d/grafana.conf` (new)
- `/opt/docker/nginx/conf.d/00-stub-status.conf` (new — internal-only nginx metrics endpoint on port 8088)

### `00-stub-status.conf`
```nginx
server {
    listen 8088;
    server_name _;
    location = /stub_status {
        stub_status;
        access_log off;
        allow 172.16.0.0/12;    # Docker bridge networks
        allow 10.0.0.0/8;       # LAN safety net
        deny all;
    }
}
```
Internal port — not published to host. `nginx-exporter` reaches `nginx:8088/stub_status` over the `homelab` network.

### `grafana.conf` (mirrors your existing per-service blocks)
```nginx
server {
    listen 80;
    server_name grafana.saltyreformed.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    http2 on;
    server_name grafana.saltyreformed.com;

    ssl_certificate     /etc/nginx/certs/fullchain.pem;
    ssl_certificate_key /etc/nginx/certs/key.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;

    # LAN-only
    allow 10.10.101.0/24;
    deny all;

    client_max_body_size 100M;

    location / {
        set $upstream_grafana grafana:3000;
        proxy_pass http://$upstream_grafana;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;

        proxy_read_timeout 300s;
    }

    location /api/live/ {
        set $upstream_grafana grafana:3000;
        proxy_pass http://$upstream_grafana;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_set_header Host $host;
    }
}
```

DNS: add an OPNsense override for `grafana.saltyreformed.com → 10.10.101.101` (matching your existing pattern for `shekel.saltyreformed.com`).

### Phase 3 verification
```bash
docker exec nginx nginx -t                               # config OK
docker exec nginx nginx -s reload
curl -k https://grafana.saltyreformed.com  -o /dev/null -w '%{http_code}\n'   # 200 from a LAN host
# From outside LAN: connection refused / 403 — confirms allow/deny works.
```

---

## Phase 4 — provisioned datasources + dashboards

### Files
- `/opt/docker/monitoring/grafana/provisioning/datasources/datasources.yaml`
- `/opt/docker/monitoring/grafana/provisioning/dashboards/dashboards.yaml`
- `/opt/docker/monitoring/grafana/provisioning/dashboards/*.json` (8 dashboards listed below)

### `datasources.yaml`
```yaml
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    editable: false
  - name: Loki
    type: loki
    access: proxy
    url: http://loki:3100
    editable: false
    jsonData:
      derivedFields:
        - name: request_id
          matcherRegex: '"request_id":"([^"]+)"'
          url: '$${__value.raw}'
```

### `dashboards.yaml`
```yaml
apiVersion: 1
providers:
  - name: 'homelab'
    orgId: 1
    folder: 'Homelab'
    type: file
    disableDeletion: false
    updateIntervalSeconds: 30
    allowUiUpdates: true
    options:
      path: /etc/grafana/provisioning/dashboards
      foldersFromFilesStructure: false
```

### Pre-built dashboards to import (Grafana.com IDs)
| ID | Title |
|---|---|
| 1860 | Node Exporter Full |
| 19792 | cAdvisor Compute Resources (current; 893 is stale, breaks on cAdvisor 0.56) |
| 13639 | Loki / Logs |
| 7587 | Cloudflare Tunnel |
| 9628 | PostgreSQL (postgres_exporter) |
| 12708 | Nginx exporter |
| 22604 | Smartctl Exporter |
| 23251 | Intel GPU Metrics |

Plus a custom **Shekel Overview** dashboard with: request rate, p95 latency (Loki LogQL on `request_duration`), error rate (`status >= 500`), `slow_request` count, DB connection saturation from `postgres_exporter{instance="shekel"}`. Ship a JSON skeleton.

### Phase 4 verification
- Log into `https://grafana.saltyreformed.com` with the admin password from `secrets/grafana_admin_password`.
- "Homelab" folder is populated with all dashboards.
- Each dashboard renders with non-empty panels (initial data may take ~5 min to backfill).
- In Explore, run `{compose_service="shekel-prod-app"}` against Loki — see Shekel logs (raw initially; structured once Phase 6 lands).

---

## Phase 5 — alerting (Grafana unified alerts)

Files: `grafana/provisioning/alerting/*.yaml`. Defer Alertmanager unless unified alerting proves insufficient.

Suggested initial rules:
- Disk space < 10% on any host filesystem
- NVMe SMART critical warning (`smartctl_device_critical_warning > 0`)
- Container OOM kill (`container_memory_failures_total{type="oom"} > 0`)
- Postgres connection saturation > 80%
- NTP offset > 1s (`node_timex_offset_seconds`)
- Any monitored target `up == 0` for > 5m

---

## Phase 6 — Shekel logging fix (upstream)

Apply this PR to `https://github.com/saltyreformed/shekel`:

In `app/utils/logging_config.py`, replace the `formatters.json` block. Recommended path is **Option B** (true RFC3339, microsecond precision):

```python
import datetime as _dt
from pythonjsonlogger.json import JsonFormatter

class RFC3339JsonFormatter(JsonFormatter):
    def formatTime(self, record, datefmt=None):
        return _dt.datetime.fromtimestamp(
            record.created, tz=_dt.timezone.utc
        ).isoformat(timespec="microseconds").replace("+00:00", "Z")
```

```python
"formatters": {
    "json": {
        "()": "app.utils.logging_config.RFC3339JsonFormatter",
        "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        "rename_fields": {
            "asctime":   "timestamp",
            "levelname": "level",
            "name":      "logger",
        },
    },
},
```

Why this is the proper fix: `python-json-logger==4.1.0` removed the `timestamp=True` kwarg but kept `rename_fields`. The `format` string is parsed for *which* attributes to include; `JsonFormatter` outputs JSON regardless. Overriding `formatTime` ensures sub-second precision (glibc `strftime` does not support `%f`).

**Activation order:**
1. PR + merge in saltyreformed/shekel
2. Image rebuild and `docker compose pull && docker compose up -d shekel-prod-app`
3. Verify: `docker logs --tail 5 shekel-prod-app | grep '"timestamp"'`
4. The `loki.process.shekel` block in `config.alloy` already expects field name `timestamp` — once verified, it picks up structured fields automatically.

Until Phase 6 ships, Shekel logs flow as plain Docker container logs and arrive in Loki without label extraction — searchable by `compose_service="shekel-prod-app"`, just less queryable on structured fields.

---

## Resource limits (apply per service in compose)

Starting points; tune after a week of operation via `docker stats`:

| Service | mem limit | mem reservation |
|---|---|---|
| prometheus | 2G | 512M |
| loki | 1.5G | 384M |
| grafana | 512M | 128M |
| alloy | 1G | 256M |
| cadvisor | 256M | 64M |
| smartctl-exporter | 64M | 32M |
| intel-gpu-exporter | 128M | 32M |
| nginx-exporter | 64M | 32M |
| {shekel,immich}-postgres-exporter | 64M | 32M |

Total: ~5.6G limit / ~1.5G reservation — well within your 60G headroom.

---

## Backups

Add to your existing `/opt/docker/backup.sh`:
- `/opt/docker/monitoring/grafana/data/grafana.db` (SQLite — only piece worth backing up; dashboards are provisioned from disk and self-restore)
- `/opt/docker/monitoring/secrets/` (chmod 700)

Prometheus and Loki data are time-bounded and re-creatable; skip them.

---

## Critical files index

### New
- `/opt/docker/monitoring/docker-compose.yml`
- `/opt/docker/monitoring/.env`
- `/opt/docker/monitoring/secrets/{grafana_admin_password,shekel_pg_exporter_password,immich_pg_exporter_password}`
- `/opt/docker/monitoring/loki/config/loki.yaml`
- `/opt/docker/monitoring/prometheus/config/prometheus.yml`
- `/opt/docker/monitoring/alloy/config/config.alloy`
- `/opt/docker/monitoring/grafana/provisioning/datasources/datasources.yaml`
- `/opt/docker/monitoring/grafana/provisioning/dashboards/dashboards.yaml`
- `/opt/docker/monitoring/grafana/provisioning/dashboards/*.json`
- `/opt/docker/nginx/conf.d/grafana.conf`
- `/opt/docker/nginx/conf.d/00-stub-status.conf`
- `/etc/systemd/journald.conf.d/00-retention.conf` (root)
- `/etc/docker/daemon.json` (root)

### Edited
- `/opt/docker/jellyfin/config/system.xml` — `<EnableMetrics>true</EnableMetrics>`
- `/opt/docker/immich/.env` — `IMMICH_TELEMETRY_INCLUDE=all`
- Upstream `app/utils/logging_config.py` in saltyreformed/shekel (Phase 6)

### Existing patterns reused (no edits needed beyond what's listed above)
- `/opt/docker/nginx/nginx.conf` (already has `resolver 127.0.0.11 valid=30s` and `set $upstream_x` pattern — `grafana.conf` mirrors `shekel.conf`/`immich.conf`)
- `/opt/docker/nginx/certs/{fullchain.pem,privkey.pem}` (existing wildcard cert)
- The empty `monitoring` Docker network (already declared)
- `homelab` Docker network (joined by Grafana, Alloy, nginx-exporter)
- `backend` and `immich_default` networks (joined as `external` by the postgres exporters)
- `cloudflared --metrics 0.0.0.0:2000` (already configured per AUDIT.md item 16)

---

## End-to-end verification

After all phases:

1. **Logs**
   - Loki has labels: `compose_project`, `compose_service`, `container`, `hostname`, `job=docker|systemd-journal`, `level`, `stream`, `unit`.
   - `{compose_service="jellyfin"}` returns container logs.
   - `{job="systemd-journal", unit="docker.service"}` returns Arch system logs.
   - After Phase 6: `{compose_service="shekel-prod-app"} | json` extracts `timestamp`, `level`, `logger`, `request_id`, `event` as labels.
2. **Metrics**
   - `up` = 1 for: node, cadvisor, smartctl, intel_gpu, postgres{instance=shekel|immich}, nginx, cloudflared, jellyfin, immich, prometheus, loki, grafana, alloy.
   - `node_hwmon_temp_celsius` returns CPU + GPU + NVMe + DDR5 temps.
   - `smartctl_device_critical_warning` returns 0 across all 5 disks.
   - `container_memory_usage_bytes` returns per-compose_service rows.
3. **Dashboards**
   - Node Exporter Full shows CPU/RAM/disk/network for the host.
   - cAdvisor Compute Resources shows all running containers.
   - PostgreSQL shows both shekel + immich instances.
   - Smartctl shows all 6 devices (1 NVMe + 5 spinning).
   - Intel GPU shows engine utilization during Jellyfin transcode.
4. **Reverse proxy**
   - LAN client → `https://grafana.saltyreformed.com` → Grafana login. Cert valid (existing wildcard).
   - Off-LAN client → blocked by `allow 10.10.101.0/24; deny all;`.
5. **Self-monitoring**
   - Stack scrapes its own metrics; failures show up as `up == 0` and trigger Phase 5 alerts.

---

## Sources verified

- Grafana Alloy v1.16.1 — https://github.com/grafana/alloy/releases
- Loki v3.7.1 — https://github.com/grafana/loki/releases
- Prometheus v3.11.3 — https://github.com/prometheus/prometheus/releases
- Grafana v13.0.1 — https://github.com/grafana/grafana/releases
- cAdvisor v0.56.2 — https://github.com/google/cadvisor/releases
- smartctl_exporter v0.14.0 — https://github.com/prometheus-community/smartctl_exporter/releases
- postgres_exporter v0.19.1 — https://github.com/prometheus-community/postgres_exporter/releases
- nginx-prometheus-exporter 1.5.1 — https://github.com/nginx/nginx-prometheus-exporter/releases
- Loki TSDB single-binary config — https://grafana.com/docs/loki/latest/configure/storage/
- Alloy `prometheus.exporter.unix` — https://grafana.com/docs/alloy/latest/reference/components/prometheus/prometheus.exporter.unix/
- Alloy `loki.source.docker` — https://grafana.com/docs/alloy/latest/reference/components/loki/loki.source.docker/
- Alloy `loki.source.journal` — https://grafana.com/docs/alloy/latest/reference/components/loki/loki.source.journal/
- Immich monitoring — https://docs.immich.app/features/monitoring/
- Jellyfin monitoring — https://jellyfin.org/docs/general/post-install/networking/advanced/monitoring/
- python-json-logger v4.x quickstart — https://nhairs.github.io/python-json-logger/latest/quickstart/
- Intel GPU exporter — https://github.com/onedr0p/intel-gpu-exporter
- Shekel logging_config.py — https://github.com/saltyreformed/shekel/blob/main/app/utils/logging_config.py
