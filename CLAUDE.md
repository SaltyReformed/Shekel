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

14. **ONE SOURCE OF TRUTH FOR ANY VALUE.** A value is either DERIVED or STORED, and each kind has
    exactly one home. **Derived: ONE WALK** -- one producer, and every caller reaches it. Two
    spellings that agree today are still two spellings, so agreement is not the test: a fifth
    spelling of one interest expression agreed over 200,000 randomised draws and parted at 500,000,
    `$565.37` against `$565.36`. Where a layer puts the shared leaf out of reach, MOVE THE LEAF --
    the placement is the root cause and the duplication is its symptom. **Stored: ONE PLACE** -- the
    same fact in two columns is two sources, so delete one. A value that is derived AND stored has
    both problems, and the stored copy is a stale cache with no reconciler: that is what the
    cash-balance arc exists to delete. **The tell is an INVARIANT** -- where a rule says two places
    must always agree, they are one value with two homes and a maintenance contract, and the remedy
    is to delete a home rather than keep them in step. An invariant that cannot be violated because
    there is nothing to violate is worth more than one a reconciler enforces. Rulings `balance:R-JA`
    (the rule), `R-IZ` (the derived half) and `R-IY` (a derivable column is deleted, not
    maintained), developer 2026-09-02.

## Automated enforcement

Many rules above are backed by deterministic gates, not just prose. Fix what a gate flags at the
root; never silence it with a bare disable.

- **Per-edit hooks (`scripts/hooks/`)** lint each `app/`/`scripts/` Python edit and hard-block on
  errors and the custom checkers `shekel-decimal-from-float` / `shekel-refname-compare`; templates
  and `requirements.txt` have their own guards.
- **Stop hook** runs full `pylint app/` -- the only place cross-file `duplicate-code` is caught --
  and hard-blocks once `scripts/hooks/ENFORCE_PYLINT_FLOOR` exists (the 10.00/10 lock-in).
- **Custom checkers:** `tools/pylint/shekel_checkers/` (+ tests), loaded via `.pylintrc`. Add one
  when a rule is an AST pattern rather than hoping a reviewer remembers it.
- **CI + pre-commit** run `pylint app/` (checkers as hard `--fail-on`) and the full suite per PR;
  `useless-suppression` is on, so a disable that suppresses nothing is itself a finding.
- **Plan gate (`tools/plan_gate/`)** grades the PLANNING documents against
  `docs/plans/conventions.md` -- every finding names a live owner, an identity class shares one tick
  state, an unruled fork refuses a tick on either remedy, the index and the specifications agree
  both ways, and the dependency graph is referential and acyclic. It also grades the ORDER: ranks
  are dense, a rank never precedes an unshipped blocker's, the derived `starts` column is
  recomputed, every step's description is one complete sentence, and every archived document
  declares itself one on its first line. **Editing a planning document is what runs it**
  (pre-commit, scoped to those files; CI runs `pytest tools/plan_gate`).
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

# Tests -- full suite ~4.5-5 min at -n 12 (~11,800 tests); see Tests section
./scripts/test.sh                             # full suite (restarts test-db first)
./scripts/test.sh tests/path/test_file.py::test_name -v  # single test (fast feedback)
python scripts/build_test_template.py         # first-time setup; rebuild after migrations

# Lint (custom checkers load via .pylintrc; same gate CI enforces)
pylint app/ --fail-on=E,F,shekel-decimal-from-float,shekel-refname-compare,shekel-bare-money-quantize,shekel-disable-rationale,shekel-transaction-status-bypass,shekel-ledger-model-bypass,shekel-unclassified-fenced-export,shekel-private-module-import

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
`projected -> done|received|credit|cancelled`, and every one of those back to `projected` (revert).
No status is terminal.

**Established patterns -- use these, do not reinvent:** Ownership helpers in
`app/utils/auth_helpers.py`. Security response rule: 404 for both "not found" and "not yours."
Structured logging via `log_event()`. Dependencies pinned in `requirements.txt` -- no new packages
without approval.

**Reference tables: IDs for logic, strings for display only.** Enums in `app/enums.py`, cached in
`app/ref_cache/`. NEVER compare against string `name` columns in Python or Jinja (gate:
`shekel-refname-compare` for Python; the template hook for Jinja).

## Definition of Done

A task is NOT complete until ALL of these are true:

1. Code is implemented in full -- no TODOs, no placeholders.
2. Docstrings and comments per coding standards.
3. `pylint app/` is clean: no new messages, and
   `--fail-on=E,F,shekel-decimal-from-float,shekel-refname-compare,shekel-bare-money-quantize,shekel-disable-rationale,shekel-transaction-status-bypass,shekel-ledger-model-bypass,shekel-unclassified-fenced-export,shekel-private-module-import`
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

