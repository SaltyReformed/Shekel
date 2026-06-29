# Level 1 / Level 2 balance-architecture remedies: correctness, scope, blast radius, fitness

Date: 2026-06-26. Companion to `recurring_loan_balance_root_cause.md`. Scope: an independent,
code-traced assessment of the Level 1 and Level 2 remedy options proposed in the root-cause
document, written after the Level 0 fix shipped (commit `aba0242`). This is a scope-of-work,
blast-radius, financial-correctness, and fitness investigation, NOT an implementation plan.

Every claim is cited to live code on `dev` at HEAD `aba0242`. Each file the root-cause document
cites was re-read and its claims verified rather than restated. Two independent design analyses
(a minimalist case and a double-entry-advocate case) were run from opposite premises and reached
the same verdict; their load-bearing citations were re-verified firsthand.

Headline conclusions (each defended below):

1. The root-cause document's diagnosis is accurate. All six producers and their boundary rules
   were verified. Two line numbers in it are swapped; the substance is correct.
2. Level 1 is correct, necessary, and recommended. It is the completion of consolidation already
   underway, not a greenfield build. Two refinements to the document's framing are warranted.
3. Level 2 as the document describes it is event-sourced materialization, not "double-entry."
   The document conflates the two. Full double-entry (every transaction and transfer balanced)
   is a strictly larger change than the document's Level 2.
4. Level 1 must precede Level 2. Verified: Level 2's backfill has nothing to reconcile against
   without the Level 1 seam.
5. The optimal architecture is Option D (the chosen direction): Level 1 everywhere, true
   double-entry for the confirmed past, live-recompute for the projected future, one seam over
   both. This holds even with cost removed from the question -- materializing the projected layer
   is a denormalization that adds a staleness / regeneration bug class for no correctness gain, so
   doing "more" than D is worse, not merely costlier. The Decision section carries the full
   combination analysis, the fact-versus-derivation principle behind it, and the chosen build
   order.

---

## Part A -- How balances actually work today (verified baseline)

There are two storage paradigms in the app, and the split is the entire story.

**Cash / checking is materialized.** The recurrence engine writes real `Transaction` rows
(`recurrence_engine.py:184-201`: `Transaction(...)`, `db.session.add`, `db.session.flush`). A
cash balance is therefore `anchor + signed sum of stored rows up to T`. Sign is applied by
`transaction_type_id` (INCOME vs EXPENSE), not stored: amounts are non-negative by CHECK
constraint (`transaction.py:123-130`), and summation applies the sign
(`balance_resolver._sum_period_as_of`: `income += ... / expense += ...`, then net). There is no
boundary rule to get wrong for cash; the rows exist or they do not.

**Loans, investments, and property are recomputed at read time.** They are not stored as
per-period balances. Each is re-derived from parameters and a today-forward projection:
- Loans: `loan_resolver.resolve_loan` replays confirmed payments forward from the latest
  `LoanAnchorEvent` (`loan_params.py` + `loan_anchor_event.py`).
- Investments: `growth_engine.project_balance` (`growth_engine.py:407`) compounds forward from
  an anchor balance + contributions.
- Property: the same growth engine with contributions zeroed (`asset_appreciation_params.py`).

Because the projection only goes forward from today, every consumer needs its own answer for
periods before the first known data point. That bolt-on is where the bug lives.

**The six producers and their boundary rules (all verified against code):**

| Producer | file:line | Behavior before first known point |
|---|---|---|
| `balance_resolver.balances_for` | `balance_resolver.py:419` | OMITS pre-anchor periods |
| `balance_resolver.balance_as_of_date` | `:787` (return at `:898-903`) | RETURNS anchor balance |
| `balance_calculator.calculate_balances[_with_interest]` | `:58` / `:130` | OMITS (`continue` at `:114-116`) |
| `account_projection.compute_loan_period_balance_map` | `:205` (`balance_from_schedule_at_date` `:162`) | RETURNS current_balance (was original_principal -- the bug) |
| `net_worth_kernel._build_investment_balance_map` | `net_worth_kernel.py:368` | REVERSE-PROJECTS |
| `net_worth_kernel._build_appreciation_balance_map` | `:466` | FLAT-CARRIES anchor |

Document discrepancy: the root-cause doc lists the two net-worth map builders at `:466/:456`;
the actual locations are `:368` (investment) and `:466` (appreciation). The behaviors are
correctly described. This is the only material error found in the document.

**What the document understates (the most important finding for sizing Level 1):** a per-account
dispatcher already exists and is partly in use.
- `account_projection.classify_account` (`account_projection.py:67`) is the single flag-driven
  taxonomy: `AMORTIZING / INTEREST / APPRECIATING / INVESTMENT / PLAIN`, branching only on
  `AccountType` boolean columns (IDs-for-logic compliant).
- `net_worth_kernel.build_account_balance_map` (`net_worth_kernel.py:255`) dispatches by that
  kind to the right engine and returns a per-period map. It is already consumed by the savings
  cockpit net-worth trend (`savings_dashboard_service/_net_worth.py:152`) and the year-end
  net-worth (`year_end_summary_service/_balances.py:86`).

