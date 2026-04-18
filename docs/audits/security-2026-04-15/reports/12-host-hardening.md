# 12 -- Host Hardening + Docker Daemon Benchmark

Audit session: S2, Section 1H. Branch: `audit/security-2026-04-15`.
Date: 2026-04-16. Auditor: Claude (Opus 4.6).

## Summary

- **Lynis version:** 3.1.6, hardening index: **64/100**
- **Docker-bench version:** v1.6.0 (CIS Docker Benchmark 1.6.0),
  score: **5** (net passes minus warnings)
- **Host OS:** Arch Linux (rolling), kernel 7.0.0-1-cachyos
- **Docker version:** 29.4.0 (current)
- **Firewall:** nftables via firewalld -- **active**, non-empty ruleset
- **SSH:** sshd running, several hardening items available
- **Finding count:** 0 Critical / 2 High / 5 Medium / 5 Low / 3 Info

---

## Lynis findings

### F-H-01: Production .env files are world-readable (644)

- **Severity:** High
- **Lynis ID:** (custom check, not a lynis test)
- **CWE:** CWE-732 (Incorrect Permission Assignment for Critical Resource)
- **Location:**
  - `/home/josh/projects/Shekel/.env` -- permissions 644 (rw-r--r--)
  - `/opt/docker/shekel/.env` -- permissions 644 (rw-r--r--)
- **Evidence:** Both .env files contain production secrets (SECRET_KEY,
  TOTP_ENCRYPTION_KEY, DATABASE_URL with password, SEED_USER_PASSWORD).
  Permissions 644 means any user on the host can read these files.
- **Impact:** A compromised non-root process or a local user account
  can read all production secrets.
- **Recommendation:** `chmod 600` on both .env files. Only the owner
  (josh) needs read access.

### F-H-02: kernel.kptr_restrict = 0

- **Severity:** High
- **Lynis ID:** KRNL-6000
- **CWE:** CWE-200 (Exposure of Sensitive Information)
- **Location:** `sysctl kernel.kptr_restrict`
- **Evidence:** `kernel.kptr_restrict = 0` allows any process to read
  kernel pointer addresses from `/proc/kallsyms`. This information
  assists in writing kernel exploits by defeating KASLR (Kernel
  Address Space Layout Randomization -- a defense that randomizes
  where kernel code is loaded in memory so an attacker can't predict
  addresses).
- **Impact:** A container escape or local exploit is easier when the
  attacker can read kernel addresses.
- **Recommendation:** Add to `/etc/sysctl.d/99-hardening.conf`:
  ```
  kernel.kptr_restrict = 2
  ```
  Then apply with `sysctl --system`. Value 2 hides pointers from all
  users including root (value 1 hides from non-root only).

### F-H-03: No Docker audit logging configured

- **Severity:** Medium
- **Docker-bench IDs:** 1.1.3, 1.1.4, 1.1.5, 1.1.7, 1.1.9, 1.1.14,
  1.1.17, 1.1.18
- **CWE:** CWE-778 (Insufficient Logging)
- **Description:** No auditd rules are configured for the Docker
  daemon, containerd, runc, the Docker socket, or `/var/lib/docker`.
  Linux auditd (the kernel audit framework) tracks who accesses
  system files and when -- without it, a compromise of the Docker
  daemon leaves no forensic trail.
- **Impact:** If the Docker daemon or socket is compromised, there is
  no audit log to determine what happened, when, or by whom.
- **Recommendation:** Install and enable auditd. Add audit rules for:
  ```
  -w /usr/bin/dockerd -k docker
  -w /var/lib/docker -k docker
  -w /run/containerd -k docker
  -w /usr/bin/containerd -k docker
  -w /usr/bin/containerd-shim-runc-v2 -k docker
  -w /usr/bin/runc -k docker
  -w /usr/lib/systemd/system/docker.service -k docker
  -w /usr/lib/systemd/system/docker.socket -k docker
  ```

### F-H-04: no-new-privileges not set as daemon default

- **Severity:** Medium
- **Docker-bench ID:** 2.14
- **CWE:** CWE-250 (Execution with Unnecessary Privileges)
- **Description:** The Docker daemon does not set
  `no-new-privileges` as the default for all containers. This was
  also flagged per-container in 1D (F-D-07) and confirmed by
  docker-bench 5.26 (all 14 running containers lack it).
