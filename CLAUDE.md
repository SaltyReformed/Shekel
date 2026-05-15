# CLAUDE.md

Shekel is a personal budget app organized around **pay periods** (biweekly paychecks) rather than
calendar months. Every transaction maps to a specific paycheck with ~2-year forward projections.

**Stack:** Python 3.12+ · Flask 3.1 · SQLAlchemy 2.0 · PostgreSQL (multi-schema) · Jinja2 · HTMX ·
Bootstrap 5

**YOU ARE THE ONLY SAFEGUARD.** This project has no QA team, no code reviewer, no CI pipeline. The
developer is a solo operator. If you miss a bug, skip an edge case, or take a shortcut, that defect
ships to production. In a budgeting app, that means real money is mismanaged. Treat every line of
code as if someone's rent payment depends on it being correct.

## Rules

These are requirements, not suggestions. Violating them is never acceptable.

1. **Do it right, not fast.** No shortcuts, no workarounds, no band-aid fixes. Do not stub with
   `pass` or `TODO`. Do not hardcode values. Do not use broad `except Exception`. Fix root causes,
   not symptoms. The correct solution is the only acceptable solution.

2. **Read before you write.** Read the ENTIRE file before changing it. Do not rely on memory or line
   numbers from planning documents.

3. **No guessing.** If uncertain, read the code or ask the developer. Do not assume what a function
   returns, what columns a table has, or what state an object is in. For ambiguous financial logic,
   ask before proceeding.

4. **NEVER ignore a problem.** If you find a failing test, bug, linter error, or logic flaw --
   whether you caused it or not -- you MUST either (a) fix it, or (b) stop and report it with full
   details. There is no third option. Do not dismiss failures as "pre-existing." Do not say
   "unrelated to my changes" without investigating and reporting. The developer has no one else to
   catch these.

5. **NEVER modify a test to make it pass.** If a test fails after your change, your code is wrong,
   not the test. Financial assertions were computed by hand. Fix your code. The only exception is
   when the developer explicitly confirms the expected behavior has changed.

6. **Stay in scope.** Only modify code related to the current task. Report out-of-scope issues but
   do not fix them without approval. Unscoped changes are unreviewed changes.

7. **Trace impact before changing interfaces.** Before modifying any function signature, return
   value, model property, or column definition, grep the entire codebase for all callers, consumers,
   and template references. Update every one.

8. **Ask before making design decisions.** If multiple valid approaches exist (adding a column vs. a
   table, denormalizing vs. not), present options with tradeoffs. Do not make architectural
   decisions unilaterally.

9. **Show your work.** Show actual terminal output from tests and linting, not summaries. The
   developer must be able to verify your claims from the output you provide.

10. **Understand before you change.** Before modifying any function over 20 lines, explain to
    yourself what it does and why. If you cannot explain it, you do not understand it. Ask the
    developer. NEVER rewrite a function from scratch unless explicitly asked.

11. **Debug, do not abandon.** When code errors, read the full traceback. Fix the specific bug. Do
    not throw away a correct approach because your first implementation had a bug.

12. **Write complete code.** Never use placeholder comments like "repeat for remaining cases" or
    "similar for other types." Write every line, every branch, every mapping entry.

13. **No gold-plating.** Implement exactly what was requested. No speculative abstractions, no
    "flexibility" or "configurability" that was not asked for, no error handling for impossible
    scenarios. If a simpler approach exists, propose it before building the complex one. If 200
    lines could be 50, rewrite it.

## Common Commands

```bash
# Dev server
flask run

# Tests -- full suite: ~4 min at -n 12 default (5,148 tests); single invocation OK
python scripts/build_test_template.py         # first-time setup; rebuild after migrations
pytest                                        # full suite with pytest.ini's -n 12
pytest tests/path/test_file.py -v             # single file (fast feedback)
pytest tests/path/test_file.py::test_name -v  # single test

# Lint
pylint app/ --fail-on=E,F

# Database migrations
flask db migrate -m "description"
flask db upgrade

# Seed (first-time setup, in order)
python scripts/seed_ref_tables.py
python scripts/seed_user.py
python scripts/seed_tax_brackets.py
```

