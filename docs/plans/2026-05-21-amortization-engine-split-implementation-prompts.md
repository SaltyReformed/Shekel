# Amortization Engine Split -- Commit Prompts

- Companion to:
  `docs/plans/2026-05-21-amortization-engine-split-implementation.md`
- Required reading for every prompt:
  `docs/audits/financial_calculations/remediation_follow_up_common.md`
- Architectural source: `docs/plans/2026-05-21-amortization-engine-split-replay-projection.md`
- Purpose: one ready-to-paste session prompt per commit (10 total)
  so each commit can be executed in its own fresh session.
- Audience: future Claude Code sessions (and the developer reading
  what each session was asked to do).

## How to use this document

1. Wait until every prerequisite commit listed under "Prereqs on
   dev" has been merged to `dev` (and `main`, via the PR-gated
   workflow in `CLAUDE.md`). Each prompt depends only on the state
   of `dev`, not on any prior session context.
2. Start a fresh Claude Code session at the project root with
   `dev` checked out.
3. Copy the entire fenced block under the commit's heading. Paste
   it as the first message in the new session. Do not edit it.
4. The session will read the canonical implementation plan
   section, the architectural plan, and the common rules, then
   re-verify against current code, do the work, run the gates,
   and stop with a structured work summary that ends by asking
   whether to commit and push. **No commit or push happens
   without your explicit go-ahead.**
5. After the commit lands on `dev` and CI is green, open a PR
   `dev` -> `main`. After merge, resync `dev`
   (`git fetch origin && git checkout dev && git merge origin/main && git push origin dev`)
   before starting the next prompt.
6. If a session reports drift between the plan and current code,
   stop and reconcile (edit the plan or adjust the prompt) before
   continuing. The plan is the floor, not a free-floating wish
   list.

Prompts are ordered to match the implementation plan's Section 8
checklist. The dependency DAG is at Section 7 of the
implementation plan -- read it once before starting.

---

## Group A -- Primitives (pure additions, no callers)

### Commit 1 -- `feat(amortization): add replay_confirmed_history primitive`

**Prereqs on dev:** none. **Closes:** Phase 1 of the architectural
plan; first half of the engine split.

```text
You are executing Commit 1 of the amortization engine split implementation
in a fresh session. Work in the project root on the dev branch.

Required reading -- read each in full BEFORE anything else (use @path so
they are fetched, do not summarize from memory or training):
- @docs/plans/2026-05-21-amortization-engine-split-implementation.md
  (Sections 0-7 for context; Section 9 "Commit 1" for the A-H
  specification)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
  (apply rules and work summary format -- mandatory)
- @docs/plans/2026-05-21-amortization-engine-split-replay-projection.md
  (the architectural "what and why"; especially the Test Pyramid
  TestReplayConfirmedHistory bullets)
- @CLAUDE.md
- @docs/coding-standards.md
- @docs/testing-standards.md
- @app/services/amortization_engine.py (read in full; this commit
  extracts the confirmed-payment branch into a new function)
- @tests/test_services/test_amortization_engine.py (read at least
  the existing TestPaymentAwareSchedule class to understand
  conventions; the new TestReplayConfirmedHistory mirrors its
  style)

Objective: add a new ReplayResult frozen dataclass and a new
replay_confirmed_history(...) function to app/services/amortization_engine.py.
The function does ONLY replay -- it has NO extra_monthly parameter, so
history cannot be what-if'ed. Reuse the engine's existing per-month
interest/principal/anchor-snap/rate-change logic intact (CLAUDE.md rule 10
-- do not rewrite); strip the contractual-fallback branch from the new
function (the architectural plan says replay stops at the last confirmed
payment_date <= as_of and does NOT fabricate contractual rows for missed
months).

Files this commit touches:
- app/services/amortization_engine.py (add ReplayResult dataclass and
  replay_confirmed_history function; no edits to existing functions; no
  callers added)
- tests/test_services/test_amortization_engine.py (add
  class TestReplayConfirmedHistory with the 11 tests C1-1..C1-11
  per Section 9 Commit 1 E table)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md
(sections "Apply these rules (every commit)" and "Work summary format").
End the session with the work summary using labels A through M verbatim.

Specific verification gates for this commit:
- grep -nE '^(from|import)\s+flask\b' app/services/amortization_engine.py
  returns empty (engine remains pure).
- grep -n "def replay_confirmed_history" app/services/amortization_engine.py
  shows exactly one match.
- grep -n "extra_monthly" app/services/amortization_engine.py inside the
  new function body shows ZERO matches (history cannot be what-if'ed).
- ./scripts/test.sh tests/test_services/test_amortization_engine.py::TestReplayConfirmedHistory -v
  all pass. C1-9 specifically verifies parity with generate_schedule's
  replay output (cross-check during migration; deleted in Commit 9).
- pylint app/ --fail-on=E,F clean.
- Full suite (./scripts/test.sh) green: N passed, zero failed/errors/xfailed.

If anything is unclear, ASK. Do not edit generate_schedule or any caller.
```

