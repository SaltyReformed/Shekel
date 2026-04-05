# CLAUDE.md

Shekel is a personal budget app organized around **pay periods** (biweekly paychecks) rather than calendar months. Every transaction maps to a specific paycheck with ~2-year forward projections.

**Stack:** Python 3.12+ · Flask 3.1 · SQLAlchemy 2.0 · PostgreSQL (multi-schema) · Jinja2 · HTMX · Bootstrap 5

**YOU ARE THE ONLY SAFEGUARD.** This project has no QA team, no code reviewer, no CI pipeline. The developer is a solo operator. If you miss a bug, skip an edge case, or take a shortcut, that defect ships to production. In a budgeting app, that means real money is mismanaged. Treat every line of code as if someone's rent payment depends on it being correct.

## Rules

These are requirements, not suggestions. Violating them is never acceptable.

1. **Do it right, not fast.** No shortcuts, no workarounds, no band-aid fixes. Do not stub with `pass` or `TODO`. Do not hardcode values. Do not use broad `except Exception`. Fix root causes, not symptoms. The correct solution is the only acceptable solution.

2. **Read before you write.** Read the ENTIRE file before changing it. Do not rely on memory or line numbers from planning documents.

3. **No guessing.** If uncertain, read the code or ask the developer. Do not assume what a function returns, what columns a table has, or what state an object is in. For ambiguous financial logic, ask before proceeding.

4. **NEVER ignore a problem.** If you find a failing test, bug, linter error, or logic flaw -- whether you caused it or not -- you MUST either (a) fix it, or (b) stop and report it with full details. There is no third option. Do not dismiss failures as "pre-existing." Do not say "unrelated to my changes" without investigating and reporting. The developer has no one else to catch these.

5. **NEVER modify a test to make it pass.** If a test fails after your change, your code is wrong, not the test. Financial assertions were computed by hand. Fix your code. The only exception is when the developer explicitly confirms the expected behavior has changed.

6. **Stay in scope.** Only modify code related to the current task. Report out-of-scope issues but do not fix them without approval. Unscoped changes are unreviewed changes.

7. **Trace impact before changing interfaces.** Before modifying any function signature, return value, model property, or column definition, grep the entire codebase for all callers, consumers, and template references. Update every one.

8. **Ask before making design decisions.** If multiple valid approaches exist (adding a column vs. a table, denormalizing vs. not), present options with tradeoffs. Do not make architectural decisions unilaterally.

9. **Show your work.** Show actual terminal output from tests and linting, not summaries. The developer must be able to verify your claims from the output you provide.

10. **Understand before you change.** Before modifying any function over 20 lines, explain to yourself what it does and why. If you cannot explain it, you do not understand it. Ask the developer. NEVER rewrite a function from scratch unless explicitly asked.

11. **Debug, do not abandon.** When code errors, read the full traceback. Fix the specific bug. Do not throw away a correct approach because your first implementation had a bug.

12. **Write complete code.** Never use placeholder comments like "repeat for remaining cases" or "similar for other types." Write every line, every branch, every mapping entry.

## Common Commands

```bash
# Dev server
flask run

# Tests -- full suite: ~11 minutes (2822 tests), always use timeout
timeout 720 pytest -v --tb=short              # full suite
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

```
Routes (Blueprints) → Services (no Flask imports) → Models (SQLAlchemy) / Schemas (Marshmallow)
```

**Services are isolated from Flask** -- they take plain data, return plain data, never import `request`/`session`. Do not violate this boundary.

**PostgreSQL schemas:** ref (lookup tables), auth (users/sessions), budget (transactions/accounts/templates), salary (pay/tax/deductions), system (audit metadata).

**Key domain concepts:** Anchor Balance (real checking balance, projections flow forward from it). Balance Calculator (period-by-period from anchor). Recurrence Engine (8 patterns from templates). Paycheck Calculator (salary + raises - taxes - deductions). Status workflow: `projected -> done|credit|cancelled`, `done|received -> settled`.

**Established patterns -- use these, do not reinvent:** Ownership helpers in `app/utils/auth_helpers.py`. Security response rule: 404 for both "not found" and "not yours." Structured logging via `log_event()`. Dependencies pinned in `requirements.txt` -- no new packages without approval.

**Reference tables: IDs for logic, strings for display only.** Enums in `app/enums.py`, cached in `app/ref_cache.py`. NEVER compare against string `name` columns in Python or Jinja.

## Definition of Done

A task is NOT complete until ALL of these are true:

1. Code is implemented in full -- no TODOs, no placeholders.
2. Docstrings and comments per coding standards.
3. `pylint app/ --fail-on=E,F` passes with no new warnings.
4. Targeted tests pass for changed files.
5. Full suite passes: `timeout 720 pytest -v --tb=short`
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

Phases 1-8 complete. Section 5A (Cleanup Sprint) complete. Section 5 (Debt and Account Improvements) complete -- April 2026. See `docs/` for plans and roadmap.

## Deployment

Docker container (Gunicorn + Nginx + Cloudflare Tunnel) on bare-metal Arch Linux. No Ubuntu packages, no exposed ports, no systemd. `.env` config: `DATABASE_URL`, `SECRET_KEY`, `TOTP_ENCRYPTION_KEY`.

## Style

No Unicode dashes. Use `--` for sentence breaks, `-` for ranges. All development on the `dev` branch.

## Standards and Protocols

Detailed standards are in these files. Read them when working on code, tests, or scripts.

- Coding standards (Python, SQL, HTML/Jinja, JS, CSS, shell): @docs/coding-standards.md
- Testing standards and problem reporting: @docs/testing-standards.md