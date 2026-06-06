# Quality Pass -- second-pass design review of pylint-cleanup-touched code

**Status: COMPLETE for design quality. All 14 files reviewed; every design-quality fix-batch
(B1-B7) is resolved (full suite 5766 passed). The only outstanding item is B8 -- 8 cosmetic
`line-too-long` in `salary/` -- which belongs to the Phase-4 mechanical lint-floor sweep in `plan.md`,
not this pass. The project-wide bundle fork is RULED: honesty-first -- a private helper's
too-many-args/locals smell is a signal to DECOMPOSE, not to wrap; bundle only for a genuine cohesive
named concept (see [[feedback_tm_args_param_object]] / the rubric below). `_PaycheckContext`
(forwarded superset) was collapsed; `_DeductionContext` (leaf reads all fields) kept; the new
`_WageBasis` is the cohesive-concept exception. Net verdict across the sweep: 0 REVERT-OVERREACH --
the bundles were used cohesively; `_PaycheckContext` was the lone outlier.** This is a distinct effort
from the pylint 10/10 cleanup (`plan.md`). The cleanup is a *floor* -- it removes mechanical smells
(too-many-args, duplicate-code, too-many-locals). This pass chases the *ceiling*: is the code now the
most pythonic, DRY, SOLID, robust, maintainable, and future-proof it can be?

## Why this pass exists

Pylint cannot see design quality. Worse: the dominant move the Phase 3 refactors made -- introduce a
context/param object + extract helpers to dissolve a smell -- is itself an unreviewed judgment call,
and it sits on a knife's edge:

- A cohesive value object that names a real domain concept -> **excellent** (the intended outcome).
- A bag of unrelated fields assembled only to get the argument/local count under threshold ->
  **stamp coupling / gold-plating** -- a *worse* design that happens to score 10/10 (CLAUDE.md rule 13).

Every such call was made by the author, in-flight, anchored on the refactor just committed. None has
had a second set of eyes. Re-verifying those calls with fresh eyes is the highest-value work here.

## Ground rule (inherited from `plan.md`)

**If you cannot cite it, you cannot claim it.** Every finding cites `file:line`. Every verdict is
recorded in the register. Behavior must stay correct: a file is not "best" if its tests are too weak
to catch a regression the next refactor could introduce -- test quality is part of the bar.

## How to run the pass on one file

1. Gather three inputs: the file, its test(s), and the **cleanup diff** (`git show <sha>` for the
   commit(s) that refactored it -- so the reviewer can judge what changed and why).
2. Hand an **independent** reviewer (a fresh subagent -- you are anchored) those three inputs plus the
   rubric below. Require it to argue *both* directions: "could this be simpler?" and "is this the
   right abstraction for the next feature?" Over-engineering findings are first-class, not an
   afterthought.
3. Confirm test quality (section G) -- weak or brittle tests are findings in their own right.
4. Triage every finding to a verdict and record it: **ACCEPT** (it is right), **REFINE** (improve
   further), or **REVERT-OVERREACH** (the cleanup over-engineered it; collapse the abstraction back).
5. Apply REFINE / REVERT-OVERREACH fixes as their own commit (`refactor(<scope>): ...`), targeted
   tests per change, full suite as the gate. ACCEPT rows need no code change -- they are the audit
   trail that the design was actually examined.

## The rubric

