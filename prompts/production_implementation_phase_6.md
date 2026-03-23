# Claude Code Prompt: Shekel Phase 6 -- Documentation and Onboarding

You are implementing Phase 6 of the Shekel production readiness plan. Phases 1 through 5 are complete. This is a personal budgeting application where errors have real financial consequences. There is no QA team. A new user cloning this project from GitHub and following the README is trusting that the documentation is accurate, complete, and will not lead them into a misconfigured or insecure deployment. Documentation errors in a financial application are not cosmetic -- they cause real harm.

Phase 6 is about documentation: ensuring that every configuration change made in Phases 1 through 5 is reflected in the README, `.env.example`, and related onboarding files. A new user should be able to go from `git clone` to a running, properly secured application by following the README alone, without needing to read source code, audit reports, or implementation plans.

---

## Ground Rules (Read These First -- They Are Non-Negotiable)

1. **Read before you write.** Before changing ANY documentation file, read the ENTIRE file first. The README has a specific structure, voice, and formatting style established across many phases of development. Your additions must be seamless, not bolted on.

2. **Verify every claim.** Every command you put in documentation must work. Every file path must exist. Every environment variable must match what the application actually reads. Every link must resolve. Do not write documentation from the implementation plan -- verify against the ACTUAL CURRENT CODE.

3. **Write for a new user, not for yourself.** The reader has never seen this codebase. They do not know what `REGISTRATION_ENABLED` does unless you explain it. They do not know that the Docker volume contains all their financial data unless you tell them. They do not know that MFA requires a separate encryption key unless you make it obvious.

4. **No application code changes.** Phase 6 is documentation only. If you find a documentation need that requires a code change (e.g., a missing config variable), document the finding and flag it. Do not change application code.

5. **Match existing documentation style.** The README uses a specific Markdown format: ATX headers, pipe tables, fenced code blocks with language hints, and concise prose. Study the existing sections before adding new ones. Do not change the voice or formatting of existing content.

6. **One commit per documentation item.** Each P6 item gets its own commit. This allows review and individual revert.

7. **All work happens on the `dev` branch.**

---

## Pre-Flight: Read Everything First

Before changing a single line, read ALL of the following files in full. You must understand the complete documentation landscape before adding to it.

```
# 1. Confirm branch and clean working tree
git branch --show-current
git status

# 2. Record the current commit hash
git log --oneline -1

# 3. Read the full README
cat README.md

# 4. Read the full .env.example
cat .env.example

# 5. Read the backup runbook
cat docs/backup_runbook.md

# 6. Check what other documentation exists
ls docs/
find docs/ -name "*.md" | sort

# 7. Read the actual config.py to know what variables the app reads
grep -n "os.getenv\|os.environ" app/config.py

# 8. Check what Phase 2 actually added for REGISTRATION_ENABLED
grep -rn "REGISTRATION_ENABLED" app/ .env.example README.md docker-compose.yml

# 9. Check what the TOTP key row currently says in the README
grep -n "TOTP" README.md

# 10. Check if backups are already mentioned
grep -n -i "backup" README.md

# 11. Check if a security section already exists
grep -n -i "security\|hardening\|external access\|cloudflare\|tailscale" README.md
```

Document what you find. Phase 2 may have already added `REGISTRATION_ENABLED` to `.env.example` (the P2-6 prompt required it). Phase 3 may have added `FORWARDED_ALLOW_IPS`. Do NOT add duplicate entries. Your job is to fill gaps, not repeat what is already there.

---

## P6-1: Document `REGISTRATION_ENABLED` in README and `.env.example`

### Context

Phase 2 (P2-6) added a `REGISTRATION_ENABLED` config toggle that controls whether the `/register` endpoint is available. When set to `false`, both the GET and POST register routes return 404 and the registration link is hidden from the login page. This is critical for deployments exposed to the internet.

### Audit Steps (Do These First)

1. **Check if `.env.example` already documents `REGISTRATION_ENABLED`.** Phase 2's prompt required adding it. If it is already there with an adequate comment, skip the `.env.example` portion and note why.

2. **Check if the README already mentions `REGISTRATION_ENABLED`.** Search for it. If it appears in an environment table or a security section, evaluate whether the documentation is adequate.

3. **Verify the actual config.** Open `app/config.py` and confirm how `REGISTRATION_ENABLED` is parsed. Confirm the default value. Confirm what values are accepted (e.g., `"true"`, `"1"`, `"yes"`, `"false"`, etc.). Your documentation must match the actual parsing logic, not what the implementation plan says.

