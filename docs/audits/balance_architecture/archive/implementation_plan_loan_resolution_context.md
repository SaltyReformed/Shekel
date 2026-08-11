> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is `docs/plans/steps.md`; the code as
> committed is the source of truth for what the app does.

# Read-pass loan resolution context: one resolution per loan, one pinned as-of

**Status: SHIPPED to `dev` 2026-07-13** -- commits `b61aee9c`, `866e30b0`, `84c6e066`, `7b7c909b`,
`2d90106a`. Full suite 7367 passed; `pylint app/` 10.00/10 with the full `--fail-on` set; 148 checker
tests; every affected page rendered live against the dev database; the before/after snapshot of all ten
producers is byte-identical.

Follows Build-Order Step 1 (`implementation_plan_level1_balance_seam.md`) and the loan read switch
(`implementation_plan_loan_read_switch.md`). Triggered by
`followup_redundant_loan_resolution.md`, which this arc RESOLVES -- and corrects.

## Context

`followup_redundant_loan_resolution.md` reported that each loan was resolved 4x per `/savings` render
and framed it as **"waste, not a wrong number."** The developer's brief was the opposite hypothesis:
that the lack of DRY was masking something worse. It was.

### What I verified (every claim measured against the dev database, user 1: Mortgage + Van Loan)

**The count was 11, not 8.** Instrumenting the real producers around one `compute_dashboard_data(1)`:

| producer | runs |
|---|---|
| `loan_resolver.resolve_loan` (full resolutions) | **11** |
| `resolve_loan_seeded` | 9 |
| `resolve_account_loan` | 7 |
| `generate_debt_schedules` | 5 |

321 SQL queries, ~630 ms; one `resolve_account_loan(Mortgage)` = 19 queries + ~19 ms (273 schedule
rows). The filing missed two callers: `_compute_cockpit_grid_section` -> `compute_property_equity` ->
`resolve_home_equity` -> `resolve_account_loan` (`_net_worth.py:524`, `home_equity_service.py:120`),
and `_loan_ever_paid_off` -> `resolve_loan(as_of=date.max)` (`_projections.py:118`). Nor was it a
`/savings` quirk: `/dashboard`'s `compute_tracks_section` ran 8, the standalone horizon 6,
`compute_debt_summary` 4.

**"Not a correctness bug" was FALSE.** The filing claimed "every resolution returns the same answer --
all four paths seed from the same genesis ledger." Two of them **cannot** seed from the ledger and
never did. `_loan_ever_paid_off` calls `resolve_loan(inputs, date.max)` with no `confirmed_view`, and
`confirmed_loan_view` returns `None` for any `as_of` after today **by contract**
(`loan_payment_service.py:529`). So the predicate was structurally pinned to the pre-read-switch anchor
replay -- and that replay is **blind to money**: `_replay_from_anchor` (`loan_resolver/_periods.py`)
builds `ConfirmedPayment(period_start=..., due_date=...)` and passes **no amount**, advancing one
SCHEDULED step per confirmed payment (`principal = period P&I - interest`) and discarding the cash.

`is_paid_off` is load-bearing: it gates the debt card's `total_debt` (`_metrics.py:282` drops a
paid-off loan), the principal-paid fraction (`:356`), the Horizon's domain and debt-free date
(`_horizon.py:144`), the payoff milestones (`:548`), and the "Paid off" badge
(`savings/dashboard.html:223`, `_cockpit.html:157`). Both failure directions are reachable through the
**production settle path**, and both are now regression-tested with a non-vacuity assertion that ALSO
evaluates the retired producer:

* **Lump-sum payoff.** Retire a $1,000 loan with one settled $1,100 payment. The ledger books the real
  principal and reads `$0.00`; the hero shows `$0`. The replay took a single ~$40 scheduled step, still
  owed ~$960, and `is_paid_off` stayed False -- no badge, still active debt in the Horizon, "Debt-free"
  pushed years out. **This is why the app required the operator to record a manual balance true-up to
  $0 after any lump-sum payoff**: a band-aid for a producer that could not see the cash (the existing
  `test_paid_off_true_when_confirmed_covers_balance` documents the workaround in its own docstring).
* **Short-paid loan.** Two $100 payments on a 2-month loan whose scheduled payment is ~$502. The replay
  exhausts the term and reaches `$0.00` while the ledger still owes ~$810. `is_paid_off` went True and
  the loan **vanished from `total_debt`**, its full original principal counted as paid. Real, still-owed
  debt left the debt card.

The ten resolutions that agreed are exactly what made the eleventh invisible. **Redundant derivation is
not a performance smell; it is where a divergence hides.**

### The other holes the redundancy masked

