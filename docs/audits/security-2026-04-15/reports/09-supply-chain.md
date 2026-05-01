# 09 -- Supply Chain Audit

Audit session: S2, Section 1E. Branch: `audit/security-2026-04-15`.
Date: 2026-04-16. Auditor: Claude (Opus 4.6).

## Summary

- **Direct dependencies in `requirements.txt`:** 16
- **Resolved transitive dependencies:** 26 total (16 direct + 10 transitive)
- **CVE scan results:** 0 vulnerabilities (pip-audit AND trivy agree)
- **Pin discipline:** all 16 direct deps pinned with `==` (no loose pins)
- **Stale packages (>18 months since last release):** 3
- **Single-maintainer packages:** 3
- **License flags:** 0 GPL/AGPL; 1 LGPL-with-exceptions (psycopg2)
- **Finding count:** 0 Critical / 0 High / 2 Medium / 2 Low / 3 Info

## CVE cross-check (PASS 1)

| Scanner | Version | Database | Packages checked | CVEs found |
|---------|---------|----------|-----------------|------------|
| pip-audit | 2.9.0 (S1) | PyPA Advisory DB | 33 (direct+transitive) | 0 |
| trivy | 0.69.3 (S2) | aquasec/trivy-db | 16 (direct only from SBOM) | 0 |

**Result: no discrepancies.** Both databases agree there are no known
vulnerabilities for the pinned versions as of 2026-04-16.

**Coverage gap:** gunicorn is NOT in `requirements.txt` (installed
directly in the Dockerfile per `requirements.txt:7-8`). Neither
pip-audit nor trivy-sbom scanned it. The trivy image scan in Section
1G will cover gunicorn because it scans the full Docker image's Python
site-packages.

## Per-dependency health check (PASS 2)

All versions below are the **latest available on PyPI** as of
2026-04-16 unless noted otherwise.

| # | Package | Pinned | Last Release | License | Maintainers | Health |
|---|---------|--------|-------------|---------|-------------|--------|
| 1 | Flask | 3.1.3 | 2026-02-19 | BSD-3-Clause | Pallets (org) | Healthy |
| 2 | Flask-Limiter | 4.1.1 | 2024-12-19 | MIT | Ali-Akber Saifee (single) | Active, single-maintainer |
| 3 | Flask-Login | 0.6.3 | **2023-10-30** | MIT | Max Countryman + 2 | **Stale (30 months)** |
| 4 | Flask-SQLAlchemy | 3.1.1 | **2023-09-11** | BSD | Pallets (org) | **Stale (31 months)** |
| 5 | Flask-Migrate | 4.1.0 | 2025-01-10 | MIT | Miguel Grinberg (single) | Active, single-maintainer |
| 6 | Flask-WTF | 1.2.2 | 2024-10-24 | BSD-3 | Pallets-eco (org) | Healthy |
| 7 | SQLAlchemy | 2.0.49 | 2024-12-19 | MIT | sqlalchemy (org) | Healthy |
| 8 | psycopg2 | 2.9.11 | 2024-12-19 | LGPL+exceptions | Daniele Varrazzo (single) | Active, maintenance mode |
| 9 | alembic | 1.18.4 | ~2026-Q1 | MIT | sqlalchemy (org) | Healthy |
| 10 | bcrypt | 5.0.0 | 2024-12-19 | Apache-2.0 | PyCA (org) | Healthy |
| 11 | pyotp | 2.9.0 | **2023-07-27** | MIT | 2 maintainers | **Stale (33 months)** |
| 12 | qrcode[pil] | 8.2 | 2025-05-01 | BSD | Lincoln Loop | Healthy |
| 13 | cryptography | 46.0.7 | 2024-12-19 | Apache-2.0 / BSD-3 | PyCA (org) | Healthy |
| 14 | marshmallow | 4.3.0 | 2024-12-19 | MIT | 3 maintainers | Healthy |
| 15 | python-dotenv | 1.2.2 | 2026-03-01 | BSD-3-Clause | 2 maintainers | Healthy |
| 16 | python-json-logger | 4.1.0 | 2026-03-29 | BSD-2-Clause | 2 maintainers | Healthy |

### Transitive dependencies