Each item is a yes/no question; a "no" is a finding. Sections A and G carry the most weight for this
codebase (A = the cleanup's dominant risk; G = the safety net that lets refactoring continue).

### A. Right abstraction (highest-risk dimension for this cleanup)

- A1. Does every newly-introduced bundle (context/param object, NamedTuple, dataclass) name a concept
  that exists in the domain -- or is it a bag assembled only to lower an arg/local count?
- A2. Are a bundle's fields used *together* by ~all of its consumers? If each consumer reads a
  different subset, that is stamp coupling; the fields should fan out as plain arguments.
- A3. Counterfactual: would this abstraction exist if pylint thresholds did not? If no, it is ceremony.
- A4. Is each extracted helper either called from >=2 sites or genuinely clarifying one site? A
  single-use helper that only relocates code adds indirection without paying for it.
- A5. Is the abstraction at the right altitude -- not leaking implementation, not mixing raw inputs
  with derived values in one "context"?

### B. DRY (real, not superficial)

- B1. Is duplicated *logic* (not merely duplicated text) extracted to one home?
- B2. Conversely: is anything DRYed that should not be -- two sites that look alike today but change
  for different reasons (false DRY couples them)?

### C. SOLID / cohesion

- C1. One reason to change per unit (single responsibility)?
- C2. Did decomposition improve cohesion -- or scatter one cohesive operation across many helpers you
  must read in sequence to understand the whole?
- C3. Do dependencies point the right way (services never import Flask; no layer violations)?

### D. Pythonic

- D1. Idiomatic constructs over manual/clever code?
- D2. Names read at the call site; types precise (no bare `list`/`dict`/`object`/`Any` where a real
  type fits)?
- D3. Guard clauses over deep nesting (max depth 3); no truthiness on business values
  (`is None`, not `not x`, for money/IDs)?

### E. Robust (financial correctness)

- E1. Edge cases handled: empty / None / zero / negative / boundary (period boundaries, cap
  crossings, zero gross, single-period inputs)?
- E2. Decimal discipline: never float in money math; constructed from strings; rounding mode explicit
  and consistent across paths that must agree?
- E3. No impossible-scenario handling that is really gold-plating (rule 13) -- and, conversely, no
  defensive branch that silently hides a real bug?
- E4. Determinism: order-independent where inputs may arrive unsorted?

### F. Maintainable / future-proof

- F1. Would a plausible next feature slot in, or fight the abstraction?
- F2. Docstrings explain *why*; comments are not redundant restatements; no stale comments or counts?
- F3. Public surface minimal and stable; private helpers genuinely private (single module, leading
  underscore)?

### G. Test quality (safety to keep changing)

- G1. Do tests pin behavior at the right granularity -- concrete values asserted, not just "it runs"?
- G2. Are any tests over-coupled to internals the refactor reshaped (brittle to the next refactor)?
- G3. Are the section-E edge cases actually covered by a test?
- G4. Any assertion that is hand-waved, tautological, or computed by the code under test rather than
  by hand?

## Worklist (files touched by the cleanup, financial-core first)

Order = correctness blast-radius x refactor aggressiveness. Financial core first, then routes.

| # | File / package | Cleanup commit(s) | Review status |
|---|---|---|---|
| 1 | `services/paycheck_calculator.py` | `15bcfd1` | **DONE** -- reviewed + refined (honesty-first bundle ruling applied) |
| 2 | `services/amortization_engine.py` | `0e8b986` `c4f01e6` `7cc8fe1` | REVIEWED -- **clean** (7 ACCEPT; the bundle exemplar) |
| 3 | `services/debt_strategy_service.py` | `a1d076e` | REVIEWED -- **clean** (6 ACCEPT, 1 LOW) |
| 4 | `services/retirement_dashboard_service.py` | `ce65229` | REVIEWED (M2: type-precision, test-gap) |
| 5 | `services/investment_dashboard_service.py` | `e3dbea7` | REVIEWED (M4: type-precision, bundle-caveat, test-gap) |
| 6 | `services/savings_dashboard_service/` (pkg) | `d05758b` `0ec5586` | REVIEWED (M2: untyped `params` bag, built-then-patched) |
| 7 | `services/year_end_summary_service/` (pkg) | `5eeb020` `b96b8b8` | REVIEWED (M2: `_ProjectionInputs` superset, None-as-mode) |
| 8 | `routes/loan/` (pkg) | `e8b910b` `f07fb1c` | REVIEWED (M4: tuple-narrow, dup resolve, untyped dict, dup render) |
| 9 | `routes/transfers/` (pkg) | `21f2a31` `c4e9015` | REVIEWED -- **clean** (3 ACCEPT, 1 LOW) |
| 10 | `routes/transactions/` (pkg) | `41cab0e` `27e99f2` | REVIEWED (M2: `_RenderTarget` unpacked, FK-keyed-by-class) |
| 11 | `routes/salary/` (pkg) | `4d7d7c1` `e834635` `131d648` | REVIEWED (M2: stale `_bp` docstring, 8 line-too-long) + found out-of-scope 500 |
| 12 | `routes/grid.py` | `86541bb` | REVIEWED (M1: dead `txn_by_period` field canonized) |
| 13 | `routes/_recurrence_form_helpers.py` + `_commit_helpers.py` | `8e01099` | REVIEWED (M2: docstring honesty, test-gap) |
| 14 | `routes/_transfer_creation_helpers.py` | `59ba11a` | REVIEWED (M1: inactive-source test-gap) |
| 15 | `routes/templates.py` + `recurrence_engine.match_periods` | `1c26575` | REVIEWED -- **clean** (all ACCEPT, 0 REFINE/REVERT-OVERREACH; first going-forward Phase-3 file under the folded-in rubric) |
| 16 | `services/growth_engine.py` | `dcf0d4e` | REVIEWED -- **ACCEPT** (behavior-equivalence verified line-for-line; both documented disables upheld, 0 REVERT-OVERREACH; 2 LOW REFINE applied -- type-precision + degenerate-period test) |
| 17 | `services/loan_resolver/` (pkg) | `41f42a8` | REVIEWED -- **ACCEPT** (all 6 behavior-equivalence points verified line-for-line; `LoanInputs` bundle + `_replay_from_anchor` sharing + `_ProjectionPrep` + `PayoffScenarios` disable all upheld; 0 REVERT-OVERREACH, 0 REFINE; F8 the lone LOW note, backstopped, no change) |

## Fold into Phase 3 going forward

For every Phase 3 file still to be refactored, this rubric review becomes part of its definition of
done: the mechanical smell-clearing commit, then an independent rubric review, then any
REFINE/REVERT-OVERREACH follow-up -- before the file is marked DONE in `plan.md`. This stops the pass
from accruing new debt while the retroactive sweep clears the backlog above.

**First going-forward file: `routes/templates.py` (`1c26575`, 2026-06-06).** The mechanical commit
cleared all 4 design smells + the `preview_recurrence` protected-access; the independent reviewer
(fresh subagent, A-G rubric) verified all 7 behavior-equivalence points against the code and returned
ALL ACCEPT -- no REFINE/REVERT-OVERREACH design change was needed. The single finding (a stale
`tests/TEST_PLAN.md` reference to the renamed `_match_periods`) was a rule-7 completeness fix folded
into the same commit, not a design refinement, so no separate follow-up commit was required. See the
register rows below.

**Second going-forward file: `services/growth_engine.py` (`dcf0d4e`, 2026-06-06).** The mechanical
commit cleared all 4 smells -- `project_balance` tm-locals by the `_PeriodInputs` / `_ProjectionState`
/ `_project_one_period` decomposition (mirroring `amortization_engine`), and the `project_balance`
tm-args/pos + `ProjectedBalance` tm-instance-attrs by developer-chosen documented disables -- and
surfaced a genuine 2-site DRY win (`_period_return_rate`, shared by the forward and reverse
projections). The independent reviewer (fresh subagent, A-G rubric) verified all 6
behavior-equivalence points line-for-line against HEAD and returned ACCEPT overall with 0
REVERT-OVERREACH; both documented disables survived the "bundle the 8 args / split the row"
over-engineering challenge. Unlike templates.py, this one carried two LOW REFINE findings -- a bare
`contribution_lookup: dict` annotation and the untested `period_days <= 0 -> 14` degenerate-period
fallback (now shared by both directions) -- both verified against the code, then folded into the same
commit. See the register rows below.

**Third going-forward file: `services/loan_resolver/` (`41f42a8`, 2026-06-06).** Two-phase in one
commit (developer-chosen): the `LoanInputs(loan_params, anchor_events, payments, rate_changes)` bundle
+ `_replay_from_anchor` (shared) + `_build_forward_inputs`->`_ProjectionPrep` setup extraction cleared
`resolve_loan` tm-locals and `compute_payoff_scenarios` tm-args/locals; `PayoffScenarios`(10/7) took a
documented disable; the resulting 1009-line module was split into the `app/services/loan_resolver/`
package (decision #5). The independent reviewer (fresh subagent, A-G rubric) verified all 6
behavior-equivalence points line-for-line and returned **ACCEPT overall, 0 REVERT-OVERREACH, 0
REFINE**. It specifically tested the highest-risk calls: `LoanInputs` is a genuine cohesive concept
(every consumer reads all/most 4 fields, the clump every caller co-loads -- A1/A2 pass, not stamp
coupling); `_replay_from_anchor` sharing is sound because it does ONLY replay, never `project_forward`,
so the resolver's "balance derived independently of projection" invariant holds structurally; and the
`PayoffScenarios` disable survives the "restructure into nested sections" challenge (10 flat columns
read by one consumer; `PayoffRequest`/`AmortizationRow` precedent). The lone LOW note (F8) is a
non-issue backstopped by the package-wide purity guard -- no code change. See the register rows below.

## Register (findings + verdicts)

One row per finding. Verdict is ACCEPT / REFINE / REVERT-OVERREACH. Cite `file:line`.

| File | Rubric | Finding (file:line) | Verdict | Resolution / commit |
|---|---|---|---|---|
| `paycheck_calculator.py` | A1/A2/A3 | `_PaycheckContext` (170-178) overlapped `_DeductionContext` on 4/5 fields; `_compute_tax_lines` read all 5 but forwarded to `_bracket_federal` which read only 3 -- a transient arg-folder for the locals threshold, not a domain concept | **REVERT-OVERREACH (done)** | Bundle fork ruled honesty-first. Collapsed `_PaycheckContext`; split `_compute_tax_lines` into `_calibrated_tax_lines`/`_bracket_tax_lines`; `cumulative_wages` computed in the orchestrator; introduced the cohesive `_WageBasis` (gross/taxable/cumulative) so both halves stay <=5 args honestly. `_DeductionContext` kept (leaf reads all 5) |
| `paycheck_calculator.py` | A4/D2 | `_bracket_federal` (370) took opaque `ctx` then unpacked it; sibling `_bracket_state` (400) took plain args -- asymmetric, ctx not needed | **REFINE (done)** | `_bracket_federal` now takes plain args (`profile, gross_biweekly, pay_periods_per_year, bracket_set, annual_pre_tax`), matching `_bracket_state` |
| `paycheck_calculator.py` | D2 | `DeductionBreakdown.pre_tax/post_tax: list` (99-100) -- coding standard requires specific collection types | **REFINE (done)** | Now `list[DeductionLine]` |
| `paycheck_calculator.py` | E3/G3 | `group.index(period)` `except ValueError` (503-509) silently returned `floor_value` in a money-reconciliation path; effectively dead for real callers and untested | **REFINE (done)** | Removed per rule 13 (no handling for impossible scenarios). `period` is guaranteed in `group` by construction (its own year + `annual_salary` derived from it); a genuine invariant violation now fails loud rather than silently under-paying a cent |
| `paycheck_calculator.py` | A1/A2/F9 | Output sections `PeriodInfo`/`Earnings`/`TaxLines`/`DeductionBreakdown` (82-157) name real concepts, read as cohesive groups by every consumer, totals on the owning section | ACCEPT | Strongest part of the refactor -- this is the model the input contexts should have aspired to |
| `paycheck_calculator.py` | A4/C1 | `_residue_cents` (516) + `_compute_deductions` (285) are single-site but genuinely clarify (isolate audited arithmetic / co-locate pre+post pairing) | ACCEPT | -- |
| `paycheck_calculator.py` | E2/G1/G3/G4 | Net-pay sum is Decimal-exact-equivalent; high-risk edges (full-year reconciliation, FICA cap crossing, calibration/bracket agreement, partial-context fallback, determinism) pinned with hand-computed values, public-API tests dominate | ACCEPT | Behavior preserved; suite is strong enough to keep refactoring safely |
| `templates.py` (`1c26575`) | A1/A4/B1 | `_validate_template_form` shared by `create_template` + `update_template` (2 sites); the old create validated FKs unconditionally, the old update only `if "X" in data` -- the unified `in data` helper preserves both because `TemplateCreateSchema` makes `account_id`/`category_id` required (always in `data`) | ACCEPT | Genuine 2-site DRY; behavior verified against the schema, not assumed |
| `templates.py` (`1c26575`) | A4/C2/F2 | `_apply_fields_and_propagate_rename` and `_build_preview_rule` are single-site but cohesive: the former carries the rename-desync rationale + isolates the bulk name UPDATE; the latter isolates the `request.args` parsing so the route reads `rule.interval_n`/`rule.start_period_id` | ACCEPT | Genuinely clarifying, not pure relocation |
| `templates.py` (`1c26575`) | A4 | `_render_preview_html` is single-use, ~7-line presentation relocation | ACCEPT (lean REFINE) | Reviewer: harmless presentation-isolation that aids the route read; not worth churn -- kept |
| `recurrence_engine.py` (`1c26575`) | F3/D1/E4 | `_match_periods` -> public `match_periods`; tm-return (8/6) cleared via a single-return accumulator rather than a dispatch dict | ACCEPT | The fn is pure, tested by 27 direct units, and called cross-module -- the underscore mislabeled de-facto public API. Accumulator is simpler here: heterogeneous branch locals (`n`/`offset`, month+day) would force lambda shims in a dict. All 8 cases (incl. unknown-default + the `or 1` divide guard) map 1:1; `EVERY_PERIOD` aliases `candidates` exactly as before, and no caller mutates the result |
| `templates.py` (`1c26575`) | F2/Rule-7 | Stale `tests/TEST_PLAN.md:190` reference to `_match_periods()` after the rename | REFINE (folded into `1c26575`) | Updated to `match_periods()`. The dozen dated audit/investigation/plan docs (`docs/audits/*`, `bug_investigation_02`, `financial_calculations` inventory) are intentionally LEFT as point-in-time snapshots -- editing them would falsify the historical record (rule 6) |
| `growth_engine.py` (`dcf0d4e`) | A1/A2/A3 | `_PeriodInputs` (255) + `_ProjectionState` (281) -- are these count-lowering bags? Every field is read by `_project_one_period`; the frozen-constants vs mutable-carry split is meaningful and mirrors `amortization_engine`'s ProjectionInputs/_ProjectionState | ACCEPT | Real concepts, not bags; matches the blessed sibling-engine pattern. The internal `_PeriodInputs` is consistent with keeping `project_balance`'s public 8-arg signature (callers never see it) |
| `growth_engine.py` (`dcf0d4e`) | B1/B2/A4 | `_period_return_rate` (228) shared by `project_balance` + `reverse_project_balance` -- false-DRY check: could the two rates legitimately diverge? | ACCEPT | No: reverse INVERTS the forward formula, so the rate is the same number by definition. Genuine 2-site DRY; sharing makes the can't-diverge invariant structural rather than incidental |
| `growth_engine.py` (`dcf0d4e`) | A3/F3 | The two documented disables -- `project_balance` tm-args/pos (400) and `ProjectedBalance` tm-instance-attrs (24). Reviewer tested "bundle the 8 args" and "split the row" | ACCEPT | Both survive the over-engineering challenge: callers vary the 8 args independently (param object = stamp coupling), DTO fields are irreducible columns mirroring `AmortizationRow`. Honesty-first, symbol-named, why-commented |
| `growth_engine.py` (`dcf0d4e`) | D2 | `_project_one_period`'s `contribution_lookup: dict` (310) was bare; the lookup's element type is fully known | REFINE (folded into `dcf0d4e`) | Tightened to `dict[date, tuple[Decimal, bool]]` (or None). `employer_params` left `dict`-or-None -- heterogeneous config, untyped module-wide; a TypedDict is out of this file's scope |
| `growth_engine.py` (`dcf0d4e`) | G3/E1 | The `period_days <= 0 -> 14` degenerate-period fallback (247), now shared by BOTH projection directions, had no direct unit test | REFINE (folded into `dcf0d4e`) | Added `test_degenerate_period_falls_back_to_14_days` -- a 0-day period grows exactly as a real 14-day period (hand-pinned 25.98); closes the now-doubly-load-bearing branch |
| `loan_resolver/_periods.py` (`41f42a8`) | A1/A2/A3 | `LoanInputs` (128) -- cohesive domain concept or a count-lowering bag? | ACCEPT | Genuine: the 4 fields are the exact clump EVERY caller co-loads (three loads/site); `resolve_loan`, `compute_payoff_scenarios`, `_replay_from_anchor`, `_build_forward_inputs` each read all/most 4. A3 holds -- a "loaded loan data" bundle is defensible without thresholds. Mirrors `PayoffRequest`/`AmortizationRow` |
| `loan_resolver/_periods.py` (`41f42a8`) | A4/B1/E1 | `_replay_from_anchor` (211) shared by the resolver's balance derivation + the composer -- does sharing it violate the "balance derived independently of the schedule generation" invariant? | ACCEPT | No: the helper does ONLY replay (anchor-select + `replay_schedule`), never `project_forward`. The invariant is about projection not moving the balance; replay IS the balance derivation. Genuine 2-site DRY; the resolver and composer walk the same replay and cannot diverge |
| `loan_resolver/_payoff.py` (`41f42a8`) | A1/A4/A5 | `_ProjectionPrep` (222) + `_build_forward_inputs` (250) -- single-use bag that just relocates code, or genuine? | ACCEPT | Single-use but genuinely clarifying: dissolves the composer's tm-locals and leaves a thin "project 3 ways, then summarize" orchestrator. All 3 fields are replay-derived (no raw/derived mixing -- A5 clean); the summary metrics correctly stay inline in the composer (byte-identical to the original) |
| `loan_resolver/_payoff.py` (`41f42a8`) | A3/F3 | `PayoffScenarios` (35) `too-many-instance-attributes` disable -- restructure into nested sections instead? | ACCEPT | 10 irreducible result columns (3 chart slices + history + 6 metrics) read flat by one consumer; nesting would fragment one contract for no gain. Documented, symbol-named, matches `AmortizationRow`/`PayoffRequest` |
| `loan_resolver/` (pkg) (`41f42a8`) | C3/F3 | Package layering + public surface | ACCEPT | Acyclic DAG `_periods <- _payoff <- _state` (`_state` depends on both, still acyclic); `__init__` re-exports 6 public symbols with a correct sorted `__all__`; every import path preserved; 0 new R0801 (no split-trap) |
| `test_loan_resolver.py` (`41f42a8`) | G1/G2/G3 | ~52 wrapped call sites + repointed source guards | ACCEPT | All wraps byte-identical (same arg order/values); hand-computed assertions intact ($2,398.20 ARM constant, etc.); edge cases still covered (empty anchors, projected-only, ARM window, trueup, zero-rate, tie-break, confirmed-past-as_of). `_loan_resolver_package_source()` globs the package dir generically (survives a future re-split) and tokenizes cleanly |
| `test_loan_resolver.py` (`41f42a8`) | G2 | F8: `inspect.getsource(compute_payoff_scenarios)` (composer-only purity guard) no longer reaches the extracted `_build_forward_inputs` | ACCEPT (LOW, no change) | No coverage gap: the package-wide guard `test_resolver_is_pure_no_flask_no_db` scans `_payoff.py` in full (incl. `_build_forward_inputs`). Repointing the composer-only guard too is optional polish, not worth churn (reviewer concurred) |

## Sweep results (files 2-14, run `wvq2yd9aa`, 2026-06-05)

13 independent reviewers. **1 HIGH (out of scope), 22 MEDIUM, 51 LOW; 37 ACCEPT, 37 REFINE, 0
REVERT-OVERREACH.** Key signal: **zero REVERT-OVERREACH** across all 13 -- the other files used their
bundles cohesively; `_PaycheckContext` was the outlier. `amortization_engine`, `debt_strategy_service`,
and `transfers/` came back essentially clean (the bundle exemplars). Each finding below is a reviewer
recommendation to **verify-then-apply** (confirm the claim against the code first, as done for the HIGH).

### HIGH -- out of scope (latent production 500; needs a decision)

- **`app/templates/investment/dashboard.html:109`** calls `url_for('salary.salary_listing')`; no such
  endpoint exists (it is `salary.list_profiles`). werkzeug BuildError (500) on the no-deduction-linked
  `{% else %}` branch. Introduced by `b994539` (unrelated to the cleanup); not caught by tests (that
  account/profile combination is unhit). **VERIFIED.** One-line fix; out of scope for this pass.

### Proposed fix-batches (the 22 MEDIUM; financial-core first)

| Batch | Findings | Verdict | Gist |
|---|---|---|---|
| B1 type-precision (D2) | retirement F2, investment F3, savings F1 | REFINE | bare `list`/`dict` -> precise element types; savings: promote the `params` grab-bag dict to a typed frozen `_AccountParams` |
| B2 year_end | year_end F1, F2 | REFINE | tighten `_ProjectionInputs` (forwarded-superset, read-subset) toward a cohesive `_InvestmentProjectionInputs` + plain args; replace `_get_account_balance_map(inputs=None)` mode-flag with an explicit base-only path |
| B3 loan | loan F1, F2, F3, F4 | REFINE | narrow `_resolve_loan_state` 3-tuple -> `LoanState` (callers discard 2); extract the duplicated 4-step resolve core; return a typed `_RouteLoanContext` instead of an untyped dict; extract `_render_rate_history` (dup query+render) |
| B4 transactions | transactions F1, F2 | REFINE | make the `_RenderTarget` sinks take the bundle (stop unpacking at call sites -- same asymmetry as `_bracket_federal`); re-key `_resolve_owned_fks` off model-class (silent-overwrite trap if two FKs share a model) |
| B5 grid | grid F1 | REFINE | remove the dead `_GridRowData.txn_by_period` field (computed + forwarded but no template reads it; cleanup canonized dead weight) -> 5-field contract |
| B6 docs-only | salary F1, recurrence F1, investment F1/F2 | REFINE/ACCEPT | fix `salary/_bp.py` docstring (`raises`/`deductions` -> `items`); rename `RecurrenceFormContext` doc to "processing-options param object" (keep bundle); note investment `_ProjectionContext` dual limit source (keep) |
| B7 test-gaps (G) | retirement F1, investment F7, recurrence F2, transfer-creation F1 | REFINE | add hand-computed/value-pinning tests: gap-net-biweekly scaling; zero-annual-limit branches; update/resolve "no auto-offset" invariant; "inactive source account" money-routing guard |
| B8 lint-floor (Phase 4) | salary F2 | REFINE | 8 `line-too-long` in `salary/` (copied through the split); wrap -- belongs to Phase 4 mechanical sweep |

Full per-finding detail (LOWs + ACCEPT rationales) in run `wvq2yd9aa` output. LOWs are predominantly
ACCEPTs validating good design, plus minor nits foldable into the batches above.

### Fix-batch progress (session of 2026-06-06)

Full suite 5755 passed (= baseline) after every commit below; each file held/returned to 10.00/10.

| Batch | Commit | Status |
|---|---|---|
| HIGH (latent 500) | `92f2bd0` | DONE -- endpoint fixed; **reframed: unreachable dead branch, not a live 500** (route always sets `salary_profile_url` in the deduction path). Regression-pinned the reachable invariant |
| B1 type-precision | `bfbdd65` | DONE (retirement `_ProjectionBatch`, investment `_PeriodList` union + projection/contribution types) |
| B1b savings params | `c4f4551` | DONE (`_AccountParams` typed dataclass; `scenario_id` off the params bag) |
| B2 year_end | `5c2308a` | **F2 DONE** (balance-map `_base`/`_dispatch` split). **F1 ACCEPTED as-is** (developer call 2026-06-06: won't reshape -- see below) |
| B3 loan | `1f48c01` | DONE (F1 tuple-narrow, F2 shared `_resolve` core, F3 `_RouteLoanContext` composing `LoanContext`, F4 `_render_rate_history`) |
| B5 grid | `e212629` | DONE (dead `txn_by_period` field removed) |
| B6 docs | `9395d3b` | DONE (salary `_bp` module list, `RecurrenceFormContext` framing, `_ProjectionContext` feed/dual-source note) |
| B4 transactions | `11a4837` | DONE -- F1 applied (both intermediate sinks take `_RenderTarget`, leaf stays plain); **F2 ACCEPTED as-is** (re-key would trade footgun for reorder-fragility -- see below) |
| B7 test-gaps | `f1cab18` | DONE -- 11 new value-pinning tests across the 4 gaps (full suite 5766 = 5755 baseline + 11) |

**B2-F1 -- ACCEPTED as-is (2026-06-06, will NOT reshape):** reshaping `_ProjectionInputs` to the
investment trio + fanning out `debt_schedules`/`interest_params_map` is an invasive 5-file change to
correct, well-tested projection plumbing for a *partial* gain (the trio is still subset-read within
each chain). The reviewer rated it the weakest offender (REFINE; honest-docstring minimum bar already
met) and F2 already shipped the clear win, so the developer chose to leave F1. Do NOT re-raise it.

### B4 + B7 done (2026-06-06, continued) -- fix-batch sweep complete

Both remaining MEDIUM batches are landed. The only outstanding item is **B8** (8 `line-too-long` in
`salary/`), which is a Phase-4 mechanical lint-floor sweep tracked in `plan.md`, not a design-quality
finding -- the design-quality fix-batches (B1-B7) are now all resolved.

**B4-F1 -- `_RenderTarget` threaded (applied, `11a4837`):** confirmed the nuance against the code and
went one step past the "stays plain" note. `_mark_done_success_response` (single caller, reads all
three fields) and `_stale_transaction_response` now take the bundle; the latter as **optional**
(`target=None`) so its 7 non-mobile callers pass nothing -- no empty-bundle ceremony -- while the 2
mobile callers stop unpacking. `_render_mobile_card` stays plain (it reads only `card_prefix`/
`can_edit`, never `render_mode` -- the genuine subset leaf). Bonus: an optional `_RenderTarget` is a
*stronger* contract than three independently-defaulted scalars, which permitted half-specified states
(`render_mode="mobile_card"` with a defaulted `card_prefix`). Behavior preserved; 170 route tests +
full suite green.

**B4-F2 -- `_resolve_owned_fks` ACCEPTED as-is (will NOT re-key):** the reviewer flagged the
model-class-keyed return as a "silent-overwrite trap if two FKs share a model." Verified: every spec
is ownership-checked in the loop regardless of dict collisions, so the 404 IDOR gate is **never**
weakened -- a collision touches only the convenience map, and all 6 call sites use distinct models.
Re-keying to a positional list would trade that hypothetical, no-security-impact footgun for
*reorder-misassignment* fragility (a broader risk for an IDOR probe), and the model-keyed `objs[Model]`
access is self-documenting and reorder-robust. A guard against duplicate models would be rule-13
handling of an impossible scenario. Resolution: documented the one-spec-per-model precondition in the
docstring (neutralizes the "trap" quality) and left the structure. **If you disagree, this is the one
B4 call to revisit.**

**B7 -- 4 test-gaps closed (`f1cab18`):** 11 hand-computed value-pinning tests; each gap verified to be
genuinely uncovered before writing. See the commit body and register rows for the arithmetic. Full
suite 5766 = 5755 baseline + 11.