1. **The loan's displayed balance never passed through the seam.** `LoanState` bundles rich detail WITH
   `current_balance` -- a balance-at-today -- and W9906 binds on function NAMES, so it cannot see an
   attribute read. `resolve_account_loan` was excluded as a "rich primitive"
   (`balance_seam.py`, `_BALANCE_PRODUCERS` header), and the loan tile, the net-worth hero that reduces
   over it, the debt card, the Horizon's index-0 liability, the equity card's mortgage leg, and
   `/debt-strategy` all read `.current_balance` outside the one tested place with every gate silent.
   They agreed with the seam only because both paths bottomed out in the same ledger -- **agreement by
   luck**, the exact failure signature of the balance-bug family.
2. **The clock was hidden.** `generate_debt_schedules` took no date and called `date.today()` itself.
   The Taxes tab computes a display-tz `today` (`analytics.py:237`), threads it into
   `compute_tax_report`, and `_build_schedule_a` handed it to `generate_debt_schedules`, which
   **silently discarded it**. Prod pins `TZ: America/New_York`, so the two agree in production today --
   a live hazard, not a live miscalculation, but it breaks under CI's UTC and meant no caller could ever
   ask for a historical or what-if as-of. 12+ independent `date.today()` derivations in one render.

### Root cause

**A loan's resolved state was not a first-class, as-of-pinned, read-pass-scoped value.** With no single
"the loan, resolved" object, every consumer re-derived it off a hidden clock -- some outside the seam,
one with a retired producer that could not read the ledger. The DRY violation and the correctness holes
are one defect seen from two sides, and "waste, not a wrong number" is what kept the holes invisible.

A cache is therefore the wrong fix. `flask.g` would break the Flask-free service boundary and go STALE:
the loan write paths (`loan_recurrence_sync.py:97`, the transfer posting sync) resolve loans **mid-
mutation**, so a request-scoped cache would serve a pre-write loan to a write-then-render request.
Threading raw `debt_schedules` into the seam -- the follow-up's own recommendation -- would have fixed
only `/savings`, added an optional input that corrupts silently when stale, and fixed none of the holes.

## Design

`BalanceContext` (`app/services/resolution_context.py`): a value object built ONCE per **read pass**,
carrying the pinned `as_of`, the baseline scenario, and a lazy memo of each loan's `ResolvedLoan`.
Threaded into the `balance_at` seam in place of the bare `scenario`.

```
loan_resolution  (resolve_loan_bundle: params + anchor facts + context + LoanState)
      ^
resolution_context  (BalanceContext: as_of + scenario + memo[account_id] -> ResolvedLoan)
      ^
net_worth_kernel  (generate_debt_schedules(accounts, ctx) reads ctx.loan_state)
      ^
balance_at seam   (build_maps / balance_at / ... take ctx; + loan_figures)
      ^
consumers         (routes build the ctx and pass it down)
```

Acyclic, Flask-free, no global state. **Read pass, not request**: a write path simply builds a fresh
context after its write, so there is no cache-invalidation class of bug -- there is no cache, only a
memo whose lifetime is the one read it was built for.

**Two dates, deliberately distinct.** `ctx.as_of` is the resolver's NOW (what is confirmed, what the
loan currently owes). The `as_of` argument of `balance_at.balance_at()` is the VALUATION date. They
were conflated while "now" was an implicit `date.today()` inside each producer, so a caller could ask
for a historical valuation and silently get it measured against a loan resolved at today.

**`as_of` source.** The context takes an EXPLICIT `as_of`, defaulting to `date.today()` at every
existing call site, so no number moves. `app/utils/dates.display_today()` already exists and already
carries the project's ruling -- "storage and the resolver's replay boundary stay UTC (`date.today()`);
this is the presentation-layer 'now'" (`dates.py:75-95`) -- so the resolver's default basis is NOT
changed. The win is that `as_of` is now *injectable and honored*: the Taxes tab's display-tz `today`
finally reaches the resolver.

## What shipped

| commit | what |
|---|---|
| `b61aee9c` | `BalanceContext` + `ResolvedLoan` + `resolve_loan_bundle`; the seam's 26 call sites take `ctx`; `_DashboardCoreData.scenario` -> `balance_ctx`; the tracks section shares ONE pass across its three producers |
| `866e30b0` | `is_paid_off` onto the genesis ledger; `_loan_ever_paid_off` and its `date.max` probe deleted |
| `84c6e066` | `debt_schedule_rows` (rows, no balance); `generate_debt_schedules` fenced; the resolution-count gate; the type-drift test |
| `7b7c909b` | `balance_at.loan_figures` (rich detail, balance deliberately ABSENT); the loan resolver fenced; `/debt-strategy`, the equity card, and the loan tile rerouted through the seam; the phantom-paydown fix |
| `2d90106a` | the three follow-ups closed |

### Measured result

| producer | resolutions | SQL queries |
|---|---|---|
| `/savings` | 11 -> **2** | 321 -> 209 |
| `/dashboard` tracks | 8 -> **2** | 113 -> 92 |
| `/savings` horizon | 6 -> **2** | 145 -> 118 |

