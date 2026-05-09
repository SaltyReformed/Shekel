# Deployment Configurations

This directory holds the version-controlled deployment configs for
Shekel. Two deployment modes are supported. Each mode has a distinct set
of files in this directory; the wrong mode's files are inert in the
other mode.

| Mode | Reverse proxy | Scope | Active files |
|------|---------------|-------|--------------|
| Bundled | `shekel-prod-nginx` (in-stack) | Single-stack hosts, fresh-host bring-up, the README Quick Start | `nginx-bundled/nginx.conf` |
| Shared | A separate Nginx managed outside this stack | Hosts that already run a homelab Nginx in front of multiple services | `nginx-shared/nginx.conf`, `nginx-shared/conf.d/shekel.conf`, `docker-compose.prod.yml` |

The maintainer's production homelab runs **shared mode**. Generic
deployments default to **bundled mode**.

## Directory Layout

```
deploy/
├── README.md                           This file.
├── nginx-bundled/
│   └── nginx.conf                      Mounted into the bundled
│                                       shekel-prod-nginx service by
│                                       the repo's docker-compose.yml.
├── nginx-shared/
│   ├── nginx.conf                      Mirror of /opt/docker/nginx/
│   │                                   nginx.conf on the homelab host.
│   └── conf.d/
│       └── shekel.conf                 Mirror of /opt/docker/nginx/
│                                       conf.d/shekel.conf (per-service
│                                       vhost included by the file
│                                       above).
└── docker-compose.prod.yml             Compose override that selects
                                        shared mode (joins the app to
                                        the homelab network and parks
                                        the bundled nginx in the
                                        "disabled" profile).
```

## Bundled Mode (default)

Used by the README Quick Start and by anyone bringing up Shekel on a
host that does not already have a reverse proxy.

```bash
cd /opt/shekel
docker compose up -d
```

The repo's `docker-compose.yml` mounts `deploy/nginx-bundled/nginx.conf`
into the `shekel-prod-nginx` container at `/etc/nginx/nginx.conf:ro`.
Edits to that file take effect on the next `docker compose up -d`.

To customize without forking, drop a sibling
`docker-compose.override.yml` next to `docker-compose.yml` and adjust
the `nginx.volumes` block. Do not edit the repo file in place on the
host -- the repo file is the source of truth and is reset on every
`git pull`.

## Shared Mode (homelab production)

Used when a separate, longer-lived Nginx already fronts other services
on the host (Jellyfin, Unifi, etc.). The shared Nginx is defined in a
*different* compose file on the host (`/opt/docker/docker-compose.yml`)
and is *not* in this repo.

```bash
cd /opt/shekel
docker compose \
  -f docker-compose.yml \
  -f deploy/docker-compose.prod.yml \
  up -d
```

In shared mode:

* `deploy/docker-compose.prod.yml` joins `shekel-prod-app` to the
  external `homelab` bridge network and parks the bundled
  `shekel-prod-nginx` service in the `disabled` profile, so
  `docker compose up -d` starts only `db`, `redis`, and `app`.
* The shared Nginx (managed under `/opt/docker/nginx/` on the host)
  proxies traffic to `shekel-prod-app:8000` over the `homelab`
  network, using the vhost in `deploy/nginx-shared/conf.d/shekel.conf`.
* `deploy/nginx-shared/nginx.conf` is the main config for the shared
  Nginx. The repo file is the source of truth; the host copy at
  `/opt/docker/nginx/nginx.conf` must match.

The `homelab` network must exist before `docker compose up`:

```bash
docker network ls --filter name=homelab
# If missing:
docker network create homelab
```

## Sync Procedure (Shared Mode)

The shared-mode files in this directory must stay in step with the
host. The repo is the source of truth.

1. Make the change in the repo on the `dev` branch and commit.
2. On the host, pull the change and copy each updated file to its
   runtime path:
   ```bash
   cd /opt/shekel
   git pull --ff-only
   sudo cp deploy/nginx-shared/nginx.conf            /opt/docker/nginx/nginx.conf
   sudo cp deploy/nginx-shared/conf.d/shekel.conf    /opt/docker/nginx/conf.d/shekel.conf
   sudo cp deploy/docker-compose.prod.yml            /opt/docker/shekel/docker-compose.override.yml
   ```
3. Validate the Nginx config before reloading:
   ```bash
   sudo docker exec nginx nginx -t
   ```
4. Reload Nginx without dropping connections:
   ```bash
   sudo docker exec nginx nginx -s reload
   ```
5. If only the compose override changed, recreate the affected
   containers:
   ```bash
   cd /opt/docker/shekel
   docker compose up -d
   ```

See `docs/runbook.md` -> "Shared-mode deployment" for full details
including the rollback procedure and the planned `scripts/config_audit.py`
drift check.

## See Also

* `docs/runbook.md` -- full operational runbook (deploy, restart,
  rollback, troubleshooting).
* `scripts/config_audit.py` -- skeleton drift-check script that will
  compare the on-host runtime configs against the repo copies.
* `docs/audits/security-2026-04-15/scans/` -- audit-window snapshots of
  the on-host configs, retained as historical evidence and to verify
  that the runtime config has not drifted further since the audit.