### Implementation (Only for Gaps)

**`.env.example` (if not already documented):**
Add near other auth-related variables:

```
# Set to 'false' to disable public user registration.
# When disabled, /register returns 404 and the registration link is hidden.
# Default: true (registration open)
# REGISTRATION_ENABLED=true
```

Comment it out (with `#` prefix) since the default is `true` and most users do not need to change it. The comment above it explains when and why to change it.

**README environment table (if not already documented):**
Find the environment variables table in the Quick Start section. Add a row:

```
| `REGISTRATION_ENABLED` | No | Set to `false` to disable public registration. Default: `true`. See [Security](#security) for guidance. |
```

**README Troubleshooting table (if it exists and does not already cover this):**
Add a row:

```
| `/register` returns 404 | `REGISTRATION_ENABLED` is set to `false` in `.env`. Set to `true` or remove the line to re-enable. |
```

### Verification

```
# Confirm .env.example mentions REGISTRATION_ENABLED
grep -n "REGISTRATION_ENABLED" .env.example

# Confirm README mentions REGISTRATION_ENABLED
grep -n "REGISTRATION_ENABLED" README.md

# Confirm the documented default matches the code
grep -n "REGISTRATION_ENABLED" app/config.py
```

### Commit

```
git add README.md .env.example
git commit -m "docs: document REGISTRATION_ENABLED in README and .env.example (P6-1)"
```

---

## P6-2: Add Security Hardening Notes to README

### Context

The README does not provide security guidance for different deployment scenarios. A user who exposes Shekel via a Cloudflare Tunnel or Tailscale needs to know which settings to change. A user running on a private LAN needs to know the defaults are sufficient. Without this guidance, users will either over-configure (breaking things) or under-configure (leaving the app open).

### Audit Steps

1. Check if a "Security" section already exists in the README.
2. Check if `docs/runbook.md` already covers security. It likely does (the project has an operations runbook). The README should summarize and link to the runbook, not duplicate it.
3. Read the existing README structure to determine where a Security section belongs. The implementation plan says "after Troubleshooting." Read the README's table of contents or heading structure and find the right location.

### Implementation

Add a "Security" section to the README. Place it after "Troubleshooting" but before any developer-focused sections. The section must cover three deployment scenarios with clear, actionable instructions:

```markdown
## Security

### LAN-Only Deployment

If Shekel is only accessible on your local network, the default configuration is sufficient. You should still:

- Change the default seed password on first login (Settings > Security > Change Password).
- Set up automated backups (see [Backups](#backups)).

### External Access (Cloudflare Tunnel, Tailscale, etc.)

If you expose Shekel outside your local network, take these additional steps:

1. **Disable public registration.** Set `REGISTRATION_ENABLED=false` in your `.env` to prevent strangers from creating accounts.
2. **Enable MFA for all users.** Go to Settings > Security > Enable TOTP. This requires `TOTP_ENCRYPTION_KEY` to be set (see the environment table above).
3. **Verify HTTPS.** Cloudflare Tunnel and Tailscale handle TLS automatically. If using a different method, ensure your reverse proxy terminates HTTPS.
4. **Change the default seed password immediately** if you used the default `ChangeMe!2026`.

### General Recommendations

- Back up your database before entering real financial data. See [Backups](#backups).
- Keep your `.env` file secure. It contains your database password and encryption keys. Never commit it to version control.
- The application sets security headers (CSP, HSTS-ready, X-Frame-Options) automatically in production mode.
```

**Tone and style notes:**

- Use imperative voice ("Set", "Enable", "Verify"), not passive ("should be set", "can be enabled").
- Link to other README sections where referenced (Backups, environment table). Use anchor links.
- Do NOT enumerate every security feature the app has (CSRF, bcrypt, etc.). That is for the audit report, not the user README. Only cover what the user needs to DO.
- Do NOT mention the audit report, implementation plan, or any internal documents. The README is for users, not developers.

### Verification

```
# Read the section and evaluate: could a non-technical user follow every instruction?
grep -A 30 "## Security" README.md

# Verify all anchor links resolve
# (manually check that #backups, #security, etc. point to real headings)
```

### Commit

```
git add README.md
git commit -m "docs: add security hardening guidance for LAN and external deployments (P6-2)"
```

---

## P6-3: Document Backup Setup in README

### Context