---

### Commit 2 -- `feat(amortization): add project_forward primitive`

**Prereqs on dev:** Commit 1 merged. **Closes:** Phase 2; second
half of the engine split.

```text
You are executing Commit 2 of the amortization engine split implementation
in a fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/plans/2026-05-21-amortization-engine-split-implementation.md
  (Sections 0-7; Section 9 "Commit 2" A-H)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @docs/plans/2026-05-21-amortization-engine-split-replay-projection.md
  (Test Pyramid TestProjectForward bullets; pay close attention to the
  monthly_override + extra_monthly interaction -- "the critical
  regression-prevention assertion")
- @CLAUDE.md, @docs/coding-standards.md, @docs/testing-standards.md
- @app/services/amortization_engine.py (post-Commit-1 state; re-read
  in full)

Objective: add project_forward(...) to app/services/amortization_engine.py.
The function does ONLY forward projection from a known starting state.
extra_monthly lives here and only here; an explicit monthly_override
parameter (dict[(year, month), Decimal]) routes the user's planned
payments. When an override exists for a month it is used as the total
payment and extra_monthly is NOT added; when no override exists,
contractual_payment + extra_monthly is used. Reuse the engine's existing
contractual/extra branch logic intact (CLAUDE.md rule 10 -- do not
rewrite); preserve overpayment cap, negative-amortization, and ARM
rate-change re-amortization behavior.

Files this commit touches:
- app/services/amortization_engine.py (add project_forward function;
  no edits to existing functions; no callers added)
- tests/test_services/test_amortization_engine.py (add
  class TestProjectForward with the 11 tests C2-1..C2-11)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md.
End with the work summary using labels A through M verbatim.

Specific verification gates for this commit:
- grep -nE '^(from|import)\s+flask\b' app/services/amortization_engine.py
  returns empty.
- grep -n "def project_forward" app/services/amortization_engine.py
  shows exactly one match.
- C2-4 (override months never receive extra) MUST pass -- this is the
  primitive-level regression lock that makes the bug structurally
  impossible.
- C2-10 hand-computed payoff matches the independently-computed value
  in the architectural plan (~$279,985 starting balance / 6% / 336
  months / $200 extra).
- ./scripts/test.sh tests/test_services/test_amortization_engine.py::TestProjectForward -v
  all pass.
- pylint app/ --fail-on=E,F clean.
- Full suite green.

If anything is unclear, ASK. Do not edit generate_schedule or any caller.
```

---

### Commit 3 -- `feat(loan): scenario composer in loan_resolver`

**Prereqs on dev:** Commits 1, 2 merged. **Closes:** Phase 3; the
composer that collapses route-layer scenario composition. Provides
the originally-reported-bug regression lock.

```text
You are executing Commit 3 of the amortization engine split implementation
in a fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/plans/2026-05-21-amortization-engine-split-implementation.md
  (Sections 0-7; Section 9 "Commit 3" A-H; Section 10 -- the symptom
  walkthrough so you understand WHY C3-10 is the load-bearing test)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @docs/plans/2026-05-21-amortization-engine-split-replay-projection.md
  (Scenario composer test bullets at lines 332-381 of that plan)
- @CLAUDE.md, @docs/coding-standards.md, @docs/testing-standards.md
- @app/services/loan_resolver.py (read in full; this commit adds a
  function to this file)
- @app/services/loan_payment_service.py (read prepare_payments_for_engine
  and load_loan_context to understand the payment-record shape the
  composer receives)
- @app/services/amortization_engine.py (post-Commit-1+2 state; the
  composer is the first caller of the new primitives)
- @tests/test_services/test_loan_resolver.py (read existing
  TestResolveLoan-style classes to match conventions)

Objective: add a PayoffScenarios frozen dataclass and
compute_payoff_scenarios(...) function to app/services/loan_resolver.py.
Calls replay_confirmed_history ONCE, then project_forward THREE times
from the same starting state. Routes projected payments through
monthly_override (forward only). Chart and summary cannot diverge
because they derive from one return value. No caller is rewired in
this commit (Commits 4-7 do that).

Files this commit touches:
- app/services/loan_resolver.py (add PayoffScenarios dataclass and
  compute_payoff_scenarios function; do NOT modify resolve_loan in
  this commit -- that is Commit 6)
- tests/test_services/test_loan_resolver.py (add
  class TestComputePayoffScenarios with the 15 tests C3-1..C3-15;
  C3-10 is the originally-reported-bug regression test and MUST be
  hand-computed against a ~$279,985-ish starting balance with $500
  extra)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md.
End with the work summary using labels A through M verbatim.

Specific verification gates for this commit:
- grep -nE '^(from|import)\s+flask\b' app/services/loan_resolver.py
  returns empty.
- grep -n "def compute_payoff_scenarios" app/services/loan_resolver.py
  shows exactly one match.
- grep -nF '.quantize(Decimal("0.01"))' app/services/loan_resolver.py
  empty (use round_money for rounding boundary).
- C3-10 (the originally-reported-bug regression) MUST pass with a
  hand-computed expected months_saved. This is the load-bearing
  composer-level regression lock for the user's reported bug; without
  it any future change could silently reintroduce the same defect.
- C3-11 (temporal-gap property) MUST pass -- it generalizes the lock to
  arbitrary origination-to-first-confirmed gaps.
- ./scripts/test.sh tests/test_services/test_loan_resolver.py::TestComputePayoffScenarios -v
  all 15 pass.
- pylint app/ --fail-on=E,F clean.
- Full suite green.

If anything is unclear, ASK. Do not edit resolve_loan, payoff_calculate,
dashboard, or any other consumer.
```

