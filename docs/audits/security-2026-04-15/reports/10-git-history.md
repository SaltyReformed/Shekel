# 10 -- Git History Secrets Scan

Audit session: S2, Section 1F. Branch: `audit/security-2026-04-15`.
Date: 2026-04-16. Auditor: Claude (Opus 4.6).

## Summary

- **Scanners:** gitleaks v8.30.1 (git history), detect-secrets v1.5.0
  (working tree)
- **Commits scanned (gitleaks):** 454
- **Bytes scanned (gitleaks):** ~13 MB in 743ms
- **Gitleaks findings:** 1 (real -- old SECRET_KEY in git history)
- **Detect-secrets findings:** 489 total, 0 real secrets (all false
  positives after classification)
- **Finding count:** 1 Critical / 0 High / 0 Medium / 0 Low / 1 Info

## Gitleaks results

### F-F-01: Flask SECRET_KEY committed in initial commit (CRITICAL)

- **Severity:** Critical
- **OWASP:** A02:2021 Cryptographic Failures
- **CWE:** CWE-798 (Use of Hard-Coded Credentials)
- **Rule:** generic-api-key
- **Commit:** `f9b35ecb5d71751923fceb77544fe57b18818ae2` ("initial
  build", 2026-02-21, author: SaltyReformed)
- **File:** `app/config.py:21`
- **Secret (redacted):** `a637...9f2e` (64 hex chars, Flask SECRET_KEY)
- **Evidence:**
  ```
  Match: SECRET_KEY", "a637...9f2e"
  Entropy: 3.82
  ```
- **Current status:** The key has been ROTATED. The production
  SECRET_KEY (observed via `docker exec` in Section 1D) starts with
  `5d5a...` -- a different value. The old key `a637...` is no longer
  in use.
- **Impact:** Even though the key was rotated, it remains in the git
  history. Anyone who clones the (private) repo can extract it.
  If this key was ever used to sign Flask session cookies in
  production, those cookies could be forged by someone with the old
  key. Flask's `itsdangerous` signer uses the SECRET_KEY to sign
  session cookies -- a leaked key allows session forgery for the
  period when it was active.
- **Remediation (requires developer action):**
  1. **Confirm rotation:** Verify the production SECRET_KEY is
     different (done -- confirmed in 1D).
  2. **Invalidate old sessions:** If any sessions signed with the
     old key could still be valid (e.g., long-lived "remember me"
     cookies), force re-login for all users.
  3. **Remove from git history:** Use `git filter-repo` or BFG
     Repo-Cleaner to rewrite history and remove the commit that
     contains the key. This requires a force-push, which is
     destructive and must be coordinated.
  4. **Update .gitignore and pre-commit:** Ensure no future config
     file with hardcoded secrets can be committed. Consider adding
     a gitleaks pre-commit hook.

  Steps 3-4 are Phase 3 actions. Step 1 is already confirmed. Step 2
  should be evaluated by the developer.

## Detect-secrets results

detect-secrets v1.5.0 scanned all files in the working tree (not git
history). It found 489 pattern matches across 206 files. After
classification, **zero are real secrets:**

| Category | Count | Explanation |
|----------|-------|-------------|
| `.venv/` + `.audit-venv/` (third-party libs) | 309 | SQLAlchemy connection string examples, cryptography OIDs, faker test data, etc. |
| `docs/audits/` (our own scan outputs) | 77 | Container inspect JSONs contain env vars with secrets -- these are expected audit artifacts |
| `migrations/versions/` (Alembic revision IDs) | 41 | Hex hashes used as migration identifiers, not secrets |
| `tests/` (test fixture passwords) | 34 | Test users with known passwords (`"Secret Keyword"` pattern on `password` fields) |
| `docs/` plan files (example URLs) | 12 | Documentation with example `postgresql://user:pass@host` URLs |
| `app/templates/base.html` (SRI hashes) | 3 | Subresource Integrity hashes for CDN-loaded Bootstrap/HTMX scripts |
| Config/env (expected patterns) | 13 | `.env.example` placeholders, CI test credentials, dev compose passwords, `.env` (gitignored), seed script password param |

### Cross-check: gitleaks vs detect-secrets

The gitleaks finding (old SECRET_KEY in commit `f9b35ec`) is NOT
present in the current working tree because the hardcoded key was
replaced with `os.getenv("SECRET_KEY")`. This is why detect-secrets
(working-tree scanner) did not flag it -- demonstrating that **git
history scanning and working-tree scanning are complementary tools,
not substitutes.** The git history retains the secret even after
the code is fixed.

detect-secrets found no secrets that gitleaks missed in the history
scan. This is a clean cross-check.

## Scanner coverage notes

- **gitleaks** scans git history commit-by-commit. It found the one
  historical secret. It does NOT scan the working tree or untracked
  files.
- **detect-secrets** scans the working tree (all files, including
  untracked). It found zero real secrets but produced 489 false
  positives, most from third-party library code in virtual
  environments. A `.secrets.baseline` file with allow-listed false
  positives would reduce future noise.

No secrets were detected in git history at commit `4d92ef1` by
gitleaks v8.30.1, except for the one finding documented as F-F-01.

---

## Scan artifacts produced

| File | Contents |
|------|----------|
| `scans/gitleaks.json` | Gitleaks JSON report (1 finding) |
| `scans/gitleaks.sarif` | Gitleaks SARIF report (1 finding) |
| `scans/detect-secrets-baseline.json` | detect-secrets baseline (489 pattern matches, 0 real) |
