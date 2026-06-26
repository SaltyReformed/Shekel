# CLAUDE.md

Shekel is a personal budget app organized around **pay periods** (biweekly paychecks) rather than
calendar months. Every transaction maps to a specific paycheck with ~2-year forward projections.

**Stack:** Python 3.12+ · Flask 3.1 · SQLAlchemy 2.0 · PostgreSQL (multi-schema) · Jinja2 · HTMX ·
Bootstrap 5

**YOU ARE THE ONLY SAFEGUARD.** This project has no QA team and no human code reviewer. CI
(`.github/workflows/ci.yml`: pylint + the full pytest suite) runs on every pull request and on
pushes to `main`, and a branch protection rule on `main` blocks the merge until that `lint-and-test`
check is green. CI is therefore an enforced pre-merge gate -- but it is only as good as the tests,
and no human will catch a bad assertion or a missing case for you. The developer is a solo operator.
If you miss a bug, skip an edge case, or take a shortcut, that defect ships to production. In a
budgeting app, that means real money is mismanaged. Treat every line of code as if someone's rent
payment depends on it being correct.

## Rules

Requirements, not suggestions. Several are now backed by deterministic gates (see Automated
enforcement); where a gate enforces a rule, fix what it flags at the root rather than silencing it.

1. **No shortcuts, workarounds, or band-aids.** No stubbed `pass` or `TODO`, no hardcoded values, no
   broad `except Exception` (gate: pylint `broad-exception-caught`). Fix root causes, not symptoms;
   the correct solution is the only acceptable one.

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

## Automated enforcement

Many rules above are backed by deterministic gates, not just prose. Fix what a gate flags at the
root; never silence it with a bare disable.

- **Per-edit hooks (`scripts/hooks/`)** lint each `app/`/`scripts/` Python edit and hard-block on
  errors and the custom checkers `shekel-decimal-from-float` / `shekel-refname-compare`; templates
  and `requirements.txt` have their own guards.
- **Stop hook** runs full `pylint app/` -- the only place cross-file `duplicate-code` is caught --
  and hard-blocks once `scripts/hooks/ENFORCE_PYLINT_FLOOR` exists (the 10.00/10 lock-in).
- **Custom checkers:** `tools/pylint/shekel_checkers.py` (+ tests), loaded via `.pylintrc`. Add one
  when a rule is an AST pattern rather than hoping a reviewer remembers it.
- **CI + pre-commit** run `pylint app/` (checkers as hard `--fail-on`) and the full suite per PR;
  `useless-suppression` is on, so a disable that suppresses nothing is itself a finding.
- **Judgment the linters cannot mechanize** (float-on-money boundaries, IDOR, transfer invariants,
  DRY/SOLID, test quality) is the `code-reviewer` subagent and the `/standards` command.

A gate is a floor, not a ceiling: the judgment rules (2, 3, 6, 8, 10, 13) still apply.

## Common Commands

```bash
# Dev server (containerized -- the primary workflow since 2026-06-12;
# full prod parity: entrypoint pipeline, shekel_app role, redis
# rate limiting, hardened rootfs; live reload via the bind mount)
docker compose -f docker-compose.dev.yml up -d && docker logs -f shekel-dev-app

# Dev server fallback (host process; owner-role DB, no entrypoint gates)
flask run

# Tests -- full suite ~65 s at -n 12 (~5,500 tests); see Tests section
./scripts/test.sh                             # full suite (restarts test-db first)
./scripts/test.sh tests/path/test_file.py::test_name -v  # single test (fast feedback)
python scripts/build_test_template.py         # first-time setup; rebuild after migrations

# Lint (custom checkers load via .pylintrc; same gate CI enforces)
pylint app/ --fail-on=E,F,shekel-decimal-from-float,shekel-refname-compare,shekel-bare-money-quantize,shekel-disable-rationale,shekel-original-principal-as-balance

# Database migrations
flask db migrate -m "description"
flask db upgrade
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
`app/ref_cache.py`. NEVER compare against string `name` columns in Python or Jinja (gate:
`shekel-refname-compare` for Python; the template hook for Jinja).

## Definition of Done

A task is NOT complete until ALL of these are true:

1. Code is implemented in full -- no TODOs, no placeholders.
2. Docstrings and comments per coding standards.
3. `pylint app/` is clean: no new messages, and
   `--fail-on=E,F,shekel-decimal-from-float,shekel-refname-compare,shekel-bare-money-quantize,shekel-disable-rationale,shekel-original-principal-as-balance`
   passes (the per-edit and Stop hooks enforce this in-loop).
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

## Standards

Full standards: `docs/coding-standards.md` and `docs/testing-standards.md`. They are not
force-loaded; the path-scoped rules in `.claude/rules/` (`coding`, `database`, `testing`, `deploy`)
load the essentials automatically when you touch matching files and point you to the full doc.

## Tests

~5,500 tests, ~65 s at `-n 12`. Run via `./scripts/test.sh` (not bare `pytest`) -- it restarts the
`shekel-dev-test-db` container first and falls through to plain pytest in CI. Single test:
`./scripts/test.sh tests/path/test_file.py::test_name -v`; `SKIP_DB_RESTART=1` skips the restart on
chained runs. Rebuild the template after migrations: `python scripts/build_test_template.py`.
`.claude/rules/testing.md` and `docs/testing-standards.md` carry the full guidance.

## Deployment

Docker (Gunicorn + Nginx + Cloudflare Tunnel) on bare-metal Arch Linux: no Ubuntu packages, no
exposed ports, no systemd. `.env`: `DATABASE_URL`, `SECRET_KEY`, `TOTP_ENCRYPTION_KEY`. The compose,
hardening, and prod-override-sync conventions auto-load via `.claude/rules/deploy.md` when you touch
`deploy/` or compose files.

## Development Status

See `docs/project_roadmap_v5.md` for the roadmap and direction. Planning docs lag the code: treat
the codebase and recent git history as the source of truth for what is actually shipped, not any
doc's stated status.

## Style

No Unicode dashes. Use periods, commas, semicolons, or colons for sentence breaks. Use - for ranges.

## Git Workflow

Develop on `dev` or a short-lived feature branch off it. `main` is branch-protected: direct pushes
are rejected, and a merge requires an open pull request whose `lint-and-test` (CI) check is green.
CI runs on pull requests and on pushes to `main` -- NOT on pushes to `dev`, so `dev` work is
validated when you open its PR. To ship `dev` to `main`: open a PR `dev` -> `main`, wait for the
green check, then merge via the PR. Do NOT
`git checkout main && git merge dev && git push origin main` -- branch protection rejects it. After
a PR merges, resync `dev` so the next PR is not flagged out of date:
`git fetch origin && git checkout dev && git merge origin/main && git push origin dev`.