---

## Group B -- Migration (user-visible fix + architectural consistency)

### Commit 4 -- `fix(loan): payoff calculator routes through scenario composer` (USER-VISIBLE FIX)

**Prereqs on dev:** Commits 1, 2, 3 merged. **Closes:** Phase 4;
the originally reported visual bug on `/accounts/<id>/loan`.

```text
You are executing Commit 4 of the amortization engine split implementation
in a fresh session. Work in the project root on the dev branch.

This commit lands the user-visible fix. The Payoff Calculator on
/accounts/<id>/loan currently renders an Accelerated chart series that
diverges from the Original at the origination date, runs parallel through
the confirmed window around today, then resumes its accelerated descent.
After this commit Accelerated tracks Committed through the historical
region and departs only at and after today's month boundary.

Required reading -- in full:
- @docs/plans/2026-05-21-amortization-engine-split-implementation.md
  (Sections 0-7; Section 9 "Commit 4" A-H; Section 10 end-to-end #1)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @docs/plans/2026-05-21-amortization-engine-split-replay-projection.md
  (Layer 3 route-collapse section)
- @CLAUDE.md, @docs/coding-standards.md, @docs/testing-standards.md
- @app/routes/loan.py (read in full -- payoff_calculate is the target,
  but the entire file's helpers and imports are in scope)
- @app/templates/loan/_payoff_results.html (every Jinja variable read
  in this template is a context-key contract the route must satisfy)
- @app/services/loan_resolver.py (post-Commit-3 state -- the composer
  is now available)
- @app/services/amortization_engine.py (post-Commit-2 state -- new
  primitives available)
- @tests/test_routes/test_loan.py (read TestPayoffCalculator to see
  current assertions; new chart-shape tests are net additions)

Objective: rewrite the mode == "extra_payment" branch of
payoff_calculate (re-grep app/routes/loan.py:1184-1364 -- the line
range from the architectural plan was wrong; the function actually
extends to ~1364) to use ONE compute_payoff_scenarios call instead of
the three direct generate_schedule calls + calculate_summary call.
Preserve every template context key the existing _payoff_results.html
reads (payoff_summary, chart_labels, chart_original, chart_committed,
chart_accelerated, has_payments, committed_months_saved,
committed_interest_saved). The mode == "target_date" branch (~lines
1314-1359) is NOT touched in this commit -- it migrates in Commit 7.

Files this commit touches:
- app/routes/loan.py (payoff_calculate extra-payment branch only)
- app/templates/loan/_payoff_results.html (NO changes expected;
  re-grep current Jinja variable reads and confirm every one is
  produced by the new route code -- if a context key needs renaming,
  fix the template too rather than reverting)
- tests/test_routes/test_loan.py (add class TestPayoffChartShape
  with the 8 tests C4-1..C4-8; C4-2 / C4-3 are the HTTP-level
  regression locks for the user's reported visual bug)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md.
End with the work summary using labels A through M verbatim.

Specific verification gates for this commit:
- grep -n "amortization_engine.calculate_summary" app/routes/loan.py
  empty (this was the only production caller; removed here, function
  itself deleted in Commit 9).
- grep -n "amortization_engine.generate_schedule" app/routes/loan.py
  shows references ONLY in (a) the mode == "target_date" branch and
  (b) refinance_calculate. Both migrate in Commit 7.
- C4-2 (accelerated == committed in historical region) MUST pass --
  the HTTP-level regression lock for the user's reported bug.
- C4-3 (strict inequality past today) MUST pass.
- ./scripts/test.sh tests/test_routes/test_loan.py -v all pass.
- pylint app/ --fail-on=E,F clean.
- Full suite green.
- Manual smoke: start the dev server (flask run), open
  /accounts/<id>/loan for a loan with confirmed payments, submit the
  Payoff Calculator with a nonzero extra (e.g. $500). Confirm the
  Accelerated line tracks Committed through the historical region and
  departs only at/after today's month. Capture a before/after
  description in the work summary.

If a template context key MUST change to make the route work, document
that change in the work summary (D. Files changed) and explain why
preservation was impossible. If anything else is unclear, ASK.
```

---

### Commit 5 -- `refactor(loan): dashboard chart paths via scenario composer`