## Architecture

```text
Routes (Blueprints) → Services (no Flask imports) → Models (SQLAlchemy) / Schemas (Marshmallow)
```

**Services are isolated from Flask** -- they take plain data, return plain data, never import
`request`/`session`. Do not violate this boundary.

**PostgreSQL schemas:** ref (lookup tables), auth (users/sessions), budget
(transactions/accounts/templates), salary (pay/tax/deductions), system (audit metadata).

**Key domain concepts:** Anchor Balance (real checking balance, projections flow forward from it).
Balance Calculator (period-by-period from anchor). Recurrence Engine (8 patterns from templates).
Paycheck Calculator (salary + raises - taxes - deductions). Status workflow:
`projected -> done|credit|cancelled`, `done|received -> settled`.

**Established patterns -- use these, do not reinvent:** Ownership helpers in
`app/utils/auth_helpers.py`. Security response rule: 404 for both "not found" and "not yours."
Structured logging via `log_event()`. Dependencies pinned in `requirements.txt` -- no new packages
without approval.

**Reference tables: IDs for logic, strings for display only.** Enums in `app/enums.py`, cached in
`app/ref_cache.py`. NEVER compare against string `name` columns in Python or Jinja.

## Definition of Done

A task is NOT complete until ALL of these are true:

1. Code is implemented in full -- no TODOs, no placeholders.
2. Docstrings and comments per coding standards.
3. `pylint app/ --fail-on=E,F` passes with no new warnings.
4. Targeted tests pass for changed files.
5. Full suite passes.
6. Test output (pass/fail counts) shown to developer.
7. Migrations tested in both upgrade and downgrade directions.
8. Commit message format: `<type>(<scope>): <what changed>`
9. Developer asked if they want to commit and push.

## Transfer Invariants

**CRITICAL INVARIANTS (violating any one is a critical bug):**

1. Every transfer has exactly two linked shadow transactions (one expense, one income).
2. Shadow transactions are never orphaned and never created without their sibling.
3. Shadow amounts, statuses, and periods always equal the parent transfer's.
4. No code path directly mutates a shadow. All mutations go through the transfer service.
5. Balance calculator queries ONLY budget.transactions. NEVER also query budget.transfers.

## Development Status

Phases 1-8 complete. Section 5A (Cleanup Sprint) complete. Section 5 (Debt and Account Improvements)
complete -- April 2026. See `docs/` for plans and roadmap.

## Deployment

Docker container (Gunicorn + Nginx + Cloudflare Tunnel) on bare-metal Arch Linux. No Ubuntu
packages, no exposed ports, no systemd. `.env` config: `DATABASE_URL`, `SECRET_KEY`,
`TOTP_ENCRYPTION_KEY`.

**Compose conventions (use these on every new service):**

- **Resource caps:** `deploy.resources.limits: { cpus, memory, pids }` plus `reservations` for
  long-running services. Do not use the legacy `mem_limit`/`pids_limit`/`cpus` top-level keys.
- **Image pinning:** `image: name:tag@sha256:<digest>`. The tag is human-readable; the digest is
  immutable. For production-side enforcement, use `${VAR:?msg}` interpolation so a missing
  digest fails the compose parse loud.
- **Hardening defaults:** `security_opt: [no-new-privileges:true]`, `cap_drop: [ALL]`,
  `read_only: true`, non-root `user:`, `tmpfs:` for any path the process writes. Add caps back
  one at a time with a comment explaining what specific entrypoint step needs them.
