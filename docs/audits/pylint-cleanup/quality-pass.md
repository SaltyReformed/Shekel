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
| 18 | `services/retirement_gap_calculator.py` | `2b0f5ca` | REVIEWED -- **ACCEPT** (all 4 behavior-equivalence points verified line-for-line incl. whole-expression quantize order; dead `planned_retirement_date` removal grep-confirmed write-only; `too-many-instance-attributes` disable upheld; 0 REVERT-OVERREACH, 0 REFINE; lone LOW note on single-use `_sum_projected_balances`, accepted) |
| 19 | `routes/debt_strategy.py` (route) | `8449f21` (+ test `9efb7b4`) | REVIEWED -- **ACCEPT** (all 9 behavior-equivalence points verified line-for-line; `_ResultsError` funnel + `_StrategyResults` bundle + 3-tuple asymmetry all upheld after arguing both directions; 6 dead render kwargs grep-confirmed; 0 REVERT-OVERREACH, 0 REFINE; lone MED was a pre-existing route-level test gap on the reachable compute-error funnel + custom selection -- closed in `9efb7b4`, +3 tests) |
| 20 | `services/investment_projection.py` (+ `projection_inputs.py` wrapper) | `bf111f0` | REVIEWED -- **ACCEPT** (all 5 steps verified behavior-equivalent line-for-line incl. the De Morgan employer-guard rewrite and `if current_period:` -> `is None` against PayPeriod; dead `account_id` removal confirmed across both functions + all 5 consumers; the documented tm-args/pos disable upheld after the reviewer checked all 5 consumers and found `all_periods`/`current_period` independently varied -- a param object would be stamp coupling, the `growth_engine.project_balance` precedent is real; 4 single-use helpers accepted as honesty-first decomposition; 0 REVERT-OVERREACH, 0 REFINE; lone watch-item a PRE-EXISTING negative-deduction `# BUG` test comment, out of scope, reported as plan.md P-3) |
| 21 | `services/budget_variance_service.py` | `b5a9d56` | REVIEWED -- **ACCEPT** (Option B: `VarianceFigures` value object + `of()` factory; all 5 behavior-equivalence points verified line-for-line -- the `of()` arithmetic == the 4 former inline computes, the empty-report `sum()`->int-`0` through `_pct`'s `== Decimal("0")` guard yields `variance_pct=None`, sort keys equivalent, byte-identical CSV/template output, value-frozen tests; `VarianceFigures` clears the cohesive-named-concept bar -- all 4 fields travel together at every consumer A1/A2; 0 REVERT-OVERREACH, 0 REFINE; lone nit -- derived fields stored vs `@property` -- accepted, matches pre-refactor design + gated behind the sole `of()` constructor) |
| 22 | `services/calibration_service.py` | `4e625fe` | REVIEWED -- **ACCEPT** (frozen `PayStubActuals` param object over `derive_effective_rates`'s 6 inputs; behavior verified equivalent line-for-line incl. the string-coercion path + both `ValidationError` guards + the four rate computes -- only the values' source changed, params -> `actuals.`; A1/A2/A5 cleared -- all 6 fields consumed together by both routes + the function, the model persists the 5 `actual_*` as a cohesive cluster, the one derived field `taxable_income` is documented, no stamp coupling; 3 findings all ACCEPT after verification -- `frozen=True` upheld (non-frozen sibling `DerivedRates` out-of-scope, rule 6) and the `Decimal` field hints verified against the production contract (`CalibrationSchema`/`CalibrationConfirmSchema` use `fields.Decimal`, the str path is test-only defensiveness); 0 REVERT-OVERREACH, 0 REFINE) |
| 23 | `app/ref_cache.py` | `ebcda36` (+ test `d2b1c31`) | REVIEWED -- **ACCEPT** (C'-dict: 14 module globals -> one never-rebound `_RefState` with the 13 maps in a single `enum_ids` registry + a frozen `_RefSpec` registry-spec + `_require_init()` guard helper; behavior verified BYTE-IDENTICAL **empirically** -- the reviewer built a harness running old-vs-new through every edge class [missing table, empty table, missing row, re-init] and proved identical `unavailable` lists, RuntimeError text/order, and DB query order; `enum_ids` collapse upheld as genuine design not a count-dodge [it enables the single-loop `init()`; named fields would have needed a `too-many-instance-attributes` disable]; kept-separate `_acct_type_meta` block upheld [different shape/columns, not false DRY]; `model: type` annotation accepted [no `type[db.Model]` convention exists, tightening = gold-plating]; 0 REVERT-OVERREACH; 1 REFINE applied -- F5, the previously-untested bootstrap/`unavailable` path that `app/__init__.py:192` depends on, now pinned by `d2b1c31`) |
| 24 | `app/ref_seeds.py` | `32c403a` | REVIEWED -- **ACCEPT** (3-lens panel: behavior-equivalence / simplicity / right-abstraction; pure extract-into-named-steps decomposition of `seed_reference_data` into a thin orchestrator + 3 single-use phase helpers, NO bundle/param object introduced so A1/A2 are N/A; all 3 reviewers `behavior_equivalent=yes`, one proving deep-AST equality of the fully-inlined after-body vs the original single function incl. loop interiors -- flush stays between categories and AccountType inserts, `cat_lookup` queried post-flush, 7-column refresh + dict/string step-3 branch + verbose strings + query order all unchanged; A4 single-use helpers ACCEPTED as genuinely-clarifying cohesive named steps that surface the flush invariant [honesty-first, the canonical too-many-locals/branches remedy], C2 cohesion improved not scattered, F1 next-feature [a migration adding a ref row] slots in with zero helper edits; 0 REVERT-OVERREACH; the lone LOW D2 finding -- the new + pre-existing untyped signatures -- was the only split verdict [2 ACCEPT vs 1 REFINE] and the developer chose to REFINE: hints added via a `TYPE_CHECKING` block + `from __future__ import annotations` [`ModuleType`/`Session` lazy strings, zero runtime imports, preserves the side-effect-free discipline]) |
| 25 | `routes/settings.py` | `1d52d3f` | REVIEWED -- **ACCEPT** (3-lens panel: behavior-equivalence / simplicity / right-abstraction+scope; all `behavior_equivalent=yes` -- all 8 sections' 19 render kwargs byte-identical per set-diff [empty both directions], the `update` loop applies the same 6 fields with the same `in data and is not None` guard, the `default_grid_account_id` IDOR branch + all 3 flash/redirect paths unchanged, `_render_companions_section` key-by-key parity with HEAD; the `show` per-section-loader + `_empty_section_context()` shape mirrors the `routes/loan` dashboard precedent and the `update` allowlist loop the `templates.py` allowlist-write precedent; 0 REVERT-OVERREACH, 0 REFINE; the SCOPE question -- touching the non-flagged `_render_companions_section` + removing `_empty_companions_context` -- ruled by all 3 as JUSTIFIED DRY not rule-6 creep [the empty-defaults contract was triplicated; the helper is the single home the `show` smell forced]; the lone LOW D2 note [the lifted icon list as a `list`] folded in as the immutable tuple `_ACCOUNT_TYPE_ICON_CHOICES`) |
| 26 | `routes/obligations.py` | `7a77db9` | REVIEWED -- **ACCEPT** (3-lens panel: behavior-equivalence / simplicity / right-abstraction; all `behavior_equivalent=yes`; **12 findings all ACCEPT, 0 REVERT-OVERREACH, 0 REFINE** -- the `_next_occurrence` if/elif single-return accumulator is byte-equivalent to the old per-pattern early-returns [every branch walked, the hoisted `day`/`month` pure reads unused by the period/unknown branches, end_date guard + unknown->None fallthrough preserved -- the `match_periods` precedent]; the `summary` loaders reproduce the queries verbatim and `_build_items` preserves the E-24/HIGH-05 row-iff-subtotal invariant [verified via `test_expired_templates_excluded`, not just claimed]; the constrained `TypeVar` in `(TransactionTemplate, TransferTemplate)` ruled correct-not-overkill by 2 reviewers [a plain Union can't express renderer/template type-pairing]; the 3 single-use loaders genuine query-extraction [loan.py precedent]; the `day`/`month` hoist an accepted E3 tiny-waste) |
| 27 | `routes/accounts/detail.py` | `c5182ea` | REVIEWED -- **ACCEPT** (3-lens panel: behavior-equivalence / simplicity / right-abstraction; all `behavior_equivalent=yes` -- every helper char-identical to what it replaced in both handlers [verified vs `git show HEAD`], `project_balance_horizons` proven == the old inline horizon loop [the util's `is None` guard == per-iteration truthiness since PayPeriod has no `__bool__`], both render kwarg sets byte-identical, the F-6 static guard held [`balances_for` present, bare `calculate_balances(` absent], `selectinload(entries)` preserved; the 3 helpers + horizon-util reuse all genuine DRY/extraction, comment preservation verified; 0 REVERT-OVERREACH; **2 REFINEs applied before commit** -- MED: renamed `_resolve_current_balance` -> `_current_period_balance` to clear a cross-file name collision with the semantically-different `investment_dashboard_service._resolve_current_balance`; LOW: typed all 3 helpers via `from __future__` annotations + a TYPE_CHECKING block) |
| 28 | `routes/categories.py` | `5b32148` | REVIEWED -- **ACCEPT** (single fresh reviewer, proportionate to a 2-site extract; behavior_equivalent=yes -- `_create_form_error_response` byte-equivalent to both old inline error blocks [HX branch literally identical; only errors dict + message varied], the duplicate + success paths byte-unchanged, 7->5 returns; 6 ACCEPT, 0 REFINE, 0 REVERT-OVERREACH -- A4 genuine 2-site DRY not count-dodge, B1 one home, D2 `Response | tuple[Response, int]` precise [`errors: dict` left untightened to match surrounding route code -- gold-plating avoided], F1 keeping it create-specific upheld [`edit_category` has no HTMX-jsonify branch -- folding it in would change edit's HTMX contract or add a rule-13 suppress-flag]; create error paths pinned by existing tests) |
| 29 | `routes/accounts/anchor.py` | `ab16669` | REVIEWED -- **ACCEPT** (single fresh reviewer; behavior_equivalent=yes -- the success-merge preserves the DUPLICATE-vs-COMMITTED asymmetry verified against `anchor_service`'s rollback contract [DUPLICATE re-fetches post-`rollback()`; COMMITTED `refresh`es + logs; logger COMMITTED-only, matching HEAD], `_anchor_conflict_response` byte-identical to both old conflict blocks; 5 findings all ACCEPT, 0 REFINE, 0 REVERT-OVERREACH -- A4 conflict-helper genuine 2-site DRY, B2 success-merge legitimate shared-tail not false-DRY, D2 `-> tuple[str, int]` correct, the route at exactly 6 returns maps 1:1 onto 6 distinct HTTP outcomes [irreducible, no disable]; one PRE-EXISTING out-of-scope test-depth gap noted -- `test_double_submit` lacks a committed-balance body assertion on the DUPLICATE render) |
| 30 | `routes/entries.py` | `6e3c32d` | REVIEWED -- **ACCEPT** (single fresh reviewer; behavior_equivalent=yes BYTE-verified -- `_execute_entry_update` body character-identical to the replaced try/except/success block, all names module-scope, the preamble + `version_id` pop untouched; all findings ACCEPT, 0 REFINE, 0 REVERT-OVERREACH -- A4/C1-C2 genuine cohesive execute+translate unit [the `_execute_transfer_update` precedent, divergence-returns-response-itself correct for a route with no post-commit branching], extraction-over-disable the right judgment, D2 untyped helper justified by the untyped siblings; 2 deferred tracker notes -- coordinated `flask.typing.ResponseReturnValue` typing pass for the helper cluster, and the 7-line ownership preamble DRY across the 3 mutating routes -- both correctly out of scope) |
| 31 | `app/__init__.py` | `e22a1a5` | REVIEWED -- **ACCEPT** (single fresh reviewer; behavior_equivalent=yes verified 4 ways -- `_BLUEPRINT_MODULES` == the old register order, every `getattr(module, "<name>_bp")` resolves to the same Blueprint, a full `create_app` build registers all 23 in order = 166 URL rules, a grep of all `*_bp = Blueprint` returns exactly these 23; the design fork [data-driven loop vs explicit-imports + a documented too-many-locals disable] argued BOTH ways and the loop ruled correct -- genuine decision-#3 refactor [DRY: removes the 23x import+register pairing; eliminates the disable rather than parking it; the `<name>_bp` convention is total + fails-loud], the one cost being greppability of individual `_bp` registrations, mitigated by the documented convention; all ACCEPT, 0 REFINE, 0 REVERT-OVERREACH -- removing the now-useless `import-outside-toplevel` disable confirmed correct) |
| 32 | `services/savings_goal_service.py` | `7dad8d7` | REVIEWED -- **ACCEPT** (single fresh reviewer; behavior_equivalent=yes -- byte-for-byte branch table vs HEAD confirmed every per-pattern Decimal expression identical [division order preserved], `once` early-return kept + `once_id` still used, unknown->`else` None, no quantization added; FINANCIAL function so Decimal discipline checked -- intact; all ACCEPT, 0 REFINE, 0 REVERT-OVERREACH -- D1/D3 accumulator the right shape over a dispatch dict [heterogeneous branches: `every_n` extra local, shared arm, bare passthrough], E3 keeping `once` explicit-vs-unknown a meaningful documented distinction not an inconsistency, the `None`-fold safe at the sole consumer which skips None uniformly) |
| 33 | `services/interest_projection.py` | `62fd7a2` | REVIEWED -- **ACCEPT** (single fresh reviewer; behavior_equivalent=yes byte-for-byte -- `_days_in_quarter` body identical to the old inline calc [only `=`->`return`], quarterly formula + other branches/guards/round_money untouched, Decimal discipline intact; all ACCEPT, 0 REFINE, 0 REVERT-OVERREACH -- A4 the divisor extraction genuinely clarifying + parallels `_days_in_year_for_window`, and the **alternative full-3-branch dispatcher ruled REVERT-OVERREACH** [gold-plating: asymmetric math, formula co-location has review value]; the reviewer-flagged PRE-EXISTING Q4 year-rollover test gap -- made load-bearing by the extraction -- was closed in the same commit with a hand-computed `test_q4_year_rollover_period` [92-day Q4 -> 17.12] + a stale `/91` test-docstring fix) |
| 34 | `services/loan_payment_service.py` | `1cfbcdb` | REVIEWED -- **ACCEPT** (single fresh reviewer; behavior_equivalent=yes BYTE-LEVEL-diff verified -- `_redistribute_to_distinct_months` body identical to old Step 2 [only `result: list[PaymentRecord]` type added], `_resolve_loan_piti` identical modulo the documented `continue`->`return None` + `today`->`as_of` transforms, escrow Step 1 == HEAD, the P-1 functions not in the diff, the `loan_resolver` deferred import moved-not-duplicated; FINANCIAL file checked -- Decimal PITI end-to-end; all ACCEPT, 0 REFINE, 0 REVERT-OVERREACH -- A4 both genuinely-clarifying dense-sub-step extractions [the `_days_in_quarter`/`_project_one_account` precedent], **leaving the P-1-adjacent escrow Step 1 INLINE ruled the correct conservative call** [extracting for symmetry = gold-plating against the unstable P-1 target], C3 the moved import keeps the one-way dependency; P-1 remains open + untouched, correct) |

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

**Fourth going-forward file: `services/retirement_gap_calculator.py` (`2b0f5ca`, 2026-06-06).** The
mechanical commit cleared all 4 design smells (`calculate_gap` tm-args/pos/locals; `RetirementGapAnalysis`
tm-instance-attributes). This is the first file where the trust-but-verify pass overturned the plan's own
ratified approach: the 2026-06-06 paired-fork session had ratified "keyword-only + relocate
`planned_retirement_date` to the caller (KEEP the field)", but verification before coding showed the
result field is write-only (read by NO production consumer -- template, `_build_chart_data`, all of app/),
so the developer chose FULL removal (param AND field) as the root-cause/DRY fix; and dropping the param to
5 args clears tm-positional by itself at the default max=5, so keyword-only was dropped as an unrequested
interface change. tm-locals cleared by two pure helpers (`_after_tax_projected_savings`,
`_sum_projected_balances`); `RetirementGapAnalysis`(10/7) took a documented disable (flat row-per-field
aggregate; `AmortizationRow`/`PayoffRequest`/`ProjectedBalance` precedent). The independent reviewer (fresh
subagent, A-G rubric) verified all 4 behavior-equivalence points line-for-line -- including that
`_after_tax_projected_savings` quantizes the *whole* `(traditional*(1-rate)+roth)` expression (no premature
rounding) and that the dead-field removal breaks nothing (independent grep) -- and returned **ACCEPT, 0
REVERT-OVERREACH, 0 REFINE**. The lone LOW note (single-use `_sum_projected_balances`) was accepted; the
reviewer's stated justification ("reverting re-trips tm-locals") was verified WRONG and corrected --
inlining the sum alone leaves exactly 15 locals, which passes -- so the helper is kept for
orchestrator-altitude cohesion, not threshold-necessity. No code change beyond the mechanical commit. See
the register rows below.

**Fifth going-forward file: `routes/debt_strategy.py` (`8449f21`, 2026-06-06).** The mechanical commit
cleared all 3 design smells on the `calculate` route handler (tm-locals 17/15, tm-return 7/6,
tm-branches) by genuine decomposition. The design fork was tm-return: the 5 duplicated `_results.html`
error renders were collapsed through a new private `_ResultsError(Exception)` + one try/except (the
developer chose this over a no-exception merge or a documented disable), the IDOR 404 left as a direct
return outside the funnel. The independent reviewer (fresh subagent, A-G rubric) argued BOTH "simpler?"
(is the exception a count-dodge? is the `_StrategyResults` bundle gold-plating? is the 3-tuple return
inconsistent?) and "right abstraction?" -- and upheld all three: the exception expresses the genuine
single-error-contract DRY win (5 identical renders -> 1), the bundle is a cohesive named result read at
two sites (vs a 4-tuple unpacked positionally), and the 3-tuple is the honest choice for heterogeneous
inputs unpacked once at the single call site (a bundle there would be the count-dodging bag the
anti-pattern warns against). All 9 behavior-equivalence points verified line-for-line, returning
**ACCEPT, 0 REVERT-OVERREACH, 0 REFINE**. The lone MED finding -- a PRE-EXISTING route-level test gap
(no test pinned the reachable compute-error funnel via a duplicate/incomplete `custom_order`, nor that
`_select_result` returns the custom run) -- was closed in a SEPARATE test-hardening commit (`9efb7b4`,
+3 route tests), per the developer's choice and the reviewer's advice not to widen the refactor commit;
the route-unreachable baseline/avalanche/snowball except was deliberately NOT mocked (rule 13). See the
register rows below.

**Sixth going-forward file: `services/investment_projection.py` (+ `projection_inputs.py`) (`bf111f0`,
2026-06-06).** The mechanical commit cleared all design smells on the coupled
`calculate_investment_inputs` (the pure, DB-free computation) and its thin pass-through wrapper
`build_investment_projection_inputs` (which lives in the DB-capable sibling module and is the "single
splat home"). Three moves: (1) removed the dead `account_id` param -- the wrapper forwarded it and the
callee ignored it -- at root from both signatures, all 5 production call sites, and all 18 test calls;
(2) decomposed the 5 steps into four single-purpose private helpers (tm-locals); (3) for the residual
6 args (1 over max), a documented scoped disable on BOTH public functions rather than a param object.
The design fork the developer chose was the disable (Option A) over a `(all_periods, current_period)`
"calendar" bundle (Option B) or a full request object (Option C). The independent reviewer (fresh
subagent, A-G rubric) made the behavior-equivalence check the priority for this financial code and
verified all 5 steps line-for-line -- including proving the De Morgan rewrite of the employer-type
guard across `{none,'',None,0,match,flat_percentage}` and that `if current_period:` -> `is None` is
equivalent because `PayPeriod` defines no `__bool__` -- returning **ACCEPT, 0 REVERT-OVERREACH, 0
REFINE**. It specifically tested the disable choice against all 5 consumers and confirmed they vary
`all_periods` and `current_period` independently (one passes `year_periods[0]`, another
`post_anchor[0] if post_anchor else pre_anchor[-1]`, the dashboards `ctx.current_period`), so the
"calendar" bundle would NOT be cohesive and a param object would introduce the very stamp coupling it
would claim to cure -- the disable is the lower-ceremony, honest choice, and the
`growth_engine.project_balance` precedent is genuine. The lone watch-item is a PRE-EXISTING,
out-of-scope, untouched negative-deduction `# BUG` test comment (`test_investment_projection.py`),
reported as plan.md P-3. No code change beyond the mechanical commit. See the register rows below.

**`budget_variance_service.py` (`b5a9d56`) -- DONE.** The developer chose Option B (extract
`VarianceFigures` + an `of()` factory) over documented disables on the three result DTOs, because the
(estimated/actual/variance/variance_pct) quad both STORED across all four levels with naming drift AND
its `variance = actual - estimated; variance_pct = _pct(...)` computation was hand-written 4x
(R0801-invisible -- the locals differed at each level, the `investment_dashboard_service` precedent).
The independent reviewer (fresh subagent, A-G rubric) argued both directions, prioritized the
financial-correctness behavior-equivalence check, and verified all 5 points line-for-line: the `of()`
arithmetic is character-identical to each former inline compute; the empty-report path (`sum([])` ->
int `0`, then `of(0, 0)` -> `_pct(0, 0)` returns `None` via the `== Decimal("0")` guard) reproduces the
old report-level zeros + null pct; the sort keys, CSV, and template output are byte-identical; and the
test updates are path-only with values frozen. `VarianceFigures` clears the cohesive-named-concept bar
(A1/A2 -- all four fields are read together by the CSV exporter, the template, and the chart builder;
not stamp coupling). Verdict **ACCEPT, 0 REVERT-OVERREACH, 0 REFINE**; the one nit (derived
`variance`/`variance_pct` are stored fields rather than `@property`) was accepted as consistent with the
pre-refactor design and gated behind the sole `of()` constructor -- turning them into properties would
be an unrequested redesign (rule 13). No code change beyond the mechanical commit. See the register
rows below.

**`calibration_service.py` (`4e625fe`) -- DONE.** The mechanical commit cleared all 3 design smells on
`derive_effective_rates` (tm-args/pos/locals) by bundling its 6 inputs into a new frozen
`PayStubActuals` value object -- the textbook param-object move for a public function whose args are a
cohesive named concept ([[feedback_tm_args_param_object]]). The independent reviewer (fresh subagent,
A-G rubric) prioritized the financial-correctness behavior-equivalence check and verified it
line-for-line: the six `Decimal(str(...))` coercions, both `ValidationError` guards, and the four rate
computes are byte-identical, only re-sourced from `actuals.`. `PayStubActuals` clears the
cohesive-named-concept bar (A1/A2/A5): the `CalibrationOverride` model already persists the five
`actual_*` as one unit, all six fields are consumed together by both routes + the function (no stamp
coupling), and the one derived field (`taxable_income`, the route-computed federal/state divisor) is
documented. Verdict **ACCEPT, 0 REVERT-OVERREACH, 0 REFINE**; all 3 findings resolved to ACCEPT after
verification -- the `frozen=True` choice upheld (the non-frozen sibling `DerivedRates` is a separate
out-of-scope item, rule 6), and the `Decimal` field annotations verified to match the production
contract (`CalibrationSchema`/`CalibrationConfirmSchema` use `fields.Decimal`; the string-input test
exercises the project's standard construct-from-strings defensiveness, so widening the hint to
`Decimal | str` would misrepresent the contract). No code change beyond the mechanical commit. See the
register rows below.

**`ref_cache.py` (`ebcda36`, + test `d2b1c31`) -- DONE.** The mechanical commit cleared all 3 `init`
design smells (tm-locals/branches/statements 31/15, 51/12, 124/50) and ALSO removed the 5
`global`-statement disables + a 15x-duplicated init-guard, via the developer-chosen from-scratch best
design (C'-dict). This is the first Phase-3 file whose design fork was deliberated with the developer
in-session (Fork A "contained, one residual `global`" vs Fork B "full encapsulation, zero globals");
the developer chose Fork B, and a trust-but-verify catch DURING planning forced its refinement: a
14-named-field `_RefState` dataclass trips `too-many-instance-attributes` (verified --
`ProjectedBalance`/`AmortizationRow` are dataclasses carrying exactly that disable), so the named-field
form would NOT have reached Fork B's zero-disable goal. The maps were therefore collapsed into a single
`enum_ids` registry keyed by enum class (C'-dict), which is disable-free AND more DRY. The independent
reviewer (fresh subagent, A-G rubric) prioritized the financial-bootstrap behavior-equivalence check
and verified it **empirically** -- it extracted the HEAD copy and drove both versions through all four
edge classes (missing table, existing-but-empty table, missing enum row, re-init) against a real test
DB, proving identical `unavailable` lists, byte-identical `RuntimeError` text/order, and an unchanged DB
query/rollback sequence (the single load+sweep loop issues no DB work in the sweep, so collapsing the
old two-phase structure changes nothing). Verdict **ACCEPT, 0 REVERT-OVERREACH**; the `enum_ids`
collapse, the kept-separate `_acct_type_meta` block, and the loose `model: type` hint were all upheld
after arguing both directions. The lone REFINE -- F5, a genuine pre-existing test gap on the bootstrap/
`unavailable` path that `app/__init__.py:192` consumes to gate Jinja globals (and which the refactor's
`enum_ids` pre-seed made load-bearing) -- was verified uncovered, then closed by a hand-pinned
regression test in a separate commit (`d2b1c31`, the `8449f21`+`9efb7b4` precedent). See the register
rows below.

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
| `retirement_gap_calculator.py` (`2b0f5ca`) | E2 | `_after_tax_projected_savings` (`66-94`) -- does the extraction preserve the original rounding ORDER (quantize the whole `traditional*(1-rate)+roth`, not quantize-then-add)? | ACCEPT | Verified line-for-line vs HEAD: helper quantizes the whole expression with `ROUND_HALF_UP`, identical to the inline original; the hand-pinned `420000.00`/`100000.00`/`540000.00` after-tax assertions still pass. No premature rounding introduced |
| `retirement_gap_calculator.py` (`2b0f5ca`) | E3/F1 | Removing the `planned_retirement_date` param AND result field -- root-cause fix or loss of a contract field? | ACCEPT | The field was write-only: independently grep-confirmed that NO production consumer reads `gap_result.planned_retirement_date` (template renders 10 other fields; `_build_chart_data` reads 3; nowhere else in app/). Removing dead result state is the anti-band-aid fix; the date stays available to any future view via `settings`/pension columns, so re-adding is a one-liner if a real need appears (rule 13) |
| `retirement_gap_calculator.py` (`2b0f5ca`) | A4/C2 | `_after_tax_projected_savings` is single-site -- genuine clarification or count-dodge? | ACCEPT | Genuine: it has real branching (traditional vs Roth bucketing), names a domain concept, documents the *why* (traditional taxed on withdrawal). Decomposition, not a wrapper; it is the load-bearing extraction (without it `calculate_gap` is 18 locals > 15) |
| `retirement_gap_calculator.py` (`2b0f5ca`) | A4 | `_sum_projected_balances` (`50-63`) -- branchless 3-line sum whose name restates `sum`; borderline "relocates code" | ACCEPT (LOW, no change) | Kept for orchestrator-altitude symmetry with `_after_tax_projected_savings` (calculate_gap reads as a uniform Step 1-6 delegation; inlining one raw accumulator amid delegating steps reads worse). NOTE: the reviewer's "reverting re-trips too-many-locals" rationale was verified WRONG -- inlining the sum alone leaves exactly 15 locals (passes); the helper is a cohesion choice, not threshold-necessary |
| `retirement_gap_calculator.py` (`2b0f5ca`) | A1/A3/F3 | `RetirementGapAnalysis` `too-many-instance-attributes` disable (10/7) -- restructure into nested sections (PaycheckBreakdown style) instead? | ACCEPT | Documented disable correct: `_gap_analysis.html` renders all 10 as a flat row-per-field table with pre-tax/after-tax counterparts side-by-side and NO sub-totals (unlike `PaycheckBreakdown`'s `taxes.total`/`deductions.total_pre_tax`), so nesting would fragment one concept for zero gain. Matches the ratified `AmortizationRow`/`PayoffRequest`/`ProjectedBalance` precedent; symbol-named + why-commented |
| `test_retirement_gap_calculator.py` (`2b0f5ca`) | G1/G3/G4 + rule-5 | Deleted `test_planned_retirement_date_passed_through`; updated `test_result_field_completeness` 11->10 fields | ACCEPT | Deletion is the sanctioned rule-5 exception (it asserted a field that no longer exists -- removed behavior, not a test edited to pass); the field-contract guard lives in `test_result_field_completeness`, which is updated to the 10-field set, so contract coverage is preserved. Section-E edges (zero/negative SWR, 0/100/negative tax, negative pay, empty/all-zero accounts, large values) all still pinned with hand-computed values |
| `debt_strategy.py` (`8449f21`) | A3/B1/F1 | `_ResultsError` (61-72) + the single `except` (343-344) -- is the internal exception a count-dodge to drop tm-return 7->3, or a genuine concept? | ACCEPT | Survives the over-engineering challenge: the endpoint has ONE error contract (render `_results.html` with a message at HTTP 200), previously duplicated across 5 separate early-return renders -- the funnel is the DRY collapse the count drop is a symptom of, not the goal. Carries exactly the user message (`str(exc)`), mirrors the already-caught Marshmallow `ValidationError` / Flask `abort()` idiom. A new failure mode is one `raise`; the IDOR 404 correctly stays OUTSIDE the funnel (different HTTP contract) |
| `debt_strategy.py` (`8449f21`) | A1/A2 | `_StrategyResults` (75-94) -- bare 4-tuple or named bundle? | ACCEPT | Named bundle correct: the four results are read by NAME at two sites (`_build_comparison` reads all four, `_select_result` reads three), so a positional 4-tuple would be the readability loss `feedback_tm_args_param_object` warns against. They are one cohesive "the simulations for this request" value (not stamp coupling); `frozen=True`; precise `StrategyResult \| None` hints; matches the `PayoffScenarios`/`AmortizationRow` precedent. A 5th scenario slots in as one field + one `_select_result` branch |
| `debt_strategy.py` (`8449f21`) | A1/A4 | `_parse_calculate_form` returns a bare 3-tuple while `_compute_strategies` returns a bundle -- inconsistent? | ACCEPT (lean REFINE, not worth churn) | The 3-tuple is unpacked at EXACTLY ONE site, immediately, into named locals; the three values are heterogeneous raw inputs (a Decimal, a strategy string, an optional ID list) that fan out, not a cohesive named concept -- bundling them would be the count-dodging bag the anti-pattern warns against. The asymmetry is intentional and honest (inputs fan out -> tuple; results travel together -> bundle) |
| `debt_strategy.py` (`8449f21`) | E1/F1 | 6 render kwargs removed (baseline/avalanche/snowball/custom_result/extra_monthly/debt_accounts) -- correctness risk if any is actually read? | ACCEPT | VERIFIED by independent grep of `_results.html`: those names appear ONLY as `comparison.*` sub-keys or loop-literal strings (`['avalanche','snowball']`, `selected_strategy == 'avalanche'`) -- never as top-level reads; the template is not `{% include %}`/`{% import %}`d elsewhere. All six were dead render context; removal is correct, output byte-identical (tests assert on HTML and pass) |
| `test_debt_strategy.py` (`9efb7b4`) | G3 | Pre-existing gap: no route test pinned the reachable compute-error funnel (duplicate/incomplete `custom_order`) nor that `_select_result` returns the custom run | REFINE (separate commit `9efb7b4`) | +3 route tests: `test_duplicate_custom_order_renders_error_banner` + `test_incomplete_custom_order_renders_error_banner` (each asserts the specific service message at HTTP 200, not a 500), `test_custom_selection_drives_chart_order` (avalanche leads with the higher-rate debt; a custom order opposite of that puts the other debt first -- a real discriminator for selection). The baseline/avalanche/snowball except is route-unreachable (schema bounds extra/strategy; zero-payment loans skipped) so NOT tested via mocking (rule 13, no tests for impossible scenarios) |
| `investment_projection.py` (`bf111f0`) | E1/E2/E3 (behavior-equivalence) | Does the 5-step decomposition compute EXACTLY what the original monolith did? | ACCEPT | Verified line-for-line vs `HEAD~`: Step 1 gross-fallback ordering (last-deduction gross -> `salary_gross_biweekly` -> ZERO), Step 2 average (`sum` no-start, distinct-period count, `num>0` quantize guard, int-0-never-used), Step 3 employer `getattr(...) or ZERO` defaults, Step 4 YTD independent re-filter of `all_contributions`, Step 5 inlined `annual_contribution_limit` getattr -- all identical. Corroborated by the C18-1 equivalence test + 39 passing tests |
| `investment_projection.py` (`bf111f0`) | A1/A3/A4/C2 | The 4 single-use private helpers (`_periodic_from_deductions`/`_average_transfer_contribution`/`_employer_params`/`_ytd_contributions`) -- relocation or genuine? | ACCEPT | Each names a real step (1:1 with the monolith's "Step N" comments), carries its own docstring rationale, and is the ratified honesty-first response to too-many-locals (decompose, not param-bag -- [[feedback_tm_args_param_object]]). The orchestrator now reads as a 5-line recipe; cohesion improved, not scattered |
| `investment_projection.py` / `projection_inputs.py` (`bf111f0`) | A1/A2/A3/A5 | The documented `too-many-arguments,too-many-positional-arguments` disable on both public functions vs a param object | ACCEPT | Reviewer checked ALL 5 consumers: `all_periods` and `current_period` are sourced/varied independently (`year_periods[0]`; `post_anchor[0] if post_anchor else pre_anchor[-1]`; `ctx.current_period`), and `_average_transfer_contribution` uses neither, so a `(all_periods,current_period)` "calendar" bundle is NOT cohesive (fails A2) and a full request object would be the stamp coupling the disable avoids. `growth_engine.project_balance` carries the identical disable for the identical reason -- precedent verified. Lower-ceremony AND honest |
| `investment_projection.py` (`bf111f0`) | D3/F2 | Two guard-clause rewrites: employer-type `if not emp_type or emp_type == "none": return None` (was `if emp_type and emp_type != "none":`) and `if current_period is None:` (was `if current_period:`) | ACCEPT | De Morgan equivalence proven across `{none,'',None,0,match,flat_percentage}`; the `is None` rewrite is equivalent (PayPeriod has no `__bool__`/`__len__`) AND advances the `.claude/rules/coding.md` "`is None`, not truthiness" standard. Stale "Lazy import" comment removed (it described a lazy import that does not exist -- the imports are top-level) |
| `test_investment_projection.py` / `test_projection_inputs.py` (`bf111f0`) | G1/G2/G3 | Test quality after the `account_id` removal + signature change | ACCEPT | Tests target the public functions, not the new private helpers (not over-coupled to the decomposition). Assertions are hand-computed Decimals with arithmetic comments; the C18-1 equivalence test cross-checks wrapper == inline splat field-by-field. All section-E edges still covered (None current_period, empty contributions, negative deduction, zero-rate, salary fallback, deduction-gross-override, employer-none). The `account_id=10` removal was purely mechanical; no edge weakened |
| `test_investment_projection.py` (`bf111f0`) | G3 (out of scope) | `test_negative_deduction_amount` asserts a `-500.00` deduction is accepted, with a pre-existing `# BUG: negative deduction amount is silently accepted` comment | ACCEPT (reported, not fixed) | Predates this work; the refactor preserved it bit-identically. Whether a negative contribution deduction should be rejected is financial policy (CLAUDE.md rule 3), out of scope for the lint cleanup (rule 6); reported as plan.md P-3 (rule 4) for a developer decision |
| `budget_variance_service.py:41` (`VarianceFigures`) | A1/A2/A3/B1 | The `VarianceFigures` value object + `of()` factory -- cohesive concept or a count-dodge? | ACCEPT | All 4 fields are read together by every consumer (`csv_export_service.export_variance_csv` emits all four per row; `_variance.html` renders all four per row; `_build_variance_chart_data` reads 3/4) -- whole-value cohesion, clears A2 (not stamp coupling). `of()` (4 call sites) consolidates the `variance = actual - estimated; variance_pct = _pct(...)` compute that was 4x duplicated (B1). The abstraction would exist without the threshold (A3 -- it also fixes the `estimated`/`estimated_total`/`total_estimated` naming drift). The strongest part of the change |
| `budget_variance_service.py` (`b5a9d56`) | E1/E2/E4 (behavior-equivalence) | Does `of()` + the migrated consumers compute EXACTLY what the 4 former inline blocks did? | ACCEPT | Verified line-for-line: `of()` (`variance = actual - estimated; _pct(variance, estimated)`) is character-identical to the txn/item/group/report computes; empty-report `sum([])` -> int `0`, `of(0, 0)` -> `_pct`'s `estimated == Decimal("0")` guard returns `None` (== old `total_variance_pct`); sort keys `abs(x.figures.variance)` == old `abs(x.variance)`; CSV/template byte-identical; Decimal discipline preserved (no float). 205 targeted + 5769 full suite pass |
| `budget_variance_service.py:50-53` (`VarianceFigures` fields) | A5 | Derived `variance`/`variance_pct` stored as frozen fields rather than `@property` | ACCEPT | Consistent with the pre-refactor design (which also stored all four flat), and the sole production constructor is `of()`, so the derived fields cannot be hand-supplied inconsistently in app code. Converting to properties is an unrequested redesign (rule 13); no change |
| `test_budget_variance_service.py` / `test_csv_export_service.py` (`b5a9d56`) | G1/G2/G4 | Test quality after the DTO restructure | ACCEPT | Assertions are concrete hand-computed Decimals (arithmetic in docstrings), path-only updated with values frozen byte-identical; not over-coupled to internals (they read the public `figures` shape, which is the contract). The csv `Fake*Variance` stand-ins faithfully mirror the new nested shape via `FakeVarianceFigures`; the None-pct CSV test still drives `_pct` at all 4 levels |
| `calibration_service.py:35` (`PayStubActuals`, `4e625fe`) | A1/A2/A3/A5 | The `PayStubActuals` frozen bundle over the 6 `derive_effective_rates` inputs -- domain concept or a count-dodge bag? | ACCEPT | All 6 fields are consumed together by `derive_effective_rates` and both route callers; no consumer reads a subset (not stamp coupling, A2). The five `actual_*` already exist as a cohesive cluster in the `CalibrationOverride` model (one CHECK constraint per field). The one derived field (`taxable_income`) is the federal/state divisor, meaningless detached from the five amounts, and documented (A5). Mirrors the sibling `DerivedRates` output dataclass (symmetric input/output). The textbook public-fn param-object move ([[feedback_tm_args_param_object]]) |
| `calibration_service.py` (`4e625fe`) | E1/E2/E4 (behavior-equivalence) | Does the bundled `derive_effective_rates` compute EXACTLY what the 6-arg version did? | ACCEPT | Verified line-for-line: the six `Decimal(str(actuals.x))` coercions are character-identical to the former `Decimal(str(x))` (only re-sourced); both guards (`gross <= ZERO`, `taxable <= ZERO`) and their messages/`match=` substrings unchanged; the four `(.../base).quantize(RATE_PLACES, ROUND_HALF_UP)` computes untouched. The string-coercion path still works (the dataclass stores `str` verbatim, the function coerces). Decimal discipline preserved. 236 targeted + 5769 full suite pass |
| `calibration_service.py:35-59` (`4e625fe`) | C1/D2/F1 | `frozen=True` vs the non-frozen sibling `DerivedRates`; `Decimal` field hints vs the tested `str` input | ACCEPT | `frozen=True` is correct for an immutable pay-stub snapshot (the route frames calibration as an "immutable snapshot") and matches the codebase's param-object convention; the inconsistency is `DerivedRates` being non-frozen, which is untouched and out of scope (rule 6). The `Decimal` hints match the production contract -- `CalibrationSchema`/`CalibrationConfirmSchema` use `fields.Decimal` (validation.py:2199-2266), so production always passes Decimal; the `str` path is a test-only defensiveness check pinning the standard `Decimal(str(...))` idiom. Widening to `Decimal \| str` would misrepresent the contract; keeping `Decimal` is correct |
| `ref_cache.py:59` (`_RefState`/`enum_ids`, `ebcda36`) | A1/A3/B1 | The single `enum_ids` registry collapse -- cohesive concept or a `too-many-instance-attributes` dodge? | ACCEPT | Argued both ways: a 13-named-field dataclass WOULD trip R0902 (verified -- `ProjectedBalance`/`AmortizationRow` are dataclasses carrying that disable), so the collapse does avoid a count -- BUT it is independently the cleaner design: the 13 maps are uniform (same type/lifecycle), the registry already treats them as one, and the collapse is what ENABLES the single-loop `init()` (the actual smell-killer). Genuine design, not merely a dodge |
| `ref_cache.py:81` (`_RefSpec` + `_build_ref_specs`, `ebcda36`) | A4/B1/C2 | The registry-spec -- relocation or genuine DRY? | ACCEPT | Folds 4 hand-written per-table repetitions (load lambda / `unavailable` row / `or {}` / enum sweep) into one row per table; `label`/`error_prefix` derived (single source of truth, verified == `__tablename__`/`__name__` incl. `RoleEnum`->`UserRole`); `init()` reads top-to-bottom (load->sweep->meta), cohesion improved not scattered |
| `ref_cache.py` (`ebcda36`) | E1/E2/E4 (behavior-equivalence) | Does the single load+sweep loop compute EXACTLY what the two-phase original did? | ACCEPT | Verified EMPIRICALLY (reviewer harness, old-vs-new through all 4 edge classes against a real test DB): identical `unavailable` order/content, byte-identical `RuntimeError` text/order, unchanged DB query/rollback sequence (the sweep issues no DB work, so collapsing the two phases changes nothing), and the `enum_ids[spec.enum] = {}` pre-seed preserves KeyError-on-unavailable-accessor. Existing-but-empty table stays fatal in both |
| `ref_cache.py:271` (`_acct_type_meta` block, `ebcda36`) | B2 | Keep the meta block separate while folding the `account_types` filter into `_RefSpec.query` -- false DRY or correct? | ACCEPT | Correct: the meta block reads different columns (`icon_class`/`max_term_months`) into a different-shaped cache (`dict[int, _AcctTypeMeta]` keyed by PK, not by enum member); it changes for a different reason, so forcing it into the registry would over-couple two unrelated cache shapes. Matches the original's structure |
| `ref_cache.py:93` (`model: type`, `ebcda36`) | D2 | The loose `model: type` annotation | ACCEPT | No `type[db.Model]` convention exists in app/, and the declarative base would not statically expose per-model columns anyway; tightening it invents a one-off convention for zero checker gain (gold-plating). ACCEPT as-is -- a "fix" would be REVERT-OVERREACH |
| `test_ref_cache.py` (`d2b1c31`) | G3 | F5: the bootstrap/`unavailable` path (`_load_rows` ProgrammingError -> `None` -> `unavailable`, consumed by `app/__init__.py:192` to gate Jinja globals) had NO committed test | REFINE (separate commit `d2b1c31`) | Verified genuinely uncovered (only the missing-row fatal path + conftest healthy init existed); the refactor's `enum_ids` pre-seed made it load-bearing. Added `test_init_records_unavailable_table_and_keeps_others_usable`: forces the `loan_anchor_sources` query to raise ProgrammingError and pins all three guarantees (reported-unavailable / others-still-usable / KeyError accessor). Verified green; full suite 5769->5770 |
| `test_ref_cache.py` (`ebcda36`) | G1/G2 | Test quality after the globals -> `_cache` reshaping | ACCEPT | The existing tests exercise only the public accessors + direct DB queries -- they reference no module globals, so the internal reshaping broke nothing and over-couples to nothing; the public surface (15 accessors + `init`) is unchanged. No tautological assertions |
| `ref_seeds.py` (`32c403a`) | E4 (behavior-equivalence) | Does the 3-helper decomposition of `seed_reference_data` execute EXACTLY what the single function did? | ACCEPT | Proven by deep-AST equality: the reviewer inlined the 3 helpers back in call order (deferred import -> categories body -> orchestrator `session.flush()` -> account-types body -> other-tables body), stripped docstrings, and `ast.dump`-compared against the original single-function body -- equal including all loop interiors. The 6 risk points all hold: flush stays after categories + before AccountType inserts; `cat_lookup` queried post-flush; the 7-column existing-row refresh is unchanged; the dict/string step-3 branch is unchanged; verbose f-strings identical; DB query/add order preserved. 38 seed-dependent targeted + 5770 full suite pass |
| `ref_seeds.py` (`32c403a`) | A4/C2/A3/F1 | The 3 helpers are each single-use (one call site, the orchestrator) -- indirection-adding relocation, or genuine? | ACCEPT | Single-use and threshold-driven (A3: would not exist absent tm-locals 19/15 + tm-branches 15/12), BUT each names a genuinely distinct, independently-describable seeding step (categories insert-only / account-types upsert-with-metadata-refresh / other-tables name-only+dict) that BEFORE was demarcated only by `-- Step N --` comments; the extraction lifts the one subtle cross-step invariant (categories -> flush -> AccountType FK) to the orchestrator altitude where it cannot be missed. Honesty-first decompose, not a param-bag -- the ratified too-many-locals/branches remedy ([[feedback_tm_args_param_object]]); cohesion improved, not scattered (C2). F1 confirmed: a future migration adding a ref row is a one-line seed-tuple append with zero helper edits |
| `ref_seeds.py` (`32c403a`) | D2 | The new helper signatures + the pre-existing public `seed_reference_data` carried no type hints (the coding standard asks for hints on every signature) | REFINE (folded into `32c403a`) | The only split verdict in the panel (2 ACCEPT -- matches the file's untyped style + the `ref_cache` `model: type` / `growth_engine` untyped-dict precedent -- vs 1 REFINE). Developer chose REFINE: typed all 4 signatures (`session: Session`, `ref_models: ModuleType`, `verbose: bool`, `-> None`) via a `TYPE_CHECKING` block + `from __future__ import annotations`, so the annotations are lazy strings and ZERO runtime imports are added -- preserving the module's side-effect-free-at-import discipline (mirrors `loan_resolver/_periods.py`). Verified import-neutral (`import app.ref_seeds` loads the same module set as HEAD). Trust-but-verify catch: reviewer-1's "7 typed `session:` params in app/" was inaccurate -- grep found ZERO `session: Session` annotations, so this is the first; it does not change the outcome |
| `routes/settings.py` (`1d52d3f`) | E1/E4 (behavior-equivalence) | Does the per-section dispatch render BYTE-IDENTICAL template context, and the `update` loop apply identical field writes? | ACCEPT | All 3 reviewers verified `behavior_equivalent=yes`. GET path: the before/after render-kwarg sets are identical for all 8 sections (the 19-key union set-difference is empty both directions); `_empty_section_context()` enumerates all 19 defaults (incl. `types_in_use=set()`), each loader overrides only its slice with the same queries/order_by as the original inline blocks. POST path: `_SIMPLE_SETTINGS_FIELDS` lists the exact 6 fields in original order, the loop guard `field in data and data[field] is not None` is identical, the `default_grid_account_id` IDOR branch (get/user_id==current/is_active + "Invalid grid account." flash+redirect) is byte-for-byte, and all 3 flash/redirect paths are preserved. `_render_companions_section` via `_empty_section_context()`+update yields exactly the old 19-key set. 91 targeted + 5770 full suite pass |
| `routes/settings.py` (`1d52d3f`) | Rule-6 SCOPE / B1 | The refactor removed `_empty_companions_context` and rerouted `_render_companions_section` (a function with NO smell) through `_empty_section_context` -- creep, or justified? | ACCEPT | All 3 reviewers ruled JUSTIFIED DRY, not creep. The empty-defaults contract was TRIPLICATED before (show() inline 19 kwargs, `_empty_companions_context`'s 4 companion keys, and `_render_companions_section`'s 19 hand-listed kwargs); `_empty_section_context()` is the single home the `show` tm-locals smell forced, and leaving the other two copies would re-admit the exact drift the smell exposed. The reroute is behavior-identical (key-by-key parity verified). Endorsed for inclusion |
| `routes/settings.py` (`1d52d3f`) | A4/C2 | The 4 new section loaders are each single-use; `_load_security_context` is only 5 lines, and `general` stays inline -- ceremony or genuine? | ACCEPT | A4 allows a single-use helper that genuinely clarifies one site. Each loader lifts a 15-25-line query block out of a 130-line if/elif into a named, independently-testable, one-reason-to-change unit (C2 cohesion improved; the loan.py/grid.py precedent). `_load_security_context` (2 statements) is the weakest A4 case but is kept for dispatch uniformity (one-loader-per-section); `general` stays inline because wrapping a single `list_active_accounts()` call would be pure ceremony -- the asymmetry is correct, not a defect. F1: a new section = new loader + 4 default keys; a new scalar setting = one tuple entry |
| `routes/settings.py` (`1d52d3f`) | D2/E1 | The static icon list was lifted to a module constant but as a `list` passed by reference into the render context (was rebuilt per request) | REFINE (folded into `1d52d3f`) | LOW, rated "no change required" by the reviewer but applied: changed `_ACCOUNT_TYPE_ICON_CHOICES` from a `list` to a `tuple`. The data is immutable static config and the only consumer is `{% for icon in icon_choices %}` (verified -- no test asserts its type/equality), so the tuple is behavior-identical, signals immutability precisely (D2), and removes the shared-mutable-by-reference aliasing concern entirely. Distinct from the `ref_cache` `model: type` ACCEPT precedent -- a tuple-for-constant is standard Python, not an invented convention |
| `routes/obligations.py` (`7a77db9`) | E1/E4 (behavior-equivalence) | Does the `_next_occurrence` single-return accumulator + the hoisted `day`/`month` produce the SAME date/None as the old per-pattern early-returns? | ACCEPT | All 3 reviewers walked each branch vs `git show HEAD:`: every_period/every_n -> `period.start_date`-or-None (PayPeriod query unchanged); monthly/monthly_first -> `_next_monthly(today, day)` with `day=day_of_month or 1`; quarterly/semi_annual -> `_next_periodic_month(today, month, day, 3|6)` where the old per-branch `start_month` maps to the hoisted `month=month_of_year or 1`; annual -> `_next_annual(today, month, day)`; `else -> None` matches the old trailing `return None`. The early end_date guard stays `return None`. The hoist is safe: both are pure `attr or 1` reads, unused by the period/unknown branches. The date-math helpers (`_next_monthly` clamp+rollover, `_next_annual`, `_next_periodic_month`) were untouched. 18 targeted + 5770 full suite pass |
| `routes/obligations.py` (`7a77db9`) | B1/E (behavior-equivalence: summary) | Do the 3 loaders + `_build_items` reproduce the exact queries and preserve the E-24/HIGH-05 inclusion-vs-subtotal invariant? | ACCEPT | The loaders reproduce the verbatim queries (joinedload sets, filter predicates incl. the `transaction_type_id` equality -- transfer correctly has none -- and `order_by(sort_order, name)`); the moved type-id resolution is pure/cached so timing is immaterial. `_build_items` gates rows on `template_monthly_or_none(...) is not None`, the identical predicate, and `committed_monthly` sums through the same aggregator -- so a row is rendered iff it contributes to its subtotal (invariant preserved, verified via `test_expired_templates_excluded` which pins both the missing row AND the absent $3,250 subtotal contribution). All 10 render kwargs + the 5 totals/net/has_any unchanged |
| `routes/obligations.py` (`7a77db9`) | A4/D2 | The constrained `TypeVar` on `_build_items`, and the 3 single-use loaders -- correct precision / genuine, or overkill / ceremony? | ACCEPT | TypeVar: `_TemplateT in (TransactionTemplate, TransferTemplate)` with `renderer: Callable[[_TemplateT, Decimal], dict]` is the minimal way to bind each call site's template type to its renderer's parameter type -- a plain `Union` would wrongly allow an expense renderer to see a transfer template (contravariance), so it is correct precision not overkill (2 reviewers concurred). Loaders: each owns one genuinely-distinct query (different model/joins/filters), lifting a 15-25-line block out of the route -- the loan.py loader precedent; A4's "genuinely clarifying one site" arm, and `_build_items` is called >=2 sites (B1 real dedup, the 3 loops were identical modulo renderer). No false-DRY, no gold-plating; F1: a 4th section or new recurrence pattern slots in cleanly |
| `routes/obligations.py` (`7a77db9`) | E3 | Hoisting `day`/`month` above the dispatch computes them even for branches (every_period, unknown) that discard them -- tiny waste? | ACCEPT | LOW. Two pure `getattr or 1` reads per call regardless of branch; negligible cost, and the hoist removes the per-branch duplication of the same two `or 1` defaults (clarity win). The alternative (computing inside each branch) would re-duplicate them across 3 branches. Accepted as-is |
| `routes/accounts/detail.py` (`c5182ea`) | E1/E2/E4 (behavior-equivalence) + F-6 guard | Do the 3 helpers + the horizon-util reuse produce byte-identical behavior in both handlers, and does the F-6 source guard still hold? | ACCEPT | All 3 reviewers verified vs `git show HEAD`: `_current_period_balance` char-identical to both old inline `current_bal` blocks; `_build_period_data` carries period/balance/interest+`Decimal("0.00")` only when `interest_by_period` is supplied (interest page) and omits it for checking; `_load_account_transactions` query verbatim with `if not scenario or not period_ids: return []` the exact De Morgan of the old `(...) if scenario and period_ids else []`, disable moved with it; `project_balance_horizons` == the old inline horizon loop (HORIZON_OFFSETS 6/13/26, same match+break; the util's upfront `is None` guard == the old per-iteration `if current_period:` because PayPeriod declares no `__bool__`/`__len__`); both render kwarg blocks byte-identical (diff exit 0). F-6: `balance_resolver.balances_for` present (line 323), bare `balance_calculator.calculate_balances(` count 0 (`calculate_balances_with_interest(` has `_` after, doesn't match), `selectinload(entries)` preserved |
| `routes/accounts/detail.py` (`c5182ea`) | A1/B1 (DRY) / B2 (false-DRY check) | Is the checking_detail horizon dedup true-DRY, and `_build_period_data`'s optional-interest shape sound? | ACCEPT | The inline horizon loop was a VERBATIM copy of `project_balance_horizons` (already imported, used by `interest_detail` + the savings dashboard `_projections.py:374`) -- all 3 sites compute the same 3/6/12-month horizon by the same rule and change together, so it is true-DRY not B2 false-DRY. `_build_period_data`'s single optional `interest_by_period` kwarg is the right altitude for exactly two consumers/one optional column -- a generic "extra columns" mechanism would be gold-plating (rule 13), and a future 3rd column is a one-line change (F1) |
| `routes/accounts/detail.py` (`c5182ea`) | D2/F3 (cross-file naming collision) | `_resolve_current_balance` collided with a semantically-different same-named private helper in `investment_dashboard_service.py:149` | REFINE (applied in `c5182ea`) | MED, flagged by reviewer 2 (reviewer 3 ACCEPTed it as "different altitudes"); verified the collision is real (also a near-twin `_resolve_current_balances` in `retirement_dashboard_service.py:787`). Both are private so no import collision, but a maintainer grepping the name gets two different contracts. Renamed mine -> `_current_period_balance`, which also reads more accurately (it picks the current period's balance from an already-built map, it does not resolve-by-query like the investment one). Not a merge -- the two are genuinely different altitudes (picker vs producer) |
| `routes/accounts/detail.py` (`c5182ea`) | D2 (type hints on every signature) | The 3 new helpers had no type hints | REFINE (applied in `c5182ea`) | LOW. Consistent with the ref_seeds precedent (developer chose to add hints) and the obligations going-forward convention (new private helpers typed). Added `balances: dict[int, Decimal]`, `interest_by_period: dict[int, Decimal] | None = None`, `account: Account`, `scenario: Scenario | None`, `all_periods: list[PayPeriod]`, `current_period: PayPeriod | None`, `anchor: AnchorPoint | None`, and `-> Decimal | None` / `list[dict]` / `list[Transaction]`. Used `from __future__ import annotations` + a TYPE_CHECKING block (PayPeriod/Scenario/AnchorPoint are typing-only -- zero runtime imports, matching `loan_resolver/_periods.py` and `ref_seeds`). `list[dict]` kept for the template-row return per the `_render_*` convention (a TypedDict would be gold-plating) |

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
