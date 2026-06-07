# Pylint 10/10 Cleanup -- Master Plan and Progress Tracker

> **Definition of done has TWO gates.** This tracker is the mechanical floor. A
> file is not DONE here until it has also passed the design-quality rubric in
> [`quality-pass.md`](quality-pass.md): an independent reviewer arguing both
> "simpler?" and "right abstraction for the next feature?", findings triaged to
> ACCEPT/REFINE/REVERT-OVERREACH with `file:line` citations and recorded in that
> doc's register, fixes shipped as their own commit with the full suite as the
> gate. Verify every finding against the code before applying. The auto-loaded
> `.claude/rules/pylint-cleanup.md` carries the short form.

**Status: Phases 0-2 DONE; Phase 3 IN PROGRESS. As of 2026-06-06 app/ is 9.92/10 with ZERO
`duplicate-code` (R0801) clusters, zero `useless-suppression`, zero E/F; 105 visible messages.
Full suite 5771 passed (new baseline -- +1 the Q4-rollover test below).** Phase 3 (design smells) has
THIRTY-ONE files plus the form-mutation helper family complete. The newest,
**`services/interest_projection.py` DONE** (`62fd7a2`), cleared `calculate_interest`'s `too-many-locals`
(17/15) by extracting the quarterly branch's dense quarter-length arithmetic into
`_days_in_quarter(period_start)` (parallels the existing `_days_in_year_for_window` divisor helper) --
17->13 locals, no disable. FINANCIAL: byte-identical (only `days_in_quarter =` -> `return`), the
quarterly formula + daily/monthly branches + guards + round_money untouched. The reviewer ruled the
full-3-branch-dispatcher alternative REVERT-OVERREACH gold-plating (the math is deliberately asymmetric;
co-locating the formulas has review value), and flagged a pre-existing gap the extraction made
load-bearing -- the Q4 `next_q_month > 12` year-rollover branch was untested -- so this commit ALSO adds
`test_q4_year_rollover_period` (Q4 2026 = 92 days; interest 17.12 hand-computed independently) + fixes a
stale `/91` test docstring. Independent quality-pass: behavior_equivalent=yes (byte-for-byte), all
ACCEPT, 0 REFINE, 0 REVERT-OVERREACH. 0 disables (82); 0 new R0801; visible 106->105; smell items 17->16
(11 8-symbol + 5 instance-attr); score 9.92 held; full suite 5770->5771 passed. Before it,
**`services/savings_goal_service.py` DONE** (`7dad8d7`), cleared
`amount_to_monthly`'s `too-many-return-statements` (8/6) by a single-return accumulator -- the explicit
`once` early `return None` kept, then an if/elif assigns `monthly = <expr>` per pattern (`else =`
None for unrecognized ids), one final `return monthly`; 8->2 returns, no disable. A financial function:
every per-pattern Decimal expression preserved byte-identically (division order intact), no
quantization added. Independent quality-pass: behavior_equivalent=yes (byte-for-byte verified), all
ACCEPT, 0 REVERT-OVERREACH (accumulator-over-dispatch-dict upheld; `once` kept explicit). 0 disables
(82); 0 new R0801; visible 107->106; smell items 18->17 (12 8-symbol + 5 instance-attr); score 9.92
held; full suite 5770 passed. Before it, **`app/__init__.py` DONE** (`e22a1a5`), cleared
`_register_blueprints`'s `too-many-locals` (24/15) at the root by replacing the 23 explicit deferred
`from app.routes.X import X_bp` imports + 23 register calls with a data-driven loop over the new
`_BLUEPRINT_MODULES` tuple (the 23 module names, canonical order), registering
`getattr(module, f"{name}_bp")` after an `importlib.import_module` -- 24->3 locals. Because no import
statements remain in the function, the now-useless `import-outside-toplevel` disable was REMOVED
(disables **83->82**). Behavior bit-identical (verified 4 ways: same 23 modules in the same order; all
`getattr` resolve to the same Blueprint objects incl. the 5 package + 3 multiword names; a full
`create_app` build registers all 23 in order, 166 URL rules). The `<name>_bp` convention is total
across `app/routes` and fails LOUD (AttributeError/ModuleNotFoundError at startup) on violation.
Independent quality-pass: behavior_equivalent=yes, all ACCEPT, 0 REVERT-OVERREACH (the
data-driven-loop-vs-explicit+disable fork argued both ways -- the loop ruled correct: DRY win + disable
eliminated, the lone cost being greppability of individual `_bp` registrations, mitigated by the
documented convention). 0 disables added; visible 108->107; smell items 19->18 (13 8-symbol + 5
instance-attr); score 9.92 held; full suite 5770 passed. Before it, **`app/routes/entries.py` DONE**
(`6e3c32d`), cleared `update_entry`'s
`too-many-return-statements` (8/6) by extracting the service-call + commit + 4-way error-translation
tail into the private `_execute_entry_update(entry_id, txn, data)` (the `transfers._execute_transfer_update`
precedent) -- `update_entry` 8->5 returns, the helper 4; no disable. Helper body byte-identical to the
replaced block (left untyped to match the sibling untyped response helpers). Independent quality-pass:
behavior_equivalent=yes (byte-verified), all ACCEPT, 0 REFINE, 0 REVERT-OVERREACH; two deferred tracker
notes (a coordinated `flask.typing.ResponseReturnValue` typing pass for entries' private-helper cluster;
the 7-line ownership preamble shared by update_entry/toggle_cleared/delete_entry -- a separate DRY
refactor). 0 disables (83); 0 new R0801; visible 109->108; smell items 20->19 (14 8-symbol + 5
instance-attr); score 9.92 held; full suite 5770 passed. Before it,
**`app/routes/accounts/anchor.py` DONE** (`ab16669`), cleared `true_up`'s
`too-many-return-statements` (7/6) by merging the two success returns (DUPLICATE_SAME_DAY + COMMITTED
converge on one OOB-success build via an if/else that sets `account` -- re-fetch after the service's
rollback vs refresh+log -- then a single return; 7->6) and extracting the 2-site `_anchor_conflict_response`
helper (the 409 conflict render shared by the form-version guard + the STALE_CONFLICT outcome); no
disable. Behavior bit-identical (verified against `anchor_service`'s rollback contract -- the
re-fetch/refresh asymmetry + COMMITTED-only log preserved; the 6 returns map 1:1 onto 6 distinct HTTP
outcomes). Independent quality-pass: behavior_equivalent=yes, all ACCEPT, 0 REFINE, 0 REVERT-OVERREACH
(one PRE-EXISTING out-of-scope test gap noted -- `test_double_submit` does not assert the DUPLICATE
render shows the committed balance). 0 disables (83); 0 new R0801; visible 110->109; smell items 21->20
(15 8-symbol + 5 instance-attr); score 9.92 held; full suite 5770 passed. Before it,
**`app/routes/categories.py` DONE** (`5b32148`), cleared
`create_category`'s `too-many-return-statements` (7/6) by extracting the dual HTMX-or-flash error
response (byte-identical at the schema-validation + blank-name guards) into the typed
`_create_form_error_response` helper -- genuine 2-site DRY, returns 7->5, no disable. The duplicate +
success paths and the create-error-path test coverage are unchanged; `edit_category` correctly NOT
folded in (no HTMX-jsonify branch -- a different contract, rule 13). Independent quality-pass:
behavior_equivalent=yes, 6 ACCEPT, 0 REFINE, 0 REVERT-OVERREACH. 0 disables (83); 0 new R0801; visible
111->110; smell items 22->21 (16 8-symbol + 5 instance-attr); score 9.92 held; full suite 5770 passed.
This begins the **low-fork batch** (developer direction 2026-06-06: clear the function-decomposition +
tm-args-param-object files first, then consult on the package splits, instance-attr dispositions, and
the 2 criticals). Before it, **`app/routes/accounts/detail.py` DONE** (`c5182ea`), cleared both detail
handlers' `too-many-locals` (`interest_detail` 16/15, `checking_detail` 17/15) by genuine
decomposition (no disable): 3 cohesive private helpers (`_current_period_balance` + `_build_period_data`
are 2-site DRY across both handlers; `_load_account_transactions` encapsulates the disabled per-account
query + computes `period_ids` internally), plus replacing `checking_detail`'s inline 3/6/12-month
horizon loop with the already-imported `project_balance_horizons` (a Phase-2-missed verbatim DRY also
used by `interest_detail` and the savings dashboard). Behavior bit-identical (every helper
char-identical to what it replaced; the F-6 static guard held -- `balance_resolver.balances_for`
present, bare `calculate_balances(` absent; `selectinload(entries)` preserved so the entries-aware
E-25/CRIT-01/F-009 reduction still applies). Independent 3-lens quality-pass: all
`behavior_equivalent=yes`, 0 REVERT-OVERREACH; 2 REFINEs applied -- renamed `_resolve_current_balance`
-> `_current_period_balance` (a cross-file name collision with the semantically-different
`investment_dashboard_service._resolve_current_balance`) and typed all 3 helpers (`from __future__`
annotations + a TYPE_CHECKING block for PayPeriod/Scenario/AnchorPoint, zero runtime imports). 0
disables (83); 0 new R0801; visible 113->111; smell items 24->22 (17 8-symbol + 5 instance-attr); score
9.92 held; full suite 5770 passed. Before it, **`app/routes/obligations.py` DONE** (`7a77db9`), cleared
`_next_occurrence`'s `too-many-return-statements` (7/6) by a single-return accumulator (if/elif
assigning `next_date`; the early end_date guard kept -- the `recurrence_engine.match_periods`
precedent) and `summary`'s `too-many-locals` (18/15) by extracting 3 query loaders
(`_load_recurring_expenses`/`_load_recurring_income`/`_load_recurring_transfers`) + the deduplicated
`_build_items(templates, renderer, as_of)` (the 3 build loops differed only in the renderer; generic
over a constrained TypeVar) -- NO disable. Behavior bit-identical (every pattern branch incl. the
unknown->None fallthrough, the E-24/HIGH-05 row-iff-subtotal invariant, and all 10 render kwargs
preserved). Independent 3-lens quality-pass: all `behavior_equivalent=yes`, 12 findings all ACCEPT, 0
REVERT-OVERREACH, 0 REFINE (the constrained TypeVar ruled correct-not-overkill; the 3 single-use
loaders genuine query-extraction). 0 disables (83); 0 new R0801; visible 115->113; smell items 26->24
(19 8-symbol + 5 instance-attr); score 9.92 held; full suite 5770 passed. Before it,
**`app/routes/settings.py` DONE** (`1d52d3f`), cleared `show`'s
`too-many-locals` (21/15) + `update`'s `too-many-branches` (13/12) by genuine decomposition (no
disable): `show`'s ~19 parallel template-variable locals collapse into a single
`_empty_section_context()` default dict that per-section loaders (`_load_categories_context` /
`_load_tax_context` / `_load_account_types_context` / `_load_security_context`; the existing
`_load_companions_context` reused; trivial `general` stays inline) override via `context.update(...)`
-- the ratified `routes/loan` dashboard per-section-builder precedent -- and `update`'s six identical
`field in data and ... is not None` copies collapse into an allowlist loop over the new
`_SIMPLE_SETTINGS_FIELDS` (the IDOR-checked `default_grid_account_id` branch stays inline). The
`_empty_section_context()` helper ALSO dissolved the empty-defaults contract that was triplicated
(`show` inline + `_empty_companions_context` + `_render_companions_section`'s hand-listed kwargs):
`_empty_companions_context` removed, `_render_companions_section` routed through the shared helper --
a genuine DRY win the quality-pass ruled justified, not scope creep. Independent 3-lens quality-pass:
all `behavior_equivalent=yes` (all 8 sections' 19 render kwargs byte-identical per set-diff; the
`update` loop + IDOR branch + 3 flash/redirect paths unchanged), 0 REVERT-OVERREACH, 0 REFINE; the
lone LOW D2 note (the lifted icon list as a list) folded in as the immutable tuple
`_ACCOUNT_TYPE_ICON_CHOICES`. 0 disables (83); 0 new R0801; visible 117->115; smell items 28->26 (21
8-symbol + 5 instance-attr); score 9.91->9.92; full suite 5770 passed. Before it,
**`app/ref_seeds.py` DONE** (`32c403a`), cleared `seed_reference_data`'s
`too-many-locals` (19/15) + `too-many-branches` (15/12) by genuine decomposition into a thin
orchestrator + three cohesive single-responsibility step helpers
(`_seed_account_type_categories`/`_seed_account_types`/`_seed_other_ref_tables`) -- NO disable --
lifting the one load-bearing cross-step invariant (the flush that makes the category PKs visible to
the AccountType FK) to the orchestrator altitude where it cannot be missed, and threading
`ref_models` into each helper so the deferred `app.models.ref` import stays in one place
(side-effect-free-at-import discipline preserved). All 4 signatures typed via a `TYPE_CHECKING` block
+ `from __future__ import annotations` (lazy-string annotations, ZERO runtime imports added -- the
developer-chosen resolution of the quality-pass D2 finding; mirrors `loan_resolver/_periods.py`).
Independent 3-lens quality-pass (behavior-equivalence / simplicity / right-abstraction): all
`behavior_equivalent=yes` -- one reviewer proved deep-AST equality of the inlined-after body vs the
original -- 0 REVERT-OVERREACH; the lone LOW D2 type-hint finding resolved by adding the hints.
Public `seed_reference_data(session, *, verbose=False)` + 3 script/app call sites unchanged. 0
disables (83); 0 new R0801; visible 119->117; smell items 30->28 (23 8-symbol + 5 instance-attr);
score 9.91 held; full suite 5770 passed. Before it, **`app/ref_cache.py` DONE** (`ebcda36`), cleared
all 3 design smells on
`init` (`too-many-locals` 31/15, `too-many-branches` 51/12, `too-many-statements` 124/50) AND removed
the 5 `global`-statement disables + a 15x-duplicated init-guard -- the developer-chosen from-scratch
best design (C'-dict). The 14 module globals (13 maps + `_initialized`) became one never-rebound
`_RefState` (`_cache`) whose 13 maps collapse into a single `enum_ids` registry keyed by enum class,
which keeps the dataclass under `too-many-instance-attributes` with NO disable (the named-fields
alternative would have needed one -- verified that the `ProjectedBalance`/`AmortizationRow` dataclasses
carry exactly that disable). A frozen `_RefSpec` (derived `label`==`__tablename__` /
`error_prefix`==`__name__`; a `query` method carrying the `account_types` built-in-only filter) +
`_build_ref_specs` drive a single load/sweep loop; a `_require_init()` helper replaces the 15x guard.
`init()` mutates `_cache` in place, so ZERO `global` statements remain; the file is 10.00/10 with only
the unavoidable circular-import `import-outside-toplevel` (KEEP). Behavior byte-identical: the public
free-function accessor API, the `unavailable` return contract (consumed by `app/__init__.py:192` to
gate Jinja globals), and the missing-row `RuntimeError` text/order are unchanged (error prefixes/labels
derive from the model, verified equal for all 12 incl. `RoleEnum`->`UserRole`; the single load+sweep
loop preserves the DB query/rollback order). Independent quality-pass (fresh subagent, A-G rubric):
ACCEPT, 0 REVERT-OVERREACH, behavior verified BYTE-IDENTICAL empirically (the reviewer built a harness
running old-vs-new through every edge case); 1 REFINE applied -- F5, a hand-pinned regression test for
the previously-untested bootstrap/`unavailable` path (`test(ref-cache)` `d2b1c31`). 88->83 disables
(the 5 `global` removed); 0 new R0801; visible 123->119; smell items 33->30 (25 8-symbol + 5
instance-attr); score 9.91 held; full suite 5769->5770 passed. Before it,
**`services/calibration_service.py` DONE** (`4e625fe`), cleared all 3
design smells on `derive_effective_rates` (`too-many-arguments` 6/5, `too-many-positional-arguments`
6/5, `too-many-locals` 16/15) by bundling its 6 inputs into a new frozen `PayStubActuals` value object
-- the cohesive pay-stub snapshot both callers co-load (the five `actual_*` mirror the
`CalibrationOverride` columns; `taxable_income` is the route-computed federal/state divisor) -- so the
public entry point takes ONE arg (~11 locals); NO disable, behavior bit-identical (the
`Decimal(str(...))` construct-from-strings coercion preserved verbatim). The 2 production callers
(`salary/calibration.py` preview + confirm) + 13 test call sites rewrapped in `PayStubActuals(...)`,
values frozen byte-identical. Independent quality-pass (fresh subagent, A-G rubric, behavior verified
line-for-line incl. the string-coercion path + both `ValidationError` guards): ACCEPT, 0
REVERT-OVERREACH, 0 REFINE (3 findings, all ACCEPT after verification: `PayStubActuals` clears the
cohesive-named-concept bar A1/A2 -- all 6 fields consumed together by both routes + the function, no
stamp coupling; the `Decimal` field hints match the production contract -- `CalibrationSchema` /
`CalibrationConfirmSchema` use `fields.Decimal`, so the string path is test-only defensiveness;
`frozen=True` upheld, the non-frozen sibling `DerivedRates` left untouched as out-of-scope, rule 6).
0 disables (88); 0 new R0801; visible 126->123; smell items 36->33 (28 8-symbol + 5 instance-attr);
score 9.91 held; full suite 5769 passed. Before it, **`services/budget_variance_service.py` DONE** (`b5a9d56`), cleared all 3
`too-many-instance-attributes` smells (`TransactionVariance` 8/7, `CategoryItemVariance` 9/7,
`VarianceReport` 8/7) by the developer-chosen extraction of a frozen `VarianceFigures(estimated,
actual, variance, variance_pct)` value object + an `of(estimated, actual)` factory -- NO disables. The
factory is the single home for the `variance = actual - estimated; variance_pct = _pct(variance,
estimated)` computation that was hand-written 4x (txn/item/group/report-total -- R0801-invisible
because the locals differed at each level); the 4 DTOs now each hold ONE `figures` field, which ALSO
dissolves the `estimated`/`estimated_total`/`total_estimated` naming drift. All consumers migrated to
`.figures.*` (the `analytics._build_variance_chart_data` chart builder,
`csv_export_service.export_variance_csv`, the `_variance.html` template); tests are path-only with
expected values frozen byte-identical (the csv test's 4 `Fake*Variance` stand-ins gained a nested
`FakeVarianceFigures`; the `test_services_no_flask` scanner-pin docstring's cited expression was
repointed). Independent quality-pass (fresh subagent, A-G rubric, all 5 behavior-equivalence points
verified line-for-line incl. the empty-report `sum()` -> int-`0` path through `_pct`'s `== Decimal("0")`
zero-guard and the None-pct CSV path): ACCEPT, 0 REVERT-OVERREACH, 0 REFINE (lone nit -- derived
`variance`/`variance_pct` stored vs `@property` -- accepted: matches the pre-refactor design and is
gated behind the sole `of()` constructor). 0 disables (88); 0 new R0801; visible 129->126; smell items
39->36 (31 8-symbol + 8->5 instance-attr); score 9.91 held; full suite 5769 passed. Before it,
**`services/investment_projection.py` (+ its `projection_inputs.py`
wrapper) DONE** (`bf111f0`), cleared all design smells on the coupled `calculate_investment_inputs`
(tm-args/pos/locals) and its pure pass-through wrapper `build_investment_projection_inputs`
(tm-args/pos). The dead `account_id` param -- forwarded by the wrapper, ignored by the callee --
was removed at root from both signatures + all 5 production call sites + 18 test calls (clears
`unused-argument`). `calculate_investment_inputs`'s 5 steps decomposed into
`_periodic_from_deductions` / `_average_transfer_contribution` / `_employer_params` /
`_ytd_contributions` (clears tm-locals). The residual 6 independent, heterogeneous inputs (1 over
max; verified all 5 consumers vary `all_periods`/`current_period` independently, so no cohesive
sub-bundle exists and a param object would be stamp coupling) take a documented
scoped+named+commented `too-many-arguments,too-many-positional-arguments` disable on BOTH public
functions, mirroring the sibling `growth_engine.project_balance`. Overlapping Phase-4 residue cleared
in the same lines (missing `salary_gross_biweekly` param-doc, 3 long `employer_params` lines, the
stale "Lazy import" comment). Independent quality-pass (fresh subagent, A-G rubric, all 5 steps
verified line-for-line incl. the De Morgan employer-guard rewrite and the `if current_period:` ->
`is None` equivalence): ACCEPT, 0 REVERT-OVERREACH, 0 REFINE (the lone watch-item a PRE-EXISTING,
untouched negative-deduction `# BUG` test comment, reported as P-3). +2 documented disables (86->88);
0 new R0801; visible 139->129; smell items 44->39 (31 8-symbol + 8 instance-attr); score 9.90->9.91;
full suite 5769 passed. Before it, **`routes/debt_strategy.py` DONE** (`8449f21`), cleared all 3 design
smells on the `calculate` route handler (tm-locals 17/15 + tm-return 7/6 + tm-branches) by genuine
decomposition (no disables, behavior bit-identical; full suite 5766 the gate). The 5 duplicated
`_results.html` error renders (schema reject / malformed custom order / no debts / a simulation
`ValueError`) now funnel through a new private `_ResultsError` + a SINGLE try/except in the handler
-- the DRY collapse of one error contract -- leaving 3 returns; the IDOR 404 stays a direct return
(distinct HTTP contract, NOT funneled). Extracted `_parse_calculate_form` + the IDOR set-check
`_custom_order_has_unknown_account` + `_compute_strategies` (-> the frozen `_StrategyResults`(4)
bundle, both distinct log labels preserved) + `_select_result`; `_build_comparison` retargeted to the
bundle. Grep-verified and removed 6 render kwargs the template never reads
(baseline/avalanche/snowball/custom_result/extra_monthly/debt_accounts). Independent quality-pass
(fresh subagent, A-G rubric, all 9 behavior-equivalence points verified line-for-line): ACCEPT, 0
REVERT-OVERREACH, 0 REFINE; the lone MED finding -- a pre-existing route-level test gap on the
REACHABLE compute-error funnel (a duplicate/incomplete `custom_order` the route does not dedupe) +
the custom-vs-avalanche selection -- closed in a separate test commit (`9efb7b4`, +3 route tests; the
baseline/avalanche/snowball except is route-unreachable so NOT mocked, rule 13). 0 disables added
(86); 0 new R0801; visible 142->139; smell items 47->44 (36 8-symbol + 8 instance-attr); score 9.90
held; full suite 5766 passed. Before it, **`services/retirement_gap_calculator.py` DONE** (`2b0f5ca`), cleared all
4 design smells (`calculate_gap` tm-args/pos/locals; `RetirementGapAnalysis` tm-instance-attributes) by
a developer-chosen root-cause REMOVAL of the dead `planned_retirement_date`. Verification showed the
result field is write-only (read by no production consumer -- not `_gap_analysis.html`, not
`_build_chart_data`, nowhere in app/), so dropping both the param (6->5 args clears tm-args +
tm-positional at the pylint default max=5, NO disable) AND the `RetirementGapAnalysis` field (11->10
attrs) is the DRY fix, not relocating the write (developer chose full removal over the plan's ratified
relocate-option once the dead-field finding surfaced; keyword-only proved unnecessary). `calculate_gap`
tm-locals (19->14) via the pure `_after_tax_projected_savings` (trad/Roth bucketing + whole-expression
quantize -- the load-bearing extraction) + `_sum_projected_balances` (orchestrator-altitude symmetry,
not threshold-necessary). `RetirementGapAnalysis`(10/7) documented scoped disable (flat row-per-field
aggregate; `AmortizationRow`/`PayoffRequest`/`ProjectedBalance` precedent). Single keyword caller + 27
test calls updated; deleted `test_planned_retirement_date_passed_through` (rule-5 exception);
`test_result_field_completeness` 11->10. Independent quality-pass (fresh subagent, A-G rubric, all 4
behavior-equivalence points verified line-for-line incl. the whole-expression quantize order): ACCEPT,
0 REVERT-OVERREACH, 0 REFINE (lone LOW note on `_sum_projected_balances` accepted; the reviewer's
threshold rationale corrected -- inlining the sum alone leaves 15, which passes). +1 documented disable
(85->86); 0 new R0801; visible 146->142; smell items 51->47 (39 8-symbol + 8 instance-attr); score
9.89->9.90; full suite 5766 passed. Before it, **`services/loan_resolver.py` DONE** (`41f42a8`; two-phase in one
commit, developer-chosen), cleared all 4 design smells (`resolve_loan` tm-locals;
`compute_payoff_scenarios` tm-args + tm-locals; `PayoffScenarios` tm-instance-attributes) by the
developer-chosen **`LoanInputs` bundle + package split**. `LoanInputs(loan_params, anchor_events,
payments, rate_changes)` -- the data clump EVERY caller co-loads (three separate loads per site) --
is shared by `resolve_loan` (5->2 args, clearing its tm-locals 16->~10) and
`compute_payoff_scenarios` (6->3 args); `compute_monthly_payment_baseline` was left untouched (its
`unused-argument` disable ties to OPEN problem P-1). The shared `_replay_from_anchor` dedupes the
anchor-select+replay both use -- replay ONLY, never `project_forward`, so the resolver's documented
"balance derived independently of projection" invariant holds structurally (genuine 2-site DRY).
`compute_payoff_scenarios` tm-locals (26->~13) via `_build_forward_inputs` -> the frozen
`_ProjectionPrep`(3) setup bundle, leaving a thin "project three ways, then summarize" orchestrator
(summary kept inline). `PayoffScenarios`(10/7) -> documented scoped disable (cohesive single-return
result aggregate; `PayoffRequest`/`AmortizationRow` precedent). The decomposition pushed the module
past 1000 lines, so it was split into the `app/services/loan_resolver/` package (decision #5):
`_periods` (rate periods/anchor/replay + `LoanInputs`) / `_state` (`LoanState` + `resolve_loan` +
`compute_monthly_payment_baseline`) / `_payoff` (`PayoffScenarios` + composer); `__init__` re-exports
the public API so every import path is preserved; **0 new R0801** (no split-trap). ~52 call sites
wrapped in `LoanInputs(...)` across 8 files (values frozen byte-identical); 3 test
source-inspection guards repointed to scan the package dir via `_loan_resolver_package_source()`;
the C15-3 demoted-column allow-list `services/loan_resolver.py:` -> `services/loan_resolver/`.
Independent quality-pass review (fresh subagent, A-G rubric, all 6 behavior-equivalence points
verified line-for-line): ACCEPT, 0 REVERT-OVERREACH, 0 REFINE (F8 the lone LOW note -- the
composer-only `inspect.getsource(compute_payoff_scenarios)` purity guard's reach narrowed by the
`_build_forward_inputs` extraction, but the package-wide purity guard covers `_payoff.py` in full,
so no gap, no change). +1 documented disable (84->85); package 10.00/10; score 9.89 held; visible
150->146; smell items 55->51 (42 8-symbol + 9 instance-attr); full suite 5767 passed. Before it,
**`services/growth_engine.py` DONE** (`dcf0d4e`), cleared all 4 design
smells (`project_balance` tm-locals/args/positional; `ProjectedBalance` tm-instance-attributes).
tm-locals by genuine decomposition mirroring `amortization_engine`: a frozen `_PeriodInputs` (the
loop's fixed constants) + a mutable `_ProjectionState` (the evolving balance/YTD/limit/year carry) +
`_project_one_period`, leaving `project_balance` a ~14-local orchestrator; the byte-identical
period-day->compound-rate math shared by the forward + reverse projections extracted to the shared
`_period_return_rate` -- a genuine 2-site DRY win that makes the forward/reverse can't-diverge
invariant structural. The `project_balance` args (8/5) and the `ProjectedBalance` 9-attr DTO were the
developer-chosen documented scoped+named+commented disables: the pure-leaf engine's 8 inputs vary
independently per caller (the what-if overlay overrides `periodic_contribution` + nulls
`contributions`, year-end forces ytd=0 -- a param object would be stamp coupling; reusing
`InvestmentInputs` would cycle since it imports `growth_engine`; all callers pass keyword so
tm-positional is moot), and `ProjectedBalance` is a cohesive per-period schedule row mirroring
`AmortizationRow` (`is_confirmed` = the deliberately-plumbed confirmed/projected distinction). The
independent quality-pass review (fresh subagent, A-G rubric, all 6 behavior-equivalence points
verified line-for-line against HEAD): ACCEPT overall, both disables upheld, 0 REVERT-OVERREACH; 2 LOW
REFINE folded in (tightened `contribution_lookup` -> `dict[date, tuple[Decimal, bool]] | None`; added
`test_degenerate_period_falls_back_to_14_days` for the now-shared `period_days <= 0 -> 14` branch).
+2 documented disable lines (82->84); 0 new R0801; 0 useless-suppression; instance-attrs 11->10
visible. Score 9.89 held; visible 154->150; smell items 59->55 (45 8-symbol + 10 instance-attr).
Before it, **`routes/templates.py` DONE** (`1c26575`), cleared all 4 design smells
(`update_template` tm-locals/return/branches; `preview_recurrence` tm-locals) PLUS the
`preview_recurrence` protected-access by genuine decomposition (no disables, behavior bit-identical;
242 targeted + full suite 5766 the gate): the developer-chosen shared `_validate_template_form`
(account/category ownership + envelope-only-on-expense) now drives BOTH `create_template` and
`update_template` -- genuine 2-site DRY (create's required `account_id`/`category_id` are always in
`data`, so the `in data` guards preserve both paths) -- plus `_apply_fields_and_propagate_rename`
collapse `update_template` to ~11 locals/6 returns/8 branches; `preview_recurrence` -> ~9 locals via
`_build_preview_rule` (request.args -> transient rule) + `_render_preview_html`. The protected-access
(`recurrence_engine._match_periods`) cleared by the developer-chosen BROAD scope: promote the pure,
directly-tested, cross-module-called matcher to the public `match_periods`, which ALSO cleared its
own Tier-3 tm-return (8/6) via a single-return accumulator (developer-chosen over a dispatch dict --
the branches' heterogeneous locals would force lambda shims; the reviewer agreed). Renamed 2 internal
callers + 2 doc refs + the test import/27 call sites + the TEST_PLAN.md header (name-only, decision
#5). Independent quality-pass review (fresh subagent, A-G rubric, all 7 behavior-equivalence points
verified against the code): ALL ACCEPT, 0 REFINE/REVERT-OVERREACH design changes; the lone finding a
stale `TEST_PLAN.md` reference folded into the rename. 0 disables added (82); 0 new R0801;
instance-attrs unchanged at 11. Score 9.88->9.89; visible 161->154; smell items 64->59 (48 8-symbol +
11 instance-attr). Before it, **`routes/grid.py` DONE** (`86541bb`), cleared all 4 design smells by
genuine decomposition (no disables, behavior bit-identical): a frozen `_GridRowData` NamedTuple
replacing `_build_grid_row_data`'s 6-tuple return (the six values are the per-render "row contract"
spliced into `grid/grid.html`, so naming them collapses the 6-local unpack to ONE local in both
`index` -- clearing its `too-many-locals` -- and `_build_plan_view` -- halving its locals), plus
`_build_plan_view` taking the existing `_GridContext` (`ctx`, given a new `user_id` field --
developer-chosen over deriving `ctx.scenario.user_id`) in place of the unpacked
`account`/`scenario`/`current_period`/`user_id` (8->5 args, clearing `too-many-arguments` +
`too-many-positional-arguments`); the 4 remaining loaded values fan out to different consumers so
they stay unbundled (stamp-coupling avoided, per the `build_recurring_transfer_template` precedent).
Impact-traced clean: the touched helpers are private + called/constructed only in grid.py
(`companion.py` references them in a comment only); no test constructs them; `RowKey`/`grid_bp`
public surface untouched. Fixed two stale docstring counts en route ("5-tuple" -> named-6; "eight"
-> "six" `plan_*` keys). 0 disables added (82); 0 new R0801; instance-attrs unchanged at 11
(NamedTuple fields are not counted by R0902). Before it,
**`routes/_transfer_creation_helpers.py` DONE** (`59ba11a`), cleared
its last `too-many-arguments` (the `build_recurring_transfer_template` 6-field `TransferTemplate`
factory) by a developer-chosen genuine structural reduction -- NOT a param object or a disable:
`derive_from_loan` dropped from the helper (6->5 args), relying on the column's `False` model/server
default for contributions, while the loan-payment creator (the only caller that needs it) sets
`template.derive_from_loan` itself on the returned row before the flush -- keeping the loan-only
concern at the loan call site, 0 disables added (82). Strengthened `test_create_transfer_success`
with `assert tpl.derive_from_loan is True` (previously-unasserted route coverage that now locks the
flag). Before it, **`routes/_recurrence_form_helpers.py` + `routes/_commit_helpers.py`
DONE** (`8e01099`), dissolved all 8 design smells across the two files by the developer-chosen
Max-DRY decomposition (no disables, behavior bit-identical; full suite 5755 the gate): a new frozen
`RedirectTarget(endpoint, kwargs)` value type (+ `to_response()`, the single home for the
`redirect(url_for(e, **(k or {})))` idiom; also unified the `redirect_kwargs` vs
`redirect_endpoint_kwargs` naming drift) composed into two frozen context objects --
`RecurrenceFormContext` (collapses the verbatim triplicated
`end_date_value`/`redirect`/`include_due_day_of_month` tail shared by build/update/resolve:
`build_recurrence_rule_from_form` 7->4 args + 16->13 locals, update/resolve 6->3 args) and the
shared `StaleConflictContext` (logger/log_label/log_id/flash_message/redirect, drives
`_commit_helpers`' `handle_stale_conflict`/`commit_or_handle_stale`/`regenerate_and_commit_or_stale`
6/6/7->1/1/2 AND the pre-flush mirror `handle_stale_form_conflict` 8->3). ~30 call sites rewrapped
across 8 route files (templates/transfers/accounts/savings/salary/investment/loan) + 9 test
call-shapes (decision #5, values frozen byte-identical); 0 new R0801 (the repeated context-wrapping
did not cluster), 0 disables (82). `_transfer_creation_helpers` redirect helpers moved to
`RedirectTarget` too (its remaining `build_recurring_transfer_template` smell then cleared in the
next commit, `59ba11a` -- see above). Before it,
**`services/retirement_dashboard_service.py` DONE** (`ce65229`), dissolved all 5 design smells +
the dead `salary_profiles` parameter by genuine decomposition (no disables, behavior bit-identical;
full suite 5755 the gate): `compute_gap_data` (38 locals/51 stmts) -> 14 locals as a thin
delegation pipeline over cohesive pure helpers (the central `calculate_gap` kept visible -- 6
genuine cross-phase inputs); `_project_retirement_accounts` 8 args/8 pos/31 locals -> 1 arg via the
frozen `_RetirementProjectionContext`(7) + `_load_projection_batch`/`_resolve_current_balances`/
`_project_one_account`, the dead param removed at root (cleared the `unused-argument`). Four
cohesive frozen bundles (`_PensionSummary`/`_CurrentPay`/`_RetirementProjectionContext`/
`_ProjectionBatch`); NO `_RetirementBaseData` -- the three top-level loads fan out, so bundling them
would be stamp coupling (kept as plain locals); pre-existing dead module `logger` removed; 0 new
R0801, 0 new tm-instance-attributes, 0 disables (82). Before it,
**`services/paycheck_calculator.py` DONE** (`15bcfd1`), cleared all 5 design smells by genuine
decomposition (no disables, behavior bit-identical; full suite 5755 the gate): `PaycheckBreakdown`
(13/7 instance-attrs) was restructured (developer-chosen over a documented disable) into FOUR
cohesive nested sections -- `PeriodInfo`/`Earnings`/`TaxLines`/`DeductionBreakdown` (4/7) -- with the
section totals moved onto the owning section (`taxes.total`, `deductions.total_pre_tax`,
`earnings.take_home_rate_pct`) and ~all consumer accesses migrated to the nested form (Option B: app
services + the 2 salary templates that actually render a breakdown + 6 test files incl.
`test_paycheck_calculator`'s 371 path-only/values-frozen assertions); `calculate_paycheck` 37->13
locals via two frozen contexts (`_DeductionContext`/`_PaycheckContext`, developer-chosen ISP split) +
`_compute_deductions`/`_compute_tax_lines`/`_bracket_federal`/`_bracket_state`; `_calculate_deductions`
7->2 args (takes `_DeductionContext`, resolves pct_id internally -- cached, behavior-identical);
`_gross_biweekly_for_period` 16->12 via `_residue_cents`; 0 new R0801, 0 disables added (82), and the
3 total-property `missing-function-docstring` (Phase 4) cleared as a bonus. Before it,
**`services/investment_dashboard_service.py` DONE** (`e3dbea7`), dissolved all 6 design smells by
genuine decomposition (no disables, behavior bit-identical; route-level `test_investment.py` the
gate): the developer-chosen single frozen `_ProjectionContext` (6 fields) loaded once by
`_load_projection_context` centralizes the entries-aware current balance + the projection-inputs
splat + the contribution timeline both the dashboard and growth-chart bodies resolved inline
(S6-01 dup), and the shared `_run_growth_projection` / `_build_chart_series` primitives dedupe two
R0801-invisible duplications (the `project_balance` splat + the cumulative-contribution chart loop;
variable names differed so R0801 never clustered them). `compute_dashboard_data` 26->6 locals,
`compute_growth_chart_data` 28->11, `_project_dashboard_balances` 8 args/19 locals -> 3/8,
`_compute_contribution_prompt` 7->4 args, `_compute_what_if_overlay` 6->4 args; 0 new R0801, 0
disables added (82). Before it,
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
| 3 | Design-smell refactors | 158 visible smells + smells revealed by Phase 1 | IN PROGRESS (20 files + the form-helper family done: `calibration_service.py`, `budget_variance_service.py`, `investment_projection.py` (+ `projection_inputs.py` wrapper), `debt_strategy.py` (route), `retirement_gap_calculator.py`, `loan_resolver/`, `growth_engine.py`, `templates.py`, `grid.py`, `salary/`, `amortization_engine.py`, `savings_dashboard_service/`, `year_end_summary_service/`, `transactions/`, `transfers/`, `loan/`, `debt_strategy_service.py`, `investment_dashboard_service.py`, `paycheck_calculator.py`, `retirement_dashboard_service.py`, `_recurrence_form_helpers.py` + `_commit_helpers.py` + `_transfer_creation_helpers.py`) |
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
| app/ref_cache.py:147 | global-statement | **REMOVED** (`ebcda36`, Phase 3) | The 5 `global` rebinds (147-151) eliminated by the C'-dict refactor: the 13 maps + `_initialized` moved onto a single never-rebound `_RefState` (`_cache`) whose dicts `init()` mutates in place, so no `global` is needed. Phase 1 classified these KEEP+DOC; Phase 3 resolved them at the root instead. |
| app/ref_cache.py:148 | global-statement | **REMOVED** (`ebcda36`, Phase 3) | (same C'-dict refactor; see :147) |
| app/ref_cache.py:149 | global-statement | **REMOVED** (`ebcda36`, Phase 3) | (same C'-dict refactor; see :147) |
| app/ref_cache.py:150 | global-statement | **REMOVED** (`ebcda36`, Phase 3) | (same C'-dict refactor; see :147) |
| app/ref_cache.py:151 | global-statement | **REMOVED** (`ebcda36`, Phase 3) | (same C'-dict refactor; see :147) |
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
- **services/investment_dashboard_service.py** -- Status: **DONE** (`e3dbea7`; file now 10.00/10,
  zero smell messages). All 6 design smells resolved by genuine decomposition (no logic change, no
  disables, behavior bit-identical; the route-level `test_investment.py` regression suite is the
  load-bearing gate). The developer-chosen single frozen `_ProjectionContext` (6 fields:
  params/current_balance/inputs/contributions/deductions/active_profile) is loaded once by
  `_load_projection_context`, centralizing the entries-aware current balance, the projection-inputs
  splat, and the per-period contribution timeline that `compute_dashboard_data` and
  `compute_growth_chart_data` previously each resolved inline (S6-01 dup; `params` passed in so
  neither surface re-queries `InvestmentParams`). Two shared primitives dedupe the
  R0801-invisible duplications: `_run_growth_projection(ctx, periods)` (the identical
  `growth_engine.project_balance` splat) and `_build_chart_series(projection, periods,
  current_balance)` (the identical cumulative-contribution chart loop -- both surfaces ran it with
  different variable names so R0801 never clustered them). `compute_dashboard_data` tm-locals 26->6
  (thin: load ctx, merge dict fragments via `**`); `_project_dashboard_balances` tm-args/locals
  8/19 -> 3/8 (takes `ctx`, returns a dict fragment); `_compute_contribution_prompt` tm-args 7->4
  (takes `ctx`, returns the template-keyed dict directly so `**` spreads it); `compute_growth_chart_data`
  tm-locals 28->11 (delegates to the new `_growth_chart_context`); `_compute_what_if_overlay`
  tm-args 6->4 (takes `ctx`). `_compute_employer_per_period(inputs)` extracts the cap->employer
  2-step (HIGH-07/F-043/F-055). The replaced `_projection_inputs_for_account` had no callers but
  the two entry points + a prose comment in `test_income_service.py` (left intact). 0 new R0801;
  0 disables added (82); `_ProjectionContext` (6 fields) adds no tm-instance-attributes smell.
  55 investment route + 91 integration/income + full suite 5755 passed.
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
- **routes/templates.py** -- Status: **DONE** (`1c26575`; file now 9.96/10, only the pre-existing
  `line-too-long`:324 Phase-4 residue remains). All 4 LIVE design smells (`update_template`
  tm-locals 16/15 + tm-return 8/6 + tm-branches 15/12; `preview_recurrence` tm-locals 17/15) PLUS
  the `preview_recurrence` protected-access (716) resolved by genuine decomposition (no disables,
  behavior bit-identical). **`update_template`:** the developer-chosen shared `_validate_template_form`
  (account/category ownership + envelope-only-on-expense) replaces the near-duplicate inline blocks in
  BOTH `create_template` and `update_template` -- genuine 2-site DRY collapsing the 3 FK/tracking
  guard-returns to 1 (8->6 returns, no disable: a disable at the 6/6 limit would itself be a
  useless-suppression). Verified behavior-identical: `TemplateCreateSchema` makes
  `account_id`/`category_id` required, so they are always in `data` on create -- the `in data` guards
  match the old unconditional create checks AND the old optional update checks. `_apply_fields_and_propagate_rename`
  (allowlist write + the rename-desync propagation) drops the remaining locals/branches; net ~11
  locals / 6 returns / 8 branches. **`preview_recurrence`:** `_build_preview_rule` (parse `request.args`
  -> transient `RecurrenceRule`, so the route reads `rule.interval_n`/`rule.start_period_id` instead of
  separate locals) + `_render_preview_html`; ~9 locals. The every_n offset condition was wrapped en
  route (cleared 1 `line-too-long`). **Protected-access (developer-chosen BROAD scope):** promoted
  `recurrence_engine._match_periods` -> public `match_periods` (it is a pure function, tested directly
  by 27 units, and called cross-module by this preview route -- the leading underscore mislabeled a
  de-facto public API), which ALSO cleared the Tier-3 `match_periods` tm-return (see the Tier-3 entry).
  Impact-traced: 2 internal callers (`_resolve_generation_plan`, `can_generate_in_period`), 2 doc refs
  (`can_generate_in_period` docstring, `_recurrence_common` comment), the test import + 27 call sites,
  and the `TEST_PLAN.md` pattern-matching header -- all name-only (decision #5), no assertion change.
  Independent quality-pass review (`1c26575` working tree, fresh subagent, A-G rubric): verified all 7
  behavior-equivalence points against the code, returned ALL ACCEPT (0 REFINE/REVERT-OVERREACH design
  changes); the lone finding -- a stale `TEST_PLAN.md` reference -- was a rule-7 completeness fix folded
  into the rename commit. **(The plan's earlier `update_template` tm-statements was already stale -- the
  live tree had no tm-statements smell here.)** 0 disables added (82); 0 new R0801; instance-attrs
  unchanged at 11. Score 9.88->9.89; visible 161->154; smell items 64->59. 242 targeted (recurrence +
  templates + template_flags + optimistic_locking) + **full suite 5766 passed.**
- **services/retirement_dashboard_service.py** -- Status: **DONE** (`ce65229`; file now
  10.00/10, zero smell messages). All 5 design smells + the dead `salary_profiles` parameter
  resolved by genuine decomposition (no disables, behavior bit-identical; full suite 5755 the
  gate). `compute_gap_data` (38 locals/51 stmts) -> 14 locals: a thin delegation pipeline over
  cohesive pure helpers -- `_compute_pension_benefit` (`_PensionSummary`), `_compute_current_pay`
  (`_CurrentPay`), `_resolve_planned_retirement_date`, `_build_projection_context` (loads the
  accounts internally), `_compute_gap_net_biweekly` (the gap-comparison salary block rewritten as
  guard clauses -- verified result-identical), `_resolve_estimated_tax_rate` (parallels
  `_resolve_swr_fraction`), `_build_chart_data`. The central `calculate_gap` call stays VISIBLE in
  the orchestrator -- it takes 6 genuine inputs drawn from different phases, so wrapping it would
  only relocate the smell (a 6-arg helper) or create a fan-out `_GapInputs` clump; extracting only
  the peripheral transforms is the cleaner altitude. `_project_retirement_accounts` 8 args/8 pos/31
  locals -> 1 arg (`ctx`) via the frozen `_RetirementProjectionContext` (7 fields) + decomposition
  into `_load_projection_batch` (`_ProjectionBatch`) / `_resolve_current_balances` /
  `_project_one_account`. The dead `salary_profiles` parameter (passed at the one call site, used
  nowhere in the body) removed at the root -- clears the `unused-argument` too. **Bundle decision
  (developer-chosen, SOLID-reasoned):** four frozen dataclasses, each passing the
  travel-together cohesion test (`_PensionSummary` result, `_CurrentPay` snapshot,
  `_RetirementProjectionContext` param object, `_ProjectionBatch` once-loaded shared inputs); NO
  `_RetirementBaseData` bundle for the three top-level loads -- they FAN OUT to different consumers
  (pensions -> benefit+date, salary -> pay+gap, settings -> swr+tax+date), so bundling them would
  be stamp coupling (an ISP smell), and they stay plain orchestrator locals. The per-account
  projection dict shape (the calculate_gap / slider / template / test contract) preserved verbatim.
  Preserved byte-identical (`git diff`-verified): `_resolve_swr_fraction`, `compute_slider_defaults`,
  module docstring, constants. The CRIT-04 / E-12 `is None` conventions and the LOW-05 tax-rate
  carry-open kept verbatim, so the whole-module source-inspection guard
  (`test_no_truthiness_on_financial_values`) stays green. Also removed a pre-existing dead module
  `logger` + the now-unused `import logging` (developer-approved; pylint-invisible, matches the
  `investment_dashboard_service` precedent's reported case -- here resolved rather than left). 0 new
  R0801; 0 new `too-many-instance-attributes` (each bundle <=7 fields); 0 disables (82); E/F 0;
  useless-suppression 0. Score 9.87 held; visible 180->174; smell items 82->77 (66 8-symbol + 11
  instance-attr). 94 targeted (service + route) + **full suite 5755 passed.**
- **routes/_recurrence_form_helpers.py** + **routes/_commit_helpers.py** -- Status: **DONE**
  (`8e01099`; both files now smell-free). The two were refactored together because their smells
  share one bundle: all 8 messages (5 in `_recurrence_form_helpers`, 3 in `_commit_helpers`)
  resolved by the developer-chosen **Max-DRY + RedirectTarget** decomposition (no disables, behavior
  bit-identical; full suite 5755 the gate). New frozen `RedirectTarget(endpoint, kwargs)` value type
  in `routes/_redirect_target.py` (+ `to_response()` -- the single home for the
  `redirect(url_for(e, **(k or {})))` idiom shared ~9 ways across the helper layer; also unified the
  `redirect_kwargs` vs `redirect_endpoint_kwargs` naming drift), composed into two frozen contexts:
  `RecurrenceFormContext` (`end_date_value`/`redirect`/`include_due_day_of_month`) collapses the
  verbatim triplicated signature tail shared by `build_recurrence_rule_from_form` (7->4 args,
  16->13 locals) / `update_recurrence_rule_from_form` (6->3) / `resolve_recurrence_rule_for_update`
  (6->3); and the shared `StaleConflictContext`
  (`logger`/`log_label`/`log_id`/`flash_message`/`redirect`) drives `_commit_helpers`'
  `handle_stale_conflict`/`commit_or_handle_stale`/`regenerate_and_commit_or_stale` (6/6/7->1/1/2)
  AND the pre-flush mirror `handle_stale_form_conflict` (8->3). `StaleConflictContext` lives in
  `_commit_helpers.py` (the canonical handler's home) and is imported by `_recurrence_form_helpers`;
  one-way edge, no cycle. ~30 call sites rewrapped in `StaleConflictContext(...)` /
  `RecurrenceFormContext(...)` across 8 route files
  (templates/transfers/accounts/savings/salary/investment/loan); the `_transfer_creation_helpers`
  redirect helpers (`validate_and_resolve_source_account`, `flush_template_or_namedup_redirect`)
  moved to `RedirectTarget` too (naming unification, not a smell). Test patch update (decision #5,
  no assertion change): `test_recurrence_form_helpers.py` 9 call-shapes rewrapped, all asserted
  values frozen byte-identical. **Split-trap check: 0 new R0801** -- the repeated context-wrapping
  did NOT cluster (log_labels / flash strings / endpoints differ; the existing one-sided
  `duplicate-code` disables on the templates/transfers create-update preambles still cover them).
  0 disables added (82); useless-suppression 0; E/F 0. Score 9.87->9.88; visible 174->166; smell
  items 77->69 (58 8-symbol + 11 instance-attr). 754 targeted (helper + 7 route suites) + **full
  suite 5755 passed.**
- **routes/_transfer_creation_helpers.py** -- Status: **DONE** (`59ba11a`; file now 10.00/10,
  smell-free). Its last smell -- `build_recurring_transfer_template` (a 6-field shared
  `TransferTemplate` factory, an argument clump unrelated to the redirect/stale-conflict bundles
  above) -- cleared by a developer-chosen **genuine structural reduction**, explicitly NOT a param
  object (which would only mirror the entity's own columns -- single-consumer stamp coupling, zero
  DRY payoff) and NOT a disable: `derive_from_loan` dropped from the helper (6->5 args, at the
  limit), relying on the column's `False` model/server default for contributions and every generic
  transfer; the loan-payment creator -- the only caller that needs it -- assigns
  `template.derive_from_loan` itself on the returned row before the flush (`_resolve_transfer_amount`
  returns the computed bool: `True` for the monthly-payment default, `False` for a user-supplied
  amount override), keeping the loan-only concern at the loan call site. Behavior bit-identical.
  Strengthened the route test `test_create_transfer_success` with `assert tpl.derive_from_loan is
  True` -- previously-unasserted coverage (no `amount` posted -> the route opts into live derivation)
  that now locks the flag. 0 disables added (82); useless-suppression 0; E/F 0. Score 9.88 held;
  visible 166->165; smell items 69->68 (57 8-symbol + 11 instance-attr). 253 targeted (investment +
  loan) + **full suite 5755 passed.**
- **routes/grid.py** -- Status: **DONE** (`86541bb`; file now 10.00/10, zero smell messages). All 4
  design smells (`_build_plan_view` tm-args/pos/locals; `index` tm-locals) resolved by genuine
  decomposition (no disables, behavior bit-identical; 221 grid + 93 companion targeted + full suite
  5755 the gate). New frozen `_GridRowData` NamedTuple (6 fields) replaces `_build_grid_row_data`'s
  6-tuple return -- the six values are the per-render "row contract" spliced into `grid/grid.html`,
  so naming them collapses the 6-local unpack to ONE local in both `index` (clears tm-locals) and
  `_build_plan_view` (halves its locals). `_build_plan_view` 8->5 args (clears tm-args +
  tm-positional) by taking the existing `_GridContext` (`ctx`) -- given a new `user_id` field
  (developer-chosen over deriving `ctx.scenario.user_id`) -- in place of the unpacked
  `account`/`scenario`/`current_period`/`user_id`; the 4 remaining loaded values
  (`all_transactions`/`balances`/`all_categories`/`amount_overrides`) fan out to different consumers,
  so they stay unbundled (stamp-coupling avoided, per the `build_recurring_transfer_template`
  precedent). Impact-traced clean: `_build_plan_view`/`_build_grid_row_data`/`_GridContext` are
  private + called/constructed only in grid.py (`companion.py` references them in a comment only); no
  test constructs them; `RowKey`/`grid_bp` public surface untouched. Fixed two stale docstring counts
  en route ("5-tuple" -> named-6; "eight" -> "six" `plan_*` keys). 0 new R0801 (no split-trap);
  instance-attrs unchanged at 11 (pylint does not count NamedTuple fields toward R0902); 0 disables
  added (82); useless-suppression 0; E/F 0. Score 9.88 held; visible 165->161; smell items 68->64
  (53 8-symbol + 11 instance-attr).
- **services/paycheck_calculator.py** -- Status: **DONE** (`15bcfd1`; file now 10.00/10, zero smell
  messages). All 5 design smells resolved by genuine decomposition (no disables, behavior
  bit-identical; full suite 5755 the gate). **`PaycheckBreakdown` (13/7) restructured into 4 nested
  sections (developer-chosen over a documented disable, after I corrected a bad attribute count: a
  TaxLines-only nest is 10/7, still failing; only a full 4-group nest reaches 4/7):** `period`
  (`PeriodInfo`: period_id/is_third_paycheck/raise_event), `earnings` (`Earnings`:
  annual_salary/gross_biweekly/taxable_income/net_pay + `take_home_rate_pct`), `taxes` (`TaxLines`:
  federal/state/social_security/medicare + `total`), `deductions` (`DeductionBreakdown`:
  pre_tax/post_tax + `total_pre_tax`/`total_post_tax`). The former flat totals moved onto the owning
  section. **Consumer migration (Option B, developer-chosen over a backward-compat delegating-property
  facade, which I flagged as itself a DRY/maintainability smell):** every access -> nested form across
  app services (income_service, year_end/_income_tax, salary/_helpers+profiles, recurrence_engine,
  savings/_orchestrator, retirement_dashboard, dashboard_service), the 2 salary templates that
  actually render a breakdown (breakdown.html, projection.html -- the other 7 "matches" were
  collisions: `data.income_tax.*` year-end aggregate, FicaConfig `medicare_rate`, SalaryProfile
  `annual_salary`, calibrate-rate fields), and 6 test files **path-only with all expected values
  frozen byte-identical** (test_paycheck_calculator's 371 assertions + 11 nested constructor rewrites
  + 7 `_calculate_deductions` call-shape updates; the savings C26-3 source-inspection guard's target
  string repointed to `current_breakdown.earnings.gross_biweekly`). `calculate_paycheck` 37->13 locals
  via the two frozen contexts `_DeductionContext`(5) + `_PaycheckContext`(5) (developer-chosen tighter
  ISP over a single shared context) + the deduction bundle `_compute_deductions` + the tax dispatch
  `_compute_tax_lines`/`_bracket_federal`/`_bracket_state` (each <=15 locals/<=5 args; net-pay sum is
  Decimal-exact-equivalent to the prior per-line subtraction). `_calculate_deductions` 7->2 args
  (takes `_DeductionContext`; resolves pct_id from the cache internally -- behavior-identical; the
  now-dead test helper `_pct_id` + its unused `CalcMethodEnum` import removed).
  `_gross_biweekly_for_period` 16->12 locals via the extracted `_residue_cents`. 0 new R0801, 0
  disables added (82), 0 useless-suppression, 0 E/F; the 3 total-property `missing-function-docstring`
  (Phase 4) cleared as a bonus. 590 targeted + **full suite 5755 passed.**

### Tier 3 -- single-function or low-count files

- services/investment_projection.py: **DONE** (`bf111f0`; file now 10.00/10, zero smell messages).
  `calculate_investment_inputs` tm-args/pos/locals cleared by (a) removing the dead `account_id` param
  (forwarded by the `projection_inputs` wrapper, never read by the callee -- the `salary_profiles` /
  `planned_retirement_date` dead-param precedent; clears `unused-argument`), (b) decomposing the 5
  steps into `_periodic_from_deductions` / `_average_transfer_contribution` / `_employer_params` /
  `_ytd_contributions` (tm-locals), and (c) a documented scoped+named+commented
  `too-many-arguments,too-many-positional-arguments` disable for the residual 6 independent inputs
  (1 over max; no cohesive sub-bundle -- a param object would be stamp coupling; mirrors
  `growth_engine.project_balance`). Done jointly with `projection_inputs.build_investment_projection_inputs`
  (the coupled wrapper). Cleared overlapping Phase-4 residue (param-doc, 3 long lines, stale comment).
  Independent quality-pass: ACCEPT, 0 REVERT-OVERREACH, 0 REFINE. Full suite 5769 passed.
- routes/debt_strategy.py: **DONE** (`8449f21`; file now 10.00/10, zero smell messages). `calculate`
  tm-locals (17/15) + tm-return (7/6) + tm-branches all cleared by genuine decomposition (no disables,
  behavior bit-identical). The 5 duplicated `_results.html` error renders funnel through a new private
  `_ResultsError` + one try/except (DRY collapse of a single error contract -> 3 returns); the IDOR
  404 stays a direct return. Extracted `_parse_calculate_form` / `_custom_order_has_unknown_account`
  (IDOR set-check) / `_compute_strategies` (-> frozen `_StrategyResults`(4), both distinct log labels
  kept) / `_select_result`; `_build_comparison` retargeted to the bundle; 6 grep-verified dead render
  kwargs removed. Independent quality-pass: ACCEPT, 0 REVERT-OVERREACH, 0 REFINE (lone MED: a
  pre-existing route-level test gap on the reachable compute-error funnel + custom selection, closed
  in test commit `9efb7b4`, +3 tests). 0 disables added (86); 0 new R0801; full suite 5766 passed.
- services/loan_resolver.py: **DONE** (`41f42a8`; now the `app/services/loan_resolver/` package).
  `resolve_loan` tm-locals + `compute_payoff_scenarios` tm-args/locals cleared by the developer-chosen
  frozen `LoanInputs(loan_params, anchor_events, payments, rate_changes)` bundle (shared by both;
  the data clump every caller co-loads) + the shared `_replay_from_anchor` (2-site DRY, replay-only
  so the resolver's independent-balance invariant holds) + `_build_forward_inputs`->`_ProjectionPrep`
  setup extraction (composer left a thin "project 3 ways, then summarize" orchestrator).
  `compute_monthly_payment_baseline` untouched (its `unused-argument` disable ties to OPEN P-1).
  `PayoffScenarios`(10/7) documented scoped disable (see instance-attr table). too-many-lines
  (introduced by the decomposition, 1009) resolved by the decision-#5 package split
  (`_periods`/`_state`/`_payoff` + `__init__` re-exports; import paths preserved; 0 new R0801). ~52
  call sites wrapped (values frozen); 3 source guards + the C15-3 allow-list repointed to the package.
  Independent quality-pass: ACCEPT, 0 REVERT/REFINE. Package 10.00/10; full suite 5767 passed.
- services/retirement_gap_calculator.py: **DONE** (`2b0f5ca`; file clears all 4 design smells, only the
  pre-existing `line-too-long`:145 Phase-4 residue remains). `calculate_gap` tm-args/pos (6 params)
  cleared by REMOVING the dead `planned_retirement_date` pass-through param (6->5 args clears both at the
  pylint default max=5, NO disable) -- developer chose full removal over the plan's ratified
  relocate-the-write option once verification showed `gap_result.planned_retirement_date` is write-only
  (read by no production consumer: not `_gap_analysis.html`, not `_build_chart_data`, nowhere in app/);
  the field was also dropped from `RetirementGapAnalysis` (11->10 attrs). `calculate_gap` tm-locals
  (19->14) via the pure helpers `_after_tax_projected_savings` (the trad/Roth bucketing + whole-expression
  quantize -- the load-bearing extraction) + `_sum_projected_balances` (per-account total, kept for
  orchestrator-altitude symmetry, not threshold-necessary). `RetirementGapAnalysis`(10/7) documented
  scoped disable (see instance-attr table). Single keyword caller (`retirement_dashboard_service`) + 27
  test calls updated; deleted `test_planned_retirement_date_passed_through` (rule-5 exception -- removed
  behavior); `test_result_field_completeness` 11->10 fields; removed the now-unused `datetime.date`
  import. Independent quality-pass: ACCEPT, 0 REVERT-OVERREACH, 0 REFINE. +1 documented disable (85->86);
  0 new R0801; visible 146->142; smell items 51->47; full suite 5766 passed.
- services/calibration_service.py: **DONE** (`4e625fe`; file now 10.00/10, zero smell messages).
  `derive_effective_rates` tm-args/pos (6 args) + tm-locals (16/15) cleared by bundling its 6 inputs
  into a new frozen `PayStubActuals` value object (1 arg, ~11 locals; NO disable, behavior
  bit-identical -- the `Decimal(str(...))` construct-from-strings coercion preserved verbatim). The
  bundle is a genuine domain concept: the five `actual_*` mirror the `CalibrationOverride` columns (the
  model persists them as a cohesive unit) and `taxable_income` is the route-computed federal/state
  divisor (not a stored column). 2 production callers (`salary/calibration.py` preview + confirm) + 13
  test call sites rewrapped, values frozen byte-identical. Independent quality-pass (fresh subagent,
  A-G rubric): ACCEPT, 0 REVERT-OVERREACH, 0 REFINE (the `Decimal` field hints verified against the
  production contract -- `CalibrationSchema` uses `fields.Decimal`; `frozen=True` upheld, non-frozen
  sibling `DerivedRates` left untouched out-of-scope). Full suite 5769 passed.
- services/growth_engine.py: **DONE** (`dcf0d4e`) -- `project_balance` tm-locals (23/15)
  by `_PeriodInputs`+`_ProjectionState`+`_project_one_period` decomposition (mirrors
  `amortization_engine`); tm-args/pos (8/5) by a developer-chosen documented scoped disable
  (pure stdlib leaf; the 8 inputs vary independently per caller); the period-day->rate math
  shared with `reverse_project_balance` via the new `_period_return_rate` (2-site DRY).
  `ProjectedBalance` (9/7) documented disable (see instance-attr table). file 10.00/10, zero
  smell messages.
- services/recurrence_engine.py: `_match_periods`:453 tm-return **RESOLVED** (`1c26575`; renamed to
  the public `match_periods` + single-return accumulator -- pulled in with the `routes/templates.py`
  protected-access fix, developer-chosen broad scope). `generate_for_template`:55 tm-locals was
  already absent in the live tree (stale plan entry). File now 10.00/10, zero smell messages.
- app/ref_cache.py: **DONE** (`ebcda36`; file now 10.00/10, zero smell messages, and ZERO disables
  except the unavoidable circular-import `import-outside-toplevel`). `init` tm-locals/branches/statements
  (31/15, 51/12, 124/50) cleared by the developer-chosen from-scratch best design (C'-dict): the 14
  module globals (13 maps + `_initialized`) collapsed into one never-rebound `_RefState` (`_cache`)
  holding a single `enum_ids` registry keyed by enum class (keeps it under
  `too-many-instance-attributes` with NO disable, where a 14-named-field dataclass would have needed
  one) + `acct_type_meta` + `initialized`. A frozen `_RefSpec` (derived `label`/`error_prefix`; a
  `query` method carrying the `account_types` built-in-only filter) + `_build_ref_specs` drive a single
  load/sweep loop; a `_require_init()` helper DRYs the 15x init-guard. In-place `_cache` mutation
  removed all 5 `global` disables. Behavior byte-identical (public free-function API, the `unavailable`
  return contract, and `RuntimeError` text/order; the 12 error prefixes/labels derive from
  `model.__name__`/`__tablename__`, `RoleEnum`->`UserRole` verified; single load+sweep preserves DB
  query/rollback order). Independent quality-pass: ACCEPT, 0 REVERT-OVERREACH, equivalence verified
  empirically; 1 REFINE applied -- F5 hand-pinned bootstrap/`unavailable` regression test (`d2b1c31`).
  Full suite 5769->5770 passed.
- app/ref_seeds.py: **DONE** (`32c403a`; file now smell-free -- only the pre-existing Phase-4
  `line-too-long`:93-98 Status-dict residue remains). `seed_reference_data` tm-locals (19/15) +
  tm-branches (15/12) cleared by genuine decomposition into a thin orchestrator + three cohesive
  single-responsibility step helpers (`_seed_account_type_categories` / `_seed_account_types` /
  `_seed_other_ref_tables`); NO disable. The one load-bearing cross-step invariant -- the flush that
  makes the category PKs visible to the AccountType FK -- lifted to the orchestrator altitude
  (between steps 1 and 2, restated in the helper docstrings); `ref_models` threaded into each helper
  so the deferred `app.models.ref` import stays in one place (the side-effect-free-at-import
  discipline preserved). All 4 signatures typed via a `TYPE_CHECKING` block + `from __future__ import
  annotations` (lazy-string annotations, ZERO runtime imports added -- the developer-chosen
  resolution of the quality-pass D2 type-hint finding; mirrors the sibling
  `loan_resolver/_periods.py` pattern). Public `seed_reference_data(session, *, verbose=False)` + the
  3 script/app call sites unchanged. Independent 3-lens quality-pass (behavior-equivalence /
  simplicity / right-abstraction): all `behavior_equivalent=yes` -- one reviewer proved deep-AST
  equality of the inlined-after body vs the original (loop interiors included) -- 0 REVERT-OVERREACH;
  the lone LOW D2 finding (untyped signatures) resolved by adding the hints rather than ACCEPT-ing
  per precedent (developer choice). 0 disables added (83); 0 new R0801; visible 119->117; smell items
  30->28 (23 8-symbol + 5 instance-attr); score 9.91 held; full suite 5770 passed.
- routes/accounts/detail.py: **DONE** (`c5182ea`; file now 10.00/10, zero messages). Both handlers'
  `too-many-locals` (`interest_detail` 16/15, `checking_detail` 17/15) cleared by genuine
  decomposition (no disable). Three module-private helpers: `_current_period_balance(balances,
  current_period, anchor)` (the identical current-period-balance-else-anchor-fallback 2-liner both
  handlers had -- 2-site DRY) and `_build_period_data(all_periods, balances, interest_by_period=None)`
  (dedupes both "one row per period with a balance" loops -- they differed only in whether the row
  carries an `interest` field -- 2-site DRY), plus `_load_account_transactions(account, scenario,
  all_periods)` (encapsulates `interest_detail`'s per-account transaction query + its one-sided
  `duplicate-code` disable, computing `period_ids` internally). `checking_detail`'s INLINE 3/6/12-month
  horizon loop was a verbatim copy of `project_balance_horizons` (already imported + used by
  `interest_detail` and the savings dashboard) -> replaced with the shared util (Phase-2-missed DRY;
  the util's upfront `is None` guard == the old per-iteration truthiness guard since PayPeriod has no
  `__bool__`). Behavior bit-identical (every helper char-identical to what it replaced across the
  present/None current_period, no-scenario, no-periods, empty-balances, anchor-fallback, and
  balance-without-interest->0.00 paths; both render kwarg sets unchanged). **F-6 static guard held**
  (`balance_resolver.balances_for` present, bare `balance_calculator.calculate_balances(` absent --
  `calculate_balances_with_interest(` is distinct; `selectinload(entries)` preserved). All 3 helpers
  private; route surface unchanged. Independent 3-lens quality-pass: all `behavior_equivalent=yes`, 0
  REVERT-OVERREACH; **2 REFINEs applied** before commit -- (1) MED: renamed `_resolve_current_balance`
  -> `_current_period_balance` to clear a cross-file name collision with the semantically-different
  `investment_dashboard_service._resolve_current_balance` (and name what it does: pick-from-map, not
  resolve-by-query); (2) LOW: typed all 3 helpers via `from __future__ import annotations` + a
  TYPE_CHECKING block (PayPeriod/Scenario/AnchorPoint, zero runtime imports). 0 disables added (83); 0
  new R0801; visible 113->111; smell items 24->22; score 9.92 held; full suite 5770 passed.
- services/loan_payment_service.py: `prepare_payments_for_engine`:362 tm-locals; `live_loan_transfer_amounts`:463 tm-locals -- `-`
- app/__init__.py: **DONE** (`e22a1a5`; `_register_blueprints` tm-locals cleared). The 23 explicit
  deferred `from app.routes.X import X_bp` imports (each bound a local; deferred inside the function to
  avoid the blueprint<->`app` cycle) + 23 `register_blueprint` calls replaced by a data-driven loop
  over the new module-level `_BLUEPRINT_MODULES` tuple (the 23 module names, canonical registration
  order), registering `getattr(module, f"{name}_bp")` after `importlib.import_module(f"app.routes.{name}")`.
  24->3 locals. Since no `import`/`from` statements remain in the function, the now-useless
  `import-outside-toplevel` disable was REMOVED (a bonus -- disables 83->82, no useless-suppression, no
  newly-firing import-outside-toplevel since `importlib.import_module` is a CALL not an import). Behavior
  bit-identical (reviewer verified 4 ways: identical module set + order; every `getattr` resolves to the
  same Blueprint incl. the 5 package blueprints + 3 multiword names; full `create_app` build registers
  all 23 in order = 166 URL rules; grep of all `*_bp = Blueprint` returns exactly these 23). The
  `<name>_bp` convention is total + filesystem-enforced and fails LOUD (AttributeError/ModuleNotFoundError
  at startup) on any violation. **Design fork RESOLVED (data-driven loop over explicit-imports+disable):**
  the quality-pass argued both ways -- greppability of individual `_bp` registrations is lost (mitigated
  by the documented convention) but the loop is the genuine refactor (decision #3), eliminates the 23x
  import+register pairing AND the disable, and adding a blueprint is now a one-line tuple append.
  Independent quality-pass: behavior_equivalent=yes, all ACCEPT, 0 REFINE, 0 REVERT-OVERREACH. (The 5
  pre-existing `W0613` framework-mandated error-handler `e` args in `_register_error_handlers` are a
  separate item, untouched.) disables 83->82; visible 108->107; smell items 19->18; score 9.92 held;
  full suite 5770 passed.
- routes/accounts/anchor.py: **DONE** (`ab16669`; file now 10.00/10, zero messages). `true_up`
  tm-return (7/6) cleared by (a) merging the two success returns -- DUPLICATE_SAME_DAY + COMMITTED
  build the identical OOB success response, so an if/else sets up `account` (DUPLICATE re-fetches the
  committed row -- the service returns after `rollback()`; COMMITTED `refresh()`es + logs) then a
  single shared success build/return -- and (b) extracting `_anchor_conflict_response(account) ->
  tuple[str, int]`, the 409 conflict render shared by the pre-flush form-version-mismatch guard and
  the post-service STALE_CONFLICT outcome (genuine 2-site DRY; correctly NOT shared with
  `inline_anchor_update`, which uses a different template/kwarg). Returns 7->6 (the limit; 6 distinct
  HTTP outcomes -- 404 / 400-validation / 400-no-period / 409-form-stale / 409-service-stale /
  200-success -- so irreducible, no disable). Behavior bit-identical: verified against
  `anchor_service`'s outcome/rollback contract that DUPLICATE's `db.session.get` re-fetch + COMMITTED's
  `db.session.refresh` + COMMITTED-only `logger.info` are all preserved. Independent quality-pass:
  behavior_equivalent=yes, all ACCEPT, 0 REFINE, 0 REVERT-OVERREACH. **Watch-item (PRE-EXISTING, not
  introduced):** `test_double_submit_creates_one_history_row` asserts the DUPLICATE 200 + history count
  but not that r2's rendered body shows the committed balance -- the DUPLICATE re-fetch (which my merge
  preserves) rests on inspection there; a 1-line `assert <balance> in r2.data` would close it (deferred:
  needs the exact rendered format verified, and it predates this cleanup). 0 disables added (83); 0 new
  R0801; visible 110->109; smell items 21->20; score 9.92 held; full suite 5770 passed.
- routes/categories.py: **DONE** (`5b32148`; file now 10.00/10, zero messages). `create_category`
  tm-return (7/6) cleared by extracting the dual HTMX-or-flash error response -- byte-identical at the
  schema-validation and blank-name guards -- into the typed `_create_form_error_response(errors,
  flash_message) -> Response | tuple[Response, int]` (400 JSON for HTMX, else flash+redirect to
  settings#categories); each guard returns it (7->5 returns), genuine 2-site DRY, no disable. The
  duplicate guard (no HTMX-jsonify -- pre-existing asymmetry) + both success returns unchanged;
  `edit_category` NOT folded in (no HTMX branch -- different contract, rule 13). Behavior bit-identical;
  the create error paths (HX 400, non-HX flash, blank-name message) are all pinned by existing tests.
  Independent quality-pass (single fresh reviewer): behavior_equivalent=yes, 6 ACCEPT, 0 REFINE, 0
  REVERT-OVERREACH. 0 disables added (83); 0 new R0801; visible 111->110; smell items 22->21; score
  9.92 held; full suite 5770 passed.
- routes/entries.py: **DONE** (`6e3c32d`; file now 10.00/10, zero messages). `update_entry`
  tm-return (8/6) cleared by extracting the service-call + commit + 4-way error-translation tail into
  `_execute_entry_update(entry_id, txn, data)` (StaleDataError->409 conflict list; the C-19
  IntegrityError backstop->idempotent credit-payback; NotFound/Validation->400; success->refreshed
  list + balanceChanged). `update_entry` 8->5 returns; the helper has 4 (under the ceiling). The
  `transfers._execute_transfer_update` precedent; sharpens cohesion (route = guards+validation+stale
  check; helper = commit/error-translation). Helper body byte-identical to the replaced block
  (`version_id` popped before the call, so not forwarded to the service); left UNTYPED to match the
  sibling untyped response helpers (`_stale_entry_response`/`_render_entry_list`/
  `_credit_payback_idempotent_response`) -- the divergence from `_execute_transfer_update` (returns the
  response itself vs None) is correct since this route has no post-commit caller-side render branching.
  Independent quality-pass: behavior_equivalent=yes (byte-verified), all ACCEPT, 0 REFINE, 0
  REVERT-OVERREACH. **Two deferred tracker notes (out of scope here):** (1) entries' untyped
  private-helper cluster is a candidate for a coordinated typing pass (return type
  `flask.typing.ResponseReturnValue`) so the going-forward type-new-helpers convention is not eroded;
  (2) the 7-line `txn = get_accessible_transaction; if None 404; entry = get; if not owned 404`
  preamble is shared verbatim by `update_entry` + `toggle_cleared` + `delete_entry` -- a genuine DRY
  opportunity (a `(txn, entry)`-returning resolver or a decorator), a separate refactor with its own
  design question. 0 disables added (83); 0 new R0801; visible 109->108; smell items 20->19; score
  9.92 held; full suite 5770 passed.
- routes/obligations.py: **DONE** (`7a77db9`; file now 10.00/10, zero messages). `_next_occurrence`
  tm-return (7/6) cleared by a single-return accumulator -- the per-pattern `if pid in (...): return
  <date>` dispatch becomes an if/elif assigning `next_date`, then one `return next_date`; the early
  end_date-in-the-past guard stays an early `return None`; `day`/`month` hoisted above the dispatch
  (pure `attr or 1` reads, unused by the period/unknown branches). Mirrors the ratified
  `recurrence_engine.match_periods` accumulator (chosen over a dispatch dict for heterogeneous
  branches); the existing `duplicate-code` disable block (the 7 pattern-id lookups) is unchanged.
  `summary` tm-locals (18/15) cleared by extracting the 3 verbose queries to loaders
  (`_load_recurring_expenses`/`_load_recurring_income`/`_load_recurring_transfers`; the expense/income
  type-id resolution moved inside) + `_build_items(templates, renderer, as_of)`, which dedupes the 3
  byte-identical build loops (they differed only in the renderer) and is generic over a constrained
  `TypeVar` in (`TransactionTemplate`, `TransferTemplate`) -- the loan.py loader precedent. summary ->
  ~14-local orchestrator. Behavior bit-identical: every pattern branch (incl. unknown->None), the
  loaders' exact queries, the E-24/HIGH-05 row-iff-subtotal invariant (rows + `committed_monthly` both
  via `obligations_aggregator`), and all 10 render kwargs preserved. All helpers private to the module;
  no source guard. Independent 3-lens quality-pass: all `behavior_equivalent=yes`, 12 findings all
  ACCEPT, 0 REVERT-OVERREACH, 0 REFINE (TypeVar correct-not-overkill; loaders genuine extraction; the
  hoist E3 tiny-waste accepted). 0 disables added (83); 0 new R0801; visible 115->113; smell items
  26->24; score 9.92 held; full suite 5770 passed. (One transient full-suite run failed to connect to
  the test DB -- a quality-pass reviewer's own `./scripts/test.sh` restarted the shared container
  mid-run; a clean isolated restart-first run was 5770. Lesson: run the full suite ALONE, never
  alongside `pylint app/` or a quality-pass workflow whose reviewers may restart the test DB.)
- routes/settings.py: **DONE** (`1d52d3f`; file now 10.00/10, zero messages). `show` tm-locals
  (21/15) cleared by replacing the ~19 parallel template-variable locals with a single
  `_empty_section_context()` default dict + per-section loaders (`_load_categories_context` /
  `_load_tax_context` / `_load_account_types_context` / `_load_security_context`; `_load_companions_context`
  reused; trivial `general` inline) merged via `context.update(...)` -- the `routes/loan` dashboard
  per-section-builder precedent. `update` tm-branches (13/12) cleared by collapsing the six identical
  `field in data and ... is not None` field-copies into an allowlist loop over the new
  `_SIMPLE_SETTINGS_FIELDS` (the E-28/HIGH-06/PA-01 percent->fraction rationale moved onto the
  constant comment); the IDOR-checked `default_grid_account_id` branch + its flash/redirect stay
  inline. `_empty_section_context()` also DRYs the empty-defaults contract that was triplicated:
  `_empty_companions_context` removed (subsumed), `_render_companions_section` routed through the
  shared helper (quality-pass ruled this a justified DRY win, not rule-6 scope creep, since all three
  shared the one template-default contract the smell forced). Static icon list lifted to the
  immutable tuple `_ACCOUNT_TYPE_ICON_CHOICES`. Behavior bit-identical (all 8 sections' 19 render
  kwargs byte-identical per set-diff; `update` field set/guard + IDOR + 3 flash/redirect paths
  unchanged). Public show/update endpoints + URLs unchanged; no source guard. Independent 3-lens
  quality-pass: all `behavior_equivalent=yes`, 0 REVERT-OVERREACH, 0 REFINE (the lone LOW D2 icon-list
  note folded in as the tuple). 0 disables added (83); 0 new R0801; visible 117->115; smell items
  28->26; score 9.91->9.92; full suite 5770 passed.
- services/account_service.py: `create_account`:86 tm-args -- `-`
- services/balance_calculator.py: `calculate_balances`:33 tm-branches -- `-`
- services/entry_service.py: `create_entry`:129 tm-args/pos -- `-`
- services/interest_projection.py: **DONE** (`62fd7a2`; file now 10.00/10, zero messages).
  `calculate_interest` tm-locals (17/15) cleared by extracting the quarterly branch's quarter-length
  arithmetic (`q_start_month`/`q_start`/`next_q_month`/`q_end` -- 4 intermediate locals) into
  `_days_in_quarter(period_start) -> Decimal`, parallel to the existing `_days_in_year_for_window`
  divisor helper. 17->13 locals; no disable. FINANCIAL: the helper body is byte-identical to the old
  inline calc (only `days_in_quarter =` -> `return`), the quarterly formula
  `balance * quarterly_rate * (period_days / days_in_quarter)` + the daily/monthly branches + the early
  guards + `round_money` are untouched; `Decimal(str(...))` discipline preserved; the L-05 actual-length
  rationale moved into the helper docstring. Untyped to match the file's untyped helpers. Independent
  quality-pass: behavior_equivalent=yes (byte-for-byte vs HEAD), all ACCEPT, 0 REFINE, 0
  REVERT-OVERREACH -- the extract-the-divisor altitude ruled correct, and extracting ALL 3 compounding
  branches into a dispatcher ruled REVERT-OVERREACH (gold-plating: the math is deliberately asymmetric;
  co-locating the 3 monetary formulas has review value). **Closed a reviewer-flagged PRE-EXISTING gap
  the extraction made load-bearing** (same commit): the Q4 `next_q_month > 12` year-rollover branch in
  `_days_in_quarter` was untested -- added `test_q4_year_rollover_period` (Q4 2026 = Oct 31 + Nov 30 +
  Dec 31 = 92 days; interest 17.12, hand-computed independently, NOT via the function under test) + fixed
  the `TestQuarterlyCompounding` docstring's stale `/91` to the actual-length behavior. 0 disables added
  (82); 0 new R0801; visible 106->105; smell items 17->16; score 9.92 held; full suite 5770->5771
  passed.
- services/projection_inputs.py: **DONE** (`bf111f0`; file now 10.00/10, zero smell messages).
  `build_investment_projection_inputs` tm-args/pos cleared jointly with the wrapped
  `calculate_investment_inputs`: dropped the dead `account_id` (6->... still 6 after removal, 1 over
  max) and documented the same scoped+named+commented `too-many-arguments,too-many-positional-arguments`
  disable -- this is a thin 1:1 forward of the same six independent inputs, so it mirrors the callee's
  disposition. The wrapper stays the "single splat home" (canary at :236 intact -- still the only
  `salary_gross_biweekly=salary_gross_biweekly,)` site). Independent quality-pass: ACCEPT.
- services/savings_goal_service.py: **DONE** (`7dad8d7`; file now 10.00/10, zero messages).
  `amount_to_monthly` tm-return (8/6) cleared by a single-return accumulator: the explicit `once`
  (valid-non-recurring) case stays an early `return None`, then an if/elif assigns `monthly = <expr>`
  per pattern with `else: monthly = None` (unrecognized id), then one `return monthly`. Returns 8->2;
  the `match_periods`/`_next_occurrence` precedent. FINANCIAL function: every per-pattern Decimal
  expression byte-identical (division order preserved -- e.g. `amount * PAY_PERIODS_PER_YEAR / n /
  MONTHS_PER_YEAR`), `Decimal(str(...))` construction intact, NO quantization added (the result is
  intentionally un-quantized per the docstring; callers round). `once` kept explicit + distinct from
  the `else` (both yield None, which the sole consumer `obligations_aggregator` skips uniformly, so the
  fold is behavior-safe). Independent quality-pass: behavior_equivalent=yes (byte-for-byte verified each
  branch vs HEAD), all ACCEPT, 0 REFINE, 0 REVERT-OVERREACH (accumulator ruled the right shape over a
  dispatch dict -- the branches are heterogeneous; keeping `once` explicit upheld as a meaningful
  documented distinction). 0 disables added (82); 0 new R0801; visible 107->106; smell items 18->17;
  score 9.92 held; full suite 5770 passed.
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
| services/paycheck_calculator.py:83 | 13/7 | **RESTRUCTURED (no disable), `15bcfd1`** -- `PaycheckBreakdown` split into 4 nested sections (period/earnings/taxes/deductions) -> 4/7; see the Tier-2 entry |
| services/spending_trend_service.py:52 | 11/7 | - |
| services/spending_trend_service.py:81 | 8/7 | - |
| services/retirement_gap_calculator.py:23 (`RetirementGapAnalysis`) | 10/7 | **DISABLE (documented), `2b0f5ca`** -- cohesive single-return result aggregate (10 figures rendered as a flat row-per-field table by `retirement/_gap_analysis.html`); count reduced 11->10 by removing the dead `planned_retirement_date` field; `AmortizationRow`/`PayoffRequest`/`ProjectedBalance` precedent; scoped+named+commented |
| services/loan_resolver/_payoff.py (`PayoffScenarios`) | 10/7 | **DISABLE (documented), `41f42a8`** -- cohesive single-return result aggregate (3 chart slices + history + 6 summary metrics read flat by one consumer); `PayoffRequest`/`AmortizationRow` precedent; scoped+named+commented |
| services/calendar_service.py:71 | 10/7 | - |
| services/calendar_service.py:87 | 10/7 | - |
| services/carry_forward_service.py:87 | 10/7 | - |
| services/budget_variance_service.py:41 (`TransactionVariance`) | 8/7 | **RESTRUCTURED (no disable), `b5a9d56`** -- the (estimated/actual/variance/variance_pct) quad extracted to the shared `VarianceFigures` value object; DTO now holds one `figures` field (5 attrs). The variance/pct compute (hand-written 4x, R0801-invisible) consolidated into `VarianceFigures.of`; naming drift dissolved. See the Progress Log |
| services/budget_variance_service.py:55 (`CategoryItemVariance`) | 9/7 | **RESTRUCTURED (no disable), `b5a9d56`** -- `figures: VarianceFigures` field -> 6 attrs (same extraction) |
| services/budget_variance_service.py:82 (`VarianceReport`) | 8/7 | **RESTRUCTURED (no disable), `b5a9d56`** -- `figures: VarianceFigures` field -> 5 attrs (same extraction) |
| services/growth_engine.py:24 | 9/7 | **DISABLE (documented), `dcf0d4e`** -- `ProjectedBalance` cohesive per-period schedule-row DTO mirroring `AmortizationRow`; scoped+named+commented |
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
Next by live density (re-measured 2026-06-06 after **`services/interest_projection.py` DONE** `62fd7a2`; 16
smell items remain [11 of the 8-symbol set + 5 `too-many-instance-attributes`], down from 17).
**Working order is now low-fork-batch-first** (developer direction 2026-06-06): clear the
function-decomposition + tm-args-param-object files, THEN consult on the package splits
(`carry_forward_service`, `schemas/validation`), the instance-attr dispositions
(`calendar_service`/`spending_trend_service`/`carry_forward_service` -- developer prefers RESTRUCTURE
where feasible over a documented disable), and the 2 criticals (`auth.py`, `transfer_service.py`).
The remaining low-fork files:
`services/loan_payment_service.py` (`prepare_payments_for_engine` +
`live_loan_transfer_amounts` tm-locals -- note OPEN P-1 here), `services/balance_calculator.py`
(`calculate_balances` tm-branches + the `:121` block disable). The tm-args param objects in the batch:
`services/tax_calculator.py` (`calculate_federal_withholding` tm-args/locals) and
`services/entry_service.py` (`create_entry` tm-args/pos). (The instance-attr files
`calendar_service`/`spending_trend_service` and the `_build_month_summary` / `_compute_item_trend`
disables they carry are in the deferred instance-attr group above.) Two files still carry **undisposed
Phase-1 smell-disables**
needing a Phase-3 decompose-or-document decision and extra care -- `routes/auth.py` (module tm-lines
+ `reauth`/`mfa_verify`/`mfa_confirm`; security-critical, do not rewrite from scratch) and
`services/transfer_service.py` (`create_transfer`/`update_transfer`; transfer-invariant-critical);
`services/budget_variance_service.py` also retains 3 function tm-args/pos disables (`compute_variance`
/ `_get_transactions_for_window` / `_query_by_date_range`) distinct from its already-resolved
instance-attrs. (Trust-but-verify note 2026-06-06: the Phase-1 "deferred smell-disables" list was
stale -- `dashboard_service:306`, `transfer_service:693`, and `auth.py:357` no longer exist in the
tree; verify against live pylint, not the checklist.) **`ref_cache.py` design fork RESOLVED:** the developer chose the
full-encapsulation route (the plan's Fork B) over the contained Fork A, refined during planning into
**C'-dict** -- a single never-rebound `_RefState` whose 13 maps live in one `enum_ids` registry keyed
by enum class, rather than 13 named fields. That refinement was forced by a trust-but-verify catch: a
14-named-field dataclass trips `too-many-instance-attributes` (verified -- `ProjectedBalance` /
`AmortizationRow` are dataclasses that carry exactly that disable), so named fields would NOT have
reached the zero-disable goal that motivated Fork B; the `enum_ids` collapse stays disable-free AND is
more DRY (the registry already treats the 13 maps uniformly). In-place `_cache` mutation removed all 5
`global` disables with NO residual `global _initialized` -- Fork A's one-disable compromise proved
unnecessary once the flag became an object attribute. The remaining `too-many-instance-attributes`:
`calendar_service` (2), `spending_trend_service` (2), `carry_forward_service` (1). **Note on the retirement_gap_calculator approach (trust-but-verify
divergence from the 2026-06-06 paired-fork ratification):** the ratified plan was keyword-only +
relocate `planned_retirement_date` to the caller (KEEP the field). Verification before coding showed
(a) the result field is write-only -- read by NO production consumer (template, `_build_chart_data`,
all of app/) -- so the developer chose FULL removal (param AND field) as the root-cause/DRY fix, not
relocation; and (b) dropping the param to 5 args clears tm-positional by itself at the default max=5,
so keyword-only was unnecessary and skipped (no unrequested interface change). The only remaining
module tm-lines are `schemas/validation.py` + `services/carry_forward_service.py` (both Tier-3 ->
package split per decision #5).

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
| app/services/investment_projection.py | line-too-long:3, missing-param-doc:1, unused-argument:1 -- **RESOLVED `bf111f0`** (Phase 3 cleared all three in the `calculate_investment_inputs` decomposition: 3 long `employer_params` lines wrapped, `salary_gross_biweekly` param-doc added, dead `account_id` removed at root) |
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
| app/services/retirement_dashboard_service.py | unused-argument:1 -- **RESOLVED `ce65229`** (Phase 3 removed the dead `salary_profiles` param at root) |
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
  accessors read; class encapsulation out of scope). **CORRECTED in Phase 3 (`ebcda36`):** the
  "encapsulation out of scope" rationale was a Phase-1 *lint*-scoping call; the Phase-3 quality pass
  applied the *design* lens and the developer chose full encapsulation (C'-dict) -- the 13 maps +
  `_initialized` moved onto a single never-rebound `_RefState` whose dicts `init()` mutates in place,
  so all 5 `global` statements are gone with no residual disable.
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

### P-3 -- negative paycheck deduction accepted without validation (investment contribution)

- **Where:** `app/services/investment_projection.py` `_compute_deduction_per_period` (and thus
  `calculate_investment_inputs`) applies a deduction `amount` with no sign guard;
  `tests/test_services/test_investment_projection.py::test_negative_deduction_amount` asserts the
  current behavior (a `-500.00` deduction yields `periodic_contribution == -500.00`) and carries a
  pre-existing inline `# BUG: negative deduction amount is silently accepted` comment.
- **Found via:** the `bf111f0` Phase-3 decomposition's independent quality-pass review (flagged as a
  pre-existing, out-of-scope watch-item; the refactor preserved the behavior bit-identically).
- **Impact (needs developer judgement -- financial):** a negative contribution deduction lowers the
  projected periodic contribution. Whether that is ever valid input, or should be rejected at the
  service/schema boundary, is a financial-policy question (rule 3). Not fixed unilaterally (rule 6 --
  out of scope for the lint cleanup; rule 4 -- reported).
- **Recommendation:** developer decides whether to add a `ValidationError` guard (then update the
  test's asserted behavior + drop the `# BUG` comment) or to document the behavior as intended.
  Status: OPEN, reported 2026-06-06.

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
| 2026-06-05 | `e3dbea7` | 3 | **investment_dashboard_service.py -- bundle per-account inputs + extract projection primitives (developer-chosen single `_ProjectionContext`) -- file DONE:** all 6 design smells resolved by genuine decomposition (no logic change, no disables, behavior bit-identical; route-level `test_investment.py` is the gate). New frozen `_ProjectionContext` (6 fields) loaded once by `_load_projection_context` centralizes the entries-aware current balance + the projection-inputs splat + the contribution timeline that the dashboard and growth-chart bodies each resolved inline (S6-01 dup; `params` passed in so neither surface re-queries `InvestmentParams`). New shared `_run_growth_projection` + `_build_chart_series` dedupe two R0801-invisible duplications (the `project_balance` splat + the cumulative-contribution chart loop; variable names differed so R0801 never clustered them; 0 new R0801 surfaced cross-file). `compute_dashboard_data` 26->6 locals (thin: load ctx, merge dict fragments via `**`); `_project_dashboard_balances` 8 args/19 locals -> 3 args/8 locals (returns a dict fragment); `_compute_contribution_prompt` 7->4 args (takes ctx, returns template-keyed dict); `compute_growth_chart_data` 28->11 locals (delegates to `_growth_chart_context`); `_compute_what_if_overlay` 6->4 args; `_compute_employer_per_period` extracts the cap->employer 2-step. file 10.00/10, zero smell messages; 0 disables added (82); useless-suppression 0; E/F 0. Score 9.86; visible 194->188; smell items 93->87 (75 8-symbol + 12 instance-attr). NB: an unused module `logger` remains (pre-existing dead code, not pylint-flagged; left in scope-discipline, reported to developer). 55 investment route + 91 integration/income + **full suite 5755 passed.** | 9.86/10 | 188 |
| 2026-06-05 | `15bcfd1` | 3 | **paycheck_calculator.py -- restructure PaycheckBreakdown into nested sections + decompose calculate_paycheck (developer-chosen full 4-group restructure + two-context ISP + deduction bundle) -- file DONE:** all 5 design smells resolved by genuine decomposition (no disables, behavior bit-identical; full suite 5755 the gate). **`PaycheckBreakdown` 13/7 -> 4/7** by restructuring into 4 cohesive nested sections `period`(`PeriodInfo`)/`earnings`(`Earnings`)/`taxes`(`TaxLines`)/`deductions`(`DeductionBreakdown`), section totals moved onto the owning section (`taxes.total`, `deductions.total_pre_tax`/`total_post_tax`, `earnings.take_home_rate_pct`). **I corrected a bad attribute count mid-decision** (told the developer a TaxLines-only nest was "11->8"; it is actually 13->10/7 and still fails -- only the full 4-group nest reaches 4/7) and **flagged that the chosen full consumer-migration (Option B) was ~400 sites incl. 371 test assertions** vs the safer delegating-property facade (which I argued is itself a DRY/maintainability smell); developer chose Option B. Consumer migration to nested form: app services (income_service, year_end/_income_tax, salary/_helpers+profiles, recurrence_engine, savings/_orchestrator, retirement_dashboard, dashboard_service), the 2 salary templates that actually render a breakdown (breakdown.html, projection.html; the other 7 "matches" disambiguated as collisions), 6 test files **path-only/values-frozen** (test_paycheck_calculator 371 assertions + 11 nested constructors + 7 `_calculate_deductions` call updates; savings C26-3 source guard repointed to `current_breakdown.earnings.gross_biweekly`). `calculate_paycheck` 37->13 locals via frozen `_DeductionContext`(5)+`_PaycheckContext`(5) + `_compute_deductions`/`_compute_tax_lines`/`_bracket_federal`/`_bracket_state`; `_calculate_deductions` 7->2 args (takes `_DeductionContext`, resolves pct_id internally); `_gross_biweekly_for_period` 16->12 via `_residue_cents`; removed the now-dead test `_pct_id` + its unused import. file 10.00/10, 0 smell messages; 0 new R0801; 0 disables added (82); useless-suppression 0; E/F 0; no new line-too-long; 3 total-property `missing-function-docstring` (Phase 4) cleared as a bonus. Score 9.86->9.87; visible 188->180; smell items 87->82 (71 8-symbol + 11 instance-attr). 590 targeted + **full suite 5755 passed.** | 9.87/10 | 180 |
| 2026-06-05 | `ce65229` | 3 | **retirement_dashboard_service.py -- decompose `compute_gap_data` + `_project_retirement_accounts` (functional pipeline + cohesive frozen bundles, developer-chosen) -- file DONE:** all 5 design smells + the dead `salary_profiles` parameter resolved by genuine decomposition (no disables, behavior bit-identical). **Architecture (developer-reviewed):** chose the functional-pipeline style (pure helpers + frozen bundles) over a threaded mutable accumulator (fights robustness) or a stateful class (against the plain-function service convention; would relocate the smell to instance-attrs). `compute_gap_data` (38 locals/51 stmts) -> 14 locals as a thin delegation pipeline -- `_compute_pension_benefit`(`_PensionSummary`) / `_compute_current_pay`(`_CurrentPay`) / `_resolve_planned_retirement_date` / `_build_projection_context` / `_compute_gap_net_biweekly` (gap-comparison salary block rewritten as guard clauses, verified result-identical) / `_resolve_estimated_tax_rate` / `_build_chart_data`; the central `calculate_gap` kept VISIBLE in the orchestrator (6 genuine cross-phase inputs -> wrapping it only relocates the smell). `_project_retirement_accounts` 8 args/8 pos/31 locals -> 1 arg (`ctx`) via the frozen `_RetirementProjectionContext`(7) + `_load_projection_batch`(`_ProjectionBatch`) / `_resolve_current_balances` / `_project_one_account`; the dead `salary_profiles` param removed at root (cleared the `unused-argument`). **Bundle granularity decided by the travel-together cohesion test:** four cohesive frozen dataclasses kept; NO `_RetirementBaseData` for the three top-level loads -- they FAN OUT to different consumers, so bundling = stamp coupling (ISP smell), kept as plain locals. Per-account projection dict shape (calculate_gap/slider/template/test contract) preserved verbatim; `_resolve_swr_fraction` / `compute_slider_defaults` / docstring / constants byte-identical (`git diff`-verified); CRIT-04/E-12 `is None` + LOW-05 carry-open verbatim so the source guard stays green. Removed a pre-existing dead module `logger` (+ unused `import logging`, developer-approved). file 10.00/10, 0 smell messages; 0 new R0801; 0 new tm-instance-attributes; 0 disables (82); E/F 0; useless-suppression 0. Score 9.87 held; visible 180->174; smell items 82->77. 94 targeted + **full suite 5755 passed.** | 9.87/10 | 174 |
| 2026-06-05 | `8e01099` | 3 | **_recurrence_form_helpers.py + _commit_helpers.py -- bundle redirect/stale-conflict/recurrence-form args (developer-chosen Max-DRY + RedirectTarget) -- both files DONE:** all 8 design smells (5 in `_recurrence_form_helpers`, 3 in `_commit_helpers`) resolved by genuine decomposition (no disables, behavior bit-identical; full suite 5755 the gate). New frozen `RedirectTarget(endpoint, kwargs)` value type in new module `routes/_redirect_target.py` (+ `to_response()` -- the single home for the `redirect(url_for(e, **(k or {})))` idiom shared ~9 ways across the helper layer; also unified the `redirect_kwargs` vs `redirect_endpoint_kwargs` naming drift between `_transfer_creation_helpers` and the others), composed into two frozen contexts. `RecurrenceFormContext` (`end_date_value`/`redirect`/`include_due_day_of_month`) collapses the verbatim triplicated signature tail shared by `build_recurrence_rule_from_form` (7->4 args + 16->13 locals) / `update_recurrence_rule_from_form` (6->3) / `resolve_recurrence_rule_for_update` (6->3). Shared `StaleConflictContext` (`logger`/`log_label`/`log_id`/`flash_message`/`redirect`) lives in `_commit_helpers.py` (the canonical handler's home; imported by `_recurrence_form_helpers` -- one-way edge, no cycle) and drives `handle_stale_conflict`/`commit_or_handle_stale`/`regenerate_and_commit_or_stale` (6/6/7->1/1/2) AND the pre-flush mirror `handle_stale_form_conflict` (8->3). ~30 call sites rewrapped across 8 route files (templates/transfers/accounts/savings/salary/investment/loan); `_transfer_creation_helpers`' `validate_and_resolve_source_account` + `flush_template_or_namedup_redirect` moved to `RedirectTarget` too. Test patch update (decision #5, no assertion change): `test_recurrence_form_helpers.py` 9 call-shapes rewrapped, asserted values frozen byte-identical. **Split-trap check: 0 new R0801** -- the repeated `StaleConflictContext(...)`/`RecurrenceFormContext(...)` wrapping did NOT cluster (log_labels / flash strings / endpoints differ; existing one-sided dup disables on the create/update preambles still cover them). 0 disables added (82); useless-suppression 0; E/F 0. **NOT yet DONE:** `_transfer_creation_helpers.build_recurring_transfer_template` (6-field `TransferTemplate` constructor, an unrelated argument clump) remains -- a separate pending design decision. Score 9.87->9.88; visible 174->166; smell items 77->69 (58 8-symbol + 11 instance-attr). 754 targeted (helper + 7 route suites) + **full suite 5755 passed.** | 9.88/10 | 166 |
| 2026-06-05 | `59ba11a` | 3 | **_transfer_creation_helpers.py -- externalize `derive_from_loan` from `build_recurring_transfer_template` (developer-chosen genuine structural reduction) -- file DONE:** the last `too-many-arguments` in the form-mutation helper layer cleared WITHOUT a param object or a disable. The 6-field shared `TransferTemplate` factory was flagged because a param object there would only mirror the entity's own columns (single-consumer stamp coupling, zero DRY payoff -- unlike the `8e01099` bundles that dissolved real cross-function duplication), and a disable would break the 0-added streak. Instead `derive_from_loan` was dropped from the helper (6->5 args, at the limit), relying on the column's verified `False` model/server default for investment contributions and every generic transfer; the loan-payment creator -- the ONLY caller that needs it -- assigns `template.derive_from_loan` itself on the returned row before the `flush_template_or_namedup_redirect` flush (`_resolve_transfer_amount` returns the computed bool: `True` for the monthly-payment default path, `False` for a user-supplied amount override), keeping the loan-only concern at the loan call site. Behavior bit-identical. Investment caller unchanged (never passed the flag). **Closed a coverage gap:** strengthened the route test `test_create_transfer_success` with `assert tpl.derive_from_loan is True` -- previously the route's setting of the flag was unasserted (no `amount` posted -> live-derivation path), so this both covers it and locks the refactor; the `False` override path is NOT separately asserted because the model default is also `False` (an assertion there would not distinguish the code from the default). 0 disables added (82); useless-suppression 0; E/F 0. Score 9.88 held; visible 166->165; smell items 69->68 (57 8-symbol + 11 instance-attr). 253 targeted (investment + loan) + **full suite 5755 passed.** | 9.88/10 | 165 |
| 2026-06-05 | `86541bb` | 3 | **grid.py -- bundle row-data into `_GridRowData` + pass `_GridContext` to `_build_plan_view` (developer-chosen ctx-passing with a new `user_id` field) -- file DONE:** all 4 design smells (`_build_plan_view` tm-args/pos/locals; `index` tm-locals) resolved by genuine decomposition (no disables, behavior bit-identical). New frozen `_GridRowData` NamedTuple (6 fields) replaces `_build_grid_row_data`'s 6-tuple return -- the per-render "row contract" spliced into grid.html; naming the six values collapses the 6-local unpack to ONE in both `index` (clears tm-locals) and `_build_plan_view` (halves its locals). `_build_plan_view` 8->5 args (clears tm-args + tm-positional) by taking the existing `_GridContext` (`ctx`, given a new `user_id` field) instead of unpacking account/scenario/current_period/user_id; the 4 remaining loaded values fan out to different consumers so they stay unbundled (stamp-coupling avoided). Impact-traced clean (private helpers, no external callers / test constructors; `RowKey`/`grid_bp` untouched). Fixed two stale docstring counts ("5-tuple"->named-6; "eight"->"six" `plan_*` keys). file 10.00/10, 0 smell messages; 0 new R0801; instance-attrs unchanged at 11 (NamedTuple fields not counted by R0902); 0 disables added (82); useless-suppression 0; E/F 0. Score 9.88 held; visible 165->161; smell items 68->64 (53 8-symbol + 11 instance-attr). 221 grid + 93 companion targeted + **full suite 5755 passed.** | 9.88/10 | 161 |
| 2026-06-06 | `1c26575` | 3 | **templates.py -- decompose update_template + preview_recurrence + publicize match_periods (developer-chosen broad scope) -- file DONE:** all 4 live design smells (`update_template` tm-locals 16/15 + tm-return 8/6 + tm-branches 15/12; `preview_recurrence` tm-locals 17/15) PLUS the `preview_recurrence` protected-access (716) resolved by genuine decomposition (no disables, behavior bit-identical). `update_template` via the shared `_validate_template_form` (account/category ownership + envelope-only-on-expense, now used by BOTH create_template + update_template -- 2-site DRY; create's required FKs are always in `data`, so the `in data` guards preserve both paths -- verified vs TemplateCreateSchema) + `_apply_fields_and_propagate_rename` -> ~11 locals/6 returns/8 branches (8->6 returns collapses the 3 FK/tracking guards to 1; no disable, as a disable at the 6/6 limit would be a useless-suppression). `preview_recurrence` via `_build_preview_rule` (request.args -> transient rule) + `_render_preview_html` -> ~9 locals; the every_n condition was wrapped (cleared 1 line-too-long). Protected-access cleared by promoting `recurrence_engine._match_periods` -> public `match_periods` (pure, 27 direct unit tests, cross-module caller -- the underscore mislabeled a de-facto public API), which ALSO cleared the Tier-3 `match_periods` tm-return (8/6) via a single-return accumulator (developer-chosen over a dispatch dict); renamed 2 internal callers + 2 doc refs + the test import/27 calls + the TEST_PLAN.md header (name-only, decision #5). Independent quality-pass review (fresh subagent, A-G rubric, all 7 behavior-equivalence points verified against the code): ALL ACCEPT, 0 REFINE/REVERT-OVERREACH; the lone stale-doc finding folded into the rename. 0 disables added (82); 0 new R0801; instance-attrs unchanged at 11. Score 9.88->9.89; visible 161->154; smell items 64->59 (48 8-symbol + 11 instance-attr). 242 targeted + **full suite 5766 passed.** | 9.89/10 | 154 |
| 2026-06-06 | `dcf0d4e` | 3 | **growth_engine.py -- decompose project_balance + share _period_return_rate (developer-chosen documented disables for the irreducible args/DTO) -- file DONE:** all 4 design smells resolved. **tm-locals** (`project_balance` 23/15) by genuine decomposition mirroring `amortization_engine`: a frozen `_PeriodInputs` (the loop's fixed constants) + a mutable `_ProjectionState` (the evolving balance/YTD/limit/year carry) + `_project_one_period`, leaving `project_balance` a ~14-local orchestrator. **DRY win:** the byte-identical period-day->compound-rate math in `project_balance` + `reverse_project_balance` (R0801-invisible; the surrounding code differed) extracted to the shared `_period_return_rate` -- and since reverse inverts the forward formula, sharing the rate makes "the two cannot diverge" structural, not incidental. **tm-arguments/tm-positional** (`project_balance` 8/5): documented scoped+named+commented disable (the pure stdlib leaf's 8 inputs vary independently per caller -- the what-if overlay overrides `periodic_contribution` + nulls `contributions`, year-end forces ytd=0 -- so a param object = stamp coupling; reusing `InvestmentInputs` would cycle since it imports `growth_engine`; all callers pass keyword so tm-positional is moot). **tm-instance-attributes** (`ProjectedBalance` 9/7): documented disable -- a cohesive per-period schedule row mirroring `AmortizationRow`; `is_confirmed` is the deliberately-plumbed confirmed/projected distinction (`implementation_plan_section5.md`). Independent quality-pass review (fresh subagent, A-G rubric): **ACCEPT overall**, all 6 behavior-equivalence points verified line-for-line, both disables upheld, **0 REVERT-OVERREACH**; 2 LOW REFINE folded in -- tightened `contribution_lookup` -> `dict[date, tuple[Decimal, bool]] | None`, added `test_degenerate_period_falls_back_to_14_days` (the `period_days <= 0 -> 14` branch, now shared by both directions, was untested). file 10.00/10, 0 smell messages; +2 documented disable lines (82->84); 0 new R0801; useless-suppression 0; E/F 0. Score 9.89 held; visible 154->150; smell items 59->55 (45 8-symbol + 10 instance-attr). 66 growth + 242 consumer + **full suite 5767 passed.** | 9.89/10 | 150 |
| 2026-06-06 | `41f42a8` | 3 | **loan_resolver.py -- bundle loan inputs (LoanInputs) + split into a package (developer-chosen, two-phase in one commit) -- file DONE:** all 4 design smells (`resolve_loan` tm-locals 16; `compute_payoff_scenarios` tm-args 6/5 + tm-locals 26; `PayoffScenarios` tm-instance-attributes 10/7) resolved. The frozen `LoanInputs(loan_params, anchor_events, payments, rate_changes)` -- the data clump EVERY caller already co-loads (three separate loads per site: a params query + `load_loan_context` + an anchor-event query) -- is shared by `resolve_loan` (5->2 args, tm-locals 16->~10) and `compute_payoff_scenarios` (6->3 args); `compute_monthly_payment_baseline` was left untouched (its `unused-argument` disable ties to OPEN problem P-1). The shared `_replay_from_anchor` dedupes the anchor-select+replay both use -- replay ONLY, never `project_forward`, so the resolver's documented "current_balance derived independently of the schedule generation" invariant holds structurally (genuine 2-site DRY). `compute_payoff_scenarios` tm-locals (26->~13) via `_build_forward_inputs` -> the frozen `_ProjectionPrep`(3) setup bundle (replay/contractual/override/history/projection_inputs), leaving a thin "project three ways, then summarize" orchestrator (summary metrics kept inline, byte-identical to the original). `PayoffScenarios`(10/7) -> documented scoped+named+commented disable (cohesive single-return result aggregate: 3 chart slices + history + 6 summary metrics read flat by one consumer; `PayoffRequest`/`AmortizationRow` precedent). **Package split (decision #5):** the decomposition pushed the module to 1009 lines (new too-many-lines), so it became the `app/services/loan_resolver/` package -- `_periods` (rate periods/anchor/replay + `LoanInputs`) / `_state` (`LoanState` + `resolve_loan` + `compute_monthly_payment_baseline`) / `_payoff` (`PayoffScenarios` + composer + helpers); `__init__` re-exports the 6 public names (+`__all__`) so every import path is preserved; acyclic DAG `_periods <- _payoff <- _state`; **0 new R0801** (no split-trap). ~52 call sites wrapped in `LoanInputs(...)` across 8 files (6 app + 46 test, values frozen byte-identical); 3 test source-inspection guards repointed to scan the package dir via the new `_loan_resolver_package_source()` helper; the C15-3 demoted-column allow-list `services/loan_resolver.py:` -> `services/loan_resolver/` (the `.current_principal` prose moved to the `__init__`/`_state` docstrings). Independent quality-pass review (fresh subagent, A-G rubric, all 6 behavior-equivalence points verified line-for-line): **ACCEPT overall, 0 REVERT-OVERREACH, 0 REFINE**; F8 the lone LOW note (the composer-only `inspect.getsource(compute_payoff_scenarios)` purity guard's reach narrowed by the `_build_forward_inputs` extraction, but the package-wide purity guard scans `_payoff.py` in full -- no gap, no change). +1 documented disable (84->85); package 10.00/10; score 9.89 held; visible 150->146; smell items 55->51 (42 8-symbol + 9 instance-attr); 0 R0801/E/F/useless-suppression. 53 resolver + 313 consumer (loan/debt_strategy/savings/loan_payment) targeted + **full suite 5767 passed (after both the decomposition and the split).** | 9.89/10 | 146 |
| 2026-06-06 | `2b0f5ca` | 3 | **retirement_gap_calculator.py -- remove dead `planned_retirement_date` + decompose `calculate_gap` (developer-chosen full removal over the plan's relocate-the-write option) -- file DONE:** all 4 design smells resolved by genuine root-cause change (behavior bit-identical). Verification showed `gap_result.planned_retirement_date` is **write-only** -- read by NO production consumer (not `_gap_analysis.html`, not `_build_chart_data`, nowhere in app/; grep-confirmed independently by the quality-pass reviewer) -- so dropping the param AND the `RetirementGapAnalysis` field is the DRY fix, not relocating the write. `calculate_gap` **tm-arguments + tm-positional** (6 params) cleared by dropping the param 6->5 (both clear at the pylint default max=5, NO disable -- the `derive_from_loan`/`59ba11a` precedent, except here the relocated attribute would be dead so it was removed outright). **tm-locals** (19->14) via the pure helpers `_after_tax_projected_savings` (trad/Roth bucketing + whole-expression quantize -- the load-bearing extraction; without it 18>15) + `_sum_projected_balances` (per-account total -- kept for orchestrator-altitude symmetry, NOT threshold-necessary: inlining the sum alone leaves exactly 15, which passes). `RetirementGapAnalysis` **tm-instance-attributes** (11->10 after the field drop, still >7) -> documented scoped+named+commented disable (cohesive single-return aggregate, flat row-per-field render; `AmortizationRow`/`PayoffRequest`/`ProjectedBalance` precedent). Single keyword caller (`retirement_dashboard_service`) + 27 test calls updated; deleted `test_planned_retirement_date_passed_through` (rule-5 exception -- tested removed behavior); `test_result_field_completeness` 11->10 fields; removed the now-unused `datetime.date` import. Independent quality-pass review (fresh subagent, A-G rubric, all 4 behavior-equivalence points verified line-for-line incl. the whole-expression quantize order): **ACCEPT, 0 REVERT-OVERREACH, 0 REFINE** (the lone LOW note on `_sum_projected_balances` accepted; the reviewer's "reverting re-trips tm-locals" rationale was wrong and corrected -- inlining the sum leaves 15). File clears all design smells (only the pre-existing `line-too-long:145` Phase-4 residue remains); +1 documented disable (85->86); 0 new R0801; E/F 0; useless-suppression 0. Score 9.89->9.90; visible 146->142; smell items 51->47 (39 8-symbol + 8 instance-attr). 122 targeted (calculator + dashboard service + route) + **full suite 5766 passed.** | 9.90/10 | 142 |
| 2026-06-06 | `8449f21` | 3 | **debt_strategy.py (route) -- funnel calculate errors through `_ResultsError` + decompose (developer-chosen internal-exception approach) -- file DONE:** all 3 design smells on the `calculate` handler (tm-locals 17/15 + tm-return 7/6 + tm-branches) resolved by genuine decomposition (no disables, behavior bit-identical; full suite 5766 the gate). **tm-return** (the crux): the 5 duplicated `_results.html` error renders (schema reject / malformed custom order / no debts / a simulation `ValueError`) funnel through a new private `_ResultsError(Exception)` + a SINGLE try/except in the handler -- the DRY collapse of one error contract (every user-input failure renders the same banner at HTTP 200) -- leaving 3 returns; the IDOR 404 stays a DIRECT return inside the try (a distinct HTTP contract, deliberately NOT funneled). **tm-locals/branches** via cohesive extraction: `_parse_calculate_form` (schema load + custom_order int-coercion), `_custom_order_has_unknown_account` (the IDOR set-membership check), `_compute_strategies` (-> the frozen `_StrategyResults`(4) bundle; logs the two DISTINCT scenario labels internally then re-raises `_ResultsError(str(exc))` so the funnel preserves them), `_select_result` (the custom/snowball/avalanche pick); `_build_comparison` retargeted from 4 positional results to the bundle. **Dead-code:** grep-verified `_results.html` reads only error/comparison/selected_result/selected_strategy/has_arm/chart_data_json, so 6 render kwargs (baseline/avalanche/snowball/custom_result/extra_monthly/debt_accounts) were removed (developer-approved). Independent quality-pass review (fresh subagent, A-G rubric, all 9 behavior-equivalence points verified line-for-line incl. the `custom_raw` relocation, the IDOR `any()` equivalence, distinct logs, single `today`, and the 6 dead kwargs grep): **ACCEPT, 0 REVERT-OVERREACH, 0 REFINE** (the `_ResultsError`/`_StrategyResults`/3-tuple choices each upheld after arguing both simpler? and right-abstraction?). The lone MED finding -- a PRE-EXISTING route-level test gap on the reachable compute-error funnel (a duplicate/incomplete `custom_order` the route does not dedupe -> the service `ValueError` -> the banner) + the custom-vs-avalanche selection -- was closed in a separate test commit (`9efb7b4`, +3 route tests: duplicate + incomplete custom_order render the message at 200, and a custom order opposite of avalanche's rate-order drives the chart; the baseline/avalanche/snowball except is route-unreachable -- schema bounds extra/strategy, zero-payment loans are skipped -- so NOT mocked, rule 13). file 10.00/10, 0 smell messages; 0 disables added (86); 0 new R0801; useless-suppression 0; E/F 0. Score 9.90 held; visible 142->139; smell items 47->44 (36 8-symbol + 8 instance-attr). 37 debt_strategy route + 15 C-27 sweep targeted + **full suite 5766 passed.** | 9.90/10 | 139 |
| 2026-06-06 | `bf111f0` | 3 | **investment_projection.py (+ projection_inputs.py wrapper) -- drop dead account_id + decompose calculate_investment_inputs (developer-chosen documented disable for the residual args) -- both files DONE:** all design smells on the coupled `calculate_investment_inputs` (tm-args/pos/locals) + its pure pass-through wrapper `build_investment_projection_inputs` (tm-args/pos) resolved (behavior bit-identical). The dead `account_id` param -- forwarded by the wrapper, never read by the callee -- removed at root from both signatures + all 5 production call sites (investment/retirement/savings dashboards, year_end `_savings`/`_balances`) + 18 test calls (clears `unused-argument`; the `salary_profiles`/`planned_retirement_date` dead-param precedent). `calculate_investment_inputs`'s 5 steps decomposed into `_periodic_from_deductions` / `_average_transfer_contribution` / `_employer_params` / `_ytd_contributions` (orchestrator ~2 locals, clears tm-locals). The residual **6 independent, heterogeneous inputs** (1 over max after the dead-param drop; verified against ALL 5 consumers that `all_periods`/`current_period` are sourced/varied independently, so no cohesive sub-bundle exists and a param object would be stamp coupling) take a documented scoped+named+commented `too-many-arguments,too-many-positional-arguments` disable on BOTH public functions, mirroring the sibling `growth_engine.project_balance`. The wrapper stays the "single splat home" (the `salary_gross_biweekly=salary_gross_biweekly,)` canary at :236 remains the only matching site). Overlapping Phase-4 residue cleared in the same lines: missing `salary_gross_biweekly` param-doc, 3 long `employer_params` lines wrapped, stale "Lazy import" comment removed. Two guard-clause rewrites verified equivalent: the employer-type build condition (De Morgan) and `if current_period:` -> `if current_period is None:` (PayPeriod has no `__bool__`; also aligns with the `is None` coding standard). Independent quality-pass review (fresh subagent, A-G rubric, all 5 steps verified line-for-line): **ACCEPT, 0 REVERT-OVERREACH, 0 REFINE**; lone watch-item a PRE-EXISTING, untouched negative-deduction `# BUG` test comment -> reported as P-3 (financial-policy, rule 3). Both files 10.00/10, 0 smell messages; +2 documented disables (86->88); 0 new R0801; useless-suppression 0; E/F 0. Score 9.90->9.91; visible 139->129; smell items 44->39 (31 8-symbol + 8 instance-attr). 39 targeted (investment_projection + projection_inputs) + 375 consumer (retirement/savings/year_end/income services + investment/retirement/savings routes) + **full suite 5769 passed.** | 9.91/10 | 129 |
| 2026-06-06 | `b5a9d56` | 3 | **budget_variance_service.py -- extract VarianceFigures value object to dedupe the variance quad (developer-chosen Option B over documented disables) -- file DONE:** all 3 `too-many-instance-attributes` smells (`TransactionVariance` 8/7, `CategoryItemVariance` 9/7, `VarianceReport` 8/7) cleared by a frozen `VarianceFigures(estimated, actual, variance, variance_pct)` value object + an `of(estimated, actual)` factory -- NO disables. The factory is the single home for the `variance = actual - estimated; variance_pct = _pct(variance, estimated)` computation that was hand-written 4x (txn `_build_txn_variance` / item + group `_build_group_hierarchy` / report-total `compute_variance`) -- an R0801-invisible duplication because the locals differed at each level (the `investment_dashboard_service` precedent). The 4 DTOs each drop the quad to ONE `figures` field (5/6/3/5 attrs, all <=7; `CategoryGroupVariance` went 6->3 too), which ALSO dissolves the `estimated`/`estimated_total`/`total_estimated` naming drift. All consumers migrated to `.figures.*`: `analytics._build_variance_chart_data` (3 reads), `csv_export_service.export_variance_csv` (~12), the `_variance.html` template (~20). Tests are path-only with expected values frozen byte-identical (`test_budget_variance_service` ~30 assertions; the `test_csv_export_service` `Fake*Variance` stand-ins gained a nested `FakeVarianceFigures` + a `_zero_figures` empty-report default; the `test_services_no_flask` scanner-pin docstring's cited `sum(g.estimated_total...)`/`lambda g: abs(g.variance)` expressions repointed to `.figures.*`). Independent quality-pass review (fresh subagent, A-G rubric, all 5 behavior-equivalence points verified line-for-line: the `of()` arithmetic vs the 4 former inline computes, the empty-report `sum()` -> int-`0` through `_pct`'s `== Decimal("0")` zero-guard yielding `variance_pct=None`, the sort-key equivalence, byte-identical CSV/template output, and the value-frozen test updates): **ACCEPT, 0 REVERT-OVERREACH, 0 REFINE**; the lone nit (derived `variance`/`variance_pct` stored as fields vs `@property`) accepted -- consistent with the pre-refactor design and gated behind the sole `of()` constructor; making them properties would be an unrequested redesign. No schema change (no migration). file 10.00/10, 0 smell messages; 0 disables added (88); 0 new R0801; useless-suppression 0; E/F 0. Score 9.91 held; visible 129->126; smell items 39->36 (31 8-symbol + 8->5 instance-attr). 205 targeted (budget_variance + csv_export + analytics route + C-30 ownership + arch) + **full suite 5769 passed.** | 9.91/10 | 126 |
| 2026-06-06 | `4e625fe` | 3 | **calibration_service.py -- bundle derive_effective_rates inputs into the frozen PayStubActuals param object -- file DONE:** all 3 design smells on `derive_effective_rates` (`too-many-arguments` 6/5, `too-many-positional-arguments` 6/5, `too-many-locals` 16/15) cleared by bundling its 6 inputs into a new frozen `PayStubActuals` value object (1 arg, ~11 locals; NO disable, behavior bit-identical -- the `Decimal(str(...))` construct-from-strings coercion preserved verbatim). The bundle is a genuine domain concept, NOT a count-dodge: the five `actual_*` mirror the `CalibrationOverride` columns (the model persists them as a cohesive unit with one CHECK constraint per field), and `taxable_income` is the route-computed federal/state divisor (gross minus pre-tax deductions -- NOT a stored column). The 2 production callers (`salary/calibration.py` `calibrate_preview` + `calibrate_confirm`) + 13 test call sites (`test_calibration_service.py` 12, `test_paycheck_calculator.py` 1) rewrapped in `PayStubActuals(...)`, every monetary value frozen byte-identical (the string-input test keeps its strings -- a call-shape change, not a rule-5 assertion change). Independent quality-pass review (fresh subagent, A-G rubric, behavior verified line-for-line incl. the string-coercion path + both `ValidationError` guards + the four rate computes): **ACCEPT, 0 REVERT-OVERREACH, 0 REFINE** -- 3 findings, all ACCEPT after verification: (A1/A2/A5) `PayStubActuals` clears the cohesive-named-concept bar -- all 6 fields are consumed together by both routes + the function, no stamp coupling, the raw+derived altitude mix is documented; (C1/F1) `frozen=True` upheld as correct for an immutable snapshot, the non-frozen sibling `DerivedRates` left untouched (out of scope, rule 6); (D2/E2) the `Decimal` field hints match the production contract -- verified `CalibrationSchema`/`CalibrationConfirmSchema` use `fields.Decimal` (validation.py:2199-2266) so production always passes Decimal, the string path is test-only defensiveness pinning the standard construct-from-strings idiom, so widening to `Decimal \| str` would misrepresent the contract. file 10.00/10, 0 smell messages; 0 disables added (88); 0 new R0801; useless-suppression 0; E/F 0. Score 9.91 held; visible 126->123; smell items 36->33 (28 8-symbol + 5 instance-attr). 236 targeted (calibration + paycheck + salary) + **full suite 5769 passed.** | 9.91/10 | 123 |
| 2026-06-06 | `ebcda36` (+ test `d2b1c31`) | 3 | **ref_cache.py -- replace 14 module globals with a never-rebound `_RefState` + spec registry (developer-chosen C'-dict / full encapsulation) -- file DONE** (backfilled row; full detail in the status banner + the Tier-3 register): `init` tm-locals (31/15) + tm-branches (51/12) + tm-statements (124/50) cleared by a from-scratch redesign -- the 13 maps + `_initialized` collapsed onto one never-rebound `_RefState` (`_cache`) holding a single `enum_ids` registry keyed by enum class (stays under `too-many-instance-attributes` with NO disable, where 14 named fields would have needed one), driven by a frozen `_RefSpec` + `_build_ref_specs` single load/sweep loop and a `_require_init()` guard helper. In-place `_cache` mutation removed all **5 `global`-statement disables** (88->83). Behavior byte-identical (public free-function API, the `unavailable` return contract consumed by `app/__init__.py:192`, RuntimeError text/order; error prefixes/labels derive from the model, `RoleEnum`->`UserRole` verified). Independent quality-pass: ACCEPT, 0 REVERT-OVERREACH, behavior verified BYTE-IDENTICAL empirically (old-vs-new harness over every edge case); 1 REFINE applied -- F5 hand-pinned bootstrap/`unavailable` regression test (`d2b1c31`). 0 new R0801; visible 123->119; smell items 33->30 (25 8-symbol + 5 instance-attr); score 9.91 held; full suite 5769->5770 passed. | 9.91/10 | 119 |
| 2026-06-06 | `32c403a` | 3 | **ref_seeds.py -- decompose seed_reference_data into per-step helpers (no disable) + type the signatures -- file DONE:** `seed_reference_data` tm-locals (19/15) + tm-branches (15/12) cleared by genuine decomposition into a thin orchestrator + three cohesive single-responsibility step helpers (`_seed_account_type_categories` insert-only categories / `_seed_account_types` upsert-with-metadata-refresh / `_seed_other_ref_tables` name-only+dict inserts) -- NO disable. The one load-bearing cross-step invariant -- the `session.flush()` that makes the AccountTypeCategory PKs visible to the AccountType FK -- lifted to the orchestrator altitude (between steps 1 and 2, where it cannot be missed) and restated in the helper docstrings; `ref_models` threaded into each helper so the deferred `app.models.ref` import stays in exactly one place, preserving the module's side-effect-free-at-import discipline (verified: importing `app.ref_seeds` loads the same module set as HEAD; the change is runtime-import-neutral). All 4 signatures typed via a `TYPE_CHECKING` block + `from __future__ import annotations` (`session: Session`, `ref_models: ModuleType`, `verbose: bool`, `-> None` -- lazy-string annotations, ZERO runtime imports added; mirrors the sibling `loan_resolver/_periods.py` pattern). This was the developer-chosen resolution of the quality-pass's lone LOW D2 finding (untyped signatures): add the hints via the import-free pattern rather than ACCEPT per the `ref_cache`/`growth_engine` precedent. Public `seed_reference_data(session, *, verbose=False)` + the 3 script/app call sites (`app/__init__.py:851`, `scripts/seed_ref_tables.py:36`, `scripts/build_test_template.py:226`) + conftest unchanged; no structural/source guard pins the function. Independent 3-lens quality-pass (behavior-equivalence / simplicity / right-abstraction): all `behavior_equivalent=yes` -- one reviewer proved deep-AST equality of the inlined-after body vs the original single-function body (loop interiors included) -- 0 REVERT-OVERREACH, 0 HIGH/MED. 0 disables added (83); 0 new R0801; visible 119->117; smell items 30->28 (23 8-symbol + 5 instance-attr); score 9.91 held; 38 seed-dependent targeted + **full suite 5770 passed.** | 9.91/10 | 117 |
| 2026-06-06 | `1d52d3f` | 3 | **settings.py -- decompose show/update + single home for the dashboard context (no disable) -- file DONE:** `show` tm-locals (21/15) cleared by replacing its ~19 parallel template-variable locals with a single `_empty_section_context()` default dict + per-section loaders (`_load_categories_context` / `_load_tax_context` / `_load_account_types_context` / `_load_security_context`; `_load_companions_context` reused; trivial `general` inline) merged via `context.update(...)` -- the ratified `routes/loan` dashboard per-section-builder precedent (show -> ~3 locals). `update` tm-branches (13/12) cleared by collapsing the six identical `field in data and data[field] is not None -> settings.field = data[field]` copies into a loop over the new allowlist constant `_SIMPLE_SETTINGS_FIELDS` + `setattr` (the E-28/HIGH-06/PA-01 percent->fraction domain rationale moved onto the constant comment, `is not None` guard preserved for E-12 zero-is-a-value); the IDOR-checked `default_grid_account_id` branch + its flash/redirect stay inline. **DRY:** `_empty_section_context()` dissolved the empty-defaults contract that was triplicated (`show` inline + `_empty_companions_context` + `_render_companions_section`'s hand-listed kwargs) -- `_empty_companions_context` removed (subsumed), `_render_companions_section` routed through the shared helper so the GET render and the companion-form re-render can no longer drift; static icon list lifted to the immutable tuple `_ACCOUNT_TYPE_ICON_CHOICES`. Behavior bit-identical: all 8 sections supply byte-identical render kwargs (the template's "all keys always supplied" contract preserved -- verified by empty set-diff both directions), the `update` loop applies the same fields with the same guard, and the IDOR branch + all 3 flash/redirect paths are untouched. Independent 3-lens quality-pass (behavior-equivalence / simplicity / right-abstraction): all `behavior_equivalent=yes`, **0 REVERT-OVERREACH, 0 REFINE**; the scope of touching the non-flagged `_render_companions_section` ruled justified DRY (one shared template-default contract), and the lone LOW D2 note (the lifted icon list as a `list`) folded in as a `tuple`. (Note: a transient 7-"error" full-suite run was a `SKIP_DB_RESTART=1` stale-worker-DB `DuplicateDatabase` flake under concurrent `pylint app/` load, NOT a regression -- the errored files passed in isolation and a clean restart-first run was green.) file 10.00/10, 0 smell messages; 0 disables added (83); 0 new R0801; useless-suppression 0; E/F 0. Score 9.91->9.92; visible 117->115; smell items 28->26 (21 8-symbol + 5 instance-attr). 91 settings + companion targeted + **full suite 5770 passed.** | 9.92/10 | 115 |
| 2026-06-06 | `7a77db9` | 3 | **obligations.py -- single-return _next_occurrence + extract summary loaders/builder (no disable) -- file DONE:** `_next_occurrence` tm-return (7/6) cleared by a single-return accumulator: the per-pattern `if pid in (...): return <date>` dispatch becomes an if/elif assigning `next_date` then one `return next_date`; the early end_date-in-the-past guard stays an early `return None`; `day = rule.day_of_month or 1` / `month = rule.month_of_year or 1` hoisted above the dispatch (pure reads, unused by the period/unknown branches; the old per-branch `start_month` -> the hoisted `month`). The `recurrence_engine.match_periods` accumulator precedent (chosen over a dispatch dict for heterogeneous branches); the `duplicate-code` disable block (7 pattern-id lookups) unchanged. `summary` tm-locals (18/15) cleared by extracting the 3 verbose queries to `_load_recurring_expenses`/`_load_recurring_income`/`_load_recurring_transfers` (queries verbatim; expense/income type-id resolution moved inside) + `_build_items(templates, renderer, as_of)` deduping the 3 byte-identical build loops (differed only in the renderer; generic over a constrained `TypeVar` in `(TransactionTemplate, TransferTemplate)` -- the renderer/template type-pairing a plain Union can't express). summary -> ~14-local orchestrator. Behavior bit-identical: every pattern branch (incl. month-day clamp / year rollover in the unchanged `_next_monthly`/`_next_annual`/`_next_periodic_month`, and the unknown-pattern -> None fallthrough); the loaders' exact options/filter/order_by; `_build_items` preserves the E-24/HIGH-05 row-iff-subtotal invariant (rows via `template_monthly_or_none`, subtotals via `committed_monthly`, both through `obligations_aggregator`); all 10 render kwargs unchanged. All touched helpers private to the module (only `obligations_bp` is imported externally); no source guard. Independent 3-lens quality-pass (behavior-equivalence / simplicity / right-abstraction): all `behavior_equivalent=yes`, **12 findings all ACCEPT, 0 REVERT-OVERREACH, 0 REFINE** -- the constrained TypeVar ruled correct-not-overkill (2 reviewers), the 3 single-use loaders genuine query-extraction (loan.py precedent), the `day`/`month` hoist an accepted E3 tiny-waste, and the invariant verified preserved (not just claimed) via `test_expired_templates_excluded`. file 10.00/10, 0 smell messages; 0 disables added (83); 0 new R0801; useless-suppression 0; E/F 0. Score 9.92 held; visible 115->113; smell items 26->24 (19 8-symbol + 5 instance-attr). 18 obligations targeted + **full suite 5770 passed.** (A transient full-suite run hit `connection refused` on the test DB -- a quality-pass reviewer's own `./scripts/test.sh` restarted the shared `shekel-dev-test-db` container mid-run; the clean isolated rerun was 5770. Lesson: run the suite ALONE.) | 9.92/10 | 113 |
| 2026-06-06 | `c5182ea` | 3 | **accounts/detail.py -- extract shared detail-page helpers + reuse the horizon util (no disable) -- file DONE:** both detail handlers' `too-many-locals` (`interest_detail` 16/15, `checking_detail` 17/15) cleared by genuine decomposition. Three module-private helpers: `_current_period_balance(balances, current_period, anchor)` (the identical current-period-balance-else-anchor-fallback both handlers had -- 2-site DRY) + `_build_period_data(all_periods, balances, interest_by_period=None)` (dedupes both "one row per period with a balance" loops, which differed only in whether the row carries an `interest` field -- 2-site DRY) + `_load_account_transactions(account, scenario, all_periods)` (encapsulates `interest_detail`'s per-account transaction query + its one-sided `duplicate-code` disable, computing `period_ids` internally). `checking_detail`'s INLINE 3/6/12-month horizon loop was a verbatim copy of `project_balance_horizons` (already imported + used by `interest_detail` and `savings_dashboard`) -> replaced with the shared util: a Phase-2-missed DRY (the util's upfront `if current_period is None` guard == the old per-iteration `if current_period:` truthiness since PayPeriod has no `__bool__`). Behavior bit-identical: every helper char-identical to what it replaced across present/None current_period, no-scenario, no-periods, empty-balances, anchor-fallback, balance-without-interest->0.00; both `render_template` kwarg sets unchanged. **F-6 static guard held** (`balance_resolver.balances_for` present, bare `balance_calculator.calculate_balances(` absent -- `calculate_balances_with_interest(` is distinct, char after `calculate_balances` is `_` not `(`; `selectinload(entries)` preserved so the E-25/CRIT-01/F-009 entries-aware reduction still applies). All 3 helpers private; route surface unchanged; no other source guard. Independent 3-lens quality-pass (behavior-equivalence / simplicity / right-abstraction): all `behavior_equivalent=yes`, **0 REVERT-OVERREACH**; **2 REFINEs applied** before commit -- (1) MED naming: renamed `_resolve_current_balance` -> `_current_period_balance` (cross-file collision with the semantically-different `investment_dashboard_service._resolve_current_balance` -- mine picks from a map, that one resolves by query); (2) LOW: typed all 3 helpers via `from __future__ import annotations` + a TYPE_CHECKING block (PayPeriod/Scenario/AnchorPoint -- zero runtime imports). The DRY consolidation ruled true-DRY (sites change together), comment preservation verified (CRIT-01/F-009/rule-13 rationale relocated, not dropped). file 10.00/10, 0 smell messages; 0 disables added (83); 0 new R0801; useless-suppression 0; E/F 0. Score 9.92 held; visible 113->111; smell items 24->22 (17 8-symbol + 5 instance-attr). 139 accounts targeted + **full suite 5770 passed.** | 9.92/10 | 111 |
| 2026-06-06 | `5b32148` | 3 | **categories.py -- extract shared create-form error response (no disable) -- file DONE; starts the low-fork batch:** `create_category` tm-return (7/6) cleared by extracting the dual HTMX-or-flash error response (byte-identical at the schema-validation + blank-name guards -- the HX branch was literally identical, only the errors dict + message varied) into the typed private `_create_form_error_response(errors, flash_message) -> Response | tuple[Response, int]` (400 JSON for HTMX, else flash danger + redirect to settings#categories); each guard `return`s it, dropping 7->5 returns. Genuine 2-site DRY (the salary.py form-route tm-return methodology -- real DRY drops the count, no disable). The duplicate guard (flash warning + redirect, no HTMX jsonify -- pre-existing asymmetry, intentionally preserved) + both success returns (HTMX `_category_row.html`; flash + redirect) byte-unchanged; `edit_category` NOT folded in (its guards have no HTMX-jsonify branch -- a different contract, rule 13 forbids a suppress-HX flag). Behavior bit-identical; the create error paths are pinned by existing tests (`test_create_category_htmx_validation_error` 400 JSON, `test_create_category_validation_error` non-HX flash, `test_create_category_empty_{group,item}_name_after_trim` the blank-name message). Independent quality-pass (single fresh reviewer; proportionate to a 2-site extract): behavior_equivalent=yes, **6 ACCEPT, 0 REFINE, 0 REVERT-OVERREACH** (A4 genuine 2-site DRY, D2 `Response | tuple[Response, int]` precise, F1 keep-create-specific upheld). file 10.00/10, 0 smell messages; 0 disables added (83); 0 new R0801; useless-suppression 0; E/F 0. Score 9.92 held; visible 111->110; smell items 22->21 (16 8-symbol + 5 instance-attr). 65 categories targeted + **full suite 5770 passed.** | 9.92/10 | 110 |
| 2026-06-06 | `ab16669` | 3 | **accounts/anchor.py -- merge true_up success returns + extract conflict response (no disable) -- file DONE:** `true_up` tm-return (7/6) cleared by (a) merging the two success returns: DUPLICATE_SAME_DAY + COMMITTED build the byte-identical OOB success response (cell + "as of" OOB snippet + `HX-Trigger: balanceChanged`), so an if/else sets up `account` (DUPLICATE `db.session.get` re-fetches the committed row -- the service returns DUPLICATE *after* `rollback()`, expiring the in-memory row; COMMITTED `db.session.refresh` reloads the audit-trigger `updated_at`, and logs) then a SINGLE shared success build/return; and (b) extracting `_anchor_conflict_response(account) -> tuple[str, int]` -- the 409 `grid/_anchor_edit.html` conflict render shared by the pre-flush form-version guard + the post-service STALE_CONFLICT outcome (genuine 2-site DRY; NOT shared with `inline_anchor_update`, which uses `accounts/_anchor_cell.html` + `acct=` -- a deliberately distinct surface). Returns 7->6 (the R0911 limit; the 6 map 1:1 onto 6 distinct HTTP outcomes -- 404 / 400-validation / 400-no-period / 409-form-stale / 409-service-stale / 200-success -- irreducible, no disable, no `obscures-distinct-flashes` problem). Behavior bit-identical: the independent reviewer verified against `anchor_service.py`'s outcome/rollback contract (`:243-255`, `:346-360`) that the DUPLICATE re-fetch (post-rollback) vs COMMITTED refresh asymmetry and the COMMITTED-only `logger.info` are preserved exactly, and the success f-string + header are byte-identical. Independent quality-pass (single fresh reviewer): behavior_equivalent=yes, **all ACCEPT (5 findings), 0 REFINE, 0 REVERT-OVERREACH** (A4 conflict-helper DRY, B2 success-merge not false-DRY, D2 `tuple[str, int]` correct, irreducible-at-6 upheld). **Watch-item (PRE-EXISTING, out of scope):** `test_double_submit_creates_one_history_row` does not assert r2's body shows the committed balance -- the DUPLICATE re-fetch my merge preserves rests on inspection; a 1-line `assert <balance> in r2.data` would close it (deferred -- needs the rendered format verified, predates the cleanup). file 10.00/10, 0 smell messages; 0 disables added (83); 0 new R0801; useless-suppression 0; E/F 0. Score 9.92 held; visible 110->109; smell items 21->20 (15 8-symbol + 5 instance-attr). 146 accounts + grid-regression targeted + **full suite 5770 passed.** | 9.92/10 | 109 |
| 2026-06-06 | `6e3c32d` | 3 | **entries.py -- extract _execute_entry_update from update_entry (no disable) -- file DONE:** `update_entry` tm-return (8/6) cleared by extracting the service-call + commit + 4-way error-translation tail into `_execute_entry_update(entry_id, txn, data)` -- StaleDataError->409 conflict list; the C-19 IntegrityError backstop->idempotent credit-payback; (NotFoundError, ValidationError)->400; success->`_render_entry_list(txn)` + 200 + balanceChanged trigger. `update_entry` 8->5 returns (keeps its 2 ownership-guard 404s + 422 validation + 409 stale-form check + the helper call); the helper has 4 returns (under 6). The `transfers._execute_transfer_update` precedent -- sharpens cohesion (route = guards/validation; helper = commit/error-translation), not count-shifting (the 4 error returns are distinct mandated failure modes). Helper body byte-identical to the replaced block (`version_id` popped at the route before the call, never forwarded to the service); left UNTYPED to match the file's untyped sibling response helpers; the divergence from `_execute_transfer_update` (returns the response itself, not None) is correct -- this route has a fixed post-commit success render with no caller-side branching. Independent quality-pass (single fresh reviewer; byte-verified the helper body against HEAD): behavior_equivalent=yes, **all ACCEPT, 0 REFINE, 0 REVERT-OVERREACH** (A4 genuine cohesive execute+translate unit, extraction-over-disable the right call, the untyped helper justified by siblings). **Two deferred tracker notes (out of scope, reported not fixed):** (1) the entries private-helper cluster is a candidate for a coordinated typing pass (`flask.typing.ResponseReturnValue`); (2) the 7-line `txn`/`entry` ownership preamble shared verbatim by `update_entry`/`toggle_cleared`/`delete_entry` is a genuine DRY opportunity (a separate refactor). file 10.00/10, 0 smell messages; 0 disables added (83); 0 new R0801; useless-suppression 0; E/F 0. Score 9.92 held; visible 109->108; smell items 20->19 (14 8-symbol + 5 instance-attr). 49 entries targeted + **full suite 5770 passed.** | 9.92/10 | 108 |
| 2026-06-06 | `e22a1a5` | 3 | **app/__init__.py -- data-driven blueprint registration loop (no disable; removes one) -- `_register_blueprints` DONE:** tm-locals (24/15) cleared at the root -- the 23 explicit deferred `from app.routes.X import X_bp` imports (each a local; deferred to avoid the blueprint<->`app` cycle) + 23 `register_blueprint` calls replaced by a loop over the new `_BLUEPRINT_MODULES` tuple (23 module names, canonical order), registering `getattr(module, f"{name}_bp")` after `importlib.import_module`. 24->3 locals. **Bonus: the now-useless `import-outside-toplevel` disable REMOVED** (`importlib.import_module` is a CALL, not an import statement, so no import-outside-toplevel fires and the disable would be useless-suppression) -- disables **83->82**. Behavior bit-identical, reviewer-verified 4 ways: `_BLUEPRINT_MODULES` == the old 23-module register order (diff identical); every `getattr(module, "<name>_bp")` resolves to the same Blueprint (incl. the 5 package blueprints defining `Blueprint()` in `_bp.py` + re-exporting, and the multiword pay_periods/debt_strategy/static_pass); a full `create_app("testing")` build registers all 23 in order = 166 URL rules; a grep of every `*_bp = Blueprint(...)` under `app/routes/` returns exactly these 23. The `<name>_bp` convention is total + filesystem-enforced and fails LOUD (AttributeError/ModuleNotFoundError at startup) on violation -- the right failure mode for the app factory. **Design fork (data-driven loop vs explicit-imports+documented disable):** the reviewer argued both -- greppability of `grep auth_bp` is lost (mitigated: the convention is documented two lines up) but the loop is the genuine decision-#3 refactor (DRY: removes the 23x import+register pairing; the disable disappears rather than parks; adding a blueprint is a one-line append). Independent quality-pass (single fresh reviewer): behavior_equivalent=yes, **all ACCEPT, 0 REFINE, 0 REVERT-OVERREACH**. (The 5 pre-existing `W0613` error-handler `e` args are a separate framework-mandated item, untouched.) too-many-locals cleared; disables 83->82; 0 new R0801; useless-suppression 0; E/F 0. Score 9.92 held; visible 108->107; smell items 19->18 (13 8-symbol + 5 instance-attr). 114 auth-required + **full suite 5770 passed.** | 9.92/10 | 107 |
| 2026-06-06 | `7dad8d7` | 3 | **savings_goal_service.py -- single-return accumulator in amount_to_monthly (no disable) -- file DONE:** `amount_to_monthly` tm-return (8/6) cleared -- the 7 `if pattern_id == X: return <expr>` branches + trailing `return None` became a single-return accumulator: the explicit `once` (known valid-non-recurring) stays an early `return None`, then an if/elif assigns `monthly = <expr>` per pattern with `else: monthly = None` (unrecognized id), then one `return monthly`. Returns 8->2. The `match_periods`/`obligations._next_occurrence` precedent (a dispatch dict would be worse -- `every_n` needs an extra `n` local, two ids share an arm, one arm is a bare passthrough, so a dict forces lambdas + per-call constant construction). FINANCIAL function: every per-pattern Decimal expression byte-identical (operand/division order preserved), `Decimal(str(interval_n or 1))` construction intact, NO `quantize`/`round` introduced (the docstring's "NOT quantized" contract + the conversion-factor table still match). `once` kept explicit + distinct from `else` (both yield None; the sole consumer `obligations_aggregator.py:147` does `if monthly is not None: total += monthly`, skipping both uniformly -- the fold is behavior-safe). Independent quality-pass (single fresh reviewer; byte-for-byte branch table vs HEAD): behavior_equivalent=yes, **all ACCEPT, 0 REFINE, 0 REVERT-OVERREACH** (D1/D3 accumulator the right shape, E2/E3 Decimal discipline + the once-vs-unknown distinction upheld, F2 comments/docstring accurate). file 10.00/10, 0 smell messages; 0 disables added (82); 0 new R0801; useless-suppression 0; E/F 0. Score 9.92 held; visible 107->106; smell items 18->17 (12 8-symbol + 5 instance-attr). 53 savings_goal + obligations_aggregator targeted + **full suite 5770 passed.** | 9.92/10 | 106 |
| 2026-06-06 | `62fd7a2` | 3 | **interest_projection.py -- extract _days_in_quarter + pin the Q4 year-rollover branch (no disable) -- file DONE:** `calculate_interest` tm-locals (17/15) cleared by extracting the quarterly branch's quarter-length arithmetic (`q_start_month`/`q_start`/`next_q_month`/`q_end` -- 4 intermediate locals) into `_days_in_quarter(period_start) -> Decimal`, parallel to the existing `_days_in_year_for_window` divisor helper (the established "extract the divisor when non-trivial, inline the formula" pattern). 17->13 locals. FINANCIAL: the helper body is byte-identical to the old inline calc (only `days_in_quarter =` became `return`), the quarterly formula + daily/monthly branches + the early guard (`balance<=0 or apy<=0 or start>=end -> ZERO`) + `else: ZERO` + `round_money` all untouched; `Decimal(str(...))` discipline intact; the L-05 actual-length rationale moved into the helper docstring (verified accurate: 90-92 day range). Untyped to match `_days_in_year_for_window`/`calculate_interest`. Independent quality-pass (single fresh reviewer; byte-for-byte vs HEAD): behavior_equivalent=yes, **all ACCEPT, 0 REFINE, 0 REVERT-OVERREACH** -- A4 the extracted divisor genuinely clarifying (not count-shifting), and extracting ALL 3 compounding branches into a dispatcher explicitly ruled **REVERT-OVERREACH gold-plating** (rule 13; the math is deliberately asymmetric -- daily needs a year-divisor helper, monthly is a one-liner, quarterly needs the quarter-divisor -- and co-locating the 3 monetary formulas has review value). **Closed the reviewer-flagged PRE-EXISTING gap the extraction made load-bearing** (same commit): the Q4 `next_q_month > 12` year-rollover branch (Q4 spans into Jan 1 next year) had NO test -- added `test_q4_year_rollover_period` (Q4 2026 = Oct 31 + Nov 30 + Dec 31 = 92 days; `interest = 10000 * 0.01125 * (14/92) = 17.1195... -> 17.12`, hand-computed independently per rule G4, NOT via the function under test) + fixed the stale `/91` `TestQuarterlyCompounding` docstring (it described the pre-L-05 hardcoded behavior). file 10.00/10, 0 smell messages; 0 disables added (82); 0 new R0801; useless-suppression 0; E/F 0. Score 9.92 held; visible 106->105; smell items 17->16 (11 8-symbol + 5 instance-attr). **Full-suite baseline 5770->5771** (the +1 Q4 test). 26 interest_projection targeted + **full suite 5771 passed.** | 9.92/10 | 105 |
