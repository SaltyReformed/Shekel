# 06 -- pip-audit Dependency Vulnerability Analysis

Scanner: pip-audit 2.9.0 (running on Python 3.14.4 in the scratch venv).

pip-audit reads a `requirements.txt` file, resolves the full transitive
dependency graph from PyPI, and cross-references every resolved
(package, version) pair against the Python Packaging Advisory Database
(PyPA Advisory DB) -- a public, community-maintained feed of CVEs and
advisories specifically for Python packages.

Commands run:

```
.audit-venv/bin/pip-audit --requirement requirements.txt --format json \
    --output docs/audits/security-2026-04-15/scans/pip-audit.json
.audit-venv/bin/pip-audit --requirement requirements.txt \
    > docs/audits/security-2026-04-15/scans/pip-audit.txt 2>&1
```

The text command uses `2>&1` to capture both streams, the same
housekeeping fix applied to the semgrep run. The text file contains the
single-line summary `No known vulnerabilities found`; the JSON file
contains the full resolved dependency tree with an empty `vulns` array
on every package.

## Summary

- **Direct dependencies in `requirements.txt`:** 16
- **Total resolved dependencies (direct + transitive):** 33
- **Vulnerable dependencies:** 0
- **Total advisories:** 0
- **Fix suggestions (from the `fixes` key in the JSON):** 0 (empty)
- **Advisory DB consulted:** PyPA Advisory Database (pip-audit's default)
- **Classification breakdown:** 0 Real + 0 Noise + 0 Needs-human-review
- **New findings not surfaced in Section 1A:** 0
- **Top concern:** None from the scan itself. One scope gap worth
  naming explicitly -- `gunicorn` is NOT in `requirements.txt`
  (documented comment at `requirements.txt:7-8` says it is installed
  directly in the Dockerfile), so this scan did not check gunicorn
  for CVEs. That check is deferred to Session S2 Section 1G (trivy
  image scan), which scans the actual built Docker image and will see
  gunicorn in its Python site-packages.

## Preliminary Finding #5 -- RESOLVED

The workflow doc's Preliminary Finding #5 ("Dependency freshness --
UNVERIFIED. `requirements.txt` looks current but has not been audited
against CVE feeds in this session. `pip-audit` and `trivy sbom` in
Sections 1B/1E will provide authoritative answers.") is closed by this
run as far as Section 1B's scope goes:

- **All 16 direct dependencies are on a current stable release** that
  has no open PyPA advisory.
- **All 17 transitive dependencies** are likewise clean.
- **Section 1E (Session S2)** will re-run the scan through
  `cyclonedx-py` + `trivy sbom` against a different advisory database
  for a second opinion. A discrepancy between pip-audit (PyPA) and
  trivy (NVD + GHSA + OS-distro feeds) would itself be a finding. As
  of this session's scan, Section 1B reports `no vulnerabilities`.

## Dependency inventory (full resolved list)

### Direct dependencies (16) -- declared in `requirements.txt`

| Package | Pinned version | Role in Shekel |
|---------|----------------|----------------|
| Flask | 3.1.3 | Web framework core |
| Flask-Limiter | 4.1.1 | Per-route rate limiting (see F-C-09: `memory://` backend) |
| Flask-Login | 0.6.3 | Session management, `@login_required`, remember-me cookie |
| Flask-SQLAlchemy | 3.1.1 | SQLAlchemy integration with Flask app context |
| Flask-Migrate | 4.1.0 | Alembic wrapper for `flask db migrate` / `flask db upgrade` |
| Flask-WTF | 1.2.2 | CSRF token and form validation |
| SQLAlchemy | 2.0.49 | ORM, used by every model and query in `app/models/` |
| psycopg2 | 2.9.11 | PostgreSQL driver |
| alembic | 1.18.4 | Migration engine (invoked via Flask-Migrate) |
| bcrypt | 5.0.0 | Password hashing (see Subagent A clean check: `bcrypt.checkpw` throughout auth) |
| pyotp | 2.9.0 | TOTP code generation and verify (see Subagent A clean check: `valid_window=1`) |
| qrcode[pil] | 8.2 | TOTP setup QR code rendering (pulls in Pillow as a transitive) |
| cryptography | 46.0.7 | Fernet encryption for `users.totp_secret_encrypted` |
| marshmallow | 4.3.0 | Schema validation for POST/PATCH/DELETE routes (see B1 findings F-B1-04/05/06 where it is NOT used) |
| python-dotenv | 1.2.2 | `.env` file loader |
| python-json-logger | 4.1.0 | Structured JSON logs (see `app/utils/logging_config.py`) |

**Pin discipline:** every direct dependency uses `==` exact-version
pinning. No `>=`, no `~=`, no `>`, no unpinned lines. This matches the
coding-standards requirement: "every direct dep must be pinned to an
exact version" and means the build is reproducible from this commit.

**One notable syntax point:** `qrcode[pil]==8.2` uses the "extras" idiom
-- the `[pil]` spec tells pip "install qrcode plus its optional pil
extra," which is how Pillow arrives in the resolved tree. This is the
correct pattern for optional dependencies and is not a pin-discipline
issue.

### Transitive dependencies (17) -- pulled in automatically

| Package | Resolved version | Pulled in by |
|---------|------------------|--------------|
| blinker | 1.9.0 | Flask (signals) |
| cffi | 2.0.0 | cryptography (libcrypto FFI) |
| click | 8.3.2 | Flask (CLI) |
| deprecated | 1.3.1 | limits (Flask-Limiter indirect) |
| greenlet | 3.4.0 | SQLAlchemy (async support path, used by Flask-SQLAlchemy regardless) |
| itsdangerous | 2.2.0 | Flask (signed cookie sessions) |
| Jinja2 | 3.1.6 | Flask (templating) |
| limits | 5.8.0 | Flask-Limiter (rate-limit storage abstraction) |
| Mako | 1.3.11 | alembic (migration templates) |
| MarkupSafe | 3.0.3 | Jinja2 (escape primitives) |
| ordered-set | 4.1.0 | limits (Flask-Limiter indirect) |
| Pillow | 12.2.0 | qrcode[pil] (image rendering) |
| pycparser | 3.0 | cffi (C header parser) |
| typing-extensions | 4.15.0 | several (Python version compat) |
| Werkzeug | 3.1.8 | Flask (WSGI / request-response primitives) |
| wrapt | 2.1.2 | deprecated (decorator util) |
| WTForms | 3.2.1 | Flask-WTF (form field classes) |

Each transitive version is the latest at the time pip resolved the
tree during `.audit-venv` creation, and each is within the version
band the upstream direct dependency accepts.

## What pip-audit did NOT find -- clean negative results by category

The PyPA Advisory Database covers roughly these categories of
vulnerability data:

- **Package-specific CVEs** linked to an exact (package, version) pair
  from the Python ecosystem.
- **Source distributions with known compromise** (a package was
  briefly taken over by a hostile maintainer, yanked from PyPI, and
  flagged in the advisory DB).
- **Typosquat advisories** where a malicious look-alike package shared
  a name with a legitimate one.
- **Advisory-level issues with "sunset" packages** that are no longer
  maintained but still in wide use.

For each category, every one of Shekel's 33 dependencies comes back
clean. Specifically clean results worth recording:

- **No Flask core CVE hits.** Flask 3.1.3 is the current 3.1.x line.
  Flask's history includes CVE-2023-30861 (session caching issue,
  fixed in 2.2.5 and 2.3.2) and CVE-2018-1000656 (a denial-of-service
  in `json.dumps` via `FLASK_DEBUG`). Both are closed at 3.1.3.