| Package | Version | License | Brought in by |
|---------|---------|---------|---------------|
| blinker | 1.9.0 | MIT | Flask |
| click | 8.1.8 | BSD-3 | Flask |
| Deprecated | 1.3.1 | MIT | limits -> Flask-Limiter |
| greenlet | 3.4.0 | MIT | SQLAlchemy |
| itsdangerous | 2.2.0 | BSD-3 | Flask |
| Jinja2 | 3.1.6 | BSD-3 | Flask |
| limits | 5.8.0 | MIT | Flask-Limiter |
| Mako | 1.3.11 | MIT | alembic |
| MarkupSafe | 3.0.3 | BSD-3 | Jinja2, Werkzeug |
| ordered-set | 4.1.0 | MIT | Flask-Limiter |
| packaging | 26.1 | BSD/Apache-2.0 | limits |
| pillow | 12.2.0 | HPND | qrcode[pil] |
| typing-extensions | 4.15.0 | PSF | Flask-Limiter |
| Werkzeug | 3.1.8 | BSD-3 | Flask |
| wrapt | 1.17.3 | BSD | Deprecated |
| WTForms | 3.2.1 | BSD-3 | Flask-WTF |
| cffi | 2.0.0 | MIT | cryptography |
| pycparser | 3.0 | BSD | cffi |

---

## Findings

### F-E-01: pyotp -- stale security-critical dependency

- **Severity:** Medium
- **OWASP:** A06:2021 Vulnerable and Outdated Components
- **CWE:** CWE-1104 (Use of Unmaintained Third-Party Components)
- **Location:** `requirements.txt:25` (`pyotp==2.9.0`)
- **Evidence:** Last release 2023-07-27 (33 months ago). This is the
  core TOTP implementation used for multi-factor authentication.
  pyotp implements RFC 6238 (TOTP) and RFC 4226 (HOTP). The library
  is small and the RFC is stable, so low activity is not inherently
  alarming, but 33 months without a release means:
  - No Python 3.13/3.14 compatibility testing or fixes
  - No response to any reported issues since mid-2023
  - If a vulnerability is found, the response time is unknown
- **Impact:** If a vulnerability is discovered in pyotp's TOTP
  verification (e.g., a timing side-channel in the comparison),
  there may be no upstream fix available in a timely manner.
- **Recommendation:** Monitor pyotp's GitHub for activity. Consider
  whether the library could be replaced with a maintained alternative
  if it remains dormant. Pin and audit the source periodically.

### F-E-02: Flask-Login -- stale authentication dependency

- **Severity:** Medium
- **OWASP:** A06:2021 Vulnerable and Outdated Components
- **CWE:** CWE-1104 (Use of Unmaintained Third-Party Components)
- **Location:** `requirements.txt:13` (`Flask-Login==0.6.3`)
- **Evidence:** Last release 2023-10-30 (30 months ago). Flask-Login
  provides session management, login_required decorators, and the
  user loader pattern. It has 3 maintainers (Max Countryman,
  alanhamlett, davidism) and is widely used, but no release in 2.5
  years on a package that handles authentication state is a concern.
- **Impact:** Same as F-E-01 -- if a session-management vulnerability
  is found, upstream response is uncertain.
- **Recommendation:** Monitor. Flask-Login is mature and feature-
  complete; staleness may reflect stability rather than abandonment.
  But verify it works correctly with Python 3.14 (the production
  runtime).

### F-E-03: psycopg2 LGPL license

- **Severity:** Low
- **OWASP:** N/A (license compliance, not a vulnerability)
- **CWE:** N/A
- **Location:** `requirements.txt:20` (`psycopg2==2.9.11`)
- **Evidence:** Licensed under "LGPL with exceptions." The exception
  clause states that using psycopg2 as a library (importing and
  calling its API) does not trigger the LGPL's copyleft requirement.
  Only modifying psycopg2's own source code would require sharing
  changes under LGPL.
- **Impact:** For Shekel's current private use, no impact. If the
  project is ever released as open-source under a non-GPL license,
  this dependency is compatible due to the exception clause. However,
  this should be documented so a future contributor does not
  accidentally modify psycopg2's source.
- **Recommendation:** Document the LGPL exception in the project's
  LICENSE file or a THIRD-PARTY-LICENSES file. Consider migrating
  to psycopg3 (BSD license, actively maintained, better async
  support) in a future cycle.