**Prereqs on dev:** Commit 4 merged. **Closes:** Phase 5;
architectural consistency for the dashboard. Behavior-preserving.

```text
You are executing Commit 5 of the amortization engine split implementation
in a fresh session. Work in the project root on the dev branch.

The dashboard chart paths do NOT exhibit the originally reported visual
bug (none of their generate_schedule calls passes extra_monthly), but
their composition style is the same anti-pattern. This commit migrates
them to the composer for architectural consistency and as a prerequisite
for Commit 6's resolver-internal migration.

Required reading -- in full:
- @docs/plans/2026-05-21-amortization-engine-split-implementation.md
  (Sections 0-7; Section 9 "Commit 5" A-H; Section 3 R-2 for the
  four-engine-touch enumeration this commit collapses)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @docs/plans/2026-05-21-amortization-engine-split-replay-projection.md
- @CLAUDE.md, @docs/coding-standards.md, @docs/testing-standards.md
- @app/routes/loan.py (re-grep dashboard at :488-749; expect drift
  from previous reads after Commit 4 landed)
- @app/templates/loan/dashboard.html (every Jinja variable read is a
  context-key contract)
- @app/services/loan_resolver.py (post-Commit-3 state)
- @app/services/amortization_engine.py (post-Commit-2 state)
- @tests/test_routes/test_loan.py (read TestLoanDashboard*-style
  classes to understand existing dashboard assertions)

Objective: replace the dashboard's four engine touches with two
composer calls. Specifically:
- planned_schedule (loan.py:533) -> scenarios_main.history_rows +
  scenarios_main.committed_forward (where scenarios_main is one
  composer call with full payments + extra_monthly=0).
- original_schedule (loan.py:589) -> scenarios_main.history_rows +
  scenarios_main.original_forward.
- floor_schedule (loan.py:619) -> a second composer call with payments
  filtered to confirmed-only (extra_monthly=0); chart_floor =
  _balances(scenarios_floor.history_rows + scenarios_floor.committed_forward).
The resolver call via _load_loan_context (loan.py:504) stays as-is.

Every consumer of planned_schedule must continue to receive its data
unchanged: amortization tab (amortization_schedule), payment breakdown
(_compute_payment_breakdown at loan.py:574), schedule totals
(_compute_schedule_totals at loan.py:697), recurrence end_date update
(_update_transfer_end_date at loan.py:660), and summary construction
(loan.py:557). Walk this consumer list explicitly during edits; Section
3 R-2 of the implementation plan enumerates them. This is a
behavior-preserving refactor -- chart values, summary numbers, and the
amortization table must be byte-identical to pre-commit values.

Files this commit touches:
- app/routes/loan.py (dashboard function only)
- app/templates/loan/dashboard.html (NO changes expected unless a
  context key requires renaming; chart_labels, chart_original,
  chart_committed, chart_floor, amortization_schedule,
  schedule_row_totals, schedule_row_rates_pct, show_rate_column,
  schedule_totals, payment_breakdown, summary,
  current_principal_display, total_payment, has_payments all
  preserved)
- tests/test_routes/test_loan.py (add class
  TestDashboardChartComposer with the 8 tests C5-1..C5-8; C5-1..C5-4
  and C5-8 are assert-unchanged regression-safety tests)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md.
End with the work summary using labels A through M verbatim.

Specific verification gates for this commit:
- grep -n "amortization_engine.generate_schedule" app/routes/loan.py
  shows references ONLY in (a) the mode == "target_date" branch and
  (b) refinance_calculate; the dashboard body has zero matches.
- C5-1..C5-4 and C5-8 (assert-unchanged) MUST pass. If any surfaces a
  byte-difference, STOP and report -- that is a real regression caught
  here, not a value to be re-pinned.
- C5-6 (floor sits above committed when projections exist) and C5-7
  (floor == committed when no projections) lock the floor semantics.
- ./scripts/test.sh tests/test_routes/test_loan.py -v all pass.
- pylint app/ --fail-on=E,F clean.
- Full suite green.
- Manual smoke: start the dev server, open /accounts/<id>/loan, visually
  compare the chart and the amortization table to a screenshot taken
  before this commit. Capture the comparison in the work summary.

If any assert-unchanged test fails, STOP -- do not re-pin. Investigate
and report. If anything else is unclear, ASK.
```

---

### Commit 6 -- `refactor(loan): resolve_loan internals on new primitives`

**Prereqs on dev:** Commit 5 merged. **Closes:** Phase 6;
behavior-preserving migration of the resolver chokepoint.

