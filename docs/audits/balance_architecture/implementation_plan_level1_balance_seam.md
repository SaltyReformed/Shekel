# Level 1 balance-at-T seam + no-bypass checker + cross-page oracle for every account kind

Build-Order Step 1 of the Option D architecture
(`docs/audits/balance_architecture/level1_level2_scope_and_fitness.md`, Decision section).

## Context

A whole family of "the loan/investment shows the wrong balance" defects has recurred for
months across *different* files (root-cause doc table: `76c6ffb`, `d2455e8`, `6d27717`,
`8f5ef89`, `a50e8a5`, `494e55f` / PR #44). The root cause, verified firsthand against `dev`
HEAD, is structural: there is **no single "what is account A's balance at time T?" seam**.
Six producers answer that question, and the three recompute-at-read kinds (loan, investment,
property) each bolt on their own rule for periods before an account's first known data point.
Every new surface re-invents that boundary, and each re-invention has shipped a bug at least
once. Level 0 (commit `aba0242`) fixed the live instance and added checker W9905, but only
guards the two loan producers; a new surface can still invent a new boundary rule.

This plan builds **Level 1**: one module, `app/services/balance_at.py`, that is the only public
way any screen obtains an account's balance over time; a pylint checker that mechanically
forbids any other module from calling a balance producer directly; and the cross-page equality
oracle extended from cash-only to every account kind (the correctness oracle every later
Option D step reconciles against). Outcome: the entire bug class is fenced, all existing math
is reused, no schema change.

### What I verified (trust-but-verify; every claim cited to live code on `dev`)

The six producers and their boundary behaviors are exactly as the documents describe:

| Producer | Location | Pre-first-point behavior |
|---|---|---|
| `balance_resolver.balances_for` | `balance_resolver.py:419` -> `BalanceResult` | OMITS pre-anchor |
| `balance_resolver.balance_as_of_date` | `:787` (return `:898-903`) -> `Decimal` | RETURNS anchor balance |
| `balance_calculator.calculate_balances[_with_interest]` | `:58` / `:130` | OMITS (`continue` `:114-116`) |
| `account_projection.compute_loan_period_balance_map` | `:205` (helper `:162`) | RETURNS current_balance |
| `net_worth_kernel._build_investment_balance_map` | `net_worth_kernel.py:368` | REVERSE-PROJECTS |
| `net_worth_kernel._build_appreciation_balance_map` | `:466` | FLAT-CARRIES anchor |

- A per-kind dispatcher **already exists**: `account_projection.classify_account`
  (`account_projection.py:67`, returns `AccountProjectionKind` {AMORTIZING, INTEREST,
  APPRECIATING, INVESTMENT, PLAIN}, branching only on `AccountType` boolean columns) and
  `net_worth_kernel.build_account_balance_map` (`:255`), consumed at
  `savings_dashboard_service/_net_worth.py:152` and `year_end_summary_service/_balances.py:86`.
  Its signature is leaky (caller pre-assembles `debt_schedule`, `investment_params`,
  `deductions`, `salary_gross_biweekly`); both callers duplicate that unpack.
- A **second, parallel dispatcher** lives in `savings_dashboard_service/_projections.py`
  (`_project_one_account:393`, `_compute_base_balances:37`, `_project_investment:299`,
  `_loan_projected_horizons:117`). It uses the same `classify_account` but re-implements
  dispatch and seeds investments differently. **This duplication is the recurrence generator.**
- The batch assembly is shared-helper-friendly: `_load_account_params` (`_data.py:140`) already
  reuses `projection_inputs.load_active_deductions_for_accounts` and
  `income_service.get_current_gross_biweekly`; the orchestrator (`_orchestrator.py:431`) calls
  `net_worth_kernel.generate_debt_schedules`. Only the `InvestmentParams` query is still inline.
- `grid.py` threads live `amount_overrides` (`grid.py:525` `live_amount_overrides` -> `:255`
  `balances_for(..., amount_overrides=...)`). The seam must preserve this via passthrough.
- The cross-page oracle (`tests/test_integration/test_cross_page_balance_equality.py`) seeds a
  **single checking account**, compares six cash surfaces at the anchor period (today pinned
  inside it), and has a seam-injection negative control (`TestSeamInjectionLock`).
- ~34 direct producer call sites in `app/` (the doc's ~43 also counted `resolve_loan` and
  subtotals). The `:368`/`:466` line numbers in the root-cause doc are the one acknowledged swap.

### Decisions locked with the developer

1. **Investment seeding = Model-from-anchor.** The canonical "balance today" for an
   anchor-in-the-past investment is the anchor compounded forward at the assumed return through
   today, plus contributions (the net-worth kernel's *current* behavior). Rationale: it is the
   realistic accruing value an end user expects; manual re-anchor (true-up) is the escape hatch
   when modeled growth drifts too far. Consequence: the kernel is the canonical producer
   (unchanged); the **/savings tile's investment headline `current_balance` adopts the modeled
   value** (it is cash-basis today), so the /savings pinned investment tests update -- a
   developer-authorized behavior change (rule 5 exception, granted in this session).
2. **Fence scope = full fence.** Every balance read in `app/` routes through the seam (loan,
   investment, property, AND cash on grid/calendar/dashboard/obligations). The checker forbids
   all direct producer calls outside the seam + engine cluster, zero exceptions. Strongest
   single source of truth; also closes the footgun of a future surface fetching a loan balance
   via the cash producer.

### Design principles this plan holds to

- **"One boundary rule" means one *module* owns all four per-kind rules, documented and tested
  together -- not collapsing four correct behaviors into one wrong uniform one.** The fitness
  doc's refinement: cash stays OMITTED (flat-carrying cash fabricates balances it never had);
  only loan/investment/property are recompute-at-read. The seam centralizes the dispatch the
  two existing dispatchers duplicate; it reuses each engine's existing, tested boundary math.
- **Dependency direction (SOLID):** consumers (routes, savings, year-end, dashboards) -> depend
  on -> `balance_at` seam -> depends on -> engines (`balance_resolver`, `balance_calculator`,
  `account_projection`, `growth_engine`, `net_worth_kernel`). The seam never imports a consumer
  package.
- **Two rich primitives stay outside the seam and the checker:** `growth_engine.project_balance`
  (returns `ProjectedBalance` with contribution/growth detail for charts) and
  `loan_resolver.resolve_loan` / `resolve_account_loan` (returns the full `LoanState`). The seam
  *composes* them; consumers needing the rich detail (investment/retirement growth charts,
  `home_equity_service`, loan routes) keep calling them. The seam owns "balance over time
  (Decimal map/scalar)"; the engines own "rich projection detail." This is the SRP line.
- **No schema change, no migration, no data backfill.** Level 1 is a service-layer change.
  Definition-of-Done item 7 (migrations) is N/A and will be stated as such.

---

## The seam: `app/services/balance_at.py`

Three public entry points, all delegating to existing engines (zero new math):

- `balance_map(account, scenario, periods, *, amount_overrides=None) -> OrderedDict[int, Decimal] | None`
  Single-account per-period map. Internally: `classify_account` -> assemble that account's
  inputs (loan: `generate_debt_schedules([account])`; investment: shared param/deduction/gross
  loaders) -> call `net_worth_kernel.build_account_balance_map`. `amount_overrides` passes
  through to the cash path for grid parity. Returns the same `OrderedDict` shape callers
  already render.
- `balance_at(account, scenario, as_of) -> Decimal`
  Scalar at a date (no equivalent exists today). Dispatch: cash -> `balance_as_of_date`; loan ->
  `balance_from_schedule_at_date(schedule, as_of, current_balance)`; investment/property ->
  value of `balance_map` at the period containing `as_of`. Documented granularity note: cash is
  intra-period-precise (sums dated rows), the recompute kinds are period-granular (their model
  is period-keyed) -- matching how each kind is actually stored.
- `build_maps(accounts, scenario, periods) -> list[dict]`
  Batch entry preserving the existing N+1 avoidance: assemble all inputs ONCE
  (`generate_debt_schedules` for debt accounts, the shared investment-params / deduction /
  gross loaders), then loop `build_account_balance_map`. Returns the
  `{account_id, balances, is_liability}` shape `build_account_net_worth_maps` returns today
  (this function moves into the seam). A private `_assemble_account_inputs` is shared by
  `balance_map` and `build_maps` so single- and batch-assembly are one code path (DRY).

The seam's module docstring states the four per-kind boundary rules together (the
documented-once contract): PLAIN/INTEREST omit pre-anchor; AMORTIZING returns resolver
current_balance flat (never original_principal); INVESTMENT model-from-anchor
(forward post-anchor, reverse pre-anchor); APPRECIATING flat-carry anchor backward.

## The checker: `ShekelBalanceSeamChecker` (W9906, `shekel-balance-producer-bypass`)

A superset of W9905's `visit_call` name-matching plus a module allowlist (the infra the fitness
doc names). Guards these producer names: `balances_for`, `balance_as_of_date`,
`calculate_balances`, `calculate_balances_with_interest`, `compute_loan_period_balance_map`,
`balance_from_schedule_at_date`, `build_account_balance_map`, `base_account_balance_map`,
`_build_investment_balance_map`, `_build_appreciation_balance_map`. **Not** guarded:
`project_balance`, `resolve_loan`, `resolve_account_loan`, `live_amount_overrides` (rich
primitives / input builders, per the SRP line above).