So "there is no single balance-at-T function" is true as a strictly-enforced seam, but a
per-period dispatcher already exists. The catch: a second parallel dispatcher lives in
`savings_dashboard_service/_projections.py` (`_project_one_account:393` + `_compute_base_balances:37`).
It uses the same `classify_account` but re-implements dispatch and produces current balance +
3/6/12-month horizons, with its own investment seeding (`_project_investment:299` de-seeds the
current-period transfer contribution). The two dispatchers share the producers but duplicate the
dispatch and boundary handling. That duplication is the recurrence generator.

---

## Part B -- Is the current system financially correct?

Yes for recorded actuals; the recurring defect is a projection-display correctness bug at period
boundaries, not data corruption.

- Cash actuals are materialized and summed deterministically (Part A).
- Transfer integrity is enforced at three layers: the service is the only writer
  (`transfer_service.create_transfer:381`), a partial unique index caps shadows at two per
  transfer (`transaction.py:90-98`), and the five transfer invariants hold (mirrored amount /
  status / period; `_apply_status_change:484` runs the status transition machine before
  propagating).
- Loan balances are correct at read time because the resolver is the single source of truth and
  the stored `current_principal` was demoted to a non-authoritative seed (`loan_params.py:79-87`).
- The app has deliberately moved toward live-recompute so stored projections cannot go stale:
  `income_service.live_projected_net` ("treat the stored estimated_amount as a cache that cannot
  silently disagree with the salary page", `income_service.py:152-155`), `live_amount_overrides`
  (`balance_resolver.py:382-416`).

The recurring bug is narrow: for the recompute-at-read kinds, periods before the first
forward-schedule row got the wrong starting value (origination amount, an omitted/zero period, or
a disagreeing fallback). It overstates or understates a projected/historical balance on a chart
or tile. It never corrupted a stored row. Level 0 fixed the live instance; Level 1 makes it
structurally impossible.

---

## Part C -- Are Level 1 and Level 2 correct as designed?

**Level 1 (single balance-at-T seam): correct.** A single accessor that owns `classify_account`
dispatch, internalizes input assembly, applies one boundary rule, and is fenced by a checker is
the right shape, and most of it already exists (Part A). Two refinements to the document:

1. The document's boundary rule ("flat-carry the anchor backward for EVERY account") is too
   broad. Cash should stay omitted: flat-carrying cash backward fabricates balances cash never
   had, and the entire bug class lives in the recompute-at-read kinds. Correct rule: "for the
   recompute-at-read kinds (loan / investment / property), every period before the first known
   point equals the dated current/anchor value held flat; never an origination amount, never an
   omitted period, never a forward-schedule row." Cash keeps its existing materialized semantics.
2. The document's migration phase 4 ("delete the gates") conflates two concerns. The gates
   (`_CASH_GATING_KINDS`, `_loan_schedule_start_index`, `_honest_history_start_index` in
   `savings_dashboard_service/_net_worth.py:225-295`) are presentation logic (how far back to
   draw solid history on the trend), orthogonal to the balance value. They should stay; their
   removal is an optional later cleanup, not part of the fix.

**Level 2 (materialize everything as postings): described correctly, but mislabeled.** The
document's own schema -- `account_posting (account, scenario, date, signed amount, kind, source
ref, projected-vs-confirmed flag)` -- is one signed posting per change, i.e. event-sourced
materialization (`balance = sum(postings <= T)`). That is single-entry. It is NOT double-entry
(every event = two or more legs summing to zero), even though the document calls it "the textbook
double-entry / event-sourced design." This matters because converting the app so that "every
transaction and transfer follows the double-entry pattern" is the stricter, larger thing. See
Part I.

---

## Part D -- Is there a better alternative?

For ending the bug class: Level 1 is the better alternative to Level 2 (smaller, sufficient,
reuses all math). Within Level 1, the better implementation is:

- Promote the existing dispatcher behind a thin facade (e.g. `app/services/balance_at.py`)
  rather than write a new dispatcher from scratch (which would duplicate the very thing whose
  duplication is the bug) and rather than promote `build_account_balance_map` in place (its
  signature is leaky -- it demands the caller pre-assemble `debt_schedule`, `investment_params`,
  `deductions`, `salary_gross_biweekly`; both callers duplicate that unpack at
  `_net_worth.py:152-158` and `_balances.py:86-92`). The facade internalizes assembly (owns
  `generate_debt_schedules:157` + the deduction/gross fetch).
- Expose three entry points: `balance_map(account, scenario, periods)` (per-period),
  `balance_at(account, scenario, date)` (scalar -- no equivalent exists today), and a batch
  `build_maps(accounts, scenario, periods)` that preserves the existing N+1 avoidance
  (`build_account_net_worth_maps`, `_net_worth.py:101`). Omitting the batch entry would
  re-introduce per-account debt-schedule/deduction queries.

A defensible middle path toward the end-state (Part I/J): build Level 1 now; later, optionally,
materialize ONLY the confirmed (historical) layer as postings behind the seam, keeping the
projected layer on live-recompute.

---

## Part E -- Are Level 1 and Level 2 necessary to prevent recurrence?

- Level 0 alone is not sufficient. The checker `shekel-original-principal-as-balance` (W9905)
  guards only the two loan producers (`tools/pylint/shekel_checkers.py`,
  `ShekelLoanBalanceSourceChecker`). A new surface can still invent a new boundary rule on a
  different producer. The second parallel dispatcher in `_projections.py` is live proof the
  pattern recurs across files.
- Level 1 is necessary and sufficient to end the class: one module owns "balance at T," and a
  pylint checker ("no module outside the seam calls a balance producer directly") mechanically
  forbids bypass. This is buildable on the existing checker infra -- the new checker is a
  superset of W9905's `visit_call` name-matching plus a module allowlist (the seam + the engine
  cluster where producers legitimately compose each other). CI lints `app/` and `scripts/` only,
  so the 32-35 test files that call producers directly are not fenced and need no churn for the
  checker.
- Level 2 is not necessary for the bug class. It closes the same class by a costlier mechanism
  (uniform sum-of-postings) and is strictly dominated for this goal.

---

## Part F -- Must Level 1 precede Level 2?

Yes, strongly, and it is verifiable from the code, not just asserted:

- Level 2's one-time backfill must reconcile exactly against a trusted "balance at T." Today six
  producers disagree four ways at the boundary (Part A), so there is no authoritative oracle to
  reconcile postings against. Level 1 produces exactly that oracle.
- The existing penny-exact parity test
  (`tests/test_integration/test_cross_page_balance_equality.py`) is the natural backfill gate --
  but today it covers cash accounts only (its fixture seeds a single checking account; readers
  read cash balances). Level 1's work item "extend the oracle to every account kind" is the thing
  that makes a posting backfill checkable at all.

So Level 1 is not just "recommended first" -- it is the prerequisite that makes Level 2
verifiable.

---

## Part G -- Scope of work and blast radius

**Producers and consumers (verified counts):** ~43 call-site lines across the 7 core balance
producers in `app/` (most-called: `project_balance` 16, `balances_for` 15). Counting loan
resolution (`resolve_loan` 10) and subtotals pushes the "touches a balance" surface to ~55. 10
blueprints, ~35 routes, 112 templates render balances. 32-35 test files reference the producers.
Transaction writes are concentrated: 5 files / 7 construction sites (`transfer_service`,
`credit_workflow`, `recurrence_engine`, `carry_forward_service/_execute`,
`routes/transactions/create`).

**Level 1 scope (~2-3 developer-weeks):**
- Create: `app/services/balance_at.py` (~120-180 lines, zero new math, delegates to today's
  engines); `tests/test_services/test_balance_at.py`; new checker cases.
- Modify ~6-9 services: `net_worth_kernel.py` (the investment-seeding reconciliation -- the
  hardest part, below), `savings_dashboard_service/_projections.py` (largest -- replace bespoke
  dispatch with map reads), `savings_dashboard_service/_net_worth.py`,
  `year_end_summary_service/_balances.py` + `_savings.py`, `investment_dashboard_service.py`,
  `retirement_dashboard_service.py`, `dashboard_pulse_service.py`, optionally `accounts/detail.py`.
- Modify tooling/wiring (~6): the new checker in `tools/pylint/shekel_checkers.py` + `.pylintrc`,
  `post-edit-python.sh`, `ci.yml`, `.pre-commit-config.yaml`, `CLAUDE.md`/standards.
- Modify ~5-8 tests (pinned investment/year-end/net_worth_kernel suites + the extended oracle).
- Untouched: 0 of 112 templates and all but ~1-2 routes -- the seam returns the same Decimal/dict
  shapes routes already pass to templates. Level 1 is a service-layer change.
- Hardest reconciliation: the two dispatchers seed investment projections differently. The kernel
  splits at the anchor period and reverse-projects pre-anchor (`net_worth_kernel.py:422-460`); the
  savings tile splits at the current period and de-seeds the current contribution
  (`_projections.py:333-335`). They agree when anchor == current and diverge when the anchor is in
  the past. Unifying them is a genuine semantic decision (the natural survivor:
  "actual-through-today, projected strictly after," which leaves the savings tile's headline
  current balance unchanged and equal to what the oracle already locks for cash), not a
  mechanical reroute. This is the chunk that carries Level 1's real risk and test churn.

**Level 2 scope (months, depending on how far it goes):**
- New tables: `budget.account_postings` (the one genuinely new thing is a SIGNED amount column --
  every other money column carries `CHECK col >= 0`), `ref.posting_kinds`, `ref.posting_sources`,
  and (for strict double-entry) `budget.journal_entries` with a sums-to-zero constraint. Mirror
  the append-only `loan_anchor_event.py` precedent (immutability listeners, source FK, dedupe
  index).
- Audit plumbing is mandatory: any new budget table must be registered in
  `app/audit_infrastructure.py:AUDITED_TABLES`; `EXPECTED_TRIGGER_COUNT` is `len(AUDITED_TABLES)`
  and boot refuses if short. The audit trigger writes a `system.audit_log` row on every INSERT --
  a high-volume projected posting table that regenerates on every assumption edit roughly doubles
  audit write traffic or forces an audit exemption (a policy decision).
- Every write path (5 files) emits balanced postings; every read producer (~43-55 sites) routes
  through the posting reader or the seam in front of it.
- The regeneration/invalidation machine for projected postings (Part I, and Part J reason 3) is
  the dominant cost.
- 84 migrations exist today; this adds several, including an eventual destructive cutover.
- Honest effort: confirmed-layer materialization ~1-2 months; full projected materialization
  +1-2+ months; strict double-entry (chart of accounts, journal constraints, CC liability
  account, category-to-account promotion, every report rewritten) +multiple months.

---

## Part H -- Can Shekel keep working during implementation?

**Level 1: yes, cleanly.** Phased and behavior-preserving:
0. Add the seam delegating to today's producers; parity tests prove byte-identical output. No
   caller rerouted. App unchanged.
1. Extend the cross-page oracle to loan / investment / property (new fixtures: a loan with a
   pre-first-payment period and an empty/paid-off schedule; an investment with anchor == current
   and anchor-in-past; a Property), with per-kind seam-injection negative controls. This must
   happen BEFORE rerouting, because the reroute changes investment numbers and they would
   otherwise be unobservable.
2-4. Reroute net-worth, savings, year-end, and detail dashboards through the seam, one step at a
   time; each step gates on the extended oracle + full suite + pylint 10.00.
5. Turn on the checker once no `app/` site calls a producer outside the seam/cluster.
Every step is independently shippable and reversible.

**Level 2: yes, but only if Level 1 is built first** and postings are written alongside the
existing tables during a long coexistence window (`source_txn_id` backlink), with reads still
flowing through the seam and the cutover done per account kind. The destructive step (dropping or
demoting `budget.transactions`/`budget.transfers`) is effectively irreversible -- a one-way data
transform whose downgrade is realistically `raise NotImplementedError` with manual revert SQL
(per the destructive-migration rules in `.claude/rules/database.md`). The realistic posture is
indefinite coexistence: the project carries both models and both invariant sets.

---

## Part I -- Level 2 versus double-entry: what each is, and what Shekel would become

Converting the app so every transaction and transfer follows the double-entry pattern is the most
consequential option on the table, and "Level 2" and "double-entry" are easy to conflate. They are
not the same change. The distinction rests on two independent ideas.

**Two independent ideas get blurred together.**
- *How a balance is derived.* Either RECOMPUTE it from rules on every read (Shekel's loans /
  investments / property today) or MATERIALIZE it: store every change as a row and sum the rows up
  to date T (Shekel's checking today: anchor + sum of the transaction rows). Call this the
  materialization axis.
- *How many sides each event records.* Either SINGLE-ENTRY (one row changes one account; the
  category is a label) or DOUBLE-ENTRY (every event records two or more legs of equal magnitude, so
  the money's source and its destination are both accounts). Call this the entry axis.

The recurring bug lives entirely on the materialization axis: it is a recompute-at-read boundary
error. **Level 2 is a materialization-axis change; double-entry is an entry-axis change.** That is
the whole difference.

**The same events, three ways.**
- A $50 grocery purchase. Today: one row -- Checking, expense, $50. Level 2: one posting -- Checking
  -$50 (for cash this is essentially unchanged; cash is already materialized). Double-entry: two
  legs -- $50 out of Checking and $50 into a Groceries account that now carries its own running
  total.
- A $2,000 paycheck. Today and Level 2: one row, Checking +$2,000. Double-entry: two legs of $2,000
  -- into Checking, from a Salary Income account you can total.
- A $1,500 loan payment ($1,200 principal + $300 interest), where it matters. Today: the loan
  balance is RECOMPUTED on every screen by replaying payments through the amortization schedule
  (`loan_resolver`); there is no stored "principal fell $1,200" row, and because the replay runs
  only forward from today, each screen invents its own answer for periods before the first upcoming
  payment -- the recurring bug. Level 2: the payment writes rows (Checking -$1,500, loan principal
  down $1,200, Interest $300); the loan balance becomes "sum of the principal rows up to T," so
  there is no recompute and no boundary to get wrong -- the bug class is structurally gone.
  Double-entry: the SAME rows as Level 2, now required to balance (cash out $1,500 = principal
  reduction $1,200 + interest $300), with the loan as a liability account and interest as an expense
  account.

**Level 2 and double-entry form a ladder, not a fork.**
- Level 2 = make loans, investments, and property work the way checking already does (materialize
  them). The root-cause document phrases it as "make every account work the way cash already does."
- Double-entry = Level 2 PLUS balancing legs PLUS turning categories / income / interest into real
  accounts.

So double-entry is a strict superset of Level 2. The bug fix comes from the materialization half,
not the balancing half: a single-entry materialized model already kills the bug, and adding the
balancing discipline buys bookkeeping rigor, not bug-fixing. This is why the document's Level 2
schema -- one signed posting per change -- is single-entry materialization, not the double-entry it
is labeled as.

**What the current model is.** Ordinary income/expense transactions are single-entry: one row, one
account, direction by type, no balancing counter-leg (categories are flat labels -- `category.py`
group_name + item_name -- not accounts; there is no credit-card liability account, only a
payback-expense workflow in `credit_workflow.py`). Transfers are already narrow double-entry: two
balancing shadow rows. Envelope `transaction_entries` are neither -- they are a one-sided
reservation device (`max(estimated - cleared_debit - sum_credit, uncleared_debit)`, verified by the
five cross-page test cases), not a balance movement. So Shekel today is single-entry with a MIXED
derivation: checking materialized, loans / investments / property recomputed.

**What Shekel would become under full Level 2 (materialization).**
- A new `budget.account_postings` table; every balance change is a row -- each paycheck, each
  grocery, each loan principal reduction, each biweekly investment-growth step, each
  home-appreciation tick.
- Loans, investments, and property stop recomputing and start summing rows, exactly like checking;
  reads become uniform ("sum the postings") and the boundary fallback disappears.
- The forecast is the catch. Most rows are FUTURE (about two years of biweekly events x every
  account x every what-if scenario), and projected postings must be REGENERATED on every assumption
  change (a raise rewrites future paycheck postings; an ARM recast rewrites future
  principal/interest splits; an assumed-return edit rewrites future growth postings). That needs a
  regeneration engine for loans / investments / property like the one the recurrence engine already
  is for cash, with override-preservation. The audit log roughly doubles in write volume (every
  posting INSERT fires an audit-trigger row). See Part J reason 3 for why the projected half is a
  net negative.

**What Shekel would become under full double-entry.**
- A chart of accounts: Assets (checking, savings, property, investments), Liabilities (each loan,
  plus a NEW credit-card liability account the app does not have today), Income, Expenses, Equity.
- Spending categories become (or map to) accounts; "Groceries," "Salary," "Mortgage Interest" each
  carry a running balance. Today they are flat labels (`category.py`).
- Every transaction becomes a balanced journal entry with two or more legs; the single-row
  `Transaction` is wrapped or retired, and the checking anchor becomes an opening-equity posting.
- Transfers SIMPLIFY: the special shadow machinery (`transfer_service`) dissolves into an ordinary
  balanced entry like everything else.
- Envelope entries fit BADLY: a reservation ("hold back money not yet spent") is not a movement
  between accounts, so it has no natural ledger home and stays an awkward side concept.
- You gain real accounting outputs: a trial balance, an income statement, a balance sheet, and the
  built-in "debits must equal credits" error check.
- The cost: every screen, report, and write path is re-expressed in debits / credits, and the
  forecasting problem above gets harder because you are now projecting BALANCED future entries.

---

## Part J -- Fitness verdict and recommendation

**Verdict.** For this app today -- solo user, single currency, no external reconciliation,
projection-centric, already carrying narrow double-entry for transfers and dated corrections for
loans -- full double-entry is not worth its cost, and materializing the PROJECTED layer is a net
negative. Build Level 1; treat everything beyond it as optional and mostly unnecessary.

**Why double-entry is not recommended (five reasons).**
1. It does not fix the bug; Level 1 already does. Double-entry solves a different problem
   (accounting rigor and auditability). The recurring defect is a recompute-boundary problem that
   Level 1 closes completely, so double-entry would be paying for a different thing.
2. Nobody here consumes its benefits. Its payoffs -- catching hand-entry errors via the
   debits-equal-credits check, producing auditor-facing statements, reconciling across multiple
   parties / entities / currencies -- have no consumer in a one-person, one-currency budget. And
   the balance check mainly catches MANUAL-entry mistakes, but Shekel GENERATES most rows
   programmatically (recurrence engine, paycheck calculator), so there is little hand-entry for it
   to catch.
3. It fights the grain of the app (the decisive reason). A ledger records what happened; Shekel's
   core value is PROJECTING what will happen two years out and re-deriving it live as assumptions
   change. A ledger of the future is a contradiction -- future entries are not facts, they are
   current best guesses that must change. The codebase has DELIBERATELY moved toward live-recompute
   and away from stored forecasts to kill staleness: `income_service.live_projected_net` treats
   stored `estimated_amount` as a cache and re-derives projected paychecks "(the staleness gap that
   shipped the SS regression)" (`income_service.py:152-155`); `LoanParams.current_principal` was
   demoted to "Non-authoritative seed; resolver is source of truth" (`loan_params.py:79-87`);
   `live_amount_overrides` exists for exactly this reason (`balance_resolver.py:382-416`).
   Materializing the projected layer reverses that direction and re-introduces the
   stored-value-diverges-from-computed-value failure class (CRIT-01 / F-008) the remediation arc
   engineered away from, plus the new failure modes the document itself names: stale projected
   postings and regeneration races (`recurring_loan_balance_root_cause.md:236-238`).
4. The cost dwarfs the benefit: months of work, a re-modeling of the whole budget domain, doubled
   audit-write volume, a new forecast-regeneration engine, and indefinite coexistence of two data
   models (the old tables cannot be safely dropped) -- to gain rigor a solo budgeter does not need.
5. The good parts of double-entry are already present where they earn their keep. The one place two
   real accounts move (transfers) already uses balanced two-leg entries. Loans already use dated
   correction events instead of editing history (`LoanAnchorEvent`; cash anchors via
   `AccountAnchorHistory`) -- the bitemporal "never rewrite the past, post a correction" discipline
   from accounting, applied surgically. The valuable instincts are in the codebase already, without
   the full apparatus.

**Recommendation (firm).**
1. Build Level 1 now (the promoted-dispatcher seam + the extended cross-page oracle + the no-bypass
   checker). It permanently fences the bug class, reuses all existing math, ships in safe phases,
   and yields the correctness oracle any future Level 2 would need.
2. After Level 1, do NOT pursue full double-entry or full PROJECTED materialization. Level 1
   already kills the bug, so Level 2 would only be for other ends (uniform reads, an audit trail,
   read performance) that are not real needs today.
3. If you ever want to move toward the textbook end-state, the only defensible slice is to
   materialize the CONFIRMED (historical) layer behind the Level 1 seam (settled payments,
   confirmed principal reductions, true-ups) -- real audit value, no regeneration tax -- while
   leaving the projected layer on live-recompute. This never promotes categories to accounts and
   never drops the existing tables.

**Revisit triggers that would change this verdict:** multi-user shared or multi-entity ledgers;
multi-currency or tax-lot/cost-basis tracking; regulated reporting needing a trial balance;
external bank/brokerage import needing entry-level provenance; or a measured read-time recompute
bottleneck (there is none today -- full suite 6317 tests in ~103 s).

---

## Decision: Option D is the optimal architecture (chosen direction)

This section records the architecture the project has chosen and why it is optimal on the merits,
independent of effort. It sharpens Part J. Part J asked "is double-entry worth its cost?" and
recommended a confirmed-layer middle path; the analysis here removes cost from the question and
shows that same shape -- named Option D below -- is not a budget compromise but the genuinely best
design, and that doing "more" than it is worse, not merely costlier. Option D is Part J
recommendation 3 elevated to the chosen end-state.

### The principle that decides it: fact versus derivation

Every value the app stores or shows is one of two kinds, and the two want opposite strategies:

- A FACT (a payment that cleared, a paycheck that landed, a settled transfer) is an immutable
  observation. Its single source of truth is the event itself. Correct representation: store it
  once, immutably, and never recompute it.
- A DERIVATION (a projected future balance, a future loan principal split, modeled investment
  growth) is a pure function of current assumptions (salary profile, rates, returns). Its single
  source of truth is the assumption. Correct representation: recompute it on demand and store
  nothing, because a stored copy must be kept equal to f(assumptions) and the two will drift.

The counterintuitive consequence: "best regardless of effort" does NOT mean "materialize
everything." Materializing a derivation is a denormalization (storing a value already derivable),
which is a correctness liability, not an asset. Unlimited effort can make a regeneration engine
more reliable but cannot make the duplicated copy stop being a duplicate; the only thing it buys is
read speed. The codebase already discovered this: the recurrence engine materializes projected
amounts, yet `income_service.live_projected_net` recomputes over them because the stored copy is "a
cache... the staleness gap that shipped the SS regression" (`income_service.py:152-155`), and
`LoanParams.current_principal` was demoted to non-authoritative for the same reason
(`loan_params.py:79-87`). The best design finishes that move off stored derivations; it does not
reverse it.

The fact-versus-derivation line is not a new concept to bolt on: it already exists in the domain as
`Status.is_settled` ("the real-world transaction has completed", `ref.py:208`), the
`projected -> done/received -> settled` workflow enforced by `verify_transition`
(`state_machine.py`), and two append-only dated correction logs (`LoanAnchorEvent`;
`AccountAnchorHistory`, `account.py:149`). Option D extends those patterns rather than grafting on a
foreign one.

### Every combination, scored

Building blocks: L1 (the one balance-at-T seam + no-bypass checker), materialize-confirmed,
double-entry-confirmed (balanced legs + a chart of accounts), materialize-projected,
double-entry-projected, double-entry-transfers (a slice).

| # | Combination | What it adds | Bug classes it removes | New risks it creates | Verdict |
|---|---|---|---|---|---|
| 0 | Level 0 only (today) | a checker on 2 producers | none structural (point fix) | a new surface still invents a new boundary rule | insufficient |
| A | + L1 seam | one read accessor, one boundary rule, enforced | cross-surface balance divergence (the recurring bug) | ~none | necessary floor; the 80/20 |
| B | + double-entry transfers only | formalizes transfers as postings | none new (transfers are already two balanced legs) | churn for no functional gain | only as a pilot for D |
| C | + materialize confirmed (single-entry) | actuals stored as postings; stop re-deriving the past | "stored actual vs recomputed actual" drift; "the past moved" | coexistence double-counting | good, but no self-check |
| D | + double-entry confirmed; projections recompute | balanced legs + accounts on the confirmed ledger | all of A and C plus a continuous self-checking invariant | chart-of-accounts modeling; correction discipline | CHOSEN -- the optimum |
| E | + materialize projected too (single-entry everywhere) | uniform "sum postings" reads | same as C | stale projections, regeneration races, denormalization | worse than D |
| F | + double-entry everything (confirmed + projected) | the maximal ledger | same as D | D's risks plus all of E's | worse than D despite being "the most" |

The three comparisons that decide it:
- **D beats A.** Level 1 alone kills the recurring bug (one place answers "balance at T," so
  surfaces cannot disagree), but it leaves the past recomputed: the loan resolver re-derives every
  confirmed payment's effect on every read, which is fragile because a fact is being recomputed. D
  stores the fact once and adds a self-check A lacks. A is the best value; D is the best app.
- **D beats C.** Single-entry materialization stores actuals but records one side, so it has no
  internal cross-check. Double-entry records both sides and requires every event to sum to zero.
  That sum-to-zero rule is the strongest bug-prevention feature in accounting: a posting mistake
  shows up immediately as an unbalanced journal or `assets != liabilities + equity`, catchable by a
  constraint and a test. It is a continuous, self-enforcing correctness invariant.
- **D beats F (the decisive one).** F is "the most," and it is worse. The only thing F adds over D
  is materializing the projected layer, which is materializing a derivation -- a denormalization
  that stores next year's postings AND the assumptions they came from and must keep them equal
  forever. That is the stale-cache class the codebase has repeatedly engineered away from, plus
  regeneration races and override-preservation machinery, for zero correctness gain (only read
  speed, which is off the table). With unlimited effort F is strictly D plus a liability. The best
  app is not the maximal app; it is the one that draws the materialize/recompute line exactly at
  the fact/derivation boundary -- Option D.

### What Option D is

- **One read seam (Level 1).** A single `balance_at(account, scenario, date)` / `balance_map(...)`
  is the only way any screen gets a balance, with one boundary rule, enforced by a checker that
  forbids any other module from calling a balance producer. This is the structural end of
  cross-surface divergence.
- **Confirmed events are an immutable double-entry posting ledger.** When a transaction, transfer,
  loan payment, or cleared envelope entry settles, the system writes a balanced journal entry
  (debits = credits) into an append-only `account_postings` table. Confirmed balances become "sum
  of postings up to T." Corrections are new balancing postings, never edits, mirroring
  `LoanAnchorEvent` / `AccountAnchorHistory`. The confirmed loan ledger finally stores each
  payment's real principal / interest split instead of re-deriving it.
- **Projected events stay derived.** The future is computed live from assumptions through the
  existing engines, behind the same seam. A projected transaction remains an editable plan (a row
  with identity so it can be overridden, moved, marked paid); its amount's source of truth stays
  the assumption.
- **The lifecycle is the existing status workflow.** `projected -> done/received -> settled` is the
  plan-to-posted transition; the `verify_transition` choke point is where the "emit the journal
  entry" hook belongs. No new lifecycle is invented.
- **A real chart of accounts** so the second leg has a home: assets, liabilities (loans, plus a
  credit-card liability account the app lacks today), income, expenses (categories promoted to
  accounts), equity (opening balances). This unlocks an actuals income statement and balance sheet
  and makes the sum-to-zero check meaningful.
- **Transfers fold in naturally** (already two balanced legs); at settle they become an ordinary
  balanced journal entry, and the bespoke shadow machinery simplifies into the general pattern.

### Why D makes financial incorrectness and recurring bugs least likely

1. **One read seam** removes cross-surface divergence directly -- the exact recurring defect.
2. **Facts stored once, never recomputed** removes "the past changed" and "stored actual disagrees
   with recomputed actual"; the loan resolver no longer re-derives confirmed history on every read.
3. **The double-entry sum-to-zero invariant** is a continuous self-audit -- the one mechanism that
   catches a brand-new, never-seen posting bug automatically.
4. **Projections recomputed, not stored** removes the stale-cache / regeneration-race class
   entirely by never creating a second copy of a derived value (the class E/F would add).
5. **Enforcement (the checker) plus the cross-page equality oracle extended to every account kind**
   keeps all of the above from eroding -- the absence of which let the bug recur in the first place.

D beats F on bug-resistance because it matches each value's storage to its nature, so every
mechanism has one job (postings record facts; engines derive forecasts). F overgeneralizes one
mechanism onto two natures; the strain becomes the regeneration/staleness machinery. Fewer
mismatched mechanisms means fewer seams a bug can live in.

### Honest disadvantages of D

- A multi-month build: a posting table and chart of accounts, settle-time posting writers,
  append-only corrections, the category-to-account promotion, and the seam that stitches "sum of
  confirmed postings" to "live projection forward."
- Two representations coexist by design (planned shells for the future, posted ledger for the
  past). Conceptually clean (plan vs fact) but more surface area; the seam must never read both for
  the same event (the generalization of transfer invariant 5).
- Investments and property have thin confirmed data (their "past" is mostly modeled growth, not
  observed money movement), so the confirmed ledger is in practice a cash-and-loan ledger; those
  two stay projection-driven even for history.
- Promoting categories to accounts and introducing a credit-card liability account are real
  domain changes that touch budgeting UX, not just plumbing.

None of these are correctness problems; they are scope and modeling costs, worth paying for the
best app. The projected materialization in E/F, by contrast, is a correctness cost and is excluded.

### Build order (chosen; each step shippable, each gated by the extended oracle + full suite)

1. **Level 1 seam + enforcement checker + extend the cross-page equality oracle to every account
   kind.** Fixes the bug class now; becomes the reconciliation oracle for everything after.
2. **Posting ledger + chart of accounts, piloted on transfers.** Build the table and the
   balanced-journal constraint on the already-balanced, centralized event type.
3. **Post confirmed cash transactions and cleared envelope entries** at settle.
4. **Post confirmed loan payments** with their real principal / interest split; retire the
   read-time replay of confirmed history.
5. **Actuals reporting** (income statement, balance sheet, trial-balance check) on the confirmed
   ledger.
6. **Stop.** Projections, and investment / property modeling, stay on live-recompute behind the
   seam.

---

## Part K -- How these conclusions were verified (and how to re-verify)

- Diagnosis: read all six producers and quoted each boundary rule (`balance_resolver.py:419,787,898-903`;
  `balance_calculator.py:58,114-116,130`; `account_projection.py:162,205,252-264`;
  `net_worth_kernel.py:368,466,522-537`; `growth_engine.py:407,489`). Document line-number swap
  noted in Part A.
- Existing dispatcher: read `account_projection.classify_account:67`,
  `net_worth_kernel.build_account_balance_map:255`, and the second dispatcher
  `savings_dashboard_service/_projections.py:37,299,393`. Confirmed kernel consumers via grep
  (`_net_worth.py:152`, `_balances.py:86`).
- Storage paradigm: read `transaction.py` (sign convention, `entries` relationship),
  `transaction_entry.py` (envelope semantics), `transfer_service.py:282-481` (shadow
  double-entry), `loan_params.py` + `loan_anchor_event.py` (recompute-at-read).
- Live-recompute direction: read `income_service.py:135-155`, `loan_params.py:79-87`,
  `balance_resolver.py:382-416`.
- Oracle: read `tests/test_integration/test_cross_page_balance_equality.py` in full (6 surfaces,
  cash-only, seam-injection negative control).
- Counts: producer call sites (~43), transaction write paths (5 files / 7 sites), templates (112),
  migrations (84), `AUDITED_TABLES` requirement -- all from direct grep.

To re-verify the fitness pillar in one command:
`grep -n "cache\|staleness\|Non-authoritative" app/services/income_service.py app/models/loan_params.py app/services/balance_resolver.py`.

---

## Status

Investigation complete; the developer has chosen Option D (see the Decision section) and is building
toward it following the recommended build order. This document is the architecture-of-record for that
effort, alongside `recurring_loan_balance_root_cause.md`. Each build-order step is taken as its own
explicitly-scoped implementation plan, gated by the cross-page equality oracle (extended to every
account kind in step 1) and the full suite.

**Build-Order Step 1 (Level 1: the `balance_at` seam + W9906 no-bypass checker + per-kind cross-page
oracle) -- DONE (2026-06-27)** on `feat/level1-balance-seam`. Locked decisions held: investment
seeding is Model-from-anchor, and the fence is a FULL fence -- every balance read in `app/` routes
through `app.services.balance_at`, including the investment cash-basis SEED (the seam's
`investment_seed_map` wraps the kernel producer, which is itself now guarded by W9906, so there is no
display-shaped balance map a consumer can reach outside the seam). The seam grew two refinements
beyond the original three-entry plan: (a) a CASH-FLOW view (`cash_balance_map` / `cash_balance_at`)
distinct from the KIND-CORRECT view, for the single-account surfaces whose projected balance must
reconcile with their own transaction rows (grid / calendar / obligations / checking detail); and
(b) the kind-correct scalar accrues interest for an INTEREST account (consistent with the map), with
date-precise cash reserved for the explicit cash-flow scalar. The implementation plan
(`implementation_plan_level1_balance_seam.md`) carries the full per-commit record and the Commit-10
adversarial-review outcome. The presentation gates and the deferred kind-correct-grid feature
(`followup_kind_correct_grid_interest.md`) remain as the fitness doc planned.

**Build-Order Step 2 (Level 2: the append-only double-entry posting ledger + chart of accounts,
piloted on transfers) -- CODE-COMPLETE (2026-06-28)** on `feat/posting-ledger-transfers` (off `dev`,
all six commits green; pending the `dev -> main` PR so CI runs). The implementation plan
(`implementation_plan_posting_ledger_transfers.md`) carries the full per-commit record. What shipped,
exactly as Option D prescribes (confirmed facts only; reads unchanged; the legacy tables never
dropped):

- **Three `ref` catalogues** (`ledger_account_classes` with an `is_debit_normal` flag,
  `posting_kinds`, `posting_sources`) + enums + `ref_cache` accessors (Commit 1).
- **`budget.ledger_accounts`** -- the chart of accounts, one Asset/Liability ledger account paired
  per real account by a `create_account` sync hook and a historical backfill; class derived from the
  account-type category by ID, never name (Commit 2).
- **`budget.journal_entries` + `budget.account_postings`** -- the append-only ledger: one signed
  `Numeric(12,2)` `amount` (debit-positive / credit-negative, the only signed money column),
  `before_update`/`before_delete` ORM immutability guards (corrections are reversing entries, never
  edits), and the genuinely-new mechanism: a DEFERRABLE INITIALLY DEFERRED constraint trigger
  (`ck_account_postings_balanced`, centralised in `app/posting_infrastructure.py`) enforcing per-entry
  `SUM(amount)=0` and `COUNT>=2` at COMMIT. A raw-SQL migration backfills one balanced entry per
  historical settled, non-deleted transfer, making the oracle production-wide (Commit 3).
- **`app/services/posting_service.py`** -- the sole writer: `sync_transfer_postings(xfer, settled=)`
  reconciles a transfer's net posted effect to its target by emitting ONE balanced delta entry,
  idempotent across the whole lifecycle (settle / revert / archive / cancel / delete / restore). The
  posted magnitude is the SHADOW's `effective_amount` (`COALESCE(actual, estimated)`), not
  `transfers.amount` -- the value the balance calculator, the backfill, and the oracle all agree on
  (Commit 4).
- **Lifecycle wiring** at the three transfer chokepoints (`update_transfer` END, delete-reverse-before,
  restore-repost-after), gated on `is_settled` (Commit 5). A separate guard fix archives (never
  hard-deletes) any account whose ledger has postings.
- **The reconciliation oracle** (`tests/test_integration/test_posting_ledger_reconciliation.py`,
  Commit 6): per-account reconciliation (asset + liability + divergent-actual), per-entry balance,
  global trial balance, multi-scenario isolation, owner-via-`journal_entry.user_id` isolation, and
  backfilled-vs-go-forward agreement, and a per-transfer completeness check (no settled transfer is
  silently unposted) -- each non-tautological (hand-computed literals + independent cross-table
  queries + the service helpers) with two adversarial cases proving the checks are not vacuous. Full
  suite **6510 passed**; `pylint app/ scripts/` 10.00.

Reads are unchanged -- every balance still flows through the `balance_at` seam over
`budget.transactions`; the ledger is a parallel, independently-checkable record of the
confirmed-transfer subset, validated only by the oracle. Steps 3-5 (cash + envelope entries, loan
payments, actuals reporting) extend the same `posting_service` and switch confirmed reads onto the
ledger.