The backup infrastructure exists (`scripts/backup.sh`, `scripts/backup_retention.sh`, `docs/backup_runbook.md`) and has been verified in Phase 0. But the README does not mention backups at all. A new user would not know to set them up. Loss of the Docker volume means permanent loss of all financial data. This is the single most important piece of documentation for a financial application.

### Audit Steps

1. Confirm `scripts/backup.sh` exists: `ls scripts/backup*.sh`
2. Confirm `docs/backup_runbook.md` exists: `ls docs/backup_runbook.md`
3. Confirm the README does NOT already have a Backups section: `grep -n -i "backup" README.md`
4. Read `docs/backup_runbook.md` in full to understand what it covers. Your README section must accurately summarize its contents without contradicting it.
5. Determine where in the README the Backups section belongs. The implementation plan says "after the Quick Start section." Read the README structure and find the right position. It should come BEFORE the Security section (because the Security section references it).

### Implementation

Add a "Backups" section. The tone must convey urgency without being alarmist. The user needs to understand that this is not optional.

````markdown
## Backups

Shekel stores all financial data in a PostgreSQL Docker volume. **If this volume is lost, corrupted, or the host fails, your data is gone.** Set up automated backups before entering real financial data.

See [docs/backup_runbook.md](docs/backup_runbook.md) for complete instructions covering:

- Automated daily backups via `pg_dump` with configurable retention
- Off-site backup to NAS or remote storage
- Backup encryption with GPG
- Restore procedures and verification

### Quick Backup Setup

```bash
# Run a manual backup now
./scripts/backup.sh

# Add to crontab for daily automated backups (2:00 AM)
crontab -e
# Add: 0 2 * * * /path/to/shekel/scripts/backup.sh >> /var/log/shekel_backup.log 2>&1
```
````

See the runbook for retention policies, NAS configuration, and encryption setup.

```

**Critical verification:** Read `scripts/backup.sh` to confirm the usage shown above is correct. If the script requires arguments, environment variables, or must be run from a specific directory, document that. Do not show a command that will fail.

Also verify the crontab line matches what `docs/backup_runbook.md` recommends. If the runbook uses a different time or path, match the runbook.

### Verification

```

# Confirm the section exists and links correctly

grep -A 15 "## Backups" README.md

# Confirm the backup script exists at the documented path

ls scripts/backup.sh

# Confirm the backup runbook exists at the documented path

ls docs/backup_runbook.md

```

### Commit

```

git add README.md
git commit -m "docs: add Backups section with urgency note and quick setup instructions (P6-3)"

```

---

## P6-4: Add `TOTP_ENCRYPTION_KEY` Generation Instructions to README

### Context

The README's environment table has a row for `TOTP_ENCRYPTION_KEY` that says something like "Only needed before enabling MFA" or "See `.env.example` for instructions." A new user who wants MFA has to navigate to `.env.example` to find the generation command. The README should include the command directly so the user does not need to leave the page.

### Audit Steps

1. Find the current `TOTP_ENCRYPTION_KEY` row in the README's environment table:
```

grep -n "TOTP_ENCRYPTION_KEY" README.md

```

2. Read the row. Note exactly what it currently says.

3. Check `.env.example` for the generation command:
```

grep -A 5 "TOTP_ENCRYPTION_KEY" .env.example

```

4. **Verify the generation command actually works.** Run it:
```

python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

```
If it fails (e.g., `cryptography` not installed in your environment), note that the user will be running this inside the Docker container or in an environment with the app's dependencies. Provide BOTH a local command and a Docker command:
```

# If you have Python with cryptography installed:

python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Or using the Shekel Docker image:

docker exec shekel-app python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

```

5. **IMPORTANT:** Verify that `openssl rand -base64 32` does NOT produce a valid Fernet key. Fernet requires a specific URL-safe base64 encoding of exactly 32 bytes. An `openssl` output is NOT compatible. If the README or `.env.example` suggests `openssl` as an alternative, that is WRONG and must be corrected. Check:
```

grep -n "openssl" README.md .env.example

```
If found in the context of TOTP key generation, remove it and note the correction.

### Implementation

Update the `TOTP_ENCRYPTION_KEY` row in the README environment table. The exact Markdown depends on the current table structure, but the content should be:

```

| `TOTP_ENCRYPTION_KEY` | No | Required before enabling MFA. Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` or run inside Docker: `docker exec shekel-app python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |

```

If the table column is too narrow for the full command, use a footnote pattern:

In the table:
```

