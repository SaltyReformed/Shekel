# 11 -- Container Image Vulnerability Scan

Audit session: S2, Section 1G. Branch: `audit/security-2026-04-15`.
Date: 2026-04-16. Auditor: Claude (Opus 4.6).

## Summary

- **Image scanned:** `ghcr.io/saltyreformed/shekel:latest`
  (revision `91f2627`, built 2026-04-15)
- **Base OS:** Debian 13.4 (trixie) -- Python 3.14.3 slim
- **OS packages in image:** 105
- **Python packages in image:** 33 (including gunicorn 25.3.0)
- **Scanner:** trivy 0.69.3

### Vulnerability counts

| Severity | Image (OS pkgs) | Image (Python pkgs) | Config (Dockerfile) |
|----------|-----------------|--------------------|--------------------|
| Critical | 0 | 0 | 0 |
| High | 3 unique (9 occurrences) | 0 | 0 |
| Medium | 15 unique (37 occurrences) | 0 | 0 |
| Low | ~35+ unique | 0 (1 in pip, see note) | 0 |

**All vulnerabilities are in OS-level Debian packages.** Zero CVEs in
any Python package, including gunicorn 25.3.0 (which was the gap from
Section 1E).

- **Finding count:** 0 Critical / 1 High / 1 Medium / 1 Low / 2 Info

## HIGH CVE walkthrough

### CVE-2026-28390 -- OpenSSL DoS via NULL pointer in CMS

- **Packages:** libssl3t64, openssl, openssl-provider-legacy
  (3.5.5-1~deb13u1)
- **Fixed version:** 3.5.5-1~deb13u2 (**fix available**)
- **Description:** Denial of Service via NULL pointer dereference when
  processing CMS (Cryptographic Message Syntax) EnvelopedData.
- **Reachable in Shekel?** CMS is for signed/encrypted email messages.
  Shekel uses OpenSSL via Python's `cryptography` package for Fernet
  encryption of TOTP secrets. Fernet uses AES-CBC and HMAC-SHA256,
  not CMS. **Likely not reachable** via the application's code paths.
  However, the fix is available and trivial to apply.
- **Remediation:** Rebuild the image after Debian publishes
  3.5.5-1~deb13u2, or add `apt-get upgrade openssl` to the
  Dockerfile.

### CVE-2025-69720 -- ncurses buffer overflow

- **Packages:** libncursesw6, libtinfo6, ncurses-base, ncurses-bin
  (6.5+20250216-2)
- **Fixed version:** none available
- **Description:** Buffer overflow in ncurses may lead to arbitrary
  code execution.
- **Reachable in Shekel?** ncurses is a terminal UI library. Shekel is
  a web app running gunicorn with sync workers -- no interactive
  terminal usage. Python's readline may link against ncurses, but
  readline is only used in interactive Python shells (`python -c` in
  the healthcheck does not use readline). **Likely not reachable.**
  I don't know for certain, and that unknown becomes a note.
- **Remediation:** No fix available from Debian. Accept with
  monitoring. If ncurses can be removed from the image without
  breaking Python, that would eliminate the surface.

### CVE-2026-29111 -- systemd arbitrary code execution via IPC

- **Packages:** libsystemd0, libudev1 (257.9-1~deb13u1)
- **Fixed version:** none available
- **Description:** Arbitrary code execution or Denial of Service via
  spurious IPC messages to systemd.
- **Reachable in Shekel?** The container does not run systemd. D-Bus
  IPC is not exposed in the container. libsystemd0 and libudev1 are
  included in the Debian base image as dependencies of other packages
  but are not actively used. **Not reachable** in this containerized
  context.
- **Remediation:** No fix available. Not reachable. Accept with
  written justification.

## Findings

### F-G-01: OpenSSL packages have available security updates not applied

- **Severity:** High
- **OWASP:** A06:2021 Vulnerable and Outdated Components
- **CWE:** CWE-1395 (Dependency on Vulnerable Third-Party Component)
- **Location:** Docker image `ghcr.io/saltyreformed/shekel:latest`,
  packages libssl3t64, openssl, openssl-provider-legacy
  (3.5.5-1~deb13u1)
- **Evidence:** 5 OpenSSL CVEs have a fixed version available
  (3.5.5-1~deb13u2):
  - CVE-2026-28390 (HIGH) -- CMS DoS
  - CVE-2026-28388 (MEDIUM) -- delta CRL DoS
  - CVE-2026-28389 (MEDIUM) -- CMS processing DoS
  - CVE-2026-31789 (MEDIUM) -- heap overflow on 32-bit (N/A for this
    64-bit image, but still an open CVE)
  - CVE-2026-31790 (MEDIUM) -- info disclosure via invalid RSA key
  Plus 2 LOW:
  - CVE-2026-2673 -- TLS 1.3 unexpected key agreement group
  - CVE-2026-28387 -- use-after-free in DANE TLSA authentication
- **Impact:** While the application's code paths likely don't reach
  the vulnerable CMS code, OpenSSL is a foundational crypto library.
  An unpatched OpenSSL with available fixes is a standard compliance
  concern.
- **Recommendation:** Rebuild the Docker image to pull the latest
  Debian security updates. Add `apt-get update && apt-get upgrade -y`
  to the Dockerfile, or pin the base image to a Debian release that
  includes the fix.

### F-G-02: 2 HIGH OS CVEs with no fix available