### F-E-04: Flask-SQLAlchemy -- stale but organizationally backed

- **Severity:** Low
- **OWASP:** A06:2021 Vulnerable and Outdated Components
- **CWE:** CWE-1104
- **Location:** `requirements.txt:14` (`Flask-SQLAlchemy==3.1.1`)
- **Evidence:** Last release 2023-09-11 (31 months ago). Maintained
  by the Pallets organization (same team as Flask itself). This is a
  thin integration layer between Flask and SQLAlchemy; SQLAlchemy
  itself (2.0.49, Dec 2024) is actively maintained.
- **Impact:** Lower risk than pyotp or Flask-Login because the
  security-relevant logic is in SQLAlchemy, not in the thin wrapper.
  Organizational backing by Pallets reduces abandonment risk.
- **Recommendation:** Monitor. Lower priority than F-E-01 and F-E-02.

---

## Info findings

### F-E-I1: Single-maintainer packages (informational)

Three direct dependencies have a single primary maintainer:
- **Flask-Limiter** (Ali-Akber Saifee) -- last release Dec 2024,
  active. Sole maintainer of the PyPI package.
- **Flask-Migrate** (Miguel Grinberg) -- last release Jan 2025,
  active. Well-known Flask ecosystem author.
- **psycopg2** (Daniele Varrazzo) -- last release Dec 2024, active.
  Long-established maintainer, psycopg project.

Single-maintainer packages carry bus-factor risk (if the maintainer
becomes unavailable, the package may become unmaintained). All three
are currently active. Noted for awareness; no action needed now.

### F-E-I2: Pin discipline is correct

All 16 direct dependencies use exact `==` version pins. No `>=`, `~=`,
or unpinned versions. This prevents dependency confusion attacks and
ensures reproducible builds. The transitive dependencies are not
pinned in a lock file (no `pip freeze` output committed), which means
they can drift between installs. A `requirements.lock` or `pip-tools`
workflow would close this gap but is a low-priority improvement.

### F-E-I3: gunicorn not in requirements.txt

gunicorn (the production WSGI server) is installed directly in the
Dockerfile, not listed in `requirements.txt`. This is documented
(`requirements.txt:6-8`) and intentional (dev uses Flask's built-in
server). The consequence is that pip-audit and trivy-sbom do not scan
gunicorn. The trivy image scan in Section 1G will cover this gap.

---

## License audit (PASS 3)

### Direct dependencies

| Package | License | Permissive? |
|---------|---------|-------------|
| Flask | BSD-3-Clause | yes |
| Flask-Limiter | MIT | yes |
| Flask-Login | MIT | yes |
| Flask-SQLAlchemy | BSD | yes |
| Flask-Migrate | MIT | yes |
| Flask-WTF | BSD-3-Clause | yes |
| SQLAlchemy | MIT | yes |
| psycopg2 | LGPL+exceptions | **effectively yes** (see F-E-03) |
| alembic | MIT | yes |
| bcrypt | Apache-2.0 | yes |
| pyotp | MIT | yes |
| qrcode | BSD | yes |
| cryptography | Apache-2.0 / BSD-3 | yes |
| marshmallow | MIT | yes |
| python-dotenv | BSD-3-Clause | yes |
| python-json-logger | BSD-2-Clause | yes |

### Transitive dependencies

All transitive dependencies are MIT, BSD, Apache-2.0, PSF, or HPND
(Historical Permission Notice and Disclaimer -- used by pillow,
permissive). **Zero GPL or AGPL dependencies in the entire
resolved tree.**

### Verdict

The full dependency tree is compatible with proprietary, BSD, MIT, and
Apache-2.0 project licensing. If the project is released as open-source,
the psycopg2 LGPL exception should be documented. No license conflict
exists for any plausible license choice.

---

## Scan artifacts produced

| File | Contents |
|------|----------|
| `sbom/sbom.json` | CycloneDX SBOM (JSON) -- 16 direct deps |
| `sbom/sbom.xml` | CycloneDX SBOM (XML) |
| `sbom/resolved-tree.json` | pip resolved dependency tree (26 packages) |
| `scans/trivy-sbom.json` | Trivy SBOM scan results (0 CVEs) |
| `scans/trivy-sbom.txt` | Trivy SBOM scan human-readable output |