```text
You are executing Commit 6 of the amortization engine split implementation
in a fresh session. Work in the project root on the dev branch.

resolve_loan currently calls generate_schedule directly at line 598.
This commit replaces that call with compute_payoff_scenarios so
LoanState.schedule becomes scenarios.history_rows +
scenarios.committed_forward. Every downstream consumer of
LoanState.schedule (debt strategy, savings dashboard, year-end summary,
refinance) migrates automatically via the resolver chokepoint with zero
changes on their side. This is a behavior-preserving refactor.

Required reading -- in full:
- @docs/plans/2026-05-21-amortization-engine-split-implementation.md
  (Sections 0-7; Section 9 "Commit 6" A-H; Section 4 SoT map for the
  LoanState.schedule semantics; Section 12 Q-2 on the open rename
  question -- not in scope for this commit)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @docs/plans/2026-05-21-amortization-engine-split-replay-projection.md
  (Phase 6 paragraph)
- @CLAUDE.md, @docs/coding-standards.md, @docs/testing-standards.md
- @app/services/loan_resolver.py (read resolve_loan in full --
  re-grep :478-649; the function is non-trivial and uses several
  private helpers)
- @app/services/amortization_engine.py (post-Commit-2 state)
- @tests/test_services/test_loan_resolver.py (read every test class
  to identify which assertions might surface a byte-difference)
- @tests/test_integration/test_loan_unified_figures.py (F-022 invariant
  + ARM payoff consistency + per-period principal/interest; all must
  pass by construction after this commit)
- @app/services/debt_strategy.py (downstream consumer of
  LoanState.schedule; reads-only check that nothing here breaks)
- @app/services/savings_dashboard_service.py (same)
- @app/services/year_end_summary_service.py (same)

Objective: in resolve_loan (app/services/loan_resolver.py), replace
the direct generate_schedule(...) call (lines 598-610) with
compute_payoff_scenarios(...) and assemble LoanState.schedule as
scenarios.history_rows + scenarios.committed_forward. The
_replay_balance_from_anchor call (current_balance derivation),
_compute_monthly_payment ARM branch, and payoff_date/total_interest
derivation all stay unchanged in shape -- they operate on the new
schedule. The composer is called with extra_monthly=Decimal("0.00")
and the resolver's confirmed-after-anchor filtered payments.

Files this commit touches:
- app/services/loan_resolver.py (resolve_loan function only)
- tests/test_services/test_loan_resolver.py (existing C13-1..C13-11
  tests must still pass; add C6-8..C6-10 per Section 9 Commit 6 E
  table; C6-9 + C6-10 lock the is_confirmed flag propagation)
- No other files expected. If a downstream consumer test surfaces a
  byte-difference, STOP and report.

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md.
End with the work summary using labels A through M verbatim.

Specific verification gates for this commit:
- grep -n "generate_schedule" app/services/loan_resolver.py returns
  empty.
- C6-1..C6-7 (assert-unchanged) MUST pass. If any fails, STOP and
  report -- that signals the composer is not byte-equivalent to the
  prior engine call for the resolver's inputs.
- C6-9 (history rows marked confirmed) and C6-10 (forward rows
  marked unconfirmed) MUST pass.
- F-022 invariant
  (tests/test_integration/test_loan_unified_figures.py::test_months_saved_single_quantity)
  green.
- ARM payoff consistency
  (tests/test_integration/test_loan_unified_figures.py::test_arm_payoff_date_consistent_across_surfaces)
  green.
- Per-period principal/interest test
  (tests/test_integration/test_loan_unified_figures.py::test_per_period_principal_interest_single_source)
  green.
- Downstream consumer tests
  (tests/test_services/test_debt_strategy.py,
   tests/test_services/test_savings_dashboard_service.py,
   tests/test_services/test_year_end_summary_service.py) all pass
  unchanged.
- pylint app/ --fail-on=E,F clean.
- Full suite green.

If anything is unclear, ASK. Do not edit downstream consumers; this
commit's whole point is that the chokepoint migration leaves them
alone.
```

---

### Commit 7 -- `refactor(loan): calculate_payoff_by_date and refinance on new primitives`

**Prereqs on dev:** Commit 6 merged. **Closes:** Phase 7; the last
two route-level direct callers of `generate_schedule`. Behavior-
preserving (the projected-payments fix is OPT-1, deferred).

