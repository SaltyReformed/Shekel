# Shekel Monitoring Stack Setup

## Overview

Shekel outputs structured JSON logs to stdout. These logs are scraped by
Promtail and shipped to Loki for querying via Grafana.

## Architecture

```
Shekel App (JSON stdout) → Docker log driver → Promtail → Loki → Grafana
```

## Prerequisites

- Docker and Docker Compose on the Proxmox host
- Shekel stack running via `docker-compose.yml`

## Setup Steps

### 1. Create the monitoring network

```bash
docker network create monitoring
```

### 2. Add the network to Shekel's docker-compose.yml

```yaml
services:
  app:
    networks:
      - default
      - monitoring

networks:
  monitoring:
    external: true
```

### 3. Create monitoring docker-compose.yml

Save this as `monitoring/docker-compose.yml` on the Proxmox host:

```yaml
services:
  loki:
    image: grafana/loki:latest
    container_name: loki
    restart: unless-stopped
    ports:
      - "3100:3100"
    volumes:
      - loki-data:/loki
    networks:
      - monitoring

  promtail:
    image: grafana/promtail:latest
    container_name: promtail
    restart: unless-stopped
    volumes:
      - ./promtail-config.yml:/etc/promtail/config.yml
      - /var/run/docker.sock:/var/run/docker.sock
    depends_on:
      - loki
    networks:
      - monitoring

  grafana:
    image: grafana/grafana:latest
    container_name: grafana
    restart: unless-stopped
    ports:
      - "3000:3000"
    volumes:
      - grafana-data:/var/lib/grafana
    environment:
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD:-admin}
    depends_on:
      - loki
    networks:
      - monitoring

volumes:
  loki-data:
  grafana-data:

networks:
  monitoring:
    external: true
```

### 4. Start the monitoring stack

```bash
cd monitoring
docker compose up -d
```

### 5. Configure Grafana

1. Open `http://<proxmox-ip>:3000`
2. Log in (admin / admin, change password on first login)
3. Add Data Source → Loki → URL: `http://loki:3100`
4. Go to Explore → Select Loki → Run queries

## Useful LogQL Queries

| Purpose | Query |
|---------|-------|
| All auth events | `{container="shekel-app"} \| json \| category="auth"` |
| Login failures | `{container="shekel-app"} \| json \| event="login_failed"` |
| Slow requests | `{container="shekel-app"} \| json \| event="slow_request"` |
| All errors | `{container="shekel-app"} \| json \| level="ERROR"` |
| Specific user | `{container="shekel-app"} \| json \| user_id="1"` |
| Trace a request | `{container="shekel-app"} \| json \| request_id="<uuid>"` |
| Password changes | `{container="shekel-app"} \| json \| event="password_changed"` |
| MFA events | `{container="shekel-app"} \| json \| event=~"mfa_.*"` |
| Business events | `{container="shekel-app"} \| json \| category="business"` |

## Audit Log Retention

The `scripts/audit_cleanup.py` script deletes old audit log rows from
PostgreSQL. Schedule it via cron on the Proxmox host:

```cron
# Daily at 3:00 AM -- delete audit rows older than 365 days.
0 3 * * * docker exec shekel-app python scripts/audit_cleanup.py
```

To preview what would be deleted without actually deleting:

```bash
docker exec shekel-app python scripts/audit_cleanup.py --dry-run
```

## Troubleshooting

- **No logs in Grafana:** Check that `docker logs shekel-app` shows JSON
  output. Verify the Promtail container can see the Shekel container
  (`docker logs promtail`).
- **Promtail can't discover containers:** Ensure `/var/run/docker.sock`
  is mounted in the Promtail container.
- **Network issues:** Verify both stacks share the `monitoring` network
  (`docker network inspect monitoring`).
- **Gunicorn access logs appearing:** The entrypoint disables Gunicorn's
  native access log (`--access-logfile ""`). Flask's `_log_request_summary()`
  handles request logging in JSON format instead.