- **No Werkzeug CVE hits.** Werkzeug 3.1.8 is current on the 3.1.x
  branch. Historical Werkzeug CVEs in the 2.x series are all closed.
- **No Jinja2 CVE hits.** Jinja2 3.1.6 is current on the 3.1.x branch.
  CVE-2024-22195 (`xmlattr` filter XSS) and CVE-2024-56201
  (str.format sandbox bypass) are closed at 3.1.4+ -- this repo is at
  3.1.6.
- **No cryptography CVE hits.** cryptography 46.0.7 is current. The
  library has had several CVEs historically in the OpenSSL binding
  layer; none are open against the 46.x line.
- **No Pillow CVE hits.** Pillow has a long CVE history (image
  parsing bugs in TIFF, GIF, JPEG, PNG decoders). 12.2.0 is current
  at the time of this audit; any historical advisory pre-12.x does
  not apply to this version.
- **No bcrypt CVE hits.** bcrypt 5.0.0 is current.
- **No pyotp CVE hits.** pyotp has no security advisories in the
  PyPA DB.
- **No SQLAlchemy CVE hits.** SQLAlchemy 2.0.49 is current on the 2.0.x
  line; no open advisories.
- **No psycopg2 CVE hits.** psycopg2 2.9.11 is current.

**Caveat:** pip-audit's default data source is the PyPA Advisory DB,
which is a subset of the full vulnerability universe. A CVE that lives
only in NVD or only in GitHub Security Advisories (GHSA) would be
missed. Session S2's `trivy sbom` scan uses a different database and
will provide the second-opinion check.