```text
You are executing Commit 7 of the amortization engine split implementation
in a fresh session. Work in the project root on the dev branch.

Two production callers of generate_schedule remain after Commit 6:
- payoff_calculate's mode == "target_date" branch calls
  amortization_engine.calculate_payoff_by_date (route line ~1336),
  which internally calls generate_schedule twice (engine lines ~766
  standard + ~811 binary search inner).
- refinance_calculate (route line ~1444) calls generate_schedule once
  for the refi projection from a known starting balance.
This commit migrates both to project_forward. Both migrations are
behavior-preserving (D-F of the implementation plan).

Required reading -- in full:
- @docs/plans/2026-05-21-amortization-engine-split-implementation.md
  (Sections 0-7; Section 9 "Commit 7" A-H; Section 2 D-F for the
  deliberate scope limit; Section 5 OPT-1 for what we are NOT doing
  here)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @docs/plans/2026-05-21-amortization-engine-split-replay-projection.md
  (Phase 7 paragraph)
- @CLAUDE.md, @docs/coding-standards.md, @docs/testing-standards.md
- @app/services/amortization_engine.py (read calculate_payoff_by_date
  in full)
- @app/routes/loan.py (re-grep payoff_calculate target_date branch
  ~:1314-1359 and refinance_calculate ~:1367-1513)
- @tests/test_services/test_amortization_engine.py (read
  TestPayoffByDate)
- @tests/test_routes/test_loan.py (read TestLoanRefinance and the
  target_date payoff test)

Objective: rewrite calculate_payoff_by_date in
app/services/amortization_engine.py to use project_forward (twice:
once for the standard schedule, once inside the binary search loop).
Rewrite the line ~1444 generate_schedule call in refinance_calculate
to use project_forward. Both migrations preserve external behavior --
all existing tests pass assert-unchanged.

If project_forward callers need the "next pay date" calculation
(start_year/start_month/payment_day -> first projection date), decide
at commit-authoring time whether to expose engine._advance_month as a
public helper or inline the calculation at each call site. The
implementation plan recommends exposing it as
amortization_engine.advance_to_next_payment_date(year, month, day)
because both Commit 7 callers need the same computation.

Files this commit touches:
- app/services/amortization_engine.py (calculate_payoff_by_date body;
  optionally promote _advance_month to a public helper)
- app/routes/loan.py (refinance_calculate function only)
- tests/test_services/test_amortization_engine.py (TestPayoffByDate
  tests remain green; add C7-1..C7-4 + C7-6 per Section 9 Commit 7 E
  table)
- tests/test_routes/test_loan.py (TestLoanRefinance tests remain
  green; add C7-5 + C7-7 + C7-8)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md.
End with the work summary using labels A through M verbatim.

Specific verification gates for this commit:
- grep -n "generate_schedule" app/services/amortization_engine.py
  inside calculate_payoff_by_date body returns empty.
- grep -n "amortization_engine.generate_schedule" app/routes/loan.py
  returns empty (the only remaining caller, refinance_calculate,
  migrated here; Commit 4 already removed the payoff_calculate
  extra_payment branch's calls).
- grep -rn "amortization_engine.generate_schedule\|amortization_engine.calculate_summary" app/
  shows the function DEFINITIONS only (no callers remain in app/).
- C7-1..C7-5 + C7-8 (assert-unchanged) MUST pass. STOP if any fails.
- pylint app/ --fail-on=E,F clean.
- Full suite green.
- Manual smoke: run the Payoff Calculator's "Target Date" mode and the
  Refinance Calculator for known inputs; confirm the returned values
  are byte-identical to pre-commit (paste before/after into the work
  summary).

If anything is unclear, ASK. The projected-payments-in-required-extra
fix is OUT OF SCOPE for this commit -- do NOT add monthly_override
support to calculate_payoff_by_date; per D-F that is a deferred
follow-up requiring a separate user-facing decision.

If you decide to defer the projected-payments fix to a follow-up entry,
add it to docs/audits/financial_calculations/remediation_follow_up.md
as a new F-N entry (J. OUT OF SCOPE in the work summary).
```

---

## Group C -- Cleanup (migration prereq + deletion)

### Commit 8 -- `refactor(migrations): inline replay loop in d3d25212504b backfill`

**Prereqs on dev:** Commit 7 merged. **Closes:** the R-1
prerequisite for Commit 9. Migration becomes self-contained.