Allowlisted modules (may call producers; they compose each other): `app.services.balance_at`
(the seam) + `balance_resolver`, `balance_calculator`, `account_projection`,
`net_worth_kernel`, `growth_engine`. Mechanism: in `visit_call`, if the called name is a guarded
producer AND the current module (from `node.root().name`, falling back to `node.root().file`;
the read pattern the existing checkers use) is not in the allowlist -> `add_message`. Only runs
on `app/` + `scripts/` (CI's lint scope), so the ~32-35 test files that call producers directly
need no churn (fitness doc Part E).

---

## Atomic commits (sequential, each green: targeted tests + pylint 10.00 on changed files)

Per the testing-feedback note, run targeted tests per commit and the full suite at the phase-C
end and final gate (run the full suite alone -- shared test DB on :5433 flakes under concurrent
load). The extended oracle is the gate every reroute commit must keep green.

### Phase A -- seam scaffolding (behavior-preserving; no caller rerouted)

**Commit 1 -- Extract the shared investment-params batch loader (DRY prep).**
- Add `load_investment_params_for_accounts(accounts)` to `app/services/projection_inputs.py`
  (mirrors the existing `load_active_deductions_for_accounts:69`), returning the
  `{account_id: InvestmentParams}` map currently built inline at `_data.py:164-173`.
- `_data.py:_load_account_params` calls it. Behavior identical.
- Tests: a focused test for the loader; `test_savings_dashboard_service.py` stays green.

**Commit 2 -- Create `app/services/balance_at.py` (the seam) + parity tests. No caller rerouted.**
- Implement `balance_map`, `balance_at`, `build_maps`, `_assemble_account_inputs`, reusing
  `generate_debt_schedules`, the shared loaders, `income_service.get_current_gross_biweekly`,
  and `net_worth_kernel.build_account_balance_map`.
- `tests/test_services/test_balance_at.py`: prove seam output == the existing producer path,
  per kind, with hand-computed arithmetic comments:
  - cash (PLAIN + INTEREST): `balance_map` == `build_account_balance_map`; `balance_at` ==
    `balance_as_of_date`.
  - loan: pre-first-payment period AND empty/paid-off schedule -> resolver `current_balance`
    (never original_principal); `balance_at` == `balance_from_schedule_at_date`.
  - investment: anchor==current AND anchor-in-past -> == `_build_investment_balance_map`.
  - property: == `_build_appreciation_balance_map`.
  - `build_maps` == `build_account_net_worth_maps` for a mixed account set; `amount_overrides`
    passthrough matches `balances_for(..., amount_overrides=...)`.
- Gate: new tests + full `test_balance_at.py`; pylint 10.00 on the new module (the seam's public
  funcs take only `(account, scenario, periods/date)` -- the leaky kwargs are internalized, so
  no `too-many-arguments` disable needed).

### Phase B -- extend the correctness oracle BEFORE rerouting

**Commit 3 -- Generalize the cross-page oracle to loan, property, and anchor==current investment.**
- In `tests/test_integration/`, add per-kind fixtures (reuse the factory patterns:
  `_create_mortgage`/`_create_loan_account`, `_make_property` AssetAppreciationParams,
  Investment via `AccountSpec`; `seed_periods_today` for current-period determinism) and
  per-kind surface reader sets. Per kind, define the surfaces that legitimately report that
  kind's balance and a common comparison point:
  - loan: {/savings `current_balance`, year-end net-worth liability at month, net-worth-trend
    at current period, accounts loan detail, `home_equity` mortgage leg if secured}; compared at
    today, plus a pre-first-payment period asserting == resolver `current_balance` (the bug
    locus).
  - property: {/savings, year-end net-worth, net-worth-trend, `home_equity`, accounts property
    detail}; compared at today.
  - investment anchor==current: {/savings, year-end, net-worth-trend, investment dashboard};
    compared at today (both dispatchers already agree here).
- Add a per-kind seam-injection negative control (patch one reader, assert the lock bites and
  the message names the surface + value), mirroring `TestSeamInjectionLock`.
- These cases pass against today's code (they are the points that already agree), so they lock
  the surfaces before any reroute changes a number. The anchor-in-past investment case is held
  for Commit 5 (it diverges today -- that divergence is the bug).
- Gate: the new oracle classes green on current code; full `test_cross_page_balance_equality.py`.

### Phase C -- reroute every consumer through the seam (one cluster per commit)

**Commit 4 -- Reroute the savings net-worth producer.**
- `_orchestrator.py` (`:427-439`) + `_net_worth.py`: `build_account_net_worth_maps` delegates to
  `balance_at.build_maps` (or is replaced by it); the orchestrator stops pre-assembling
  `params`/`debt_schedules` for the map build (seam owns assembly). The trend/sparkline/gate
  helpers (`build_trend_periods`, `compute_net_worth_series`, `compute_sparklines`,
  `_honest_history_start_index`) keep their presentation logic and read the seam's maps.
  Net-worth numbers unchanged (kernel canonical).
- Gate: extended oracle + `test_savings_dashboard_service.py` net-worth tests + the
  `net_worth_kernel` pinned suite (unchanged).

**Commit 5 -- Reroute + unify the savings per-account tile (the Model-from-anchor unification; highest risk).**
- `_projections.py`: replace the second dispatcher. `current_balance` and the 3/6/12 horizons
  for investment / loan / property / cash come from `balance_at.balance_map` / `balance_at`.
  **Delete** `_project_investment` (and its current-period de-seeding), `_project_appreciation`,
  `_loan_projected_horizons`, and the investment/loan branches of `_compute_base_balances` --
  the bespoke dispatch whose duplication generated the recurrence.
- This makes the /savings investment headline adopt the modeled (kernel) value, and reconciles a
  real existing discrepancy: `compute_net_worth_today` (which reduces over per-account
  `current_balance`) now agrees with the net-worth trend's current point for anchor-in-past
  investments.
- Add the **anchor-in-past investment** case to the oracle (now all surfaces agree on the
  Model-from-anchor value; hand-compute the expected Decimal). Update the /savings pinned
  investment tests to the canonical values (rule 5: developer-authorized).
- Gate: full extended oracle (every kind now locked) + `test_savings_dashboard_service.py` +
  any investment-headline route test.

**Commit 6 -- Reroute the year-end summary.**
- `year_end_summary_service/_balances.py` (`build_account_balance_map:86`,
  `calculate_balances_with_interest:150`) and `_net_worth.py` (`balance_from_schedule_at_date`
  `:213,:216`) -> seam (`balance_map` / `balance_at`). `_savings.py:230` `project_balance` stays
  (rich primitive).
- Gate: extended oracle + the year-end pinned suite.

**Commit 7 -- Reroute the dashboards.**
- `investment_dashboard_service.py:183`, `retirement_dashboard_service.py:823`,
  `dashboard_pulse_service.py:119,188`, `dashboard_service.py:85`: their balance-at-T calls
  (`balances_for` / `balance_as_of_date`) -> `balance_at`/`balance_map`. Their `project_balance`
  chart calls (`investment_dashboard_service.py:287,821`, `retirement_dashboard_service.py:900`)
  stay.
- Gate: extended oracle + each dashboard's test file.

**Commit 8 -- Reroute the remaining cash consumers (full-fence completion).**
- `grid.py` (`balances_for` `:255,:785,:906` -> `balance_map` with `amount_overrides`
  passthrough; `live_amount_overrides` stays as the override-builder the grid also needs for
  `txn.live_estimated_amount` display), `calendar_service.py:609` (`balance_as_of_date` ->
  `balance_at`), `obligations_projection.py:199` (`balances_for` -> `balance_map`),
  `routes/accounts/detail.py` (`balances_for:464`, `calculate_balances_with_interest:207` ->
  seam).
- Behavior-preserving (overrides passthrough; same Decimal/dict shapes).
- Gate: extended oracle + grid/calendar/accounts/obligations route + service tests.

### Phase D -- turn on enforcement

**Commit 9 -- Add + register `ShekelBalanceSeamChecker` (W9906), wire every gate.**
- Add the checker class and helpers to `tools/pylint/shekel_checkers.py`; register it in
  `register()`. Unit tests in `tools/pylint/tests/test_shekel_checkers.py`: a consumer-module
  call to each guarded producer is flagged; calls from the seam and from each engine-cluster
  module are NOT flagged; `project_balance` / `resolve_loan` are never flagged (every flagged
  form paired with a conforming form, matching the existing checker-test style).
- Wire `shekel-balance-producer-bypass` into all six `--fail-on` enumerations:
  `.pre-commit-config.yaml` (lines 28, 43), `.github/workflows/ci.yml` (144, 156),
  `scripts/hooks/post-edit-python.sh` (67), `.claude/commands/standards.md` (16), `CLAUDE.md`
  (109, 149), and the Definition-of-Done fail-on string.
- Gate: `pytest tools/pylint/tests/test_shekel_checkers.py`; `pylint app/ scripts/
  --fail-under=10 --fail-on=...,shekel-balance-producer-bypass` exits 0 with zero W9906
  (registration is correct only because Commits 4-8 left no bypass).

### Phase E -- final gate

**Commit 10 -- Full-suite + manual verification + docs.**
- Full suite via `./scripts/test.sh` (run alone), expect all-pass at the ~6300+ baseline.
- Update `docs/audits/balance_architecture/` (both docs' Status): Build-Order Step 1 done;
  record the Model-from-anchor decision and full-fence scope; note the gates were kept (not
  removed -- that is the optional later cleanup the fitness doc defers).

---

## Verification (end to end)

- **Per commit:** the changed surface's targeted tests + the extended cross-page oracle, and
  the per-edit pylint hook (10.00 + all `--fail-on` checkers) on every touched file.
- **Checker proof (Commit 9):** add a temporary probe call to a guarded producer from a
  consumer module and confirm pylint fires W9906; confirm the same call from `balance_at.py` and
  from `net_worth_kernel.py` is silent; confirm `project_balance` / `resolve_loan` never fire.
  Remove the probe.
- **Manual (run + verify skills, dev container `docker compose -f docker-compose.dev.yml up`):**
  in BOTH themes, load /grid, /savings (net-worth cockpit), the year-end summary, /calendar,
  /dashboard, and the investment + retirement dashboards. Confirm every number is unchanged
  EXCEPT the authorized /savings investment headline for an anchor-in-past investment (now the
  Model-from-anchor value, matching the net-worth trend's current point). Seed an investment
  with a past anchor + contributions to exercise it.
- **Final gate:** full suite all-pass (run alone); `pylint app/ scripts/` 10.00 with the new
  fail-on; no migration (state N/A); ask the developer before commit/push (Definition of Done).

## Risks and rollback

- **Highest risk: Commit 5** (the investment unification changes a displayed number). Mitigation:
  the oracle's anchor-in-past investment case is added in the same commit with a hand-computed
  expected value, and the parity tests (Commit 2) prove the seam == kernel before any reroute.
- Each reroute commit is independently shippable and reversible (revert the single commit; the
  seam stays dead-but-correct). The checker (Commit 9) is the only commit that cannot precede the
  reroutes -- registering it earlier would surface ~34 warnings and drop pylint below 10.00.
- **Grid override parity:** the seam must pass `amount_overrides` straight through to
  `balances_for`; it must NOT auto-apply live overrides (that would change non-grid callers).
  Commit 2's passthrough parity test guards this.

## Out of scope (explicitly not this plan)

- Level 2 / postings ledger / chart of accounts / double-entry (Build-Order Steps 2-6).
- Removing the presentation gates (`_CASH_GATING_KINDS`, `_loan_schedule_start_index`,
  `_honest_history_start_index`) -- they are presentation logic, kept per the fitness doc.
- Fencing `project_balance` / `resolve_loan` (rich primitives, kept outside the seam by design).