- **Recommendation:** Add to `/etc/docker/daemon.json`:
  ```json
  { "no-new-privileges": true }
  ```
  This applies the restriction to ALL containers by default, rather
  than requiring each compose file to set it.

### F-H-05: Dev databases bound to 0.0.0.0

- **Severity:** Medium
- **Docker-bench ID:** 5.14
- **CWE:** CWE-668 (Exposure of Resource to Wrong Sphere)
- **Location:** `shekel-dev-db` on 0.0.0.0:5432,
  `shekel-dev-test-db` on 0.0.0.0:5433
- **Evidence:** Both development PostgreSQL containers bind to all
  interfaces, meaning they are accessible from the LAN if the
  firewall allows it.
- **Impact:** A LAN attacker can connect to the dev/test databases.
  While these contain test data (not production), they may contain
  real schema information useful for attacking the production
  database.
- **Recommendation:** Bind dev databases to `127.0.0.1` only:
  ```yaml
  ports:
    - "127.0.0.1:5432:5432"
  ```

### F-H-06: SSH hardening opportunities

- **Severity:** Medium
- **Lynis ID:** SSH-7408
- **CWE:** CWE-16 (Configuration)
- **Description:** sshd is running with default settings in several
  areas that could be tightened:
  - `MaxAuthTries` is 6 (recommend 3)
  - `AllowTcpForwarding` is YES (recommend NO unless needed)
  - `AllowAgentForwarding` is YES (recommend NO unless needed)
  - `MaxSessions` is 10 (recommend 2)
  - Default port 22 (changing is obscurity, not security, but
    reduces log noise)
- **Impact:** Default SSH settings allow more brute-force attempts
  and more forwarding capability than a homelab needs.
- **Recommendation:** Apply the tighter settings in
  `/etc/ssh/sshd_config`. If SSH is only used for LAN access,
  consider adding `AllowUsers josh` to restrict by username.

### F-H-07: Pending kernel reboot

- **Severity:** Medium
- **Lynis ID:** KRNL-5830
- **Description:** The running kernel (7.0.0-1-cachyos) may not match
  the installed kernel. A reboot is needed to apply kernel security
  patches.
- **Recommendation:** Schedule a reboot at a convenient time.

### F-H-08: GRUB boot loader not password-protected

- **Severity:** Low
- **Lynis ID:** BOOT-5122
- **CWE:** CWE-306 (Missing Authentication for Critical Function)
- **Description:** The GRUB bootloader has no password set. Anyone
  with physical access can edit boot parameters (e.g., boot into
  single-user mode without a password).
- **Impact:** Low for a server under physical control. Higher if the
  machine is in a shared physical space.
- **Recommendation:** Set a GRUB password if the machine is in a
  location where physical access is not fully controlled.

### F-H-09: Core dumps not disabled

- **Severity:** Low
- **Lynis ID:** KRNL-5820
- **CWE:** CWE-532 (Insertion of Sensitive Information into Log File)
- **Description:** Core dumps are not explicitly disabled. A crashing
  process (e.g., the Python runtime) could write a core dump
  containing secrets from memory (the TOTP encryption key, session
  data, database credentials).
- **Recommendation:** Add to `/etc/security/limits.conf`:
  ```
  * hard core 0
  ```
  And to `/etc/sysctl.d/99-hardening.conf`:
  ```
  fs.suid_dumpable = 0
  ```

### F-H-10: No file integrity monitoring

- **Severity:** Low
- **Lynis ID:** FINT-4350
- **Description:** No file integrity monitoring tool (AIDE, OSSEC,
  Tripwire, Wazuh) is installed. File integrity monitoring detects
  unauthorized changes to system files and application binaries.
- **Recommendation:** Install and configure AIDE or a similar tool
  for baseline comparison of critical paths (`/usr/bin`, `/etc`,
  `/opt/docker`).

### F-H-11: No NTP synchronization detected