```text
You are executing Commit 8 of the amortization engine split implementation
in a fresh session. Work in the project root on the dev branch.

Migration d3d25212504b imports amortization_engine and calls
generate_schedule(...) at line ~315 during the loan-anchor-events
backfill. Commit 9 deletes generate_schedule. To keep fresh-database
rebuilds working, this commit inlines the small replay loop into the
migration so it no longer depends on the engine module.

Required reading -- in full:
- @docs/plans/2026-05-21-amortization-engine-split-implementation.md
  (Sections 0-7; Section 9 "Commit 8" A-H; Section 3 R-1 for the
  diagnosis)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @CLAUDE.md, @docs/coding-standards.md, @docs/testing-standards.md
- @migrations/versions/d3d25212504b_create_loan_anchor_events_table_for_.py
  (read in full; _locate_current_principal is at ~line 250-329)
- @app/services/amortization_engine.py (read generate_schedule
  thoroughly -- the recorded-month branch at :537-574 plus the
  contractual-fallback at :575-605; the inline loop must reproduce
  BOTH branches because the migration uses both)
- @tests/test_models/test_loan_anchor_backfill.py (existing
  backfill tests; this commit's correctness condition is that they
  all stay byte-identical green)

Objective: replace the
amortization_engine.generate_schedule(...) call in
_locate_current_principal (migration d3d25212504b ~:315) with an
inline _replay_from_origination_inline helper that mirrors the
engine's recorded-month + contractual-fallback per-month math for the
migration's input shape (confirmed-only payments, no extra, no rate
changes, no anchor). Critical semantic to preserve: the original code
walked reversed(schedule) returning the LAST confirmed row's
remaining_balance. The inline helper must reproduce that exact
semantic (capture balance AFTER each confirmed payment is applied;
return the last-captured value).

Files this commit touches:
- migrations/versions/d3d25212504b_create_loan_anchor_events_table_for_.py
  (delete the amortization_engine import; add the inline helper;
  rewrite _locate_current_principal to call it)
- tests/test_models/test_loan_anchor_backfill.py (existing tests
  must stay green by construction; add C8-4 + C8-6 per Section 9
  Commit 8 E table)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md.
End with the work summary using labels A through M verbatim.

Specific verification gates for this commit:
- grep -nF "amortization_engine" migrations/versions/d3d25212504b_create_loan_anchor_events_table_for_.py
  returns empty (no import, no call).
- grep -nF "generate_schedule" migrations/versions/d3d25212504b_create_loan_anchor_events_table_for_.py
  returns empty.
- All existing backfill tests (C8-1..C8-3, C8-5) pass byte-identical;
  if any surfaces a difference, STOP and report -- the inline replay
  diverges from the engine for the migration's input shape.
- C8-6 (parametrize three representative loans and compare inline-result
  to pre-commit engine-result) MUST pass.
- ./scripts/test.sh tests/test_models/test_loan_anchor_backfill.py -v
  all pass.
- flask db downgrade base && flask db upgrade head round-trips cleanly
  on a prod-like clone; paste the resulting current_principal values
  for three representative loans into the work summary (must be
  byte-identical to pre-commit).
- pylint app/ --fail-on=E,F clean (the migration is not in app/, but
  run anyway).
- Full suite green.

If anything is unclear, ASK. Do not delete generate_schedule in this
commit -- that is Commit 9.
```

---

### Commit 9 -- `refactor(amortization): remove generate_schedule and calculate_summary`

**Prereqs on dev:** Commit 8 merged (and all prior). **Closes:**
Phase 8; the engine surface is reduced to the two primitives +
helpers.

```text
You are executing Commit 9 of the amortization engine split implementation
in a fresh session. Work in the project root on the dev branch.

After Commits 4-8 every production caller of generate_schedule and
calculate_summary has been migrated. The functions are dead production
code. This commit deletes both functions and their dedicated tests in
one atomic diff so the codebase carries no dead surfaces.

Required reading -- in full:
- @docs/plans/2026-05-21-amortization-engine-split-implementation.md
  (Sections 0-7; Section 9 "Commit 9" A-H; Section 10 end-to-end
  walkthrough)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @CLAUDE.md, @docs/coding-standards.md, @docs/testing-standards.md
- @app/services/amortization_engine.py (read in full -- this commit
  removes the two largest functions)
- @tests/test_services/test_amortization_engine.py (read in full --
  this commit removes TestPaymentAwareSchedule and other
  generate_schedule / calculate_summary-specific classes)

Objective: delete generate_schedule (~lines 326-633), delete
calculate_summary (~lines 636-737), delete AmortizationSummary
dataclass IF no caller remains (re-grep app/ first), delete the test
classes TestPaymentAwareSchedule, TestGenerateSchedule (if exists),
TestCalculateSummary (if exists), and any other test class that
exclusively tests the deleted functions. Keep
TestCalculateMonthlyPayment, TestPayoffByDate, TestCalculateRemainingMonths,
TestPaymentRecordValidation, TestRateChangeRecordValidation,
TestReplayConfirmedHistory (Commit 1), TestProjectForward (Commit 2),
and re-purpose TestAmortizationEngineRegression to use the new
primitives where it tested the deleted functions.

Files this commit touches:
- app/services/amortization_engine.py (delete generate_schedule and
  calculate_summary; possibly delete AmortizationSummary)
- tests/test_services/test_amortization_engine.py (delete the
  test classes for the removed functions)
- Possibly other test files if they had stub imports of the
  removed symbols (re-grep first)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md.
End with the work summary using labels A through M verbatim.

Specific verification gates for this commit:
- grep -rn "def generate_schedule\|def calculate_summary" app/ returns
  empty (no definitions).
- grep -rn "generate_schedule(\|calculate_summary(" app/ tests/
  migrations/ returns empty (no callers).
- grep -n "from app.services.amortization_engine import" app/ tests/
  shows no import of generate_schedule, calculate_summary, or
  AmortizationSummary (unless AmortizationSummary survives as a
  route-side return shape; verify before deleting).
- Pytest collects without errors:
  ./scripts/test.sh --collect-only -q
  shows no missing-symbol or missing-class errors.
- ./scripts/test.sh full suite green: N passed, zero failed / errors /
  xfailed.
- pylint app/ --fail-on=E,F clean.
- Manual smoke: start the dev server and exercise the Payoff Calculator
  (both modes), Dashboard (chart + amortization tab + payment
  breakdown), and Refinance Calculator. Every page must render with no
  500. Note any visible differences (there should be none).

If any unexpected app/ match for generate_schedule or calculate_summary
exists, that caller was missed by a prior commit -- STOP and report.

If anything else is unclear, ASK. This is the most consequential
deletion in the plan; if in doubt, ask before deleting.
```