- **Severity:** Medium (downgraded from HIGH because neither is
  reachable in Shekel's usage)
- **OWASP:** A06:2021 Vulnerable and Outdated Components
- **CWE:** CWE-1395
- **Location:** Docker image, ncurses and systemd packages
- **Evidence:**
  - CVE-2025-69720 (ncurses buffer overflow, no fix)
  - CVE-2026-29111 (systemd IPC code execution, no fix)
- **Impact:** Both are in packages not used by the application at
  runtime. ncurses is a terminal UI library; systemd IPC is not
  exposed in containers. These are present only because the Debian
  slim base image includes them as dependencies.
- **Recommendation:** Accept with monitoring. When fixes become
  available, rebuild the image. Consider whether a distroless or
  Alpine base image would reduce the OS attack surface (fewer
  packages installed means fewer CVEs to track).

### F-G-03: pip CVE in container (LOW)

- **Severity:** Low
- **OWASP:** A06:2021 Vulnerable and Outdated Components
- **CWE:** CWE-22 (Improper Limitation of a Pathname)
- **Location:** pip 25.3 inside the Docker image at
  `/opt/venv/lib/python3.14/site-packages/pip-25.3.dist-info/`
- **Evidence:** CVE-2026-1703 -- information disclosure via path
  traversal when installing crafted wheel files. Fixed in pip 26.0.
- **Impact:** pip is only used during image build (`pip install -r
  requirements.txt`), not at runtime. No pip commands run in the
  production container. The vulnerability requires a malicious wheel,
  which is mitigated by installing only from PyPI via requirements.txt
  with exact version pins.
- **Recommendation:** Upgrade pip in the Dockerfile (add
  `pip install --upgrade pip` before `pip install -r
  requirements.txt`). Low priority since pip is not used at runtime.

---

## Info findings

### F-G-I1: Dockerfile passes trivy config scan with 0 misconfigurations

The Dockerfile correctly implements:
- Non-root user (`USER shekel`)
- HEALTHCHECK directive
- No `ADD` with remote URLs
- No `RUN` with `--security=insecure`
- No `COPY --chown=root`

### F-G-I2: gunicorn 25.3.0 confirmed clean

gunicorn is installed directly in the Dockerfile (not in
requirements.txt). trivy's image scan found it at
`/opt/venv/lib/python3.14/site-packages/gunicorn-25.3.0.dist-info/`
with 0 CVEs. This closes the coverage gap noted in Section 1E
(F-E-I3).

---

## OS CVE inventory (MEDIUM and above, deduplicated)

| CVE | Severity | Packages | Fixed? | Reachable? |
|-----|----------|----------|--------|------------|
| CVE-2026-28390 | HIGH | openssl, libssl3t64, openssl-provider-legacy | **yes** (3.5.5-1~deb13u2) | Likely no (CMS) |
| CVE-2025-69720 | HIGH | ncurses (4 pkgs) | no | Likely no (terminal UI) |
| CVE-2026-29111 | HIGH | systemd (2 pkgs) | no | No (no systemd in container) |
| CVE-2026-27456 | MEDIUM | util-linux (9 pkgs) | no | No (mount TOCTOU, no mount in container) |
| CVE-2026-4046 | MEDIUM | glibc (2 pkgs) | no (deferred) | Possibly (iconv) |
| CVE-2026-4437 | MEDIUM | glibc (2 pkgs) | no | Possibly (DNS) |
| CVE-2026-4438 | MEDIUM | glibc (2 pkgs) | no | Possibly (DNS) |
| CVE-2026-4878 | MEDIUM | libcap2 | no | No (cap_set_file) |
| CVE-2026-34743 | MEDIUM | liblzma5 | no | Unlikely (decompression) |
| CVE-2026-28388 | MEDIUM | openssl (3 pkgs) | **yes** | Unlikely (delta CRL) |
| CVE-2026-28389 | MEDIUM | openssl (3 pkgs) | **yes** | Unlikely (CMS) |
| CVE-2026-31789 | MEDIUM | openssl (3 pkgs) | **yes** | No (32-bit only) |
| CVE-2026-31790 | MEDIUM | openssl (3 pkgs) | **yes** | Unlikely (RSA key) |
| CVE-2026-40225 | MEDIUM | systemd (2 pkgs) | no | No (udev) |
| CVE-2026-40226 | MEDIUM | systemd (2 pkgs) | no | No (nspawn) |
| CVE-2026-4105 | MEDIUM | systemd (2 pkgs) | no | No (D-Bus) |
| CVE-2026-27171 | MEDIUM | zlib1g | no | Possibly (CRC32) |
| CVE-2026-5704 | MEDIUM | tar | no | No (tar not used at runtime) |

**glibc DNS CVEs (4437, 4438)** are the most concerning "possibly
reachable" entries because the application resolves the database
hostname (`db`) via DNS on every connection. However, the DNS
resolution happens inside the Docker internal network where the DNS
server is Docker's embedded DNS -- not an attacker-controlled server.
Exploitation would require compromising Docker's DNS, which is a
much higher-privilege attack.

---

## Scan artifacts produced

| File | Contents |
|------|----------|
| `scans/trivy-image.json` | Full trivy image scan (all severities, JSON) |
| `scans/trivy-image.txt` | Trivy image scan (CRITICAL/HIGH/MEDIUM, human-readable) |
| `scans/trivy-config.json` | Trivy config scan of Dockerfile (0 findings, JSON) |
| `scans/trivy-config.txt` | Trivy config scan (human-readable) |
