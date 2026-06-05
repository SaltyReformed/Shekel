# Pylint 10/10 Cleanup -- Master Plan and Progress Tracker

**Status: Phases 0-2 DONE; Phase 3 IN PROGRESS. As of 2026-06-05 app/ is 9.86/10 with ZERO
`duplicate-code` (R0801) clusters, zero `useless-suppression`, zero E/F; 194 visible messages.
Full suite 5755 passed.** Phase 3 (design smells) has EIGHT files complete. The newest,
**`services/debt_strategy_service.py` DONE** (`a1d076e`), dissolved all 7 function smells by
genuine decomposition (no disables): the developer-chosen frozen `StrategyRequest` param object
collapsed `calculate_strategy` to ONE arg, and the frozen `_SimulationState` working-state bundle
(mirrors `amortization_engine._ProjectionState`) + the extracted `_simulate_month` cleared the
`_cascade_extra_payments` (6->3 args) / `_build_result` (9->5 args) / `calculate_strategy`
(locals 23->12) smells; 40 callers wrapped in `StrategyRequest(...)`; 0 new R0801, 0 disables
added. The prior newest
**`routes/loan.py` DONE** (two-phase, developer-chosen: `e8b910b` decomposed all FIVE flagged
function smells by honest cohesive-helper extraction -- `dashboard` (46 locals/57 stmts) via five
context-slice builders merged into the render dict, `payoff_calculate` (35 locals) via one helper
per mode branch, `refinance_calculate` (30 locals) via `_project_refinance`/`_build_refinance_comparison`,
`_compute_payment_breakdown` (18) + `create_payment_transfer` (16) via extraction -- with the shared
`_build_chart_series` deduping dashboard's + payoff's chart-series (split-trap pre-empt) and the dead
`_build_chart_data` removed; then `f07fb1c` split the 1847-line module into the `app/routes/loan/`
package -- developer-chosen 5-concern split (`dashboard`/`params`/`escrow_rates`/`calculators`/
`payment_transfer` + `_bp`/`_helpers`), dissolving the re-surfaced configured-loan-guard R0801 via
the shared `_require_configured_loan` route-guard (`abort(404)`/`abort(redirect(...))`, single-line
call sites, 0 dup disables); all 10 endpoints preserved, behavior bit-identical). The next-newest
**`routes/transfers.py` DONE** (two-phase, developer-chosen: `21f2a31` decomposed all four flagged
handler smells by honest extraction incl. the shared `_render_post_mutation_cell` split-trap
pre-empt + the `create_ad_hoc` two-try merge, then `c4e9015` split the 1457-line module into the
`app/routes/transfers/` package -- developer-chosen 6-module split with a dedicated `forms.py`,
co-locating the instance mutations + status actions in `mutations.py`; 0 new R0801; all 16 endpoints
preserved, behavior bit-identical). The previously-newest
**`routes/transactions.py` DONE** (two-phase: `41cab0e` decomposed all four handler smells by honest
extraction incl. the shared `_resolve_owned_fks` IDOR primitive + the `_RenderTarget` bundle, then
`27e99f2` split the 1532-line module into the `app/routes/transactions/` package -- 6-module merge
of edit+status into `mutations.py` to keep the transfer-shadow R0801 clique intra-file; one
documented one-sided rule-13 disable for an incidental error-translation idiom; all 16 endpoints
preserved, behavior bit-identical). The four earlier fully-complete files: **`routes/salary.py`
DONE** (`4d7d7c1` returns+dead-imports, `e834635` calibrate decomposition, `131d648` split into the
`app/routes/salary/` package); **`services/amortization_engine.py` DONE** (`0e8b986` dead-code
removal, `c4f01e6` `project_forward` decomposition, `7cc8fe1` `calculate_payoff_by_date`
`PayoffRequest` param object + `_search_extra_for_payoff` binary-search extraction; file now
10.00/10, zero smell messages); **`services/savings_dashboard_service.py` DONE** (two-phase,
developer-chosen: `d05758b` decomposed all 13 function-level smells, then `0ec5586` split the
1379-line module into the `app/services/savings_dashboard_service/` package -- all smells gone,
each sub-module 10/10, 0 new R0801); and **`services/year_end_summary_service.py` DONE** (two-phase,
developer-chosen: `5eeb020` decomposed all 11 function-level smells via the `_ProjectionInputs` +
`_YearContext` bundles + shared `_load_shadow_contributions`, then `b96b8b8` split the 2437-line
module into the 10-module `app/services/year_end_summary_service/` package -- all smells gone, each
sub-module 10/10; the split trap re-surfaced ONE intra-file R0801 dissolved by the shared
`_loan_original_principal` helper). See the
[Phase 3](#phase-3----design-smell-refactors-158-visible--the-phase-1-smell-disables)
register and the [Progress Log](#progress-log). Ratified decision #5 (module splits = genuine
package splits, not disables) is locked. Phase 2 resolved every
one of the original 75 clusters by honest extraction or a documented one-sided disable (rule 13).
**Model clusters (20):** dissolved via six `app/models/mixins.py` mixins + 5 documented bipartite
disables (`57cf12d`/`ae815bc`/`561a369`/`d806eab`). **Route/service clusters (the rest):** five
commits this session --
`e2dc36a` route-fork dedup (`_recurrence_form_helpers` +commit_or_handle_stale /
update_recurrence_rule_from_form / resolve_recurrence_rule_for_update; new
`_transfer_creation_helpers.py`; templates/transfers/investment/loan routed through them);
`7b1236d` service helpers (`utils/dates.add_months`, `utils/money.percent_complete`,
`credit_workflow.create_cc_payback_transaction`, recurrence-tail disable);
`86eb309` access + account/period helpers (`auth_helpers.get_accessible_transaction`,
`account_service.get_account_type_ids_in_use` / `list_retirement_investment_account_types`,
new `utils/period_projections.project_balance_horizons`);
`6475429` documented 13 incidental clusters (divergent queries, parallel error-renders, domain
dataclass validation, the dated balance-bucketing variant);
`eb56235` the cross-route stale-data commit CLIQUE -- extracted to new
`app/routes/_commit_helpers.py` (commit_or_handle_stale + handle_stale_conflict moved out of
`_recurrence_form_helpers`; +regenerate_and_commit_or_stale for the flush-in-try case) and routed
the plain salary/savings/account handlers through it. **Key finding: a stale-handler CLIQUE (like
the FK cliques) cannot be one-sided-disabled -- disabled-vs-disabled pairs re-fire -- so it MUST be
extracted.** Disables 67 -> 83 (+16 documented one-sided R0801 disables for the genuinely-incidental
pairs; each scoped + rule-named + why-commented). Phase 3 (design smells) is next. See
[Phase 1 closeout](#phase-1-closeout) and the [Progress Log](#progress-log).**

This document is the single system of record for driving `app/` (then `scripts/`) to a clean
`pylint` 10.00/10. It exists so any session -- including a fresh one with no memory of this
conversation -- can determine exactly what has and has not been done, and verify every claim
against the actual code.

## Ground rule for this document

**If you cannot cite it, you cannot claim it.** Every status entry must be backed by either
(a) a `file:line` reference that exists in the tree, or (b) a command in the
[Verification](#verification-commands) appendix whose output you actually ran. No guesses, no
assumptions, no "should be done." When you complete work, record the commit SHA and the
re-measured number. When in doubt, re-run pylint -- the live tool output is ground truth, this
document is the decision log and worklist around it.

## How a new session determines current status (do this first)

1. Read the [Progress Log](#progress-log) at the bottom -- it lists every commit that moved the
   needle, newest last.
2. Re-run the baseline measurement commands in [Verification](#verification-commands) and compare
   to the [Baseline snapshot](#baseline-snapshot). The deltas tell you what has actually changed
   in the tree, independent of what this document claims.
3. For per-item status, the registers below (Phase 1 disables, Phase 2 clusters, Phase 3 smells)
   carry a Status column. A blank/`-` means not started. Trust pylint's live output over a stale
   checkbox: if the register says "done" but the message still fires, the register is wrong --
   fix it.

---

## Baseline snapshot

Measured at the state below. Reproduce with the [Verification](#verification-commands) commands.

| Field | Value |
|---|---|
| Date measured | 2026-06-04 |
| Git commit (HEAD) | `591264fb5f311847fd504ff7c32a6a32cd636692` (branch `dev`, tree clean) |
| pylint | 4.0.5 |
| astroid | 4.0.4 |
| Python (local lint env) | 3.14.5 |
| **`app/` score** | **9.68/10** |
| Visible messages (`app/`) | **423** |
| Inline `# pylint: disable=` directives (`app/`) | **74**, across 28 files |
| `scripts/` score (out of scope until app/ is done) | 9.27/10 |

### Visible message breakdown (the 423)

By type: `refactor` 224, `convention` 102, `warning` 97.

By symbol:

| Count | Symbol | Resolved primarily in |
|---:|---|---|
| 83 | `missing-type-doc` | Phase 0 (config: hints are source of truth) |
| 75 | `duplicate-code` | Phase 2 (DRY) |
| 71 | `line-too-long` | Phase 4 (mechanical) |
| 54 | `too-many-locals` | Phase 3 (refactor) |
| 32 | `too-many-arguments` | Phase 3 |
| 21 | `too-many-return-statements` | Phase 3 |
| 21 | `missing-function-docstring` | Phase 4 |
| 17 | `too-many-positional-arguments` | Phase 3 |
| 13 | `too-many-branches` | Phase 3 |
| 11 | `too-many-statements` | Phase 3 |
| 9 | `too-many-lines` | Phase 3 (file splits) |
| 7 | `unused-argument` | Phase 1 (framework-mandated; mostly keep+document) |
| 4 | `redundant-returns-doc` | Phase 0 (config) |
| 2 | `missing-param-doc` | Phase 4 |
| 1 | `protected-access` | Phase 4 / Phase 1 |
| 1 | `missing-class-docstring` | Phase 4 |
| 1 | `too-many-nested-blocks` | Phase 3 |

### CRITICAL note on counting (read before trusting any total)

The **423 visible messages exclude anything currently suppressed by the 74 inline disables.**
Some of those 74 disables hide real design smells (e.g. `auth.py` `too-many-*`,
`transfer_service.py` `too-many-arguments`). When Phase 1 removes or re-scopes a disable, the
underlying message becomes visible again, so **the visible count can rise after Phase 1 before
Phase 3 drives it back down.** Do not treat a temporary increase as a regression. The only
terminal success metric is: `pylint app/` reports 10.00/10 with zero messages AND every
surviving disable is justified per `docs/coding-standards.md`.

---

## Ratified decisions (locked 2026-06-04)

These were decided with the developer. Do not silently revisit them.

1. **Scope:** `app/` to 10/10 first; `scripts/` (currently 9.27) as a follow-on pass. `tests/`
   and `migrations/` are out of scope.
2. **Type docs:** signature type hints are the single source of truth. Phase 0 disables
   `missing-type-doc` and `redundant-returns-doc` in `.pylintrc`; `missing-param-doc` /
   `missing-return-doc` stay enabled so params still require a description, just not a redundant
   type restatement. This is a DRY policy, documented in `.pylintrc`, not a suppression of signal.
3. **Smell bar:** refactor genuinely where it improves the code; for genuinely irreducible
   complexity, replace blanket disables with **scoped + rule-named + why-commented** disables per
   `docs/coding-standards.md`. **Never raise a `.pylintrc` design threshold to win a smell.**
4. **CI:** once `app/` is clean, change CI to gate the full run (fail on any message) so 10/10
   cannot silently regress. See Phase 5 for the exact command.
5. **Module splits (locked 2026-06-04):** the `too-many-lines` modules (8 remaining: `auth.py`,
   `loan.py`, `transactions.py`, `transfers.py`, `amortization_engine.py`,
   `savings_dashboard_service.py`, `year_end_summary_service.py`, `carry_forward_service.py`;
   `schemas/validation.py` is module-tm-lines too -- re-measure) are **genuinely split into
   packages**, NOT closed with a documented `too-many-lines` disable. Follow the
   `app/routes/accounts/` and now `app/routes/salary/` precedent: a leaf `_bp.py` declares the
   blueprint (cycle-break), `__init__.py` re-exports it + imports sub-modules for side-effect
   registration, shared schema singletons / helpers live in `_helpers.py`. **Preserve every URL
   and endpoint name verbatim** so `url_for` / templates / `app/__init__.py` are untouched.
   **TRAP (learned on `salary/`):** splitting can re-surface `duplicate-code` (R0801) clusters the
   monolith hid -- R0801 compares ONLY across files, so intra-file dup in the monolith was
   invisible. Resolve by genuine dedup (route through a shared helper -- e.g. the salary stale
   handlers now use `_commit_helpers.regenerate_and_commit_or_stale`) or by co-locating
   intentional parallel code in one sub-module (e.g. `salary/items.py` holds both raises and
   deductions), NEVER by a duplicate-code disable. Also: a test that monkeypatches a symbol via
   the old module path (`patch("app.routes.salary.recurrence_engine...")`) must be repointed to
   the symbol's new home (`app.routes.salary._helpers...`); this is a patch-PATH update following
   moved code, not a rule-5 assertion change.

---

## The six phases

Ordering rationale: remove redundant noise first (Phase 0) so real signal stands out; do the
highest-goal-value work (disables, DRY, complexity) in the middle; lock in via CI last.

| Phase | Title | Primary target | Status |
|---|---|---|---|
| 0 | Re-baseline + audit `.pylintrc` | -87 type-doc; +13 surfaced via max-attributes revert | DONE (`10936f4`) |
| 1 | Audit all 74 inline disables | the disables themselves | **DONE** (74->61; 13 removed, 46 KEEP, 15->P3) |
| 2 | duplicate-code / DRY | 75 clusters | **DONE** (76->0; model clusters via 6 mixins + 5 disables; route/service via shared helpers + 16 documented one-sided disables; commits `e2dc36a`/`7b1236d`/`86eb309`/`6475429`/`eb56235`) |
| 3 | Design-smell refactors | 158 visible smells + smells revealed by Phase 1 | IN PROGRESS (8 files done: `salary/`, `amortization_engine.py`, `savings_dashboard_service/`, `year_end_summary_service/`, `transactions/`, `transfers/`, `loan/`, `debt_strategy_service.py`) |
| 4 | Mechanical residue sweep | line-too-long, missing docstrings | NOT STARTED |
| 5 | Lock it in (CI) + scripts/ | CI gate, then scripts/ to 10 | NOT STARTED |

Work cadence (all phases): batch by file-cluster, one coherent commit per cluster in
`<type>(<scope>): ...` format; run targeted tests for touched files per batch; full suite at
phase boundaries and as the final gate; show actual pylint + pytest output per batch
(coding-standards rule 9). Update this document's registers and the Progress Log as you go.

---

## Phase 0 -- Re-baseline the config

**Goal:** ratify decision #2 in `.pylintrc`, removing the 87 messages that duplicate type-hint
information (`missing-type-doc` 83 + `redundant-returns-doc` 4). This is the only phase that
changes config rather than code.

**Action (expanded per developer directive 2026-06-04):** the original action was only to add the
two type-doc disables. The developer directed a full audit of `.pylintrc` itself for exclusions
that hide handleable issues, keeping only those that genuinely cannot be handled another way. Both
were done together.

**`.pylintrc` audit + changes (every row verified by measurement; commands in
[Verification](#verification-commands)):**

| Setting | Finding | Action |
|---|---|---|
| `disable=missing-module-docstring` | 0 current violations; contradicts coding-standards ("docstrings on every module, no exceptions") | **REMOVED** -- it only hid future regressions, costs 0 now |
| `disable=import-error` | 0 violations locally; CI installs full deps (`requirements-dev.txt` has `-r requirements.txt`), so resolution matches | **REMOVED** -- now catches genuinely broken imports |
| `disable=too-few-public-methods` | 52 hits, all legitimate: 43 SQLAlchemy models, 4 Flask config classes, 1 Marshmallow schema, 2 result dataclasses (pension_calculator), 2 logging filter/formatter subclasses | **KEPT** + documented; no real smell hidden, 52 inline disables would be noise |
| add `missing-type-doc` (83) + `redundant-returns-doc` (4) | type info lives in signature hints (ratified decision #2); docstring type = DRY duplication | **ADDED** to disable; param/return DESCRIPTIONS still enforced via missing-param-doc/return-doc |
| `[DESIGN] max-attributes=15` | hid 13 service-class smells (8-13 attrs each); "SQLAlchemy models" justification factually wrong -- NONE of the 13 are models | **REVERTED** to default 7; 13 surfaced -> Phase 3 |
| `[BASIC] good-names`; `[VARIABLES] ^kwargs$` | conventional / framework-mandated (removing `^kwargs$` surfaces 69 Marshmallow `**kwargs`) | **KEPT** (genuinely unavoidable) |

Confirmed only `max-attributes` was a relaxed `[DESIGN]` threshold; max-args/locals/branches/
statements/returns/nested-blocks are all at pylint defaults, so every Phase 3 smell is real at
standard thresholds (not a relaxation artifact).

**Result (measured 2026-06-04, full config, `pylint app/ --reports=n`):**
- Score: baseline 9.68 -> **9.74/10**. (pylint may print "previous run: 9.94" -- a stale cache
  artifact from the isolated `--enable=X` audit runs, not a real prior full-config score.)
- Visible messages: 423 -> **349** (delta -74 = -83 type-doc - 4 returns-doc + 13 instance-attrs).
- Verified zero: `missing-type-doc`, `redundant-returns-doc`, `import-error`,
  `missing-module-docstring`. New: `too-many-instance-attributes` 13 (the surfaced smells).
- Files changed: **`.pylintrc` only.** No application code changed, so the test suite is unaffected
  (a lint-config edit cannot change runtime behavior).

**Status:** DONE. Commit `10936f4`.

---

## Phase 1 -- Audit all 74 inline disables

**Goal:** every disable is either removed (root cause fixed) or conforms to coding-standards
(one line, names the specific rule, has a why-comment). This is the highest-value phase for the
stated goal: surfacing code hidden behind a disable.

**Method per disable:** classify into one of:
- **KEEP+DOC** -- legitimate and essential (e.g. framework-mandated signature, real circular-import
  break). Ensure it is scoped, rule-named, and carries a why-comment. Verify the claim (for
  imports: actually try hoisting; if it breaks on a real cycle, it stays).
- **REMOVE** -- the suppression is unnecessary (cargo-cult import deferral, re-export better
  expressed with `__all__`). Fix the root cause and delete the disable.
- **FIX** -- the disable hides a defect to repair now (`broad-except`, `protected-access`).
- **-> PHASE 3** -- the disable hides a design smell; classification happens here, the actual
  refactor-or-justify is tracked in Phase 3.

Status values: `-` (unreviewed), `KEEP`, `REMOVED`, `FIXED`, `P3` (handed to Phase 3).

**Rule occurrences across the 74 disable lines** (sums to 85 because 11 lines disable 2+ rules;
verify with `grep -rhno "disable=[a-z,-]*" app/ | sed 's/.*disable=//' | tr ',' '\n' | sort | uniq -c`):
`import-outside-toplevel` 41, `too-many-positional-arguments` 7, `too-many-arguments` 7,
`global-statement` 6, `wrong-import-position` 4, `too-many-return-statements` 4,
`unused-argument` 3, `too-many-locals` 3, `too-many-branches` 3, `protected-access` 2,
`unused-import` 1, `too-many-statements` 1, `too-many-lines` 1, `line-too-long` 1,
`broad-except` 1.

### Register: import-outside-toplevel (41 sites)

Prior art: commits `9971094` (hoisted 37) and `6dcc503` (removed cargo-cult in 5 modules) already
swept a batch. Remaining ones are more likely real circular breaks -- but verify each by hoisting.

**Classifier (2026-06-04, `/tmp/classify_imports.py`):** for each service/util deferred import
`(source S, target T)`, a fresh interpreter imports T and checks whether S got pulled into
`sys.modules`. If yes, hoisting `import T` into S would cycle (KEEP); if no, there is no cycle
(hoist is technically safe). Result over the 21 service/util pairs:
- **CYCLE (genuinely circular, KEEP):** `ref_cache`->`models.ref`; `logging_config`->`extensions`. (2)
- **No cycle (19):** every other pair. Reading their comments, these are deferred for DELIBERATE
  reasons, not necessity: one-way dependency-boundary documentation
  (`carry_forward_service:734/881`, `loan_payment_service:347` -- comments say "top-level works...
  documents the intentional one-way dependency") and lazy-loading of heavy chains
  (`pension_calculator:97` -- "keeps this module a stdlib-only leaf"; the `paycheck_calculator` /
  `tax_config_service` imports in dashboard/savings/recurrence). `app/__init__.py` (~19 sites, app
  factory) not yet classified -- factory-pattern deferrals are a separate, generally-justified case.
- **POLICY (decided 2026-06-04): "Keep deliberate, document each."** Keep a deferral only when it
  serves a real purpose (one-way boundary OR measurably-heavy lazy-load OR leaf-purity OR
  init-timing); add a factual why-comment where missing; hoist genuine cargo-cult (cheap target,
  no purpose), verifying each by hoist + app construct + tests.

**Heaviness measurement (`/tmp/measure_import_cost.py`):** import TIME is a flat ~220ms for every
target (the unavoidable `app.extensions`/SQLAlchemy init, paid as soon as any service imports), so
deferral saves no meaningful startup time. Marginal `app.*` modules pulled (baseline 34): only
`savings_dashboard_service` is structurally heavy (+27); all others +0..+10
(`income_service` +10, `transaction_service` +9, `paycheck_calculator` +6, `loan_resolver` +5,
`account_service`/`recurrence_engine` +4, `interest_projection` +3, `tax_config_service`/`growth_engine` +2,
`models.user`/`models.ref` +0).

**Classification of the 21 service/util sites (policy A):**
- **KEEP (10):** circular -> `ref_cache:132`, `logging_config:543`; one-way boundary (developer's
  documented intent) -> `carry_forward:734/881/882`, `loan_payment:347/506`; leaf-purity ->
  `pension_calculator:97` (keeps a stdlib-only leaf); structurally heavy -> `dashboard_service:592`
  (`savings_dashboard_service` +27); init-timing (documented) -> `ref_seeds:154`. Action: ensure
  each carries a factual why-comment.
- **HOIST candidate / verify (cargo-cult: cheap target, no boundary/leaf/heavy purpose):**
  `settings:153` (`MfaConfig` +0), `investment_projection:250` (`growth_engine` +2),
  `year_end_summary_service:1877` (`interest_projection` +3), `auth_service:804`
  (`account_service` +4). Each: full-file read -> hoist -> `create_app()` import + targeted tests.
- **DEFER decision (revisit per-file):** the `paycheck_calculator`+`tax_config_service` lazy pairs
  (`dashboard:524/525`, `recurrence:736/737`, `savings:647/648`, `retirement:186`) and
  `balance_resolver:393` (critical financial core, +10). The developer consistently defers the
  paycheck/tax subsystem as a unit; lean KEEP+document, but confirm per file (the `recurrence:736`
  site sits inside a `try:` -- inspect before deciding). `app/__init__.py` (~19 factory sites) still
  to classify.

| file:line | Verdict | Reason / commit |
|---|---|---|
| app/__init__.py:67 | - | |
| app/__init__.py:187 | - | |
| app/__init__.py:188 | - | |
| app/__init__.py:212 | - | |
| app/__init__.py:223 | - | |
| app/__init__.py:224 | - | |
| app/__init__.py:235 | - | |
| app/__init__.py:236 | - | |
| app/__init__.py:237 | - | |
| app/__init__.py:238 | - | |
| app/__init__.py:239 | - | |
| app/__init__.py:240 | - | |
| app/__init__.py:273 | - | |
| app/__init__.py:274 | - | |
| app/__init__.py:329 | - | |
| app/__init__.py:398 | - | |
| app/__init__.py:437 | - | |
| app/__init__.py:675 | - | |
| app/__init__.py:845 | - | |
| app/ref_cache.py:132 | **KEEP** | Classifier: CIRCULAR (`models.ref`->`extensions` must init before cache loads). Commented. KEEP under any policy. |
| app/ref_seeds.py:154 | - | |
| app/routes/settings.py:153 | **REMOVED (hoist)** | Cargo-cult: `app.models.user` already imported at top (line 27 for `User, UserSettings`); merged `MfaConfig` in. create_app() OK; 335 area tests pass. |
| app/services/auth_service.py:804 | **REMOVED (hoist)** | Reassessed boundary->cargo-cult: no cycle, cheap target (+4), `auth_service` imported in only 2 files (so "keep path light" is weak), no explicit boundary comment. `account_service` used only in sign-up. Hoisted to top; create_app() OK; auth + registration tests pass. |
| app/services/balance_resolver.py:393 | **KEEP** | Documented deliberate (comment 385-391): keeps the income_service (+10) and loan_payment_service stacks off the hot `balance_resolver` module-load path + cycle-avoidance. Critical financial core. Comment already good. |
| app/services/carry_forward_service.py:734 | - | |
| app/services/carry_forward_service.py:881 | - | |
| app/services/carry_forward_service.py:882 | - | |
| app/services/dashboard_service.py:524 | **REMOVED (hoist)** | Cargo-cult: not a leaf, no cycle, `paycheck_calculator` +6 (not heavy). Hoisted. create_app OK; dashboard svc+route tests pass. |
| app/services/dashboard_service.py:525 | **REMOVED (hoist)** | `load_tax_configs` hoisted alongside :524. No test source-patches it for this module. |
| app/services/dashboard_service.py:592 | - | |
| app/services/investment_projection.py:250 | **REMOVED (hoist)** | Cargo-cult: module already has 3 top-level `app.*` imports (not a leaf, despite a stale comment at 140-141 claiming a "no-top-level-app-imports convention"). Hoisted `growth_engine.ContributionRecord`. create_app() OK; tests pass. |
| app/services/loan_payment_service.py:347 | - | |
| app/services/loan_payment_service.py:506 | - | |
| app/services/pension_calculator.py:97 | **KEEP** | Verified genuine leaf-purity: module top imports are stdlib-only (logging/dataclasses/datetime/decimal), no app imports. Deferring `paycheck_calculator` keeps it importable/testable without the app stack. Comment accurate. |
| app/services/recurrence_engine.py:736 | **KEEP** | Reclassified cargo-cult->KEEP: I hoisted these, but 3 `TestPaycheckAmountFallback` tests broke -- they `monkeypatch` the SOURCE `app.services.tax_config_service.load_tax_configs` (testing-standards-preferred), which a module-level `from`-import binds-once and won't see. Reverted; added a why-comment. Local import is load-bearing for source-patchability. |
| app/services/recurrence_engine.py:737 | **KEEP** | Same as :736 (the `load_tax_configs` from-import is the one that must stay local for the test patch). |
| app/services/retirement_dashboard_service.py:186 | **REMOVED (hoist)** | Clear cargo-cult: `paycheck_calculator` is ALREADY imported at top (line 34); only the cheaper `tax_config_service` was deferred -- incoherent. Hoisted `load_tax_configs`. Tests pass. |
| app/services/savings_dashboard_service.py:647 | **REMOVED (hoist)** | Cargo-cult (not a leaf, no cycle). Hoisted `paycheck_calculator`. Tests pass. |
| app/services/savings_dashboard_service.py:648 | **REMOVED (hoist)** | `load_tax_configs` hoisted; not source-patched by any test for this module. |
| app/services/year_end_summary_service.py:1877 | **REMOVED (hoist)** | Cargo-cult: `year_end` already imports `paycheck_calculator` + many services at top (line 42-59); `interest_projection.calculate_interest` is a low-level pure-math dep. Hoisted. create_app() OK; tests pass. |
| app/utils/logging_config.py:543 | **KEEP** | Classifier: CIRCULAR (`app.extensions` pulls in this module during logging setup). KEEP under any policy; consider adding a one-line "deferred: circular via extensions" note. |

(41 rows = the exact count of `disable=import-outside-toplevel` lines, verified via
`grep -rn "disable=import-outside-toplevel" app/ | wc -l`. The raw grep is the authority;
reconcile against `grep -rn "pylint: disable" app/` when working this register.)

### Register: design-smell disables (hand to Phase 3)

| file:line | Rule(s) disabled | Verdict | Reason / commit |
|---|---|---|---|
| app/routes/auth.py:23 | too-many-lines | - | file split candidate |
| app/routes/auth.py:357 (login) | too-many-return-statements | **REMOVED (useless), Phase 3** | Verified `login` has exactly 6 returns (= pylint `max-returns` default of 6; fires only at 7+), so the disable was a `useless-suppression`. Removed it; kept the design-rationale docstring, reworded from "Pylint note: ... is suppressed" to a "Design note" (the 6 distinct semantic exits are still worth documenting). No refactor, no behavior change. |
| app/routes/auth.py:627 (reauth) | too-many-return-statements | - | |
| app/routes/auth.py:738 (mfa_verify) | too-many-return-statements, too-many-branches | - | |
| app/routes/auth.py:969 (mfa_confirm) | too-many-return-statements | - | |
| app/services/balance_calculator.py:121 | too-many-arguments, too-many-positional-arguments, too-many-locals | - | |
| app/services/budget_variance_service.py:98 (compute_variance) | too-many-arguments, too-many-positional-arguments | - | |
| app/services/budget_variance_service.py:176 | too-many-arguments, too-many-positional-arguments | - | |
| app/services/budget_variance_service.py:261 | too-many-arguments, too-many-positional-arguments | - | |
| app/services/calendar_service.py:375 | too-many-arguments, too-many-positional-arguments | - | |
| app/services/dashboard_service.py:306 (_compute_alerts) | too-many-arguments, too-many-positional-arguments | **REMOVED (useless), Phase 3** | Verified `_compute_alerts` has exactly 5 params (= `max-args`/`max-positional-arguments` default of 5; fires at 6+), so BOTH disables were `useless-suppression`. Removed. No refactor, no behavior change. |
| app/services/spending_trend_service.py:296 | too-many-locals | - | |
| app/services/transfer_service.py:283 (create_transfer) | too-many-arguments, too-many-positional-arguments, too-many-locals | - | TRANSFER INVARIANTS apply |
| app/services/transfer_service.py:445 (update_transfer) | too-many-branches, too-many-statements | - | TRANSFER INVARIANTS apply |
| app/services/transfer_service.py:693 (restore_transfer) | too-many-branches | **REMOVED (useless), Phase 3** | Verified `restore_transfer` has 8 branches (4 top-level guards + the shadow `for` loop + 3 invariant-correction `if`s; <= `max-branches` default of 12, fires at 13+), so the disable was `useless-suppression`. Removed; TRANSFER INVARIANTS untouched, no behavior change. |

### Register: fix-now and other disables

| file:line | Rule | Verdict | Reason / commit |
|---|---|---|---|
| app/routes/health.py:52 | broad-except | **KEEP** (`10936f4`+) | Verified 2026-06-04 against code + test. Deliberate and test-locked: a health endpoint must convert ANY failure (DB/driver/pool exhaustion) into a controlled "unhealthy" JSON, never a 500 traceback, and must not leak `str(exc)` (audit M5). `tests/test_routes/test_health.py` lines 33/51/75 inject a bare `Exception()` and assert status=="unhealthy" + no credential leak; narrowing to `SQLAlchemyError` breaks all three. Disable is already scoped + rule-named + commented per coding-standards. NO CHANGE. (Corrects this register's earlier pre-read "FIX" guess.) |
| app/services/balance_resolver.py:565 | protected-access | **KEEP** | Reuses `balance_calculator._sum_all` so the resolver's math cannot drift from the engine's (audit E-25; CLAUDE.md rule 10). Considered promoting `_sum_all`/`_income_amount` to public, but that weakens a deliberate encapsulation boundary (the engine owns the math; the resolver is the one sanctioned reuse) and touches the critical financial core. Usage verified: `_sum_all` used 1 internal + 1 external (this), `_income_amount` 2 internal + 1 external (706); no test calls them by name. Disable is scoped + named + thoroughly commented. |
| app/services/balance_resolver.py:706 | protected-access | **KEEP** | Same rationale as :565 (reuses `balance_calculator._income_amount`). |
| app/models/__init__.py:9 | unused-import | **REMOVED** | Replaced the blanket module-level disable with an explicit `__all__` (43 re-exported models). Verified: pylint 10.00/10 on the file; all 43 names resolve (no typos/dupes). More precise than the disable -- a stray accidental unused import is still flagged. Confirmed `app.models` is not used as a class re-export API today (only `from app.models import ref`), so these are side-effect/Alembic-discovery imports. |
| app/models/loan_anchor_event.py:168 (_block_update) | unused-argument | **REMOVED** | Renamed the SQLAlchemy-mandated unused `mapper, connection` -> `_mapper, _connection` (matches `.pylintrc ignored-argument-names=_.*`); disable no longer needed. 13 immutability tests pass; pylint 10.00 on file. |
| app/models/loan_anchor_event.py:183 (_block_delete) | unused-argument | **REMOVED** | Same rename as `_block_update`. |
| app/services/loan_resolver.py:377 (`compute_monthly_payment_baseline`) | unused-argument | **KEEP** | Verified against code + caller: body is one expression using only `loan_params, rate_changes, as_of`; `anchor_events`/`payments` genuinely unused, kept for a deliberate uniform signature mirroring `resolve_loan`. Caller `loan_payment_service.compute_contractual_pi` passes `payments=` BY KEYWORD, so rename is unsafe; `_`-prefixing a public-API param is wrong. Disable is correct + documented. (But see problem P-1.) |
| app/ref_cache.py:147 | global-statement | - | lazy-cache init; likely KEEP+DOC |
| app/ref_cache.py:148 | global-statement | - | lazy-cache init; likely KEEP+DOC |
| app/ref_cache.py:149 | global-statement | - | lazy-cache init; likely KEEP+DOC |
| app/ref_cache.py:150 | global-statement | - | lazy-cache init; likely KEEP+DOC |
| app/ref_cache.py:151 | global-statement | - | lazy-cache init; likely KEEP+DOC |
| app/routes/obligations.py:54 | global-statement | **REMOVED** | Replaced the hand-rolled module-global lazy-init (`_FREQUENCY_LABELS = None` + `global` + null-check) with `@functools.cache` on `_get_frequency_labels()`. Behaviorally identical (memoize once per process; pattern IDs stable across cloned test DBs). Only referenced inside this module; 18 obligations route tests pass; pylint file 9.80->9.85. |
| app/ref_seeds.py:31 | line-too-long | - | long data literal; reflow or KEEP+DOC. NB: 6 other long lines in this file are visible (Phase 4) |
| app/routes/accounts/__init__.py:57 | wrong-import-position | - | blueprint registration order; verify, KEEP+DOC |
| app/routes/accounts/__init__.py:58 | wrong-import-position | - | |
| app/routes/accounts/__init__.py:59 | wrong-import-position | - | |
| app/routes/accounts/__init__.py:60 | wrong-import-position | - | |

**Status:** NOT STARTED. The authoritative list of all 74 raw disable lines is
`grep -rn "pylint: disable" app/` -- reconcile the registers above against it before declaring
Phase 1 complete (no orphaned or newly added disable left unreviewed).

---

## Phase 2 -- duplicate-code / DRY (75 clusters)

**Goal:** resolve every `duplicate-code` (R0801) cluster, each one either by honest extraction
(shared helper / base / mapping) or, where the similarity is genuinely incidental and extraction
would wrongly couple unrelated modules (coding-standards rule 13), a documented
`# pylint: disable=duplicate-code` with a why-comment.

**Note on attribution:** pylint anchors all 75 R0801 messages to `app/utils/logging_config.py:1`
in some output formats; that file is NOT the problem. The real sites are the file pairs below.

**High-value structural duplications to look at first** (largest / most systemic):
- **`routes/templates.py` <-> `routes/transfers.py`** -- ~18 clusters (#39-#56). The transfers
  route appears to be a near-fork of the templates route. Strong candidate for a shared helper
  module.
- **`services/recurrence_engine.py` <-> `services/transfer_recurrence.py`** -- ~7 clusters
  (#57, #70-#75), including 35-, 33-, 28-line blocks. Likely a forked engine; extract shared core.
- **Model boilerplate** (#1-#21) -- **ALL DONE (`57cf12d`/`ae815bc`/`561a369`).** Developer
  decision (2026-06-04): extend the `app/models/mixins.py` mixin pattern for genuinely-shared
  groups, document only coincidental similarity. `mixins.py` now carries SIX shared mixins:
  `OptimisticLockMixin` (batch 2 `d806eab`), `UserScopedMixin`, `SortOrderMixin`, `IsActiveMixin`,
  `AccountScopedMixin`, `SalaryProfileScopedMixin`. Two findings recorded in the working notes
  below: (a) FK/flag mixins REORDER the mid-table column to the table tail -- safe here, verified
  via order-independent equivalence (column order is load-bearing nowhere); (b) a non-bipartite
  FK clique (`account_id`, `salary_profile_id`) CANNOT be one-sided-disabled (a triangle re-fires)
  so it MUST be a mixin. The 5 genuinely-bipartite coincidental pairs carry documented one-sided
  disables. The #1-#21 table below is STALE (line numbers predate the mixin batches and the
  clusters are now all resolved) -- kept only for historical decode.

### Phase 2 working notes (methods + empirical findings, 2026-06-04)

Two things were established by experiment this phase; record them so a fresh session does not
re-derive them.

**1. R0801 inline-disable mechanics (pylint 4.0.5, verified on throwaway files).** Needed for the
deferred call-site-residue decision -- if/when a cluster is DISABLEd rather than extracted:
- `min-similarity-lines = 4` (pylint default; no `[SIMILARITIES]` override). Default also ignores
  docstrings and comments, so a matched run can span them; only string/identifier *values* break it.
- The R0801 message anchors at **module line 1 of whichever file is processed LAST**, never at the
  duplicated block.
- A `# pylint: disable=duplicate-code` placed in **exactly ONE** of the two blocks suppresses the
  whole cluster, reliably, in both file orderings.
- **TRAP:** the same disable in **BOTH** blocks makes it **re-fire**. So a documented disable must
  be one-sided (pick one file per pair and be consistent). Raising `min-similarity-lines` to hide a
  cluster is OFF THE TABLE -- morally identical to raising a `[DESIGN]` threshold (ratified
  decision #3) and it blinds the checker to future duplication.

**2. Mixin DDL-equivalence verification recipe (for `UserScopedMixin` and any future mixin batch).**
The `app/models/mixins.py` invariant is "byte-identical DDL / empty autogenerate diff." Verify it
WITHOUT needing a migrated dev DB by diffing the compiled `CreateTable` before vs after, plus the
mapper config where relevant. The batch-2 proof (10/10 byte-identical) used exactly this:
```python
# capture BEFORE (on the pre-change tree), then AFTER (post-change), and diff per table:
from app import create_app; from app.extensions import db
from sqlalchemy.schema import CreateTable
with create_app().app_context():
    ddl = str(CreateTable(Model.__table__).compile(db.engine))   # compare BEFORE == AFTER
    # for OptimisticLockMixin also: str(Model.__mapper__.version_id_col) == f"{tbl}.version_id"
```
Byte-identical `CreateTable` (+ unchanged `__table_args__` indexes, which I did not touch) is
STRONGER than "empty autogenerate diff" and means no migration + no test-template rebuild. The
`@declared_attr` form was required for `__mapper_args__` (a plain dict captures the mixin's unmapped
column); a class-level Column keeps DDL identical. Same caution applies to any `version_id_col`- or
column-referencing mapper option in a new mixin.

**REFINEMENT (model batches M1-M3, 2026-06-04).** Byte-identical holds ONLY when the extracted
column already sat at the table tail (true for `version_id`/`created_at`/`updated_at`). The
`user_id`/`account_id`/`salary_profile_id`/`sort_order`/`is_active` columns are MID-table, and
SQLAlchemy renders mixin columns AFTER a class's own columns -- so extracting them REORDERS the
column to the tail and `CreateTable` is NOT byte-identical. Verified safe and adopted the weaker
but sufficient standard **order-independent equivalence + empty autogenerate diff**: compare the
SORTED set of normalized `CreateTable` lines before vs after (column position is the only thing
that changes; columns/types/constraints/FK-names all match), so Alembic autogenerate (which
compares by NAME) emits no migration. Justification that order is non-load-bearing here: the test
suite clones the Alembic-migrated template (order comes from migrations, not the model); no code
or test does positional row/column access; the documented `create_all`<->migration alignment
invariant is about constraint NAMES, never order; no `ordinal_position` assertion exists. Recipe
(offline, no DB/app context needed -- compile against a bare dialect):
```python
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import postgresql
import app.models                      # registers every model on db.metadata
from app.extensions import db
dl = postgresql.dialect()
sig = lambda t: sorted(l.strip().rstrip(',')
                       for l in str(CreateTable(t).compile(dialect=dl)).splitlines() if l.strip())
# capture {t.fullname: sig(t)} BEFORE (pre-change tree) and AFTER; assert equal per table.
```
Also: a NON-BIPARTITE FK clique (3+ tables sharing a byte-identical FK block) CANNOT be resolved
with one-sided disables -- a triangle either fires (un-disabled edge) or re-fires (both-sides), so
it MUST be dissolved with a mixin. `account_id` (CASCADE) and `salary_profile_id` (CASCADE) were
exactly such cliques. And watch FLASK-LOGIN: `User` inherits `UserMixin` whose `is_active`
property would shadow a mixin Column via the MRO -- keep `User.is_active` inline.

**3. `too-many-arguments` watch.** Extracting a `log_event(...)` call into a named helper with one
param per field tripped `too-many-arguments` (10 params) AND dissolved no cluster -- reverted in
batch 1. Lesson: a thin log helper only pays off at many call sites AND with <=5 params (the two
existing `_recurrence_common` log helpers stay under the limit); the regenerate-log shape does not.

Status values: `-` (unreviewed), `EXTRACT` (done via extraction), `DISABLE` (documented incidental),
with commit SHA.

| # | ~lines | Site A | Site B | Status |
|---:|---:|---|---|---|
| 1 | 8 | models/account:161-169 | models/loan_anchor_event:100-108 | - |
| 2 | 9 | models/account:45-54 | models/transfer:86-95 | - |
| 3 | 25 | models/account:78-103 | models/paycheck_deduction:128-146 | - |
| 4 | 8 | models/category:21-29 | models/savings_goal:60-68 | - |
| 5 | 11 | models/category:31-42 | models/paycheck_deduction:129-138 | - |
| 6 | 20 | models/investment_params:89-109 | models/loan_params:83-94 | - |
| 7 | 9 | models/investment_params:89-98 | models/loan_anchor_event:102-111 | - |
| 8 | 10 | models/loan_anchor_event:102-112 | models/loan_features:76-86 | - |
| 9 | 6 | models/loan_features:146-152 | models/savings_goal:68-74 | - |
| 10 | 9 | models/loan_features:76-85 | models/loan_params:83-92 | - |
| 11 | 8 | models/pay_period:20-28 | models/recurrence_rule:18-26 | - |
| 12 | 13 | models/paycheck_deduction:80-93 | models/salary_raise:85-98 | - |
| 13 | 9 | models/salary_profile:100-109 | models/transfer_template:60-67 | - |
| 14 | 17 | models/salary_profile:100-117 | models/transaction_template:59-78 | - |
| 15 | 9 | models/salary_profile:37-46 | models/tax_config:29-41 | - |
| 16 | 9 | models/savings_goal:59-68 | models/scenario:31-40 | - |
| 17 | 10 | models/savings_goal:59-69 | models/transaction_template:33-43 | - |
| 18 | 8 | models/scenario:32-40 | models/transfer_template:39-47 | - |
| 19 | 11 | models/transaction:144-155 | models/transfer:125-136 | - |
| 20 | 14 | models/transaction_template:56-70 | models/transfer_template:57-67 | - |
| 21 | 31 | models/transfer:87-118 | models/transfer_template:39-55 | - |
| 22 | 5 | routes/accounts/crud:404-409 | routes/savings:270-275 | - |
| 23 | 8 | routes/accounts/crud:96-104 | routes/settings:128-135 | - |
| 24 | 7 | routes/accounts/detail:109-116 | services/savings_dashboard_service:105-122 | - |
| 25 | 8 | routes/accounts/detail:145-153 | services/savings_dashboard_service:490-502 | - |
| 26 | 9 | routes/debt_strategy:102-111 | services/year_end_summary_service:1538-1546 | - |
| 27 | 29 | routes/entries:75-104 | routes/transactions:360-375 | - |
| 28 | 11 | routes/investment:151-162 | routes/loan:1612-1623 | - |
| 29 | 7 | routes/investment:219-226 | routes/loan:1670-1677 | - |
| 30 | 7 | routes/investment:226-233 | routes/loan:1678-1685 | - |
| 31 | 9 | routes/investment:242-251 | routes/transfers:265-273 | - |
| 32 | 10 | routes/investment:242-252 | routes/loan:1689-1699 | - |
| 33 | 5 | routes/loan:1681-1686 | routes/transfers:427-432 | - |
| 34 | 22 | routes/obligations:120-142 | services/savings_goal_service:235-256 | - |
| 35 | 7 | routes/retirement:376-383 | routes/settings:363-370 | - |
| 36 | 7 | routes/salary:1551-1558 | services/retirement_dashboard_service:193-200 | - |
| 37 | 5 | routes/salary:426-431 | routes/savings:236-241 | - |
| 38 | 6 | routes/savings:53-59 | routes/settings:73-79 | - |
| 39 | 14 | routes/templates:142-156 | routes/transfers:113-120 | - |
| 40 | 6 | routes/templates:150-156 | routes/transfers:769-779 | - |
| 41 | 15 | routes/templates:206-221 | routes/transfers:183-199 | - |
| 42 | 6 | routes/templates:260-266 | routes/transfers:114-120 | - |
| 43 | 10 | routes/templates:311-321 | routes/transfers:335-345 | - |
| 44 | 21 | routes/templates:329-350 | routes/transfers:353-371 | - |
| 45 | 8 | routes/templates:356-364 | routes/transfers:376-384 | - |
| 46 | 9 | routes/templates:429-438 | routes/transfers:450-459 | - |
| 47 | 7 | routes/templates:431-438 | routes/transfers:579-586 | - |
| 48 | 5 | routes/templates:433-438 | routes/transfers:516-521 | - |
| 49 | 5 | routes/templates:478-483 | routes/transfers:454-459 | - |
| 50 | 7 | routes/templates:528-535 | routes/transfers:452-459 | - |
| 51 | 5 | routes/templates:530-535 | routes/transfers:667-672 | - |
| 52 | 10 | routes/templates:584-594 | routes/transfers:645-656 | - |
| 53 | 5 | routes/templates:599-604 | routes/transfers:581-586 | - |
| 54 | 6 | routes/templates:629-635 | routes/transfers:714-720 | - |
| 55 | 5 | routes/transactions:409-414 | routes/transfers:766-771 | - |
| 56 | 11 | routes/transactions:415-426 | routes/transfers:772-784 | - |
| 57 | 12 | routes/transfers:247-259 | services/transfer_recurrence:127-134 | EXTRACT `7ed84c7` (dissolved: transfer generate preamble hoist shifted the create_transfer call) |
| 58 | 11 | services/amortization_engine:68-79 | services/growth_engine:74-85 | - |
| 59 | 7 | services/balance_calculator:292-299 | services/balance_resolver:626-632 | - |
| 60 | 10 | services/balance_calculator:300-310 | services/balance_resolver:637-644 | - |
| 61 | 6 | services/budget_variance_service:245-251 | services/loan_payment_service:248-254 | - |
| 62 | 5 | services/budget_variance_service:248-253 | services/dashboard_service:674-679 | - |
| 63 | 8 | services/budget_variance_service:273-281 | services/calendar_service:259-267 | - |
| 64 | 20 | services/budget_variance_service:288-308 | services/calendar_service:276-302 | - |
| 65 | 7 | services/credit_workflow:238-245 | services/entry_credit_workflow:205-212 | - |
| 66 | 6 | services/dashboard_service:143-149 | services/spending_trend_service:251-257 | - |
| 67 | 12 | services/dashboard_service:515-527 | services/savings_dashboard_service:638-650 | - |
| 68 | 18 | services/dashboard_service:575-593 | services/entry_service:583-608 | - |
| 69 | 30 | services/debt_strategy_service:360-390 | services/savings_goal_service:435-444 | - |
| 70 | 11 | services/recurrence_engine:134-145 | services/transfer_recurrence:100-121 | EXTRACT `7ed84c7` (`should_skip_period`) |
| 71 | 11 | services/recurrence_engine:282-293 | services/transfer_recurrence:169-179 | EXTRACT `7ed84c7` (`query_rows_from_effective_date`) |
| 72 | 9 | services/recurrence_engine:298-307 | services/transfer_recurrence:184-193 | EXTRACT `7ed84c7` (`partition_regeneration_rows`) |
| 73 | 35 | services/recurrence_engine:336-371 | services/transfer_recurrence:223-259 | PARTIAL `7ed84c7` -- shared logic hoisted; residual is the regenerate-tail call SEQUENCE (own-module `generate_for_template` + identical `log_event` kwargs + raise + return), now live as `recurrence_engine:333-368 <-> transfer_recurrence:168-204`. Deferred to the call-site-residue decision (see Progress Log). |
| 74 | 28 | services/recurrence_engine:51-79 | services/transfer_recurrence:39-62 | EXTRACT `7ed84c7` (`check_scenario_ownership` + `_resolve_generation_plan`) |
| 75 | 33 | services/recurrence_engine:80-113 | services/transfer_recurrence:63-87 | EXTRACT `7ed84c7` (`_resolve_generation_plan`) |

**Status:** NOT STARTED. Re-run the cluster extraction command (Verification) after Phase 3 too:
refactors shift line ranges and can create or dissolve clusters.

---

## Phase 3 -- Design-smell refactors (158 visible + the Phase 1 smell-disables)

**Goal:** every `too-many-*` / `too-many-nested-blocks` resolved by genuine decomposition, or, if
irreducible, a scoped+named+commented disable per decision #3. Scope = the 158 visible items below
PLUS the smell-disables handed over from Phase 1 (`auth.py`, `balance_calculator.py`,
`budget_variance_service.py`, `calendar_service.py`, `dashboard_service.py`,
`spending_trend_service.py`, `transfer_service.py`).

Worked file-by-file, ordered by smell density (most first). `tm-` = `too-many-`.

Status per file: `-` (not started), `WIP`, `DONE` (file emits zero smell messages, or only
documented disables), with commit SHA.

### Tier 1 -- densest

- **services/year_end_summary_service.py** -- Status: **DONE** (two-phase, developer-chosen:
  `5eeb020` decomposed all 11 function-level smells, then `b96b8b8` split the 2437-line module into
  the `app/services/year_end_summary_service/` package -- all smells gone, each sub-module 10/10, 0
  net new R0801). **Phase 1 (`5eeb020`):** two frozen bundle dataclasses (both <=7 fields, no new
  disable) -- `_ProjectionInputs` (the 5 pre-loaded parameter maps) and `_YearContext`
  (year/scenario/all_periods/year_period_ids) -- threaded through the net-worth and savings-progress
  chains in place of the four-or-five parallel keyword maps each forwarded by hand. `_compute_net_worth`
  8->3 args; `_build_account_data` 7->4; `_get_account_balance_map` 7->4 (`inputs=None` for the
  base-balance callers); `_compute_savings_progress` 10 args/6 pos/20 locals -> 3 args via the
  extracted `_savings_progress_for_account`; `_project_investment_for_year` 9 args/7 pos/30 locals ->
  4 args via `_derive_investment_jan1` + `_summarize_investment_projection`;
  `_build_investment_balance_map` 6 args/28 locals -> 5 args via `_forward_project_periods` /
  `_reverse_project_periods` / `_merge_balance_sources`. The new shared `_load_shadow_contributions`
  dedupes the two near-identical inline shadow-income queries (developer-approved); the incidental
  6-line `joinedload`+filter overlap it surfaced with `budget_variance_service._query_by_period`
  (semantically unrelated) carries a one-sided rule-13 `duplicate-code` disable. All 6 smell functions
  are private and called only internally, so no signature is exposed. **Phase 2 (`b96b8b8`):** split
  into 10 per-concern sub-modules (each well under 1000 lines: `_types`/`_data`/`_periods`/`_balances`/
  `_income_tax`/`_spending`/`_transfers`/`_net_worth`/`_savings`/`_orchestrator`), partition verified
  import-cycle-free by AST cycle-detection; `__init__` re-exports only `compute_year_end_summary`.
  **Split trap (decision #5):** the split re-surfaced ONE intra-file R0801 the monolith hid (the
  `LoanParams`->`original_principal` idiom in `_get_account_balance_map` + `_compute_debt_progress`) --
  resolved by genuine dedup into the shared `_loan_original_principal` helper (DRY win; let `_net_worth`
  drop its now-unused `db`/`LoanParams` imports). Test patch-path updates (decision #5, no assertion
  change): `_compute_entry_breakdowns` -> `._spending`; `test_loan_unified_figures` uses
  `._balances._generate_debt_schedules` / `._income_tax._compute_mortgage_interest` and its
  bare-quantize sweep now runs `grep -r --include=*.py` over the package dir; `test_income_service`
  uses `._data._load_salary_gross_biweekly`. Package pylint 10.00/10; full suite 5755 passed.
- **services/savings_dashboard_service.py** -- Status: **DONE** (two-phase: `d05758b` + `0ec5586`;
  all 14 smells resolved, file now the `app/services/savings_dashboard_service/` package, each
  sub-module 10/10). The god-function
  `_compute_account_projections` (37 locals / 63 stmts / 18 branches / 6 args) decomposed into
  `_project_one_account` + `_compute_base_balances` + `_compute_loan_account`
  (+ `_loan_projected_horizons`, `_loan_ever_paid_off`) + `_compute_needs_setup`, with a frozen
  `_ProjectionContext` bundle clearing tm-args. `_project_investment` tm-args/locals via
  `_ProjectionContext` + `_investment_horizons`. `compute_dashboard_data` tm-locals via
  `_load_dashboard_core_data` (+ `_DashboardCoreData`) + `_apply_dti_metrics` (the
  `current_breakdown.gross_biweekly` read KEPT inline for the C15-style AST guard 1a in
  `test_savings_dashboard_service.py`) + `_sum_liquid_balances`. `_compute_goal_progress`,
  `_compute_avg_monthly_expenses`, `_compute_debt_summary`, `_load_account_params` tm-locals via
  cohesive extraction. Pure extraction, no logic change; public `compute_dashboard_data` signature
  unchanged (60+ call sites + AST guards unaffected). Full suite 5755 passed. **NOTE: the plan's
  earlier `tm-nested-blocks:359` was already stale -- the live tree had no nested-blocks smell.**
  **Phase 2 (DONE `0ec5586`):** the 1379-line module split into the 8-file
  `app/services/savings_dashboard_service/` package (`__init__` re-exports the public
  `compute_dashboard_data`; `_types`/`_data`/`_projections`/`_goals`/`_metrics`/`_display`/
  `_orchestrator`). Directory named to preserve the `from app.services import
  savings_dashboard_service` path. **0 new R0801** (split trap avoided). Test patch-path updates
  (decision #5, no assertion change): AST guard 1b -> parse `_orchestrator`; AST guard 2 -> glob all
  sub-modules; `_get_dti_label` -> `._metrics`; `_load_account_params` -> `._data`; C15-3 allow-list
  entry became the `services/savings_dashboard_service/` package prefix (the two `.current_principal`
  hits are prose, not reads). Unused module `logger` dropped. Each sub-module 10/10; full suite 5755.
- **services/amortization_engine.py** -- Status: **DONE** (`7cc8fe1`; file now 10.00/10, zero smell
  messages). module tm-lines RESOLVED + all four
  `replay_confirmed_history` smells RESOLVED by removing that dead/superseded primitive (`0e8b986`;
  1204->781 lines, no package split needed). `project_forward` ALL FOUR smells RESOLVED (`c4f01e6`):
  tm-args via the new `ProjectionInputs` param object (9->3 args); tm-branches/statements via the
  `_apply_override_payment`/`_apply_contractual_payment` helpers; tm-locals 33->14 via a
  `_ProjectionState` object + `_recast_for_rate_change` + reuse of `_advance_month` (genuine
  decomposition, no disable). `AmortizationRow` (9 attrs) tm-instance-attributes -> documented
  scoped disable (cohesive schedule-row DTO). `calculate_payoff_by_date` ALL FIVE smells RESOLVED
  (`7cc8fe1`): tm-args/pos via the new `PayoffRequest` param object (10->1 arg, developer-chosen
  Shape A with `target_date` included); tm-locals 25->12, tm-return 7->6, tm-branches 14->9 via the
  `_search_extra_for_payoff` binary-search helper. `PayoffRequest` (10 attrs) -> documented scoped
  tm-instance-attributes disable (legitimate Parameter Object, same pattern as `AmortizationRow`).
  **TRAP (learned here):** the param object converts the bare `current_principal` parameter into
  `request.current_principal` reads, which the C15-3 demoted-column lock
  (`test_loan_params_demoted.py`) flags on its coarse `.current_principal` grep. Resolved per the
  lock's own protocol (developer-chosen Option A): allow-listed `amortization_engine.py` (no DB
  access / no model import -- it structurally cannot read `LoanParams`; the value is the resolver-
  derived `state.current_balance`) + recorded F-28 in `remediation_follow_up.md`. Also updated the
  in-file C2-11 structural slice marker (now bounded by the inserted `PayoffRequest`; assertions
  unchanged). Behavior bit-identical (hand-derived Decimal locks pass). Full suite 5755 passed.
- **routes/loan.py** -- Status: **DONE** (two-phase, developer-chosen: `e8b910b` decomposed all five
  function smells + removed dead code, then `f07fb1c` split the 1847-line module into the
  `app/routes/loan/` package -- 5-concern split, all 7 smell items gone, each sub-module <=621 lines,
  0 net new R0801). **Phase 1 (`e8b910b`, honest cohesive-helper extraction, no disables, behavior
  bit-identical):** `dashboard` (46/15 locals, 57/50 stmts) -> `_build_dashboard_scenarios` +
  `_build_planned_summary` + `_build_payment_summary` + `_build_dashboard_chart_context` +
  `_resolve_transfer_prompt` + `_build_schedule_tab`, with the route assembling its render context by
  merging the per-section dicts (`**` unpack), 12 locals. `payoff_calculate` (35/15) -> one helper per
  mode branch (`_payoff_extra_payment_result` / `_payoff_target_date_result`) + `_payoff_committed_savings`
  + `_build_payoff_summary`. `refinance_calculate` (30/15) -> `_project_refinance` + `_refinance_break_even`
  + `_build_refinance_comparison` (note: pylint counts args toward R0914, so the split kept each helper
  <=15). `_compute_payment_breakdown` (18/15) -> `_distribute_payment_percentages` + `_project_next_year_escrow`.
  `create_payment_transfer` (16/15) -> `_resolve_transfer_amount`. **DRY win:** dashboard's + payoff's
  near-identical three-series chart building deduped into the shared `_build_chart_series` (history +
  forward -> aligned labels + padded balances), pre-empting the Phase-2 split trap (0 new R0801).
  **Dead-code removal (developer-approved, precedent `0e8b986`):** `_build_chart_data` had zero callers
  anywhere in app/ or tests/ (pylint cannot flag module-level dead functions). **Phase 2 (`f07fb1c`,
  package split -- developer-chosen 5-concern layout):** `_bp`/`_helpers` (8 schema singletons + the
  account/anchor/resolver-state/full-context loaders + chart utils + the 2 domain constants)/`dashboard`
  (route + its 12 helpers)/`params` (create_params, update_params, true_up_balance)/`escrow_rates`
  (add_rate_change, add_escrow, delete_escrow -- HTMX, shared OOB-payment tail co-located)/`calculators`
  (payoff_calculate, refinance_calculate)/`payment_transfer` (create_payment_transfer). All 10 endpoints
  + URLs + the `from app.routes.loan import loan_bp` path preserved verbatim. **Split trap (decision #5):**
  the "load configured loan, else 404/redirect" guard shared by update_params + true_up_balance +
  create_payment_transfer re-surfaced as a cross-file R0801 once split -- resolved by genuine dedup into
  `_require_configured_loan` (a real reusable route-guard, NOT incidental), which fully encapsulates both
  rejection paths via `abort(404)` / `abort(redirect(...))` (verified werkzeug raises a 302 from a Response)
  so the call sites are a single line with no residual duplication; 0 documented dup disables. Test path
  updates (decision #5, no assertion change): the 4 static-source guards in `test_loan.py` repoint to the
  package / moved helpers (`calculate_summary` -> package-dir glob; extra_payment branch ->
  `_payoff_extra_payment_result`; dashboard -> `dashboard.py` surface; refinance -> `_project_refinance`);
  C15-3 demoted-column allow-list `"routes/loan.py:"` -> `"routes/loan/"`; C17-6 bare-quantize sweep
  `"routes/loan.py"` -> `"routes/loan"`; `_transfer_creation_helpers` `:func:` cross-refs repointed to
  `.payment_transfer`. Package pylint 10.00/10; score 9.85; visible 208->201; tm-lines 3->2; smell items
  107->100; R0801 0; useless-suppression 0; E/F 0. 223 targeted loan tests + **full suite 5755 passed**.
- **routes/transactions.py** -- Status: **DONE** (two-phase, developer-chosen: `41cab0e` decomposed
  all four handler smells, then `27e99f2` split the 1532-line module into the
  `app/routes/transactions/` package -- all smells gone, each sub-module <1000 lines, 0 net new
  R0801). **Phase 1 (`41cab0e`, function decomposition -- honest extraction, no disables, behavior
  bit-identical):** `update_transaction` -> `_apply_shadow_update` (transfer-shadow path) +
  `_resolve_status_change` (state-machine verify + Credit-block + paid_at-revert) +
  `_apply_regular_update`. `mark_done` -> frozen `_RenderTarget` bundle
  (render_mode/card_prefix/can_edit, keeps helpers <=5 args) + `_mark_done_shadow` +
  `_mark_done_regular`. `cancel_transaction` -> `_cancel_shadow`. `create_inline` -> shared
  `_resolve_owned_fks(specs)` IDOR primitive (one owned-FK-by-id check; identical 404 for "not
  found" and "not yours"; a `None` id short-circuits without a NULL-PK query), which ALSO dedupes
  `create_transaction` + `get_quick_create`/`get_full_create`/`get_empty_cell` +
  `_verify_owned_fks_in_update` (developer-approved widening to all create/form routes). Shadow
  paths still route through `transfer_service` (TRANSFER INVARIANTS untouched). **Phase 2
  (`27e99f2`, package split -- developer-chosen 6-module merge):** `_bp`/`_helpers`/`forms`(GET
  partials)/`create`/`mutations`/`carry_forward`; all 16 endpoints + URLs preserved verbatim. **Split
  trap (decision #5):** surfaced TWO intra-file dups the monolith hid. (1) The transfer-shadow +
  mark_done helpers form an inseparable R0801 clique (shared commit/stale/tail), so `edit` + `status`
  were MERGED into one `mutations.py` -- intentional parallel code kept intra-file (module-level
  co-location). (2) A genuinely incidental 6-line `commit / NotFound->404 / Validation->rollback->400`
  idiom (`carry_forward` <-> `mutations.unmark_credit`; recurs across 5 route files; sites differ in
  everything else; `_commit_helpers` is redirect-only) -> documented one-sided rule-13
  `duplicate-code` disable on the `carry_forward` side (developer-approved deviation from "never
  disable split-trap" for a genuinely incidental, non-dedupable idiom). Test patch-path update
  (decision #5, no assertion change): `test_c19` -> `...transactions.mutations.credit_workflow...`.
  0 R0801, 0 useless-suppression, 0 E/F; +1 documented disable; the 4 `line-too-long` are pre-existing
  Phase 4 residue. Targeted 323/515 + full suite 5755 passed (both phases).
- **routes/transfers.py** -- Status: **DONE** (two-phase, developer-chosen: `21f2a31` decomposed all
  four flagged handler smells, then `c4e9015` split the 1457-line module into the
  `app/routes/transfers/` package -- all 8 smell items gone, each sub-module <1000 lines, 0 net new
  R0801). TRANSFER INVARIANTS untouched (shadow paths still route through `transfer_service`).
  **Phase 1 (`21f2a31`, honest extraction, no disables, behavior bit-identical):**
  `create_transfer_template` (17 locals/7 returns) -> `_materialize_initial_transfers` (the
  one-time/recurring instance-materialization tail). `update_transfer_template`
  (17 locals/8 returns/16 branches) -> `_first_unowned_template_fk` (FK-ownership probe) +
  `_regenerate_and_commit_template` (the regenerate-then-commit tail). `update_transfer` (11 returns)
  -> single-return FK loop + `_execute_transfer_update` (the service-call+commit 4-way error
  translation) + `_render_post_mutation_cell` (shared shadow/transfer cell render). `create_ad_hoc`
  (9 returns) -> `_handle_adhoc_integrity` (deduped IntegrityError handler) + merged the two
  equivalent try blocks (the `uq_transfers_adhoc_dedupe` hit fires at flush OR commit and routes to
  the same handler; `NotFoundError`/`ValidationError` originate only in the service call, so the
  merge changes no behavior). `_render_post_mutation_cell` also replaced the verbatim shadow-cell
  block in `mark_done`/`cancel_transfer` -- a DRY win that pre-empted the Phase-2 split trap.
  **Phase 2 (`c4e9015`, package split -- developer-chosen 6-module split with a dedicated
  `forms.py`):** `_bp`/`_helpers` (4 schema singletons + 5 shared ownership/render helpers)/
  `templates` (8 template-CRUD routes + 3 helpers)/`forms` (3 grid-cell GET partials)/`mutations`
  (5 instance routes + 3 helpers); all 16 endpoints + URLs + the `transfers_bp` import path preserved
  verbatim (no `url_for`/template/`app/__init__` edit). **Split trap (decision #5):** co-locating
  `update_transfer` + `mark_done` + `cancel_transfer` in `mutations.py` kept their parallel
  service-update/cell-response code intra-file, and the Phase-1 `_render_post_mutation_cell`
  extraction pre-deduped the shadow block -- 0 new R0801. Bonus: the fresh wrapped imports cleared 2
  pre-existing `line-too-long`. Test patch-path update (decision #5, no assertion change):
  `test_transfers.py` hard-delete bypass repoints
  `app.routes.transfers.archive_helpers` -> `...transfers.templates.archive_helpers`; two docstring
  `:func:` cross-refs (`_recurrence_form_helpers`, `_transfer_creation_helpers`) repointed to the
  `.templates` submodule. Package pylint 10.00/10; score 9.84->9.85; visible 218->208; smell items
  115->107; full suite 5755 passed (both phases).

### Tier 2

- **routes/salary.py** (module tm-lines; `add_raise`, `update_raise`, `add_deduction`,
  `update_deduction` all tm-return; `calibrate_confirm` tm-locals/statements) -- Status: **DONE**
  (`4d7d7c1`/`e834635`/`131d648`). `add_raise`/`add_deduction` returns resolved by extracting the
  HTMX/redirect dual-return responders (`_respond_after_*_change`, 8 sites); `update_raise`/
  `update_deduction` keep 7 guard-clause/audit-error returns under a documented
  `disable=too-many-return-statements`. `calibrate_confirm` tm-locals/statements resolved by
  extracting `_compute_total_pre_tax` (shared with `calibrate_preview` -- a Phase-2-missed dup) +
  `_reject_if_rates_inconsistent`. Module tm-lines resolved by splitting into the
  `app/routes/salary/` package (`_bp`/`_helpers`/`profiles`/`items`/`views`/`calibration`/
  `tax_config`; none >566 lines); stale handlers routed through
  `_commit_helpers.regenerate_and_commit_or_stale`; 2 dead imports removed. 0 R0801 clusters
  preserved. Full suite 5766 passed.
- **services/investment_dashboard_service.py** (`compute_dashboard_data`:299 tm-locals;
  `_project_dashboard_balances`:424 tm-args/locals; `_compute_contribution_prompt`:488 tm-args;
  `compute_growth_chart_data`:558 tm-locals; `_compute_what_if_overlay`:698 tm-args) -- Status: `-`
- **services/debt_strategy_service.py** -- Status: **DONE** (`a1d076e`; file now 10.00/10,
  zero smell messages). All 7 function smells resolved by genuine decomposition (no logic
  change, no disables, behavior bit-identical). `calculate_strategy` tm-args/pos (6/5) resolved
  by the developer-chosen frozen `StrategyRequest` param object (6 fields bundling
  debts/extra_monthly/strategy/custom_order/start_date/max_horizon_months; PayoffRequest
  precedent), so the public entry point takes ONE arg; all 40 callers (4 route + 36 test)
  wrapped in `StrategyRequest(...)`. The five parallel per-debt working arrays (the data clump
  threaded by hand) bundled into the frozen `_SimulationState` (6 fields) + `initialize()`
  factory -- mirrors `amortization_engine._ProjectionState`; `_accrue_interest` /
  `_apply_minimum_payments` / `_cascade_extra_payments` / `_build_result` now take `state`
  (`_cascade_extra_payments` 6->3 args, `_build_result` 9->5 args). `_simulate_month` extracts
  the per-month loop body so `calculate_strategy` tm-locals 23->12 (verified REQUIRED: without
  the extraction the loop body holds the function at 16/15). 0 new R0801 (the working-state
  bundle introduced no cross-file dup); 0 disables added (82 unchanged). The route's
  `calculate` handler keeps its pre-existing tm-locals/return/branches smells (separate Tier-3
  item, untouched -- wrapping the calls added no locals/branches/returns). 66 targeted + full
  suite 5755 passed.
- **routes/templates.py** (`update_template`:290 tm-locals/return/branches/statements;
  `preview_recurrence`:651 tm-locals) -- Status: `-`
- **services/retirement_dashboard_service.py** (`compute_gap_data`:114 tm-locals/statements;
  `_project_retirement_accounts`:432 tm-args/pos/locals) -- Status: `-`
- **routes/_recurrence_form_helpers.py** (`build_recurrence_rule_from_form`:100 tm-args/locals;
  `handle_stale_conflict`:213 tm-args; `handle_stale_form_conflict`:263 tm-args) -- Status: `-`
- **routes/grid.py** (`_build_plan_view`:342 tm-args/pos/locals; `index`:433 tm-locals) -- Status: `-`
- **services/paycheck_calculator.py** (`calculate_paycheck`:125 tm-locals;
  `_gross_biweekly_for_period`:319 tm-locals; `_calculate_deductions`:559 tm-args/pos) -- Status: `-`

### Tier 3 -- single-function or low-count files

- services/investment_projection.py: `calculate_investment_inputs`:104 tm-args/pos/locals -- `-`
- routes/debt_strategy.py: `calculate`:252 tm-locals/return/branches -- `-`
- services/loan_resolver.py: `resolve_loan`:383 tm-locals; `compute_payoff_scenarios`:658 tm-args/locals -- `-`
- services/retirement_gap_calculator.py: `calculate_gap`:39 tm-args/pos/locals -- `-`
- services/calibration_service.py: `derive_effective_rates`:35 tm-args/pos/locals -- `-`
- services/growth_engine.py: `project_balance`:206 tm-args/pos/locals -- `-`
- services/recurrence_engine.py: `generate_for_template`:55 tm-locals; `_match_periods`:453 tm-return -- `-`
- app/ref_cache.py: `init`:101 tm-locals/branches/statements -- `-`
- app/ref_seeds.py: `seed_reference_data`:114 tm-locals/branches -- `-`
- routes/accounts/detail.py: `interest_detail`:50 tm-locals; `checking_detail`:237 tm-locals -- `-`
- services/loan_payment_service.py: `prepare_payments_for_engine`:362 tm-locals; `live_loan_transfer_amounts`:463 tm-locals -- `-`
- app/__init__.py: `_register_blueprints`:435 tm-locals -- `-`
- routes/accounts/anchor.py: `true_up`:206 tm-return -- `-`
- routes/categories.py: `create_category`:38 tm-return -- `-`
- routes/entries.py: `update_entry`:287 tm-return -- `-`
- routes/obligations.py: `_next_occurrence`:102 tm-return; `summary`:324 tm-locals -- `-`
- routes/settings.py: `show`:43 tm-locals; `update`:191 tm-branches -- `-`
- services/account_service.py: `create_account`:86 tm-args -- `-`
- services/balance_calculator.py: `calculate_balances`:33 tm-branches -- `-`
- services/entry_service.py: `create_entry`:129 tm-args/pos -- `-`
- services/interest_projection.py: `calculate_interest`:81 tm-locals -- `-`
- services/projection_inputs.py: `build_investment_projection_inputs`:207 tm-args/pos -- `-`
- services/savings_goal_service.py: `amount_to_monthly`:201 tm-return -- `-`
- services/tax_calculator.py: `calculate_federal_withholding`:37 tm-args/locals -- `-`
- services/transfer_service.py: `update_transfer`:445 tm-locals (plus Phase 1 disables here) -- `-`
- schemas/validation.py: module tm-lines -- `-`
- services/carry_forward_service.py: module tm-lines -- `-`

### too-many-instance-attributes (13 -- surfaced by the Phase 0 max-attributes 15->7 revert)

NOT part of the original 158 count; these were hidden by the old `max-attributes=15`. All are
`app/services/` classes (none are ORM models). Each is a per-class call: refactor a genuine
god-object, or -- if it is a legitimate Parameter Object / result aggregate -- a scoped +
rule-named + commented inline disable. Count/limit shown as reported at the default max=7.

| file:line | attrs/limit | Status |
|---|---|---|
| services/paycheck_calculator.py:83 | 13/7 | - |
| services/spending_trend_service.py:52 | 11/7 | - |
| services/spending_trend_service.py:81 | 8/7 | - |
| services/retirement_gap_calculator.py:24 | 11/7 | - |
| services/loan_resolver.py:224 | 10/7 | - |
| services/calendar_service.py:71 | 10/7 | - |
| services/calendar_service.py:87 | 10/7 | - |
| services/carry_forward_service.py:87 | 10/7 | - |
| services/budget_variance_service.py:41 | 8/7 | - |
| services/budget_variance_service.py:55 | 9/7 | - |
| services/budget_variance_service.py:82 | 8/7 | - |
| services/growth_engine.py:24 | 9/7 | - |
| services/amortization_engine.py:164 | 9/7 | **DISABLE (documented), `c4f01e6`** -- `AmortizationRow` cohesive schedule-row DTO; scoped+named+commented |
| services/amortization_engine.py:708 | 10/7 | **DISABLE (documented), `7cc8fe1`** -- `PayoffRequest` Parameter Object (NOT one of the original 13; introduced by the `calculate_payoff_by_date` param-object refactor and immediately disabled); scoped+named+commented |

**Common refactor moves** (apply judgement, not mechanically): bundle cohesive args into a
dataclass/params object (the many tm-args calculators); extract cohesive sub-steps (tm-locals);
guard clauses + dispatch maps (tm-branches / tm-return); split oversized modules (tm-lines:
`auth.py`, `loan.py`, `salary.py`, `transactions.py`, `transfers.py`, `amortization_engine.py`,
`savings_dashboard_service.py`, `year_end_summary_service.py`, `carry_forward_service.py`,
`schemas/validation.py`). Trace every signature change to all callers/tests/templates first
(coding-standards rule 7). Understand any >20-line function before changing it (rule 10);
`auth.py` and `transfer_service.py` are security-/invariant-critical -- do not rewrite from scratch.

**Status:** IN PROGRESS. **`routes/salary.py` DONE** (`4d7d7c1`/`e834635`/`131d648`; see the Tier 2
entry above for the per-smell disposition). Methodology established on this first file, reusable
for the rest:
- **tm-return on form routes:** extract the genuinely-shared response/commit branches (real DRY,
  often drops the count under the limit as a side effect); for the residual guard-clause + audit-
  error-path returns that the coding standards MANDATE, document a scoped+named+commented
  `disable=too-many-return-statements` (collapsing them trips too-many-arguments / obscures
  distinct user flashes -- plan note #3).
- **tm-locals/statements:** decompose into cohesive named helpers; watch for Phase-2-missed dups
  (vars named differently dodge R0801) that the decomposition can dedupe for free.
- **module tm-lines:** split into a package per ratified decision #5 (see its TRAP note re:
  R0801 re-surfacing + monkeypatch-path updates).
Next by live density (re-measured 2026-06-05 after `debt_strategy_service.py` DONE `a1d076e`;
93 smell items remain [81 of the 8-symbol set + 12 `too-many-instance-attributes`], down from 100):
`services/investment_dashboard_service.py` (6), then `routes/_recurrence_form_helpers.py` (5) /
`services/paycheck_calculator.py` (5) / `services/retirement_dashboard_service.py` (5), then
`routes/grid.py` / `routes/templates.py` / `services/retirement_gap_calculator.py` /
`services/growth_engine.py` / `services/loan_resolver.py` (4 each) -- the financial cores
plan-first per the developer's cadence. None of the remaining top files are module tm-lines (only
`schemas/validation.py` + `services/carry_forward_service.py` carry that, both Tier-3 -> package
split per decision #5);
`investment_dashboard_service`/`paycheck_calculator`/`retirement_dashboard_service` are pure
services, function-level decomposition only.

---

## Phase 4 -- Mechanical residue sweep

**Goal:** clear what survives Phases 0-3. Real reflow (never `# noqa`); substantive docstrings
(business purpose, not a name restatement -- coding-standards). Do last: Phase 3 refactors create
and destroy some of these.

Per-file residue at baseline (re-measure before starting; Phase 1-3 will have changed it):

| File | Residue |
|---|---|
| app/schemas/validation.py | line-too-long:12, missing-function-docstring:18, missing-class-docstring:1 |
| app/jinja_globals.py | line-too-long:19 |
| app/routes/salary.py | line-too-long:8 |
| app/ref_seeds.py | line-too-long:6 |
| app/routes/transactions.py | line-too-long:4 |
| app/models/tax_config.py | line-too-long:4 |
| app/services/investment_projection.py | line-too-long:3, missing-param-doc:1, unused-argument:1 |
| app/models/salary_profile.py | line-too-long:2 |
| app/models/salary_raise.py | line-too-long:2 |
| app/routes/templates.py | line-too-long:2, protected-access:1 |
| app/routes/transfers.py | line-too-long:2 |
| app/services/paycheck_calculator.py | missing-function-docstring:3 |
| app/__init__.py | unused-argument:5 |
| app/models/investment_params.py | line-too-long:1 |
| app/models/paycheck_deduction.py | line-too-long:1 |
| app/models/user.py | line-too-long:1 |
| app/ref_cache.py | line-too-long:1 |
| app/routes/auth.py | line-too-long:1 |
| app/services/balance_calculator.py | line-too-long:1 |
| app/services/retirement_gap_calculator.py | line-too-long:1 |
| app/services/retirement_dashboard_service.py | unused-argument:1 |
| app/services/transfer_service.py | missing-param-doc:1 |

(`unused-argument` and `protected-access` rows overlap with Phase 1 handling; resolve in whichever
phase touches the file first and note it.)

**Status:** NOT STARTED.

---

## Phase 5 -- Lock it in (CI), then scripts/

**Goal:** make 10/10 a hard pre-merge invariant for `app/`, then repeat the method for `scripts/`.

**Steps:**
1. Confirm `pylint app/` reports **10.00/10, zero messages**. Re-run the Phase 2 cluster command
   (refactors shift duplicate-code).
2. **CI change** in `.github/workflows/ci.yml`. Current command (line 114):
   `pylint app/ --fail-under=9.0 --fail-on=E,F --output-format=colorized`
   Change `--fail-under=9.0` to `--fail-under=10` (keep `--fail-on=E,F` for explicit error
   catching). Update the explanatory comment block (lines 106-112) so it states the gate is now a
   full 10.00 floor. Re-confirm the developer wants the threshold flipped before committing (it is
   the ratified decision #4, but it changes the merge gate).
3. One-time sanity check: run `pylint app/ --enable=import-error` inside the venv to confirm the
   config-level `import-error` disable in `.pylintrc` is not masking a genuinely broken import.
4. Full test suite green (`./scripts/test.sh`) as the final gate.
5. Repeat Phases 0-4 for `scripts/` (baseline 9.27/10). Build its own register if needed.

**Status:** NOT STARTED.

---

## Verification commands

Run from repo root (`/home/josh/projects/Shekel`). These reproduce every number in this document.

```bash
# Baseline metadata
git rev-parse HEAD && git rev-parse --abbrev-ref HEAD && git status --porcelain
pylint --version

# app/ score
pylint app/ --reports=n 2>&1 | grep "rated at"

# All visible messages as JSON (exit code 28 = messages emitted; 0 = clean)
pylint app/ --output-format=json > /tmp/pylint_app.json

# Counts by symbol
python3 -c "import json,collections as c; d=json.load(open('/tmp/pylint_app.json')); \
print(sum(1 for _ in d),'total'); \
[print(f'{n:4d} {s}') for s,n in c.Counter(m['symbol'] for m in d).most_common()]"

# All inline disables (the authoritative list -- 74 at baseline)
grep -rn "pylint: disable" app/ | wc -l
grep -rn "pylint: disable" app/

# Distinct duplicate-code clusters (75 at baseline)
python3 -c "import json,re; d=json.load(open('/tmp/pylint_app.json')); \
cl={tuple(sorted(f'{m2}:[{a}:{b}]' for m2,a,b in re.findall(r'==([\w\.]+):\[(\d+):(\d+)\]',m['message']))) \
for m in d if m['symbol']=='duplicate-code'}; print(len(cl),'clusters')"

# Design-smell items with locations (158 at baseline)
python3 -c "import json; d=json.load(open('/tmp/pylint_app.json')); \
S={'too-many-locals','too-many-arguments','too-many-positional-arguments','too-many-branches', \
'too-many-statements','too-many-return-statements','too-many-nested-blocks','too-many-lines'}; \
i=[m for m in d if m['symbol'] in S]; print(len(i),'smell items'); \
[print(f\"{m['path']}:{m['line']} {m['symbol']} {m['obj']}\") for m in sorted(i,key=lambda m:(m['path'],m['line']))]"
```

---

## Phase 1 closeout

**Phase 1 (disable audit) is COMPLETE.** Every one of the original 74 inline disables was read
against the actual code and either removed (root cause fixed) or classified KEEP with a verified,
documented reason -- or, for the complexity smells, handed to Phase 3. No disable was left
unreviewed. Final disposition:

### Removed -- 13 (root-cause fixes, never suppression)

| Disable | Fix | Commit |
|---|---|---|
| `models/__init__:9` unused-import | explicit `__all__` (43 models) | `a28aea5` |
| `loan_anchor_event:168/183` unused-argument (x2) | rename to `_mapper, _connection` | `a28aea5` |
| `obligations:54` global-statement | `@functools.cache` | `a6ec28a` |
| `investment_projection:250`, `settings:153`, `year_end:1877`, `auth_service:804` import-outside-toplevel (x4) | hoist (cargo-cult) | `2bd8c90` |
| `dashboard:524/525`, `savings_dashboard:647/648`, `retirement:186` import-outside-toplevel (x5) | hoist (cargo-cult) | `3548906` |

### KEEP -- 46 (verified legitimate, all documented)

- **import-outside-toplevel (32):** `app/__init__.py` factory sites (~19: blueprint registration,
  model/`ref_cache` imports that cycle because everything imports `app`, F-091/F-095 security
  closures); circular (`ref_cache:132`, `logging_config:543`); one-way boundary
  (`carry_forward:734/881/882`, `loan_payment:347/506`, `balance_resolver:393`); leaf-purity
  (`pension_calculator:97`); measurably-heavy lazy-load (`dashboard:592` -> savings_dashboard +27);
  testability/source-patch (`recurrence_engine:736/737`); init-timing (`ref_seeds:154`).
- **global-statement (5):** `ref_cache` cache-init (`global` rebinds the module-level maps the
  accessors read; class encapsulation out of scope).
- **wrong-import-position (4):** `accounts/__init__` blueprint side-effect registration.
  **CORRECTED in Phase 3 (see Progress Log):** these 4 -- plus the 5 added later by the
  `salary/__init__` package split (`131d648`) -- were classified KEEP on a mistaken rationale that
  conflated runtime import ORDER (the sub-modules genuinely must import after `_bp`, true) with what
  `wrong-import-position` actually flags (imports appearing after NON-import code). Because nothing
  but the module docstring and the `_bp` import precedes them, the rule never fires;
  `useless-suppression` (full config) proved all 9 useless and Phase 3 removed them. The
  `# noqa: F401, E402` flake8 markers stay (a different tool; out of scope for the pylint pass).
- **broad-except (1):** `health.py:52` (test-locked: tests inject bare `Exception`, assert
  controlled "unhealthy" + no credential leak).
- **protected-access (2):** `balance_resolver:565/706` (deliberate engine-math reuse, audit E-25).
- **line-too-long (1):** `ref_seeds:31` (`# fmt: off` columnar data table, documented).
- **unused-argument (1):** `loan_resolver:377` (public uniform signature; `payments=` passed by
  keyword, can't rename).

### Deferred to Phase 3 -- 15 smell-disables (`too-many-*`)

These hide complex functions/files; the refactor-or-justify decision belongs to Phase 3, not a
Phase 1 suppression call: `auth.py:23/357/627/738/969` (the login/MFA flows -- security-critical),
`balance_calculator:121`, `budget_variance_service:98/176/261`, `calendar_service:375`,
`dashboard_service:306`, `spending_trend_service:296`, `transfer_service:283/445/693`. They appear
in the Phase 3 register; Phase 3 will decompose or replace each with a scoped+commented disable.

### Handoff to Phase 2 (do this in a FRESH session)

1. Open a new session in the repo; the `project_pylint_10_cleanup` memory points here.
2. Re-run the [Verification](#verification-commands) commands; confirm the live state matches the
   last Progress Log row (disables ~61, score 9.74/10, 350 visible).
3. **Phase 2 = duplicate-code.** The full 75-cluster worklist is in
   [Phase 2](#phase-2----duplicate-code--dry-75-clusters) (note: live count is now 76 -- the +1 is
   the documented R0801 re-pairing artifact from batch 3, harmless). Big structural targets:
   `templates.py` <-> `transfers.py` (~18 clusters) and `recurrence_engine` <-> `transfer_recurrence`
   (~7). Model-boilerplate clones (#1-#21) may be incidental -- judge before forcing a base/mixin.
4. Do NOT touch the 15 smell-disables in Phase 2 -- those are Phase 3.

## Problems surfaced during the audit (report-and-decide)

Real issues found while auditing disables -- the kind of defect a disable was hiding. These are
NOT fixed unilaterally (financial logic / out of scope); they await a developer decision per
CLAUDE.md rules 3, 4, 6, 8.

### P-1 -- `payments` documented as behavior-changing but ignored (loan P&I / escrow threshold)

- **Where:** `app/services/loan_payment_service.py:283` `compute_contractual_pi` docstring
  (lines ~322-330) vs `app/services/loan_resolver.py:332` `compute_monthly_payment_baseline`
  (body at ~378-380).
- **Found via:** auditing the `unused-argument` disable at `loan_resolver.py:377`.
- **The contradiction:** `compute_contractual_pi`'s docstring states that `payments`, when
  provided, "drives the conservative current-balance approximation in
  `compute_monthly_payment_baseline` so the threshold is guaranteed to be at-or-below the true
  `state.monthly_payment`", and that without it "the baseline uses `anchor_balance`, which
  slightly overestimates the threshold." The caller passes `payments=payments` (loan_payment_service.py:353).
  But `compute_monthly_payment_baseline`'s entire body is
  `return period_for_date(_resolve_periods(loan_params, rate_changes), as_of).period_pi` -- it
  references NEITHER `payments` NOR `anchor_balance`. It returns the rate-period level P&I,
  independent of both. So the documented payments-sensitivity / anchor_balance-fallback does not
  exist in the implementation.
- **Impact (needs developer judgement -- financial):** either (a) the docstring is stale and
  misleading (doc-only fix), OR (b) the implementation is incomplete -- the escrow-subtraction
  threshold is NOT actually "guaranteed at-or-below" for a paid-down ARM as documented, which
  could mis-subtract escrow from prepared payments. Cannot determine which is correct without the
  intended financial behavior of the threshold. Rule 3 (ambiguous financial logic): ask.
- **Recommendation:** developer confirms whether the period-P&I-only behavior is correct (then fix
  the caller docstring) or whether `payments` was meant to tighten the threshold (then it is a
  calculation bug to fix). Status: OPEN, reported 2026-06-04.

### P-2 -- account-dropdown query orders inconsistently across form routes

- **Where:** `app/routes/templates.py:157-161` and `:267-271` build the account dropdown with
  `db.session.query(Account).filter_by(user_id=current_user.id, is_active=True).all()` -- NO
  `order_by`. Every other account dropdown orders by `(sort_order, name)`:
  `transfers.py:109-113` / `:287-291`, `savings.py:54-58`, `settings.py:75-78`.
- **Found via:** Phase 2 route-query cluster investigation (the would-be `list_active_accounts`
  helper has two divergent forms).
- **Impact (UX, not financial):** the template create/edit form lists accounts in arbitrary
  (effectively insertion/PK) order while every sibling form honours the user's `sort_order`. Low
  severity, but it blocks a clean shared `list_active_accounts(user_id)` extraction: unifying on
  the ordered form is a (minor) behaviour change to the template form's dropdown order.
- **Recommendation:** align `templates.py` to `.order_by(Account.sort_order, Account.name)` (fixes
  the inconsistency AND unblocks the shared helper) -- but it changes a user-visible dropdown
  order, so confirm before changing (rules 5/6). Status: **RESOLVED `a608d77`** (developer
  approved). Added `account_service.list_active_accounts(user_id)` and routed all six form sites
  through it (templates x2 now ordered, transfers x2, savings, settings); dissolved the account-
  dropdown R0801 clusters. Full suite 5766 passed.

## Progress Log

Append one row per commit that changes the score or completes register items. Newest at bottom.
Each row MUST cite a commit SHA and a re-measured number you actually ran.

| Date | Commit | Phase | What changed | app/ score after | Visible msgs after |
|---|---|---|---|---|---|
| 2026-06-04 | `591264f` | -- | Baseline recorded. No code changed. | 9.68/10 | 423 |
| 2026-06-04 | `10936f4` | 0 | Audited + re-baselined `.pylintrc`: removed `import-error` & `missing-module-docstring` disables (0 violations each), added `missing-type-doc`/`redundant-returns-doc` disables (hints are source of truth), reverted `max-attributes` 15->7 (surfaced 13 service-class smells). `.pylintrc` only; no code changed. | 9.74/10 | 349 |
| 2026-06-04 | `a28aea5` | 1 | Batch 1 (disables): removed 3 (`models/__init__`->`__all__`; `loan_anchor_event` listeners->`_mapper`/`_connection`). Audited `health.py:52` + `loan_resolver.py:377` as verified KEEP. Surfaced problem P-1. Disables 74->71; score/msgs unchanged (removals emit nothing). | 9.74/10 | 349 |
| 2026-06-04 | `a6ec28a` | 1 | Batch 2 (disables): obligations `_FREQUENCY_LABELS` global -> `@functools.cache` (removed global-statement); audited balance_resolver protected-access x2 as KEEP (engine-math reuse, E-25). Disables 71->70; score/msgs unchanged. | 9.74/10 | 349 |
| 2026-06-04 | `d064f0d` | 1 | **Phase 1 CLOSEOUT**: documented the 3 remaining KEEPs needing inline why-comments (`ref_cache` globals, `dashboard:592` heavy lazy-load, `logging_config:543` circular). **Full suite 5766 passed.** Phase 1 COMPLETE: 74->61 disables (13 removed, 46 documented KEEP, 15 smell-disables -> Phase 3). Phase 2 is next in a FRESH session. | 9.74/10 | 350 |
| 2026-06-04 | `3548906` | 1 | Batch 4 (DEFER set): hoisted 5 cargo-cult paycheck/tax deferrals (dashboard x2, savings x2, retirement x1). RECLASSIFIED `recurrence_engine:736/737` cargo-cult->KEEP -- 3 fallback tests source-patch `tax_config_service.load_tax_configs`, so the local (per-call) import is load-bearing; reverted my hoist + documented. KEEP `pension_calculator:97` (verified stdlib-only leaf), `balance_resolver:393` (documented, critical core). create_app OK; 187+81+172+43 area tests pass. Disables 66->61. | 9.74/10 | 350 |
| 2026-06-04 | `2bd8c90` | 1 | Batch 3 (hoists): 4 cargo-cult import-outside-toplevel hoisted to module top (`investment_projection`, `settings`, `year_end`, `auth_service`). `create_app()` OK (no cycle); 335 area tests pass. Disables 70->66; import-outside-toplevel 41->37. Visible 349->350 is a pylint R0801 re-pairing artifact (a pre-existing 3-way `Account`-query duplication in savings/settings/transfers got re-reported as 2 pairings instead of 1 after a 1-line shift), NOT new duplication; Phase 2 dedupes it; score unchanged. | 9.74/10 | 350 |
| 2026-06-04 | `fb394c0` | 1 | import-outside-toplevel classifier: 2/21 service-util pairs genuinely circular (`ref_cache`, `logging_config` -> KEEP); 19 non-circular (deliberate boundary/lazy-load per their comments). Policy decision pending before touching the 19 + ~19 app-factory sites. No code change. | 9.74/10 | 349 |
| 2026-06-04 | `7ed84c7` | 2 | **Batch 1 (recurrence fork):** hoisted the model-agnostic halves of the two recurrence engines into `_recurrence_common.py` (`check_scenario_ownership`, `should_skip_period`, `partition_regeneration_rows`, `query_rows_from_effective_date`) + `recurrence_engine._resolve_generation_plan`/`_GenerationPlan` (gating + pattern-match preamble; kept there for `_match_periods` access). Model-specific halves (Transaction build vs `transfer_service.create_transfer` shadow atomicity; transfer regenerate delete path) stay per-engine. Tried+reverted a `log_recurrence_regenerated` helper (added too-many-arguments, dissolved nothing). Transfer engine sheds 7 imports. Clusters 76->70 (rows 57/70/71/72/74/75 EXTRACT; row 73 PARTIAL -- regenerate-tail call-SEQUENCE residual `recurrence_engine:333-368 <-> transfer_recurrence:168-204` left live, deferred to the call-site-residue decision). No new pylint messages. **Full suite 5766 passed.** | 9.74/10 | 343 |
| 2026-06-04 | `d806eab` | 2 | **Batch 2 (model boilerplate -- OptimisticLockMixin):** developer chose "extend the mixin pattern" for the genuinely-shared optimistic-lock block. Hoisted `version_id` + `__mapper_args__` out of 10 models (account, paycheck_deduction, transaction_entry, savings_goal, salary_raise, transfer, transfer_template, transaction_template, transaction, salary_profile) into `OptimisticLockMixin`. Column at class level (byte-identical DDL); `__mapper_args__` via `@declared_attr` so each subclass binds its own `version_id_col`. Per-table `ck_*_version_id_positive` CHECKs stay in `__table_args__`. **Verified: CreateTable DDL byte-identical for all 10 tables vs baseline (empty autogenerate diff -- no migration, no test-template rebuild); `version_id_col` resolves to `<table>.version_id` on all 10.** Clusters 70->69 net (version_id dup eliminated; model boilerplate re-paired into remaining `user_id`/`sort_order`/`is_active` groups). No new pylint messages (5 line-too-long on those files are pre-existing Phase 4 items). **Full suite 5766 passed.** | 9.74/10 | 342 |
| 2026-06-04 | `a608d77` | 2 | **P-2 fix + account-dropdown dedupe:** added `account_service.list_active_accounts(user_id)` (Flask-isolated, ordered by `sort_order, name`, `is_active` only); routed all six form sites through it (templates x2 -- now ordered, fixing P-2; transfers x2; savings; settings). Dissolved the account-dropdown R0801 clusters (`savings<->transfers`, `settings<->transfers`); the templates<->transfers CATEGORY-dropdown clusters remain (separate follow-on). Clusters 69->66. No new pylint messages; `create_app` OK (no cycle). **Full suite 5766 passed.** | 9.74/10 | 339 |
| 2026-06-04 | `b58adf1` | 2 | **Batch 3 (category-dropdown dedupe):** new `app/services/category_service.py` with `list_active_categories(user_id)` (Flask-isolated, parallel to `account_service.list_active_accounts`); routed the 5 active-category form sites through it (templates x2, transfers x3). Pure refactor -- all 5 already used the identical `is_active` + `group_name, item_name` query, so no behavior change. The grid/transactions/companion category queries use a different (all-categories) semantic and are intentionally untouched. Clusters 66->62; **score 9.74 -> 9.75** (cumulative cluster removals crossed a rounding boundary). No new messages; `create_app` OK. **Full suite 5766 passed.** | 9.75/10 | 335 |
| 2026-06-04 | `57cf12d` | 2 | **Model batch M1 (`UserScopedMixin`):** extracted the `user_id -> auth.users` CASCADE NOT-NULL FK (byte-identical across 15 tables) into `UserScopedMixin`; applied to Account, Category, PayPeriod, PensionProfile, RecurrenceRule, SalaryProfile, SavingsGoal, Scenario, TransactionEntry, TransactionTemplate, Transfer, TransferTemplate + tax_config x3. Excludes ref.* (RESTRICT/nullable), auth satellites (unique), Transaction (no user_id). **KEY: mixin columns render AFTER own columns, so user_id moves to the table tail -- NOT byte-identical, unlike OptimisticLockMixin. Verified SAFE: order-independent-equivalent for all 44 tables (no migration), because column order is load-bearing nowhere (test suite clones the Alembic template; no positional access; create_all alignment is about constraint NAMES not order).** Clusters 62->56; model<->model 20->14. No new messages. | 9.75/10 | -- |
| 2026-06-04 | `ae815bc` | 2 | **Model batch M2 (`SortOrderMixin`+`IsActiveMixin`):** extracted the two cross-cutting flag columns. SortOrder -> 7 tables, IsActive -> 10 tables. **IsActive EXCLUDES User** (Flask-Login `UserMixin.is_active` property would shadow a mixin Column via MRO; User keeps it inline). Same mid-table reorder, order-independent-verified (no migration). Clusters 56->53; model<->model 14->11 (the 11th is a harmless base-list re-pairing, dissolved by the compact 2-line base-class form). | 9.75/10 | -- |
| 2026-06-04 | `561a369` | 2 | **Model batch M3 (FK mixins + bipartite disables):** `AccountScopedMixin` (account_id CASCADE non-unique: RateHistory, EscrowComponent, LoanAnchorEvent, SavingsGoal, AccountAnchorHistory) + `SalaryProfileScopedMixin` (salary_profile_id CASCADE: SalaryRaise, PaycheckDeduction, CalibrationOverride). **KEY FINDING: the account_id + salary_profile_id FK groups form NON-BIPARTITE cliques (3+ byte-identical blocks); a one-sided `disable=duplicate-code` provably cannot cover a triangle without a both-sides re-fire, so these MUST be mixins.** The 5 genuinely-bipartite coincidental pairs documented with one-sided disables (rule 13): transaction<->transfer scenario+status, transfer<->transfer_template from/to-account, investment_params<->loan_params unique-account_id, transaction<->transaction_template RESTRICT-account_id, account_anchor_history<->loan_anchor_event UTC-day index. **All 20 model<->model clusters now resolved (model 11->0; total 53->42).** All 44 tables order-independent-equivalent (no migration). Disables 62->67 (+5 documented bipartite-pair). **Full suite 5766 passed.** | 9.76/10 | -- |
| 2026-06-04 | `e2dc36a` | 2 | **Route-fork dedup:** templates<->transfers + investment<->loan. New `_recurrence_form_helpers` helpers (`commit_or_handle_stale`, `update_recurrence_rule_from_form`, `resolve_recurrence_rule_for_update` -- the last reverses the F-24 Section-2 inline acceptance per developer direction); new `_transfer_creation_helpers.py` (validate_and_resolve_source_account, build_recurring_transfer_template, flush_template_or_namedup_redirect, generate_transfers_for_all_periods); routed templates/transfers/investment/loan through them. Remaining parallel-route call sequences documented one-sided (rule 13). Clusters 42->23. Targeted 182+326 passed. | 9.78/10 | -- |
| 2026-06-04 | `7b1236d` | 2 | **Service helpers:** new `utils/dates.add_months` (deduped debt_strategy + savings_goal; rate_period_engine's overflow-guardless variant left separate); `utils/money.percent_complete` (deduped dashboard `_safe_pct_complete` + entry_service `pct_complete`, now a thin delegate); `credit_workflow.create_cc_payback_transaction` (deduped the two CC-payback factories); recurrence regenerate-tail one-sided disable (plan note #3: a log helper trips too-many-args + dissolves no cluster). Clusters 23->19. Targeted 79+10+68 passed. | 9.78/10 | -- |
| 2026-06-04 | `86eb309` | 2 | **Access + account/period helpers:** `auth_helpers.get_accessible_transaction` (centralised the owner/companion access check copy-pasted in entries + transactions -- security-critical single definition); `account_service.get_account_type_ids_in_use` + `list_retirement_investment_account_types`; new `utils/period_projections.project_balance_horizons` (deduped the 3/6/12-month horizon loop). Clusters 22->15. Targeted 204+320+62 passed. | 9.78/10 | -- |
| 2026-06-04 | `6475429` | 2 | **Documented 13 incidental clusters:** one-sided scoped why-commented `duplicate-code` disables where extraction would couple unrelated domains or equal the inline form (rule 13): obligations pattern-id preamble, growth_engine dataclass `__post_init__`, calendar/budget-variance divergent queries, balance_resolver dated entry-bucketing (E-25), debt_strategy/year_end LoanParams idiom, retirement error-render, accounts.detail/savings_dashboard, spending_trend, loan_payment, dashboard expense-sum. Clusters 15->2. Comment-only (no behavior change). | 9.79/10 | -- |
| 2026-06-04 | `eb56235` | 2 | **Stale-data commit CLIQUE consolidation (Phase 2 CLOSE):** the cross-route stale handler was a clique across salary/savings/accounts (cliques can't be one-sided-disabled -- both-sides re-fire), so extracted to new `app/routes/_commit_helpers.py` (commit_or_handle_stale + handle_stale_conflict moved out of `_recurrence_form_helpers`; templates/transfers re-pointed; +regenerate_and_commit_or_stale for the flush-must-stay-in-the-try case -- update_account, salary). Routed the plain salary/savings/account handlers through it; the salary update_profile/raise/deduction two-branch (StaleDataError + SQLAlchemyError C-46/F-145) handlers verified to no longer cluster and left inline. **Clusters 2->0. PHASE 2 COMPLETE: 0 R0801 clusters, 0 useless-suppression, 0 E/F. Disables 67->83. Full suite 5766 passed.** | 9.79/10 | -- |
| 2026-06-04 | `4d7d7c1` | 3 | **Phase 3 file 1/N -- salary returns + dead imports:** extracted `_respond_after_raise_change`/`_respond_after_deduction_change` (HTMX-partial-else-redirect dual-return, 8 sites) -- DRY, and drops `add_raise`/`add_deduction` under the return limit as a side effect; documented `disable=too-many-return-statements` on `update_raise`/`update_deduction` (7 guard-clause/audit-error returns each). Removed dead imports `AccountType`/`AcctCategoryEnum`. 135 targeted pass. | 9.85 (file) | -- |
| 2026-06-04 | `e834635` | 3 | **salary calibrate_confirm decomposition:** extracted `_compute_total_pre_tax` (shared with `calibrate_preview` -- a Phase-2-missed dup, vars `bk` vs `preview_breakdown` dodged R0801) + `_reject_if_rates_inconsistent` (the federal/state cross-check). calibrate_confirm tm-locals(21)/statements(51) -> 0; behavior-preserving (E-20/C19-2 tampering checks unchanged). | 9.85 (file) | -- |
| 2026-06-04 | `131d648` | 3 | **salary.py -> `app/routes/salary/` package (module split, ratified decision #5):** `_bp`/`__init__`/`_helpers`/`profiles`/`items`(raises+deductions co-located)/`views`/`calibration`/`tax_config`; none >566 lines; 22 endpoints + URLs preserved (no `url_for`/template/`app/__init__` edit). Split re-surfaced 6 R0801 clusters the monolith hid (R0801 is cross-file only) -- resolved by genuine dedup: stale handlers routed through `_commit_helpers.regenerate_and_commit_or_stale`, raises+deductions co-located in `items.py`. **0 R0801 clusters, 0 new dup disables.** test_c46 patch-path + account_service docstring repointed to `_helpers`. tm-lines 9->8. **Full suite 5766 passed.** | 9.80/10 | 271 |
| 2026-06-04 | `3a9d96f` | 3 | **useless-suppression + dead-import sweep (Phase 3 start):** verifying the plan against the live tree surfaced 13 `useless-suppression` messages (full config + `--enable`; the plan wrongly claimed 0) plus 3 dead imports. Read each site to confirm the smell no longer fires, then removed: auth `login` too-many-return (6 returns = `max-returns` limit), transfer `restore_transfer` too-many-branches (8 <= 12), dashboard `_compute_alerts` too-many-args/pos (5 params = limit) -- **resolving 3 of the 15 Phase-1->Phase-3 smell-disables outright**; the 9 `wrong-import-position` disables in `accounts/`+`salary/` `__init__` (rule never fires -- only the docstring + `_bp` import precede them; corrected the mistaken Phase 1 KEEP rationale that conflated runtime order with what the rule flags); 3 unused imports in `retirement_dashboard_service`. No refactor, no behavior change. Disable lines 90->78; useless-suppression 13->0; R0801 still 0. 665 targeted tests pass. | 9.80/10 | 268 |
| 2026-06-04 | `0e8b986` | 3 | **amortization_engine dead-code removal (developer-approved):** verified `replay_confirmed_history` (+ its `ReplayResult`) had ZERO production callers -- superseded by `rate_period_engine.replay_schedule` (git `8ea2585`); `_build_payment_lookups` was fully dead. Removed all three (~417 lines) + the `TestReplayConfirmedHistory` class; fixed stale cross-references in loan_resolver/account_projection/rate_period_engine/year_end (incl. the `loan_resolver:669` docstring drift). **Resolves 5 of 15 file smells -- module tm-lines (1204->781, NO package split needed) + all four replay smells.** No behavior change. Score 9.80; visible 263; tm-lines modules 8->7; 415 targeted tests pass. | 9.80/10 | 263 |
| 2026-06-04 | `c4f01e6` | 3 | **amortization_engine `project_forward` decomposition (param objects, developer-chosen):** new frozen `ProjectionInputs` bundles the 7 shared projection inputs (9->3 args, kills tm-args; callers build one and reuse it, reinforcing the can't-diverge SSOT); `_apply_override_payment`/`_apply_contractual_payment` pure helpers kill tm-branches/statements; `_ProjectionState` + `_recast_for_rate_change` + `_advance_month` reuse drop tm-locals 33->14 (genuine, no disable); `AmortizationRow` 9-attr DTO -> scoped+commented tm-instance-attributes disable. Updated all call sites (loan_resolver x3, routes/loan refi, 2 internal payoff calls, 18 test calls). Behavior bit-identical (hand-asserted money tests pass). **project_forward 4 smells -> 0; AmortizationRow -> documented.** Score 9.80; visible 258; E/F 0; R0801 0; useless-suppression 0; 415 targeted tests pass. | 9.80/10 | 258 |
| 2026-06-05 | `7cc8fe1` | 3 | **amortization_engine `calculate_payoff_by_date` decomposition (param object + binary-search extraction, developer-chosen Shape A) -- file DONE:** new frozen `PayoffRequest` bundles the 10 inputs incl. `target_date` (tm-args/pos 10->1); `_search_extra_for_payoff` helper extracts the binary search (tm-locals 25->12, tm-return 7->6, tm-branches 14->9); `PayoffRequest` 10-attr Parameter Object -> scoped+commented tm-instance-attributes disable (verified needed, 0 useless-suppression). Traced impact: 1 production caller (routes/loan.py), 9 test call sites, the C2-11 structural slice marker (now bounded by the inserted `PayoffRequest`; assertions unchanged). **C15-3 demoted-column lock interaction (Option A):** the param object introduced `request.current_principal` reads that the lock's coarse `.current_principal` grep flagged; allow-listed `amortization_engine.py` (no DB access -- can't read `LoanParams`; value is the resolver-derived `state.current_balance`) + recorded F-28 in `remediation_follow_up.md` + fixed F-27's stale signature pseudocode. **All 5 `calculate_payoff_by_date` smells -> 0; file 10.00/10, zero smell messages.** Score 9.80->9.81; visible 258->253; E/F 0; R0801 0; useless-suppression 0; disables 79->80. 253 targeted + 4 C15-3 lock pass; **full suite 5755 passed.** | 9.81/10 | 253 |
| 2026-06-05 | `d05758b` | 3 | **savings_dashboard_service `Phase 1 of 2` -- decompose 7 functions (developer-chosen phasing):** all 13 function-level smells resolved by pure cohesive-helper extraction + context/result objects (no logic change). God-function `_compute_account_projections` (37 locals/63 stmts/18 branches/6 args) -> `_project_one_account` + `_compute_base_balances` + `_compute_loan_account` (+ `_loan_projected_horizons`, `_loan_ever_paid_off`) + `_compute_needs_setup`; frozen `_ProjectionContext` clears tm-args. `_project_investment` -> `_ProjectionContext` + `_investment_horizons`. `compute_dashboard_data` -> `_load_dashboard_core_data`(+`_DashboardCoreData`) + `_apply_dti_metrics` (gross_biweekly read kept inline for AST guard 1a) + `_sum_liquid_balances`. goals/expenses/debt/params via cohesive extraction. 3 new frozen dataclasses all <7 attrs. Public `compute_dashboard_data` signature unchanged (60+ call sites + AST guards unaffected). **Behavior bit-identical: full suite 5755 passed incl. cross-page balance-equality + loan-resolver-single-source integration tests.** 13 function smells -> 0; only module tm-lines remains (1379/1000, grew from 1035) -> Phase 2 package split. Score 9.81->9.82; visible 253->240; R0801 0; E/F 0; useless-suppression 0. | 9.82/10 | 240 |
| 2026-06-05 | `0ec5586` | 3 | **savings_dashboard_service `Phase 2 of 2` -- module -> package split (developer-chosen 8-module split) -- file DONE:** the 1379-line module (over the 1000 ceiling after Phase 1) split into `app/services/savings_dashboard_service/` (`__init__` re-exports public `compute_dashboard_data`; `_types`/`_data`/`_projections`/`_goals`/`_metrics`/`_display`/`_orchestrator`). Directory named to preserve `from app.services import savings_dashboard_service`. Dropped unused module `logger`. **0 new R0801** (split trap avoided -- the one-sided dup-code disable on `_get_current_paycheck_breakdown` moved to `_metrics.py`, still effective). Test patch-path updates (decision #5, no assertion change): AST guard 1b -> parse `_orchestrator`; AST guard 2 -> glob all sub-modules; `_get_dti_label` import -> `._metrics`; `_load_account_params` call -> `._data`; **C15-3 demoted-column lock allow-list entry became the `services/savings_dashboard_service/` package prefix** (the 2 `.current_principal` hits are prose, not reads); income_service docstring repointed. Each sub-module pylint 10/10. **Behavior bit-identical: full suite 5755 passed.** module tm-lines -> 0; **savings_dashboard_service DONE (both phases).** Score 9.82; visible 240->239; R0801 0; E/F 0; useless-suppression 0. | 9.82/10 | 239 |
| 2026-06-05 | `5eeb020` | 3 | **year_end_summary_service `Phase 1 of 2` -- decompose 6 functions (developer-chosen two-phase):** all 11 function-level smells resolved by genuine decomposition (no logic change). Two frozen bundle dataclasses (developer chose small bundles, both <=7 fields, NO new disable): `_ProjectionInputs` (the 5 pre-loaded parameter maps) + `_YearContext` (year/scenario/all_periods/year_period_ids), threaded through the net-worth + savings chains in place of the 4-5 parallel keyword maps each forwarded by hand. `_compute_net_worth` 8->3 args; `_build_account_data` 7->4; `_get_account_balance_map` 7->4 (`inputs=None` for base-balance callers); `_compute_savings_progress` 10 args/6 pos/20 locals -> 3 args via `_savings_progress_for_account`; `_project_investment_for_year` 9 args/7 pos/30 locals -> 4 args via `_derive_investment_jan1` + `_summarize_investment_projection`; `_build_investment_balance_map` 6 args/28 locals -> 5 args via `_forward_project_periods`/`_reverse_project_periods`/`_merge_balance_sources`. New shared `_load_shadow_contributions` dedupes the two near-identical inline shadow-income queries (developer-approved); the incidental 6-line joinedload+filter overlap it surfaced with `budget_variance_service._query_by_period` (semantically unrelated) -> one-sided rule-13 `duplicate-code` disable (developer-approved). All 6 smell fns private + internally-called (no exposed signature). pylint counts args as locals (R0914), so the bundle reduction also cleared the locals smells. 11 function smells -> 0; only module tm-lines remains (2437/1000) -> Phase 2. Score 9.82->9.83; visible 239->228; R0801 0; E/F 0; useless-suppression 0; disables +1. Targeted (year-end 73, integration 31, analytics+csv+savings 199) + **full suite 5755 passed.** | 9.83/10 | 228 |
| 2026-06-05 | `b96b8b8` | 3 | **year_end_summary_service `Phase 2 of 2` -- module -> package split (developer-chosen 10-module split) -- file DONE:** the 2437-line module split into `app/services/year_end_summary_service/` (10 per-concern sub-modules: `_types`/`_data`/`_periods`/`_balances`/`_income_tax`/`_spending`/`_transfers`/`_net_worth`/`_savings`/`_orchestrator`; each well under 1000 lines, `_balances` largest at ~700). Partition verified **import-cycle-free by AST cycle-detection** before writing; `__init__` re-exports only `compute_year_end_summary`. **Split trap (decision #5):** re-surfaced ONE intra-file R0801 the monolith hid (the `LoanParams`->`original_principal` idiom shared by `_get_account_balance_map` + `_compute_debt_progress`) -- resolved by genuine dedup into the shared `_loan_original_principal` helper (DRY win; `_net_worth` then dropped its now-unused `db`/`LoanParams` imports). Dropped the unused module `logger`/`logging`. Test patch-path updates (decision #5, no assertion change): `_compute_entry_breakdowns` -> `._spending`; `test_loan_unified_figures` uses `._balances._generate_debt_schedules` / `._income_tax._compute_mortgage_interest` and its bare-quantize sweep now runs `grep -r --include=*.py` over the package dir (coverage preserved); `test_income_service` uses `._data._load_salary_gross_biweekly`. The whole-app `rglob` structural guards (balance_predicates, calculate_balances sweep) pick up the sub-modules automatically. Package pylint 10.00/10 (0 messages); **behavior bit-identical: full suite 5755 passed.** module tm-lines -> 0; **year_end_summary_service DONE (both phases).** Score 9.83; visible 228->227; tm-lines 6->5; R0801 0; E/F 0; useless-suppression 0. | 9.83/10 | 227 |
| 2026-06-05 | `41cab0e` | 3 | **transactions.py `Phase 1 of 2` -- decompose 4 route handlers + dedup owned-FK checks (developer-chosen two-phase + developer-approved FK-dedup widening):** all four flagged handler smells resolved by honest extraction (no disables, behavior bit-identical). `update_transaction` (tm-return 13/6, tm-branches 24/12, tm-statements 64/50) -> `_apply_shadow_update` (transfer-shadow path, verbatim) + `_resolve_status_change` (state-machine verify + Credit-block + paid_at-revert decision; control-flow inverted to a guard clause, result-identical) + `_apply_regular_update`. `mark_done` (tm-return 11/6, tm-branches 16/12, tm-statements 51/50) -> frozen `_RenderTarget` bundle (render_mode/card_prefix/can_edit; keeps the two helpers <=5 args, dodging too-many-arguments) + `_mark_done_shadow` + `_mark_done_regular`. `cancel_transaction` (tm-return 7/6) -> `_cancel_shadow`. `create_inline` (tm-return 7/6) -> shared `_resolve_owned_fks(specs)` IDOR primitive (returns a `{model: row}` dict or `(None, (msg, 404))`; identical 404 for "not found" and "not yours"; a `None` id short-circuits without a NULL-PK query -- verified `db.session.get(Model, None)` returns None, so HTTP-behavior-identical to the prior per-route `if account_id else None` guards), which ALSO dedupes `create_transaction` + `get_quick_create`/`get_full_create`/`get_empty_cell` + `_verify_owned_fks_in_update` (one owned-FK-by-id check across all six create/form sites). Shadow paths still route through `transfer_service` (TRANSFER INVARIANTS untouched); every 404/400/409/200 body, HX-Trigger, status flip, and log line preserved. All four handler smells -> 0; only module tm-lines (1532/1000, grew from decomposition) remains -> Phase 2 split. Score 9.83->9.84; visible 227->219 (-8); R0801 0; E/F 0; useless-suppression 0; 0 new disables (81); the 4 `line-too-long` are pre-existing Phase 4 residue. Targeted 323 + **full suite 5755 passed.** | 9.84/10 | 219 |
| 2026-06-05 | `27e99f2` | 3 | **transactions.py `Phase 2 of 2` -- module -> package split (developer-chosen 6-module merge) -- file DONE:** the 1532-line module (over the 1000 ceiling after Phase 1) split into `app/routes/transactions/` (`_bp` leaf + `_helpers` schema-singletons/render/ownership/FK helpers + `forms` GET partials + `create` + `mutations` + `carry_forward`; `__init__` re-exports `transactions_bp` + imports submodules for registration). All 16 endpoint names, URLs, methods, and the `from app.routes.transactions import transactions_bp` path preserved verbatim (no `url_for`/template/`app/__init__` edit). Code relocated verbatim (AST-sliced, decorators included) -- no logic change. **Split trap (decision #5):** surfaced TWO intra-file R0801 dups the monolith hid. (1) The transfer-shadow + mark_done helpers (`_apply_shadow_update`/`_mark_done_shadow`/`_mark_done_regular`/`_cancel_shadow`) form an INSEPARABLE clique -- `_mark_done_shadow` shares the `update_transfer`+commit+stale preamble with `_apply_shadow_update` AND the `_RenderTarget` stale+IntegrityError response with `_mark_done_regular`, so no edit/status split avoids a cross-file pair. Resolved by MERGING edit+status into one `mutations.py` (module-level co-location of intentional parallel code, decision #5). (2) An incidental 6-line `commit / NotFound->404 / Validation->rollback->400` idiom (`carry_forward` <-> `mutations.unmark_credit`; the idiom recurs across 5 route files, the two sites differ in StaleData handling / return value / success body, and `_commit_helpers` is redirect-only so it can't host the HTMX `(body,status)` form) -> documented one-sided rule-13 `duplicate-code` disable on the `carry_forward` side. **This is a deliberate, developer-approved exception to decision #5's "never disable split-trap" -- which assumed every such cluster is dedupable or co-locatable-as-intentional-parallel; this one is genuinely incidental boilerplate that is neither.** Test patch-path update (decision #5, no assertion change): `test_c19` `patch("app.routes.transactions.credit_workflow.mark_as_credit")` -> `...transactions.mutations.credit_workflow...`. Each sub-module pylint-clean, all <1000 lines (largest `mutations.py` 759); **behavior bit-identical: full suite 5755 passed.** module tm-lines -> 0; **transactions.py DONE (both phases).** Score 9.84; visible 219->218; tm-lines 5->4; R0801 0; E/F 0; useless-suppression 0; disables 81->82 (+1 documented). | 9.84/10 | 218 |
| 2026-06-05 | `21f2a31` | 3 | **transfers.py `Phase 1 of 2` -- decompose 4 route handlers (honest extraction, no disables, behavior bit-identical):** all 7 function-level smells resolved. `create_transfer_template` (17 locals/7 returns) -> `_materialize_initial_transfers`. `update_transfer_template` (17 locals/8 returns/16 branches) -> `_first_unowned_template_fk` + `_regenerate_and_commit_template`. `update_transfer` (11 returns) -> single-return FK loop + `_execute_transfer_update` + the shared `_render_post_mutation_cell`. `create_ad_hoc` (9 returns) -> `_handle_adhoc_integrity` + merged the two equivalent try blocks (`uq_transfers_adhoc_dedupe` fires at flush OR commit -> same handler; NotFound/Validation only from the service call, so the merge changes no behavior). `_render_post_mutation_cell` also deduped the shadow-cell block in `mark_done`/`cancel_transfer` (DRY + Phase-2 split-trap pre-empt). TRANSFER INVARIANTS untouched. Score 9.84; visible 218->211 (-7); R0801 0; E/F 0; useless-suppression 0; 0 new disables. Targeted 101 + **full suite 5755 passed.** | 9.84/10 | 211 |
| 2026-06-05 | `c4e9015` | 3 | **transfers.py `Phase 2 of 2` -- module -> `app/routes/transfers/` package (developer-chosen 6-module split) -- file DONE:** the 1457-line module (over the 1000 ceiling after Phase 1) split into `_bp`/`_helpers` (4 schema singletons + 5 shared ownership/render helpers)/`templates` (8 template-CRUD routes + 3 helpers)/`forms` (3 grid-cell GET partials)/`mutations` (5 instance routes + 3 helpers; AST-sliced verbatim). All 16 endpoints + URLs + the `from app.routes.transfers import transfers_bp` path preserved (no `url_for`/template/`app/__init__` edit). **Split trap (decision #5): 0 new R0801** -- co-locating update_transfer + mark_done + cancel_transfer in `mutations.py` kept their parallel code intra-file, and Phase 1's `_render_post_mutation_cell` pre-deduped the shadow block. Bonus: fresh wrapped imports cleared 2 pre-existing `line-too-long`. Test patch-path + 2 docstring `:func:` cross-refs repointed to `.templates` (decision #5, no assertion change). Each sub-module pylint 10.00/10. **Behavior bit-identical: full suite 5755 passed.** module tm-lines -> 0; **transfers.py DONE.** Score 9.84->9.85; visible 211->208; tm-lines 4->3; smell items 108->107; R0801 0; E/F 0; useless-suppression 0. | 9.85/10 | 208 |
| 2026-06-05 | `e8b910b` | 3 | **loan.py `Phase 1 of 2` -- decompose 5 function smells + remove dead code (honest cohesive-helper extraction, no disables, behavior bit-identical):** all five function-level smells -> 0. `dashboard` (46/15 locals, 57/50 stmts) -> `_build_dashboard_scenarios` + `_build_planned_summary` + `_build_payment_summary` + `_build_dashboard_chart_context` + `_resolve_transfer_prompt` + `_build_schedule_tab`; the route assembles its render context by merging the per-section dicts (`**` unpack), 12 locals. `payoff_calculate` (35) -> `_payoff_extra_payment_result` / `_payoff_target_date_result` (one per mode) + `_payoff_committed_savings` + `_build_payoff_summary`. `refinance_calculate` (30) -> `_project_refinance` + `_refinance_break_even` + `_build_refinance_comparison`. `_compute_payment_breakdown` (18) -> `_distribute_payment_percentages` + `_project_next_year_escrow`. `create_payment_transfer` (16) -> `_resolve_transfer_amount`. **DRY win:** dashboard's + payoff's three-series chart building deduped into the shared `_build_chart_series` (split-trap pre-empt, 0 new R0801). **Dead-code removal (developer-approved, precedent `0e8b986`):** `_build_chart_data` had zero callers anywhere (pylint cannot flag module-level dead fns). loan.py 9.98/10 (only module tm-lines remains -> Phase 2); visible 208->202; R0801 0; useless-suppression 0; E/F 0. 223 targeted loan tests pass. | 9.85/10 | 202 |
| 2026-06-05 | `f07fb1c` | 3 | **loan.py `Phase 2 of 2` -- module -> `app/routes/loan/` package (developer-chosen 5-concern split) -- file DONE:** the 1847-line module (over the 1000 ceiling after Phase 1) split into `_bp`/`_helpers` (8 schema singletons + the `_load_loan_account`/`_require_configured_loan`/anchor/resolver-state/full-context loaders + chart utils + 2 domain constants)/`dashboard` (route + 12 helpers)/`params` (create_params, update_params, true_up_balance)/`escrow_rates` (add_rate_change, add_escrow, delete_escrow -- HTMX, shared OOB tail co-located)/`calculators` (payoff_calculate, refinance_calculate)/`payment_transfer` (create_payment_transfer); each sub-module <=621 lines, sliced verbatim by def boundary. All 10 endpoints + URLs + the `from app.routes.loan import loan_bp` path preserved (no `url_for`/template/`app/__init__` edit). **Split trap (decision #5):** the "load configured loan, else 404/redirect" guard shared by update_params + true_up_balance + create_payment_transfer re-surfaced as a cross-file R0801 once split -- resolved by genuine dedup into `_require_configured_loan` (a real reusable route-guard, NOT incidental), which fully encapsulates both rejection paths via `abort(404)`/`abort(redirect(...))` (verified werkzeug raises a 302 from a Response) so call sites are a single line, NO residual dup; 0 documented dup disables. Test path updates (decision #5, no assertion change): the 4 static-source guards in `test_loan.py` repoint to the package / moved helpers; C15-3 allow-list `"routes/loan.py:"` -> `"routes/loan/"`; C17-6 sweep `"routes/loan.py"` -> `"routes/loan"`; `_transfer_creation_helpers` `:func:` cross-refs -> `.payment_transfer`. Each sub-module + package pylint 10.00/10. **Behavior bit-identical: full suite 5755 passed.** module tm-lines -> 0; **loan.py DONE.** Score 9.85; visible 202->201; tm-lines 3->2; smell items 107->100; R0801 0; E/F 0; useless-suppression 0. | 9.85/10 | 201 |
| 2026-06-05 | `a1d076e` | 3 | **debt_strategy_service.py -- decompose 3 functions (param object + working-state bundle, developer-chosen `StrategyRequest`) -- file DONE:** all 7 function smells resolved by genuine decomposition (no logic change, no disables, behavior bit-identical). `calculate_strategy` tm-args/pos (6/5) -> frozen `StrategyRequest` param object (6 fields; developer chose this over keyword-only + a documented disable, following the `PayoffRequest` precedent), so the public entry point takes ONE arg; all 40 callers (4 route + 36 test) wrapped in `StrategyRequest(...)`. The five parallel per-debt working arrays (a data clump threaded by hand) -> frozen `_SimulationState` (6 fields) + `initialize()` factory, mirroring `amortization_engine._ProjectionState`; `_accrue_interest` / `_apply_minimum_payments` / `_cascade_extra_payments` / `_build_result` now take `state` (`_cascade_extra_payments` 6->3 args, `_build_result` 9->5 args). New `_simulate_month` extracts the per-month loop body so `calculate_strategy` tm-locals 23->12 (verified REQUIRED: without the extraction the loop body holds the function at 16/15). Empirically confirmed on a throwaway probe that keyword-only clears tm-positional but NOT tm-arguments -- which framed the param-object decision. The route's `calculate` handler keeps its pre-existing tm-locals/return/branches (separate Tier-3 item, untouched; wrapping the calls added no locals/branches/returns -- route score held 9.78 +0.00). file 10.00/10, zero smell messages. 0 new R0801; disables unchanged at 82; E/F 0; useless-suppression 0. Targeted 66 + **full suite 5755 passed.** | 9.86/10 | 194 |