- **Severity:** Low
- **Lynis ID:** TIME-3104
- **Description:** No NTP daemon detected. Accurate time is important
  for log correlation, TOTP code validity (a clock-skewed server may
  reject valid TOTP codes or accept expired ones), and TLS
  certificate validation.
- **Recommendation:** Enable `systemd-timesyncd` or install
  `chrony`/`ntpd`.

### F-H-12: No password strength module for PAM

- **Severity:** Low
- **Lynis ID:** AUTH-9262
- **Description:** No PAM password strength testing module
  (pam_cracklib, pam_passwdqc) is installed. Host user passwords
  can be weak.
- **Recommendation:** Install `pam_passwdqc` for enforced password
  complexity on the host user account.

---

## Info findings

### F-H-I1: Firewall is active and configured

nftables via firewalld is active with a non-empty ruleset. This is
the expected state per the deployment documentation. PASS.

### F-H-I2: Docker daemon not exposed on TCP

Docker-bench 2.7 confirms the Docker daemon is not listening on a TCP
port. The only access path is the Unix socket at
`/var/run/docker.sock` (root:docker, 660). PASS.

### F-H-I3: UniFi embedded MongoDB without auth (non-Shekel)

Lynis DBS-1820 detected a `mongod` process (PID 3864) running
without authorization. This is the UniFi Network Controller's
embedded MongoDB inside the `unifi` container. The configuration
file is inside the container, not at `/etc/mongod.conf` where lynis
looks. This is a UniFi hardening item, not a Shekel finding. Noted
for the host hardening record.

---

## Docker-bench summary (Shekel-specific containers only)

| Check | shekel-prod-app | shekel-prod-db | nginx |
|-------|-----------------|----------------|-------|
| 4.1 Running as root | PASS (shekel) | **WARN** (root) | **WARN** (root) |
| 5.2 AppArmor | WARN (N/A on Arch) | WARN | WARN |
| 5.3 SELinux/SecurityOpt | WARN (none) | WARN | WARN |
| 5.4 Capabilities restricted | PASS | PASS | PASS |
| 5.5 Not privileged | PASS | PASS | PASS |
| 5.8 Privileged ports | PASS | PASS | **WARN** (80, 443) |
| 5.10 Host network shared | PASS | PASS | PASS |
| 5.11 Memory limit | **WARN** | **WARN** | **WARN** |
| 5.12 CPU limit | **WARN** | **WARN** | **WARN** |
| 5.13 Read-only rootfs | **WARN** | **WARN** | **WARN** |
| 5.14 Bound to specific IP | PASS | PASS | **WARN** (0.0.0.0) |
| 5.22 Seccomp default | PASS | PASS | PASS |
| 5.26 no-new-privileges | **WARN** | **WARN** | **WARN** |
| 5.27 Health check | PASS (compose) | PASS (compose) | **WARN** |
| 5.29 PID limit | **WARN** | **WARN** | **WARN** |
| 5.32 Docker socket mounted | PASS | PASS | PASS |

**Passes:** No privileged containers, no Docker socket mounted, no
host network sharing, default seccomp profile active, not using
docker0 bridge.

**Warnings (all already captured in 1D):** No memory/CPU/PID limits,
no no-new-privileges, writable root filesystem, no AppArmor/SELinux
(not applicable on Arch).

Note: docker-bench flags the lack of AppArmor/SELinux (5.2/5.3) on
every container. Arch Linux ships with neither AppArmor nor SELinux
by default. This is an accepted platform limitation. The default
Docker seccomp profile (5.22 PASS) provides the baseline mandatory
access control instead.

---

## Kernel hardening flags

| Flag | Value | Expected | Verdict |
|------|-------|----------|---------|
| kernel.kptr_restrict | 0 | 1 or 2 | **FAIL** (F-H-02) |
| kernel.dmesg_restrict | 1 | 1 | PASS |
| kernel.unprivileged_userns_clone | 1 | 1 (needed for rootless) | PASS (Info) |

---

## Scan artifacts produced

| File | Contents |
|------|----------|
| `scans/lynis.log` | Full lynis audit log (328 KB) |
| `scans/lynis-report.dat` | Lynis machine-readable report (56 KB) |
| `scans/docker-bench.txt` | Docker-bench CIS benchmark output (363 lines) |