- **Docker secrets (compose v2, non-Swarm):** `uid:`/`gid:`/`mode:` in the secret reference are
  silently ignored. The container sees the HOST file's ownership and mode. If the consuming
  process runs as uid X inside the container, the host file must be readable by uid X --
  either chown the host file to X (sudo) or `chmod 0644` and rely on directory containment
  (mode 0700 on `secrets/`) for at-rest protection.
- **Networks:** pin subnets explicitly (`ipam.config.subnet`) for any bridge that
  `FORWARDED_ALLOW_IPS` or `set_real_ip_from` reference -- otherwise an auto-assigned subnet
  silently drifts on recreate and the trust boundary breaks.
- **External named volumes:** `external: true` on the pgdata volume (or any irreplaceable
  state) so `docker compose down -v` cannot destroy it.
- **`name:` field at top of file:** explicit project name. Defaults to the directory basename
  otherwise, which is brittle and shows up in `docker compose ls` as e.g. `docker` instead of
  the intended name.

When editing the shared-mode override `deploy/docker-compose.prod.yml` and the runtime copy
at `/opt/docker/shekel/docker-compose.override.yml`, they MUST match. `scripts/reconcile_prod_to_canonical.sh`
is the one-shot sync. The `deploy/nginx-shared/nginx.conf` has historically drifted behind the
on-host file; before any repo->host sync, diff the host file and back-port any host-only
hardening that the repo is missing.

## Style

No Unicode dashes. Use periods, commas, semicolons, or colons for sentence
breaks. Use - for ranges. All development on the `dev` branch.

## Standards and Protocols

Detailed standards are in these files. Read them when working on code, tests, or scripts.

- Coding standards (Python, SQL, HTML/Jinja, JS, CSS, shell): @docs/coding-standards.md
- Testing standards and problem reporting: @docs/testing-standards.md

## Tests -- 5,276 tests, ~62 s full suite at -n 12 (pytest-xdist default)

A single `pytest` invocation completes well under the 10-min hard
timeout. `pytest.ini` carries `-n 12 --dist=loadgroup` in `addopts`,
so the bare command runs the full suite across 12 parallel workers.
Override with `-n 0` for single-process debugging.

Per-test isolation is delivered by drop+reclone of a per-worker DB
from `shekel_test_template`: PG 18's `CREATE DATABASE ... TEMPLATE
... STRATEGY FILE_COPY` uses `file_copy_method=clone` on the btrfs-
backed PGDATA to reflink-copy the template in ~4-5 ms per clone
(`-n 0`) or ~30 ms per clone under 12-way contention.  Per-test
fixture floor: ~25 ms at `-n 0`, ~83 ms at `-n 12` (the cluster-
level `pg_database` lock serialises CREATE/DROP across xdist
workers).  Full-suite wall-clock at `-n 12` is ~62 s on a fresh
test-db container; back-to-back runs drift up to ~72 s as the
cluster's catalog cache fragments from CREATE/DROP churn -- a
`docker restart shekel-dev-test-db` returns to the ~62 s baseline.

The per-worker DB name is the stable form `shekel_test_{worker_id}`
(no PID suffix as of Phase 3b).  Two simultaneous pytest invocations
against the same cluster collide on the worker name -- the bootstrap's
orphan-cleanup active-connection filter prevents silent corruption,
so the second invocation fails loud with "database already exists".
This is a narrowing of the "concurrent pytest invocations are safe"
guarantee; sequential invocations are unaffected.

First-time setup: build the template once with
`python scripts/build_test_template.py`. Rebuild after migrations or
after edits to `app/ref_seeds.py` / `app/audit_infrastructure.py` --
the template carries the seeded reference data and the audit
triggers, and clones do not pick up source changes without a rebuild.
See `docs/testing-standards.md` "Test Run Guidelines" and "Building
the test template" for the full workflow and the per-directory batch
fallback (still supported for slow-PG environments and sequential
debugging).

## Single file or single test for fast feedback

pytest tests/path/test_file.py -v
pytest tests/path/test_file.py::test_name -v