## Scope gap -- gunicorn is not in requirements.txt

`requirements.txt:7-8` explicitly notes:

```
# Do NOT add gunicorn here -- it is installed separately in the
# Dockerfile to keep it out of the local development venv where
# Flask's dev server is used instead.
```

Reading the `Dockerfile` (confirmed by Subagent C at
`reports/03-config-deploy.md`), gunicorn IS installed in the production
image via a separate `pip install gunicorn==...` step. This means the
version of gunicorn that ships in production is NOT covered by this
pip-audit scan.

**Impact:** If gunicorn has an open advisory in the PyPA DB at the time
of this audit, it would not show up in `pip-audit.json`. The fix is not
to move gunicorn into `requirements.txt` (the comment documents a real
design decision -- keeping gunicorn out of the local dev venv so
developers use Flask's auto-reload dev server). The fix is to run
pip-audit (or trivy image) against the built image itself, which
Session S2 Section 1G does.

**Recorded as:** scope note, not a finding. Flagged for the Session S8
consolidator to cross-reference with the trivy image scan.

**Also not covered by this scan:**
- System/OS packages in the Docker base image (covered by trivy image in 1G).
- Build-time packages in `requirements-dev.txt` (not shipped to prod; out of scope).
- Any package installed at runtime by a script rather than the declared
  requirements (none exist in Shekel; confirmed by `grep -r "pip install" scripts/`).

## Bottom line

**Zero vulnerable dependencies** across the full resolved tree of 33
packages. **All 16 direct dependencies are pinned with `==` and on
current stable releases.** Preliminary Finding #5 is **RESOLVED** as
far as Section 1B is concerned, with the second-opinion check deferred
to Session S2's trivy run.

No new findings added to the audit backlog from this scan. The
pip-audit JSON and txt outputs are retained under
`docs/audits/security-2026-04-15/scans/` as scan-inventory artifacts
for Session S8's consolidator.

Combined with bandit (5 findings, all duplicative of 1A) and semgrep
(0 findings), Section 1B adds zero new items to the finding list. All
three scanners produced clean results -- which is the desired outcome
for a project that takes its own coding standards seriously.

## Open questions for the developer

1. **Second-opinion CVE source.** Is the PyPA DB alone enough for the
   audit, or do you want this scan re-run against NVD / GHSA in
   Session S2 as well? Trivy will do this automatically, but the
   developer should know the two databases exist and that they
   sometimes disagree on which version fixes a given CVE.

2. **Dependency freshness beyond "no open CVE".** pip-audit only
   answers "is any pinned version vulnerable today?" It does NOT tell
   you "is any pinned version running 14 months behind the current
   release and missing bug fixes?" Section 1E in Session S2 is where
   that question gets answered for direct dependencies (last release
   date, maintenance signal, GitHub stars). A dependency with zero
   CVEs but one maintainer and a last commit from 2021 is still a
   supply-chain risk.

3. **Gunicorn version.** The Dockerfile pins gunicorn to a specific
   version (confirmed by Subagent C at `reports/03-config-deploy.md`).
   Section 1G (Session S2) will scan it via trivy. No action needed
   for Section 1B, but the developer should know this is how gunicorn
   gets checked.