| `TOTP_ENCRYPTION_KEY` | No | Required before enabling MFA. See [MFA Setup](#mfa-setup) below. |

````

Then add a brief subsection (under Security or after the environment table):
```markdown
### MFA Setup

Multi-factor authentication (TOTP) requires an encryption key for storing secrets at rest.

Generate a key using one of these methods:

```bash
# Using the Shekel Docker container (recommended):
docker exec shekel-app python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Using a local Python environment with cryptography installed:
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
````

Paste the output into your `.env` file as `TOTP_ENCRYPTION_KEY=<key>`, then restart the app:

```bash
docker compose restart app
```

You can then enable MFA in Settings > Security > Enable TOTP.

```

**Do NOT suggest `openssl rand` as an alternative.** It produces an incompatible key format.

### Verification

```

# Confirm the README documents the generation command

grep -A 3 "TOTP_ENCRYPTION_KEY" README.md

# Confirm no openssl-based TOTP key generation is documented

grep -n "openssl.*TOTP\|TOTP.*openssl" README.md .env.example

# Should return nothing

# Verify the command works (if cryptography is available)

python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>/dev/null && echo "OK" || echo "cryptography not installed locally -- verify inside Docker"

```

### Commit

```

git add README.md
git commit -m "docs: add TOTP_ENCRYPTION_KEY generation instructions directly in README (P6-4)"

```

---

## Post-Phase: Full Documentation Review

After all four items are complete, do a final review of the README as a whole. Read it top to bottom as if you are a new user who has never seen the project. Check for:

```

# 1. Confirm all internal links resolve

# Extract all anchor links and verify each points to a real heading

grep -oP '\[._?\]\(#._?\)' README.md | while read link; do
anchor=$(echo "$link" | grep -oP '#\K[^)]+')
heading=$(echo "$anchor" | tr '-' ' ')
if ! grep -qi "## ._$heading\|### ._$heading" README.md; then
echo "BROKEN LINK: $link"
fi
done

# 2. Confirm all file path links resolve

grep -oP '\[._?\]\([^#][^)]+\)' README.md | grep -oP '\(\K[^)]+' | while read path; do
if [[ "$path" != http_ ]] && [ ! -f "$path" ]; then
echo "BROKEN PATH: $path"
fi
done

# 3. Check section ordering makes sense

grep "^##" README.md

# Expected order (approximately):

# Quick Start (Docker)

# First-Time Setup / Backups / Security (in some logical order)

# Troubleshooting

# Developer Setup

# (other sections)

# 4. Verify no duplicate sections were created

grep "^## " README.md | sort | uniq -d

# Should return nothing

# 5. Confirm no application code was changed

git diff --name-only | grep -v "README.md\|\.env\|docs/"

# Should return nothing

# 6. Confirm working tree is clean

git status

# 7. Print summary

echo "Phase 6 complete. Changes:"
echo " P6-1: REGISTRATION_ENABLED documented in README and .env.example"
echo " P6-2: Security section with LAN/external deployment guidance"
echo " P6-3: Backups section with urgency note and quick setup"
echo " P6-4: TOTP_ENCRYPTION_KEY generation instructions in README"
echo ""
echo "Final README line count:"
wc -l README.md

```

---

## What "Done Right" Means for This Phase

- A new user can go from `git clone` to a running, secured, backed-up application by following the README alone. They do not need to read `.env.example` comments for critical setup steps, open source code to understand config variables, or discover the backup system by accident.
- Every environment variable documented in the README matches what `app/config.py` actually reads, including the exact default value and accepted formats.
- The `TOTP_ENCRYPTION_KEY` generation command uses ONLY the `cryptography.fernet.Fernet` method. `openssl rand` is NOT presented as an alternative (it produces an incompatible format).
- The Backups section communicates urgency: data loss is permanent. The user is told to set up backups BEFORE entering real financial data.
- The Security section gives actionable, deployment-specific guidance. LAN users know they are fine with defaults. External-access users know exactly which settings to change.
- Every internal link (`#backups`, `#security`, etc.) resolves to a real heading.
- Every file path link (`docs/backup_runbook.md`, etc.) resolves to a real file.
- No documentation was duplicated. If Phase 2 already added `REGISTRATION_ENABLED` to `.env.example`, it was not added again.
- No application code was changed. Phase 6 is documentation only.
- The README's existing voice, formatting, and structure are preserved. New sections are seamless additions, not bolted-on appendices.
- Every commit is atomic and can be individually reverted.
```