Exactly one resolution per loan. `TestOneResolutionPerLoanPerReadPass` spies on `resolve_loan_bundle`
(the single db-facing load the memo wraps, so it counts every resolution anywhere in the pass) and makes
the DRY property a **deterministic gate**, not a hope.

## Deviations from the plan (what reality changed)

Recorded because they are the parts a future reader will not guess.

1. **C1 and C2 merged.** The plan staged "introduce the context" and "reroute the seam" as separate
   commits. A signature change is atomic -- the intermediate state would have been dead scaffolding --
   so they landed as one commit (`b61aee9c`).

2. **`display_today()` already existed, and already carried a ruling.** The plan proposed adding it and
   moving the resolver onto the display timezone. `app/utils/dates.py:75` already defines it AND
   documents the opposite ruling ("the resolver's replay boundary stays UTC"). The plan was corrected
   rather than the ruling: `as_of` became injectable with `date.today()` as the default, so no number
   moves and the Taxes tab's date is *honored* instead of discarded.

3. **`followup_horizon_loan_predicate_split.md` was a FALSE ALARM.** It claimed the Horizon's domain /
   milestones (reading `ad["loan_params"]`) would disagree with its liability band (asking the
   classifier) for a type-drifted account. They do not: `_data._load_loan_params_and_escrow` builds
   `loan_params_map` from the accounts whose TYPE carries `has_amortization` (`_data.py:92-95`) -- the
   SAME flag `classify_account` branches on (`account_projection.py:102`) -- so a drifted account never
   enters the map and all three producers skip it alike. Verified with `TestTypeDriftedLoanParamsRow`,
   the test that follow-up correctly said did not exist. **No code change was needed.**

4. **A NEW defect surfaced, and the seam was the guilty party.** Routing the loan tile's balance through
   `balance_at.balance_at` made the scalar and the resolver disagree. Investigating showed the **seam**
   was wrong: on its no-ledger fallback, `amortizing_balance_at` walked the **FULL** schedule, so
   UNCONFIRMED rows dated on or before the valuation date reduced the balance by principal the borrower
   never paid. A $240,000 loan originated 18 months ago and never paid read as **$236,853.27** owed, as
   though 17 unpaid installments had been made. It is the identical defect `loan_owed_at_dates`
   explicitly **refuses** to commit at its own boundary ("a past-or-today date would report the balance
   net of a payment that was never made -- silently UNDERSTATING the debt"), and the fallback's own
   comment always claimed it read the confirmed rows. Now it does
   (`TestUnpaidScheduleRowsNeverReduceTheDebt`).

   **Developer ruling, 2026-07-13:** keep the fix and correct the two hand-computed year-end
   debt-progress assertions, which encoded the old phantom paydown (a never-paid loan "paying off"
   $2,452 in 2025). Nothing live moves: year-end has no route (`/analytics/year-end` redirects) and
   every production loan is opened in the ledger, so production reads the ledger path and never this
   fallback.

## Residuals

* **The loan DETAIL page (`app/routes/loan`) remains allowlisted** to call the resolver directly. It is
  a genuine rich-primitive consumer (the amortization table, the payoff and refinance calculators), and
  it reads through its own `_resolve` seam. Its balance hero therefore still comes from
  `LoanState.current_balance` rather than `balance_at.balance_at`. The two are equal on the ledger path;
  rerouting it is a candidate follow-up, not a known defect.
* **`year_end_summary_service` is route-dead.** `compute_year_end_summary` has no caller outside its own
  package and `/analytics/year-end` 302s to `/analytics` (`analytics.py:349`). It was rerouted through
  the context for consistency; whether to delete it is a separate decision for the developer.
* **The no-ledger fallback is reachable only before a loan's first posting sync.** Every production loan
  carries an OPENING posting, so the fallback path is exercised by test fixtures and by a
  freshly-created loan, not by live data.

## Pointers

* The context: `app/services/resolution_context.py` (`BalanceContext`, `require_scenario`).
* The whole-loan read: `app/services/loan_resolution.py` (`ResolvedLoan`, `resolve_loan_bundle`).
* The balance-free rich view: `app/services/balance_at/_loan_figures.py` (`LoanFigures`, `loan_figures`).
* The fence: `tools/pylint/shekel_checkers/balance_seam.py` (`_LOAN_RESOLVER_PRODUCERS` /
  `_LOAN_RESOLVER_MODULES`, and `_FENCED_CALL_SURFACES` -- the one table both visitors now walk).
* The gates: `TestOneResolutionPerLoanPerReadPass`, `TestPaidOffReadsTheLedgerNotTheReplay`,
  `TestTypeDriftedLoanParamsRow` (savings), `TestUnpaidScheduleRowsNeverReduceTheDebt` (kernel).
* The corrected filing: `followup_redundant_loan_resolution.md`.