**Invariant 3 is an instance of rule 14 and its THREE clauses are in three different states.** It is
one value with two homes and a maintenance contract, and the work of making it structural is "delete
a home", not "enforce it harder".

- **Amounts -- PARTLY structural.** A DERIVED shadow stores no figure at all since plan step
  `X-au-g-2c-2`, so there is nothing to disagree. An OWNER-PRICED shadow still stores one:
  `transfer_service/_amount.apply_amount_ownership`'s TAKE arm calls `state_own_amount` on both the
  parent and each leg, so the duplicate survives on exactly that branch.
- **Statuses and periods -- NOT structural.** `budget.transactions` and `budget.transfers` each
  store `status_id` and `pay_period_id`, and the transfer service keeps the pair equal by hand.
- **And they cannot simply be dropped: invariant 5 is WHY they exist.** The shadow mirrors its
  parent so the balance fold can read `budget.transactions` alone. Removing the mirror needs the
  fold to read something else first, which is plan step `X-bi-4`'s re-point to movements. This is
  the movement family's work, not an amount cutover's.

**Until then it is enforced exactly as written above**, and the step that finally deletes a home
owes this section a rewrite from a rule someone maintains into a fact the schema makes
unrepresentable.

## Standards

Full standards: `docs/coding-standards.md` and `docs/testing-standards.md`. They are not
force-loaded; the path-scoped rules in `.claude/rules/` (`coding`, `database`, `testing`, `deploy`)
load the essentials automatically when you touch matching files and point you to the full doc.

## Tests

**~11,800 tests, ~4.5-5 min at `-n 12`** (measured 2026-08-30: 11,788 passed in 278-296 s over four
runs, a run-to-run variance of ~18 s; the previously stated "~5,500 tests, ~65 s" had gone stale by
more than 2x in count and 4x in time). Run via `./scripts/test.sh` (not bare `pytest`) -- it
restarts the `shekel-dev-test-db` container first and falls through to plain pytest in CI. Single
test: `./scripts/test.sh tests/path/test_file.py::test_name -v`; `SKIP_DB_RESTART=1` skips the
restart on chained runs. Rebuild the template after migrations:
`python scripts/build_test_template.py`. **The wrapper also defaults to `-m "not docker"`, which
DESELECTS 28 container-spawning `tests/test_deploy` tests -- they vanish from the report entirely
rather than appearing as skips, so a green `./scripts/test.sh` run is not a claim about them; CI
runs bare `pytest` and executes all 28.** `.claude/rules/testing.md` and `docs/testing-standards.md`
carry the full guidance.

## Deployment

Docker (Gunicorn + Nginx + Cloudflare Tunnel) on bare-metal Arch Linux: no Ubuntu packages, no
exposed ports, no systemd. `.env`: `DATABASE_URL`, `SECRET_KEY`, `TOTP_ENCRYPTION_KEY`. The compose,
hardening, and prod-override-sync conventions auto-load via `.claude/rules/deploy.md` when you touch
`deploy/` or compose files.

## Development Status

**All live work is four ARCS, and `docs/plans/steps.md` is the single source of truth for what to do
next.** Start there and nowhere else: it holds every step in every arc IN EXECUTION ORDER, one
sentence each. **The next step is the first row of its order table.** A row whose `starts` column
reads `NOW` can be picked up today whatever its rank, which is how two steps run in parallel.

Four more registries sit beside it, all in `docs/plans/`: `ledger.md` is every open finding in every
arc and every row names a live owner; `rulings.md` is every developer ruling, keyed `(arc, id)` with
the arc a COLUMN; `conventions.md` is the 16 rules all of it is held to; `lessons.md` is what this
project has already paid to learn. Each arc's argument and step specifications stay in its own
document: `docs/audits/balance_architecture/README.md` (balance), and `implementation_plan_*.md` for
recurrence, pay_calendar, credit_card and bank_import. `steps.md` names which document and section
holds a given step's detail. **Every arc's rulings are in `rulings.md`, keyed `(arc, id)`, since
`balance:X-ao-2a`; no arc document states one, and each carries a `The rulings` pointer instead.**

**Anything under an `archive/` or `historical/` directory is a HISTORICAL RECORD and governs
nothing** -- every such file says so on its first line. Cite one for how a decision came to be,
never for what is true now, and never as a plan of record. The code as committed is the source of
truth for what the app does; planning documents lag it.

`docs/project_roadmap_v5.md` holds the older product direction and lags the code. Planning docs lag
the code generally: treat the codebase and recent git history as the source of truth for what is
actually shipped, not any doc's stated status.

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