---

### Commit 10 -- `chore(release): full gate + verification appendix`

**Prereqs on dev:** Commit 9 merged. **Closes:** the full
amortization-split implementation. Final acceptance gate.

```text
You are executing Commit 10 of the amortization engine split implementation
in a fresh session. Work in the project root on the dev branch.

This is the final acceptance gate. No source/test/migration changes.
The only file edit is populating Section 11 of the implementation plan
with the hand-computed reconciliation values produced during execution
of Commits 1-9.

Required reading -- in full:
- @docs/plans/2026-05-21-amortization-engine-split-implementation.md
  (Sections 0-13 -- the whole document; especially Section 10
  end-to-end verification and Section 11 reconciliation appendix
  skeleton)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @CLAUDE.md, @docs/coding-standards.md, @docs/testing-standards.md
- @docs/plans/2026-05-21-amortization-engine-split-replay-projection.md
  (the architectural source; confirm every refinement R-1..R-5 is
  reflected in the executed code)

Objective: run the full gate checklist (Section 9 Commit 10 D) and
populate Section 11 of the implementation plan with the actual
pre-fix and post-fix values produced during execution. If any gate
fails, STOP -- do not commit until reconciled.

Files this commit touches:
- docs/plans/2026-05-21-amortization-engine-split-implementation.md
  (Section 11 only; fill in the reconciliation appendix entries with
  hand-computed values gathered from prior commit work summaries)
- No source / test / migration changes.

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md.
End with the work summary using labels A through M verbatim.

Specific verification gates for this commit (every gate MUST pass):
1. ./scripts/test.sh full suite green: ends in N passed, zero failed /
   errors / xfailed; wall-clock ~65 s on a fresh test-db container.
   Paste the final summary line verbatim into the work summary E.
2. pylint app/ --fail-on=E,F clean: no new warnings vs baseline.
   Paste the final pylint score line verbatim into the work summary F.
3. Migration round-trip on a prod-like clone:
   flask db upgrade && flask db downgrade base && flask db upgrade head
   completes with no errors. Paste the result into work summary G.
4. The two regression locks must be green:
   - tests/test_services/test_loan_resolver.py::TestComputePayoffScenarios::test_originally_reported_bug_regression
     (Commit 3 C3-10 -- the composer-level lock for the user's reported
     bug).
   - tests/test_services/test_amortization_engine.py::TestProjectForward::test_override_plus_extra_extra_not_added_to_override_months
     (Commit 2 C2-4 -- the primitive-level lock).
   Confirm both green and paste the test names into work summary H.
5. Standing invariants must be green:
   - tests/test_integration/test_loan_unified_figures.py (full file).
   Paste the file-level summary line into work summary H.
6. End-to-end symptom walkthrough (Section 10 scenarios 1-5). For each:
   start the dev server, exercise the scenario, capture the
   before/after observation, and confirm the corresponding automated
   lock is green. Document each in the work summary's K (assumptions /
   observations).
7. Section 11 appendix populated with hand-computed values per the
   skeleton.
8. git status shows only docs/plans/2026-05-21-amortization-engine-split-implementation.md
   modified (no other files); commit message follows
   <type>(<scope>): <what> with the required Co-Authored-By trailer
   per CLAUDE.md.

If any gate fails, STOP. Do not commit. Investigate and report.

If everything is green, the work summary closes with the M label
asking "Ready to commit and push to dev?" -- per the common rules,
NO commit or push happens without explicit go-ahead.
```

---

## Notes on executing this plan

- Run prompts in order; the dependency DAG (implementation plan
  Section 7) is binding. The two regression locks (Commit 3 C3-10
  + Commit 2 C2-4) come online by Commit 3; the user-visible fix
  lands at Commit 4. Commits 5-7 are architectural consistency.
  Commit 8 is the prerequisite for Commit 9; do not reorder.
- Each session is independent. The session reads its referenced
  files fresh and produces its work summary; it does not depend on
  any prior session's state in memory.
- Every session ends with the work summary (labels A-M verbatim
  per `remediation_follow_up_common.md`). The summary's M label
  asks "Ready to commit and push to dev?" -- do not push without
  explicit go-ahead.
- If a session reports drift between the plan and current code,
  STOP and reconcile by editing the plan (or this prompts file)
  before continuing. The plan is the floor, not a free-floating
  wish list.
- The test template does NOT need to be rebuilt by this plan (no
  schema changes, no `app/ref_seeds.py` or
  `app/audit_infrastructure.py` edits). If a session reports it
  needs to rebuild, STOP and reconcile -- something has drifted
  beyond this plan's scope.
- For tight iteration in a single session, follow-up test
  invocations should use `SKIP_DB_RESTART=1 ./scripts/test.sh
  ...` per `docs/testing-standards.md`.
