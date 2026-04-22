# Plan: Prompt for Remediation Implementation Plan

## Context

Shekel is a personal budget app where real money is at stake, maintained by a solo developer with no QA, no code reviewer, and no CI pipeline. A three-phase security audit has been conducted per `docs/security-audit-workflow.md`:

- **Phase 1 (complete):** Eight sessions (S1-S8) covering OWASP Top 10, SAST/DAST, supply chain, STRIDE threat modeling, business logic, migrations/schema, ASVS L2, and a red-team appendix. The output is `docs/audits/security-2026-04-15/findings.md` -- **160 findings** (1 Critical, 29 High, 52 Medium, 75 Low, 3 Info) against commit `3cff592` on branch `audit/security-2026-04-15`.
- **Phase 2 (this task):** Produce a remediation implementation plan.
- **Phase 3 (future):** Implement the plan.

The user is asking me to write a **prompt** (not the plan itself) that a fresh Claude Code session can execute to produce the Phase 2 remediation plan. The quality bar is `docs/implementation_plan_section8.md` -- a ~2000-line, 18-commit plan with file/line citations, full test tables, pseudocode for non-trivial logic, migration upgrade+downgrade with SQL backfills, dependency DAG, and risk/rollback per commit.

## What the Three Source Documents Established

**Workflow (`docs/security-audit-workflow.md`):** Prescribes Phase 2 output as `remediation-plan.md` with per-finding triage (Fix now / this week / this month / Accept), domain grouping (one PR per domain), dependency call-outs, complexity labels, and Phase 3 PR steps (feature branch off `dev`, regression tests in `tests/test_adversarial/` for Critical/High, scanner re-run, IDOR probe zero-failure re-run for access-control fixes, pylint re-run, findings.md updated with commit SHA + PR #).

**Findings (`docs/audits/security-2026-04-15/findings.md`):** Every finding has a fixed schema (Severity, OWASP, CWE, ASVS, Source, Location with file:line, Description, Evidence (quoted), Impact, Recommendation, Status). Explicit cross-references exist (F-001+F-016+F-017; F-028+F-080+F-082; F-009+F-077; F-002+F-035+F-006+F-017; etc.). Top-3 risks called out: (1) audit-log triggers missing from live DB; (2) five one-line crypto fixes; (3) anchor-balance + transfer invariants are convention-only. Proposed Phase 2 Accept candidates: F-092, F-093, F-094, F-147, F-148, F-149, F-152, F-154.

**Section 8 (`docs/implementation_plan_section8.md`):** Each commit has: Context, Files Modified, Model/Schema changes, Implementation Approach (line-level with real code snippets), Test table (ID, name, setup, action, expected, New/Mod -- ~15 tests per commit), Manual Verification, Downstream Effects, Rollback Notes. Uses a critical-path DAG with six ordering principles. Migrations include reversible SQL backfill. Financial correctness emphasized (Decimal, None-not-zero, user-scoping, invariant-preservation tests).

## Design Decisions (per your answers)

1. **Single file output** at `docs/audits/security-2026-04-15/remediation-plan.md` with strong ToC. Prompt allows Claude Code to propose a split if length exceeds ~5000 lines, subject to your approval.
2. **Full verification pass first** -- every finding re-checked against current code before grouping; written verification table at top of plan.
3. **Propose + rationale for deferrals** -- Claude Code writes rigorous Accept/Defer proposals for F-092/093/094/147/148/149/152/154 (and any others it identifies), then asks you via AskUserQuestion before finalizing.
4. **findings.md stays immutable** -- verification results live in the remediation plan only.

## The Prompt (ready to paste into a fresh Claude Code session)

Everything below the line is the prompt to hand to a fresh Claude Code session on your local machine. Paste it as the first user message of a new session (plan mode OFF -- Claude Code needs to read files and write the plan file, which plan mode would block).

---

# Shekel Security Audit: Remediation Implementation Plan

## Your mission

You are producing a comprehensive, production-grade remediation implementation plan for every finding in the Shekel security audit at `docs/audits/security-2026-04-15/findings.md` (160 findings -- 1 Critical, 29 High, 52 Medium, 75 Low, 3 Info).

Shekel is a personal budget app managing real money. The developer is a solo operator with **no QA team, no code reviewer, and no CI pipeline**. If a bug or edge case slips through your plan and ships, real financial harm results -- corrupted balances, mis-projected paychecks, silently lost money. If the project goes public, other users' finances are also at stake.

There are no shortcuts. There are no workarounds. There are no "we'll fix that later" band-aids. Do it right the first time, every time, even if it takes longer and costs more effort. The correct solution is the only acceptable solution.

This is a **planning-only** task. You must not write or modify application code, run migrations, run the test suite, commit, or push. Your one deliverable is a Markdown plan file. A future Claude Code session will execute it.

## Operating principles (every one of these is load-bearing)

Your work must satisfy all twelve principles. Violating any of them invalidates the plan.

1. **Do it right, not fast.** Refuse to stub, refuse to hardcode, refuse to add broad `except Exception` handlers, refuse to defer edge cases as "future work." A `# TODO` in your plan is a failure.
2. **Read before you write.** Before citing a file in the plan, read it in full. Do not rely on findings.md's quoted evidence alone; the code has evolved since commit `3cff592` and line numbers may have drifted. Verify every citation against the current file on this branch.
3. **No guessing.** If you cannot determine what a function does, what a column contains, or what state an object will be in, read more code -- or stop and ask the developer via AskUserQuestion. Financial logic is ambiguity-intolerant.
4. **Never ignore a problem.** If, while reading code for this plan, you notice a bug, an additional security issue, a failing invariant, a missing user-scope filter, a float where a Decimal should be, a test that does not assert behavior -- you MUST either fold it into the plan as a new task or report it to the developer. There is no third option. Do not dismiss it as "out of scope" without reporting.
5. **Never weaken a test to make it pass.** Not applicable here because you are not running tests, but the principle carries into the tests you specify: tests must assert exact, computed, hand-verified expected values. `assert result is not None` is not a test.
6. **Stay in scope.** Your scope is the 160 findings. Any new problem you find goes into the plan as a flagged new task, not a silent rewrite.
7. **Trace impact before any interface change in the plan.** If a commit proposes to change a function signature, return shape, column definition, or model property, the plan must enumerate every caller, template, service, and test that depends on it -- updated in the same commit.
8. **Ask before architectural decisions.** If a fix admits multiple valid shapes (new column vs new table; push log_event into service layer vs add decorator; Flask-Limiter redis backend vs separate memcached), present options with tradeoffs via AskUserQuestion. Do not decide unilaterally.
9. **Show your work.** The plan must quote findings, quote code, and cite file:line -- never paraphrase authority from memory. A statement like "the grid route does X" is insufficient; cite `app/routes/grid.py:NN`.
10. **Understand before you plan a change.** For every function over 20 lines that a commit will modify, explain (in the plan's Implementation Approach) what the function does today and why it changes. If you cannot explain it, do not plan to change it -- stop and read more.
11. **Debug, do not abandon.** If your first plan for a finding hits an obstacle (e.g., the fix breaks an invariant), revise the plan; do not pretend the finding is less severe.
12. **Write complete plans.** Never use placeholders like "repeat for remaining cases" or "similar for other fields." Every field, every migration column, every test case, every downgrade step is written out in full.

These principles come from `CLAUDE.md` and `docs/coding-standards.md` -- read them yourself before starting.

## Required reading (in this order, completely, before writing anything)

Read each file in full. Do not skim. Do not rely on prior context.

1. `CLAUDE.md` -- project rules, transfer invariants, definition of done.
2. `docs/coding-standards.md` -- Python, SQL, HTML/Jinja, JS, CSS, shell standards. Every rule is here because a past bug caused it.
3. `docs/testing-standards.md` -- test quality bar and the zero-tolerance failing-test protocol.
4. `docs/security-audit-workflow.md` -- the three-phase methodology. You are in Phase 2. Study what Phase 2 is supposed to produce and what Phase 3 expects as input.
5. `docs/audits/security-2026-04-15/findings.md` -- every one of 160 findings (~5500 lines). Read it end to end. Note the Summary, Threat Model, Accepted Risks, Scan Inventory, Verification, and Red Team appendix sections -- the red team challenged severities and confirmed them; do not re-argue.
6. `docs/implementation_plan_section8.md` -- this is the **quality bar**. Match or exceed the level of detail in: file:line citations, real code snippets, complete SQL migrations with backfills, test tables with 10-30 tests per commit including exact setup/action/expected, manual verification steps, and rollback notes. Read it cover to cover.

As you plan each commit, re-read the actual application code cited by each finding. Do not trust findings.md line numbers blindly -- verify every one.

## Output

Write exactly one file: `docs/audits/security-2026-04-15/remediation-plan.md`. Do not create any other files. Do not modify `findings.md`. Do not modify application code.

Before writing, check whether `docs/audits/security-2026-04-15/remediation-plan.md` already exists. If it does, read it in full before deciding whether to extend or replace (ask the developer). If a prior attempt exists, treat its content as provisional and subject to re-verification.

If your final plan exceeds ~5000 lines, stop and propose a split to the developer via AskUserQuestion (e.g., overview + phase-per-file) -- do not split unilaterally. Single-file output is the default.

## Phased execution

You will produce the plan in four phases. After Phases A, B, and E you will stop and check in with the developer via AskUserQuestion. After Phase F the plan is complete.

### Phase A: Verification

For every finding F-001 through F-160, do the following in order:

1. Open the file(s) cited in the finding's Location field. Read enough of the file to understand the cited region and its callers.
2. Compare the current code to the finding's Evidence block.
3. Classify the finding as exactly one of:
   - **Verified** -- pattern and evidence still present; the finding applies.
   - **Superseded** -- the vulnerable pattern has been removed or materially changed since commit `3cff592`. Cite the new code and, if you can identify it, the commit that changed it (`git log -S <token> -- <path>`).
   - **Partially applies** -- some of the cited instances remain; others were fixed. Enumerate exactly which remain.
4. Update file:line references to the current code. Treat stale line numbers as a defect of findings.md, not of the finding itself.
5. Re-check every cross-reference (e.g., F-001+F-016+F-017; F-028+F-080+F-082; F-009+F-077; F-034 precedes MFA fixes F-002/005/006). If a cross-reference no longer holds, note it.
6. If your reading of current code suggests a severity different from what findings.md claims, **flag the delta but do not change the severity** -- the red team has already affirmed severities. Record the flag for developer review.
7. If while reading you discover a new vulnerability or correctness problem that is NOT in findings.md (for example, an invariant violation, a missing user_id filter, a Decimal/float mix, a new IDOR), record it as a candidate new finding F-NEW-NNN. Do not silently roll it into an existing commit.

**Phase A output:** a table at the top of the remediation plan with these columns:

```
| Finding | Severity | Status | Current Location (file:line) | Cross-refs | Notes |
```

Plus a short section titled "New findings discovered during verification" listing any F-NEW-NNN items.

**Stop.** Call AskUserQuestion to present:
- The count of Verified / Superseded / Partially / New.
- The list of any Superseded findings with the evidence.
- Any proposed severity deltas (for developer review, not self-override).
- The list of new findings for developer acknowledgment.

Wait for the developer's response before starting Phase B. Do not proceed on your own judgment when the status of a finding is ambiguous.

### Phase B: Triage and grouping

After developer confirmation of Phase A:

1. **Verified-only severity counts.** Critical / High / Medium / Low / Info. (Superseded findings drop out.)
2. **Proposed disposition per finding:**
   - **Fix-now** -- ship in the first phase; Critical and most High findings.
   - **Fix-this-sprint** -- ship within the first three phases; most High and high-impact Medium.
   - **Fix-backlog** -- queued but not urgent; most Low and Info.
   - **Propose-defer** -- accept the risk; must include rigorous rationale. The workflow's Phase 2 candidates are F-092 (WebAuthn), F-093 (export/delete), F-094 (privacy policy), F-147 (encryption at rest), F-148 (secrets manager), F-149 (memory exposure), F-152 (read-record audit), F-154 (internal TLS). Evaluate each on its merits -- do not rubber-stamp. You may also nominate additional Defers if justified. Rationale must include: (a) threat-model delta -- what attackers can still do; (b) compensating controls currently in place; (c) cost-benefit (effort vs. residual risk); (d) deferral horizon (explicit months or a trigger event such as "before public launch"); (e) monitoring/detection that offsets the deferral; (f) re-open triggers.
3. **Dependency DAG.** Every commit depends on zero or more earlier commits. Dependencies are driven by:
   - Shared files: if commit M and commit N touch the same function, one must follow the other.
   - Data model: migrations precede consumers.
   - Infrastructure: rate-limit reliability (F-034) precedes MFA-lockout work (F-033, F-038); audit-log triggers (F-028) precede route-level log_event rollout (F-080); SECRET_KEY history rewrite (F-001) precedes cookie-flag work (F-017).
   - Safety ordering: session invalidation plumbing before downstream auth revocation paths.
4. **Commit groupings.** Rules:
   - One commit closes one or more findings that share code paths or are inseparable.
   - Do not mix unrelated findings in one commit. "Single PR per domain, not ten unrelated files" (workflow).
   - Criticals and most Highs get their own commits unless tightly coupled (e.g., F-017+F-018+F-019 crypto-flag bundle).
   - Cap commit size at what a lone developer can confidently review and test in one sitting.
5. **Phase ordering.** Follow a version of Section 8's six-principle rationale, adapted for security:
   1. **Crypto and history first** (unblocks everything else): SECRET_KEY rotation, history rewrite, cookie flags, HSTS, Cache-Control, backup-code entropy.
   2. **Rate limiting and session invalidation plumbing** (depends-on for every auth fix): F-034 Limiter backend, session_invalidated_at plumbing.
   3. **MFA and session hardening** (depends on #2): MFA session lifetime, account lockout, TOTP replay prevention, backup codes.
   4. **Audit-log restoration** (so subsequent fixes are observable): F-028 triggers, F-080 route-level log_event pass, F-082 off-host shipping.
   5. **Financial invariants** (the most consequential correctness gaps per findings.md Top 3): anchor-balance optimistic locking (F-009), TOCTOU duplication (F-008), transfer shadow mutation guards (F-007), stale-form prevention (F-010+F-046+F-048-052).
   6. **Input validation and schema/DB constraint sync** (done together, not apart): Marshmallow ranges and DB CHECKs; schema/validator mismatches.
   7. **Access-control response consistency** (after core auth is solid): 404-everywhere rule (F-087 class).
   8. **Logging and monitoring completeness.**
   9. **Config and hardening** (nginx, Gunicorn, Docker, host): version-control configs, hardening defaults.
   10. **Low/Info cleanup and dependency updates.**
6. **Proposed commit count.** State it, with reasoning. (Typical target: 40-60 commits; exact number follows from the finding count and grouping.)

**Phase B output:** four sections at the top of the remediation plan:
- Severity counts (post-verification).
- Proposed dispositions table (every Verified finding mapped to Fix-now/sprint/backlog/Defer).
- Proposed Defer section with full rationale per finding.
- Dependency DAG (text-based is fine; Mermaid if you can keep it readable) plus phase list.

**Stop.** Call AskUserQuestion to get the developer's approval of:
- Which proposed Defers they accept. (Any Defer the developer rejects moves to Fix-now or Fix-sprint.)
- Whether commit grouping matches their review bandwidth (too many / too few; too large / too small).
- Any sequence changes.
- Any new findings from Phase A that the developer wants to fold into the plan.

Do not proceed to Phase C without explicit developer confirmation on these points.

### Phase C: Per-commit detailed plan

For every commit in the approved grouping, write a section matching exactly this template. The quality bar is `docs/implementation_plan_section8.md` -- match or exceed its detail. No omissions, no placeholders, no "similar for other fields."

```
### Commit N: <short descriptive title>

**Findings addressed:** F-XXX (Severity), F-YYY (Severity), ...
**OWASP:** A0X primary; A0Y secondary
**ASVS L2 controls closed:** V3.x.y, V5.a.b
**Depends on:** Commit M1, Commit M2  (or "None -- independent")
**Blocks:** Commit N1, Commit N2  (or "None")
**Complexity:** Small / Medium / Large  (scope, not schedule)

**A. Context and rationale.** Two to five paragraphs. What problem this commit closes, why now, what ships-broken-if-skipped. Quote the highest-severity finding's Impact section verbatim. Cite CLAUDE.md invariants if they motivate this commit.

**B. Files modified.** Complete list, grouped by layer. Use current paths, not findings.md's paths.
  Models:
    - app/models/X.py
  Migrations:
    - migrations/versions/<new>.py
  Services:
    - app/services/Y.py
  Routes:
    - app/routes/Z.py
  Schemas:
    - app/schemas/W.py
  Templates:
    - app/templates/V/*.html
  Static:
    - app/static/js/..., app/static/css/...
  Tests:
    - tests/test_services/test_Y.py
    - tests/test_routes/test_Z.py
    - tests/test_adversarial/test_<security>.py  (for Critical/High findings, per workflow)
  Docs / config:
    - <as needed>

**C. Model / schema changes.** For every new or changed column:
  - Table and column name.
  - Type: Numeric(12, 2) for money (never Float, never Integer, never bare Numeric). DateTime(timezone=True) for timestamps. Date for dates. Integer with FK for ref tables.
  - Nullable: justify every nullable column in a code comment.
  - CHECK constraints with explicit names (ck_<table>_<description>).
  - FK definitions with explicit ondelete (CASCADE for user_id; RESTRICT for ref tables; CASCADE or SET NULL for inter-domain, justified).
  - Indexes (ix_<table>_<columns>), partial where appropriate.
  - Unique constraints (uq_<table>_<columns>).
  - Matching Marshmallow schema: validators must match DB CHECKs exactly (range, length, pattern).

**D. Implementation approach.** Line-by-line. For every file in B, cite file:line for insertions and modifications. For non-trivial logic, include production-ready code snippets (Python, SQL, Jinja, or JS) matching Section 8's style. For trivial changes, file:line plus a one-sentence description suffices.

Rules that must be observed and reflected in the snippets you write:
  - Money: Decimal constructed from strings ("0.1", never 0.1). Never float. Arithmetic at full precision.
  - Constant-time comparisons: bcrypt.checkpw, hmac.compare_digest, pyotp.TOTP.verify. Never string `==` on secrets or hashes.
  - Exception handling: specific exceptions only. No `except Exception`. Each except lists its exception tuple; error messages are actionable.
  - Ref-table logic: integer IDs, never string `name` comparisons. Enums from app/enums.py, cached in app/ref_cache.py.
  - User-scoping: every query filters user_id and (on soft-delete tables) is_deleted.is_(False).
  - Eager loading: joinedload/selectinload where the template or caller will access related collections.
  - Ownership helpers: use app/utils/auth_helpers.py. Do not reinvent.
  - Access-control response: 404 for both "not found" and "not yours" (workflow security rule).
  - Structured logging: call log_event() for every state change (see F-080 rollout).
  - Services never import request or session. They take plain data, return plain data.
  - Templates never compute money. Services compute; templates display.
  - CSRF: every non-HTMX form has {{ csrf_token() }}. HTMX gets CSRF via the base template's htmx:configRequest. State-changing HTMX uses hx-post, never hx-get.
  - No inline JS. All JS in app/static/js. Pass data via data-* attributes.

For any auth, crypto, session, or money change, document the exact business rule or formula in the new docstring.

**E. Migration plan.** For every schema change, specify:
  - Filename convention: `<revision>_<descriptive_slug>.py`, prior revision identified.
  - `upgrade()` body with exact `op.add_column`, `op.create_index`, `op.create_check_constraint`, `op.execute("SQL ...")` calls.
  - `downgrade()` body: fully reverse upgrade. Never `pass`. If truly impossible, raise NotImplementedError with a comment explaining why (must be justified to the developer first).
  - Backfill SQL: idempotent (WHERE ... IS NULL guards), reversible, production-ready. Quote the SQL in full.
  - NOT NULL on populated tables requires `server_default`, then (in a second migration or within this one) `alter_column(nullable=False)` after backfill.
  - Data loss considerations called out explicitly.

**F. Test plan.** Full table, no omissions.

| ID | Test file | Test name | Setup | Action | Expected | New / Mod |
|----|-----------|-----------|-------|--------|----------|-----------|
| CN-1 | tests/test_.../test_X.py | test_... | ... (explicit) | ... (explicit) | ... (exact computed value) | New |
| CN-2 | ... | ... | ... | ... | ... | Mod |

Rules:
  - ~10-30 tests per commit -- mirror Section 8's density.
  - Route tests assert response content AND status, not just status. Correct records; correct Decimal amounts (with arithmetic in a comment); correct template; correct HTML fragments for HTMX.
  - Service tests assert computed values with exact expectations. Every financial assertion includes the arithmetic that produced it (comment above the assert).
  - Edge cases get explicit tests (0, None, boundary, race, unicode, very large). An edge-case test asserts the specific edge behavior -- not just "did not raise."
  - Invariant-preservation tests for anything touching transfers, balances, shadows, periods, or reference tables.
  - Regression tests for Critical/High findings go in tests/test_adversarial/. Form for IDOR fixes: "User B requests User A's object; response is 404" (workflow).
  - Migration tests: separate test for upgrade, separate test for downgrade, test for backfill on realistic data.
  - Use existing fixtures from conftest.py (seed_user, seed_second_user, auth_client, second_auth_client). Do not create ad-hoc user setup.
  - Tests are independent. No ordering dependencies.

**G. Manual verification.** Numbered browser / curl steps. Must cover:
  - Golden path.
  - Edge cases (empty, boundary, unauthorized, soft-deleted, cross-user).
  - Regression check: other features using the same code still work.
  - Dark mode if UI.
  - Mobile viewport (Bootstrap sm/md breakpoints) if UI.

**H. Pylint.** Exact command: `pylint <touched files> --fail-on=E,F`. Expected: clean. Any new warnings must be justified with a one-line `# pylint: disable=<rule>` and a rationale comment.

**I. Targeted tests.** Exact command: `pytest <touched test files> -v --tb=short`. Expected: all pass.

**J. Full-suite gate (directory-split; never in one command; never concurrent).**
  ```
  timeout 720 pytest tests/test_services/ -v --tb=short
  timeout 720 pytest tests/test_routes/ -v --tb=short
  timeout 720 pytest tests/test_models/ -v --tb=short
  timeout 720 pytest tests/test_integration/ -v --tb=short
  timeout 720 pytest tests/test_adversarial/ -v --tb=short
  timeout 720 pytest tests/test_scripts/ -v --tb=short
  ```

**K. Scanner re-run (per workflow Phase 3).** Exact commands for bandit, semgrep, pip-audit on the touched files. Expected deltas.

**L. IDOR probe re-run.** If any access-control code was touched, re-run the DAST probe from Section 1M against a fresh dev compose. Zero failures required. Exact command.

**M. Downstream effects.** Every caller, template, test, script, or migration that this change could touch. For each: (a) why affected, (b) what this commit does about it, (c) any coordinated update the developer must notice.

**N. Risk and rollback.**
  - Specific failure modes (what breaks if X is wrong).
  - Rollback procedure: `git revert <SHA>`, `flask db downgrade <prior revision>`, data-loss consequences of rollback.
  - Behavioral invariants that a regression test (in tests/test_adversarial/) locks in.

**O. Findings.md update (for Phase 3 only; document here).** After the commit lands, the developer updates findings.md entry for each F-XXX with: `Status: Fixed in <SHA> (PR #<n>)`.
```

### Phase D: Cross-cutting concerns

After per-commit plans are complete, write a separate section titled "Cross-cutting concerns" covering patterns that span multiple commits. Quote the relevant commits; do not repeat their content.

Expected entries (adjust based on your Phase A findings):

- **Session invalidation pattern** (F-002, F-003, F-006, F-032): reusable helper location; proposed signature; where it is first introduced.
- **Stale-form / idempotency** (F-010, F-046, F-048-052): composite-unique constraint templates and request-ID dedup approach.
- **Marshmallow + CHECK constraint sync** (F-011-014, F-040-042, F-074-077): table mapping every validator range to its DB CHECK; verified in sync per commit.
- **log_event() systematic rollout** (F-028 + F-080 + F-082): triggers first, service-layer pushdown second, off-host shipping third. Architecture note on off-host destination (syslog / Loki / S3-with-object-lock) -- must be decided by the developer via AskUserQuestion if not already chosen.
- **Config version control** (F-021, F-156, F-157): where nginx, Gunicorn, compose overrides live; drift-check cron or hook.
- **Ref-table ID lookups** and enum/cache pattern (CLAUDE.md): applied per affected commit.
- **404-everywhere response rule** (F-087 class): enumerate the 51 affected routes by blueprint; note that the DAST probe confirms zero exploitable IDORs even before this consistency fix.
- **Crypto hardening bundle** (F-001 + F-004 + F-005 + F-017 + F-018 + F-019): how these fit together, why none overlap in code paths but all depend on SECRET_KEY rotation completing.
- **Backup code entropy upgrade** (F-004): secrets.token_hex(14) = 112 bits; migration to re-issue codes on next login and invalidate old codes.
- **TOTP replay prevention** (F-005): last_totp_timestep column; what locks the race.

### Phase E: Proposed Accept / Defer section

For every finding you propose to Defer (the workflow's Phase 2 candidates plus any you nominate):

- Finding ID, title, severity, OWASP.
- **Threat model delta:** what an attacker can still do.
- **Compensating controls** currently in place (cite code).
- **Cost-benefit:** effort to fix vs. residual risk.
- **Deferral horizon:** months, or explicit trigger ("before public launch", "when first B2B customer signs").
- **Monitoring and detection** that offsets the deferral.
- **Re-open triggers.**

**Stop.** Call AskUserQuestion listing every proposed Defer and ask the developer to Accept or Reject each. Any Rejected Defer moves to Fix-now or Fix-sprint and gets a commit section added in Phase C. Do not finalize the plan with any unapproved Defers.

### Phase F: Executive summary

After Phases A-E are complete, write (at the top of the plan, above the verification table) an executive summary:

- Audit headline: 160 findings; severity distribution post-verification; proposed Fix vs. Defer split.
- Top 3 risks (from findings.md, re-validated against current code).
- Proposed sequencing in one sentence per phase.
- Total commit count, estimated complexity mix (Small/Medium/Large).
- Critical path: first five commits in order, with one-line rationale each.
- Any architectural decisions still pending developer input.

## Invariants (from CLAUDE.md) that every commit in the plan must preserve

1. Every transfer has exactly two linked shadow transactions (one expense, one income).
2. Shadow transactions are never orphaned and never created without their sibling.
3. Shadow amounts, statuses, and periods always equal the parent transfer's.
4. No code path directly mutates a shadow -- all mutations go through the transfer service.
5. Balance calculator queries ONLY budget.transactions. NEVER also queries budget.transfers.

Reference tables: IDs for logic, strings for display only. Enums in `app/enums.py`, cached in `app/ref_cache.py`. NEVER compare against string `name` columns.

Services are Flask-isolated: plain data in, plain data out, no `request`/`session` imports.

## Clarification protocol (use AskUserQuestion whenever)

- A finding's severity seems materially off after verification. (Flag; do not override.)
- Two findings could be grouped or split different ways. Present the options.
- A fix needs an architectural decision (off-host log destination, Flask-Limiter backend, sessionstorage backend, WebAuthn library, secrets-manager vendor).
- An invariant tension appears (e.g., fixing a finding would require violating Invariant 4).
- The plan's total commit count seems too large for the developer's bandwidth -- offer re-grouping options.
- Findings.md's Evidence disagrees with current code and the discrepancy is load-bearing.

## Definition of done (for this plan)

The plan file is complete only when:

1. Every finding F-001 to F-160 has a verification status recorded in Phase A.
2. Every new finding discovered in Phase A has been shown to the developer and either folded in or explicitly acknowledged.
3. Every Verified finding has a disposition: assigned to a commit (Fix-now / Fix-sprint / Fix-backlog) OR approved Defer.
4. Every commit section has all fifteen subsections (A-O) filled in completely. No TODOs, no placeholders, no "similar for remaining."
5. Every migration has upgrade and downgrade fully written, with reversible backfill SQL.
6. Every test in every test table has concrete setup, action, and expected value (not `result is not None`).
7. Cross-references are consistent in both directions.
8. Every file path, line range, function name, and column name cited is current on branch `audit/security-2026-04-15`.
9. Every proposed Defer has been approved by the developer.
10. The executive summary at the top accurately reflects the detail below.
11. The plan's own linter check passes (Markdown renders, tables are well-formed, no broken relative links).
12. `pylint app/ --fail-on=E,F` would not fail on the code you propose in the snippets. (Reason through this; do not actually run pylint.)

## What you must never do

- Modify application code, templates, static files, or migrations.
- Modify findings.md.
- Run migrations, the test suite, or scanners.
- Create or modify any file other than `docs/audits/security-2026-04-15/remediation-plan.md`.
- Use broad `except Exception` in proposed snippets.
- Use floats for money in proposed snippets.
- Compare secret tokens with `==`.
- Write "TODO" or "future work" anywhere in the plan.
- Dismiss a finding or a newly-discovered issue as out-of-scope without first reporting it.
- Run the full test suite as a single command, or run two test suites concurrently. (User preference, from prior work.)
- Use Unicode em dashes or en dashes. The project style is ASCII only: `--` for sentence breaks and `-` for ranges.

## What you should do as soon as you start

1. Read the six required-reading files in full.
2. Sanity-check the current HEAD: `git log -1 --oneline` on branch `audit/security-2026-04-15`.
3. Verify that `docs/audits/security-2026-04-15/` exists and confirm whether any prior `remediation-plan.md` is present.
4. Begin Phase A.

You are the only safeguard this project has. Do not let a finding slip. Do not take a shortcut. Do it right, not fast.

---

## How to use this prompt

1. In your terminal: `cd /home/josh/projects/Shekel && claude` (or open a new Claude Code session in the repo).
2. Confirm plan mode is OFF (Claude Code needs Read, Grep, and Write to the audit directory; plan mode blocks Write).
3. Paste the prompt above as the first user message.
4. Expect Claude Code to spend substantial time on Phase A (reading 160 findings against current code). Pace expectations accordingly; the workflow sets no time budget.
5. Phase A checkpoint: Claude Code will use AskUserQuestion to confirm Verified/Superseded/New counts. Review carefully -- any Superseded classification changes which commits are needed.
6. Phase B checkpoint: approve or adjust dispositions, commit grouping, sequencing.
7. Phase E checkpoint: approve or reject each proposed Defer. Rejected Defers automatically move to Fix-now or Fix-sprint.
8. Final output lives at `docs/audits/security-2026-04-15/remediation-plan.md`.

## Critical files referenced by this prompt

- `/home/josh/projects/Shekel/CLAUDE.md`
- `/home/josh/projects/Shekel/docs/coding-standards.md`
- `/home/josh/projects/Shekel/docs/testing-standards.md`
- `/home/josh/projects/Shekel/docs/security-audit-workflow.md`
- `/home/josh/projects/Shekel/docs/audits/security-2026-04-15/findings.md`
- `/home/josh/projects/Shekel/docs/implementation_plan_section8.md`
- Output will be: `/home/josh/projects/Shekel/docs/audits/security-2026-04-15/remediation-plan.md`

## Verification that the prompt will produce the right output

The prompt is correct if a fresh Claude Code session executing it would produce a plan that:

1. Classifies every finding (160 / 160) with current file:line.
2. Groups findings into commits whose count, sequence, and dependencies the developer has explicitly approved.
3. Per commit: matches Section 8's detail -- A (context) through O (findings update) -- with real code snippets, complete SQL migrations with reversible backfills, test tables with computed expected values, per-commit pylint + targeted test + full-suite-split + scanner + IDOR-probe commands.
4. Surfaces any new findings discovered during verification, without absorbing them silently.
5. Presents proposed Defers with rigorous rationale and gets developer sign-off before they are finalized.
6. Enforces every CLAUDE.md invariant, every coding standard (Decimal from strings, specific exceptions, ID-based ref-table lookups, services-Flask-isolated, 404-for-everything, etc.), and every testing standard (exact expected values, adversarial regression for IDOR fixes, directory-split full-suite gate).
7. Leaves `findings.md` untouched.
8. Leaves application code untouched.

If Claude Code's output deviates on any of these, fall back to a clarifying question rather than accepting the plan.
