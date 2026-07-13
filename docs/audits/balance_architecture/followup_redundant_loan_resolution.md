# Follow-up: each loan is resolved 4x per `/savings` render

**Status:** OPEN (not started). Found 2026-07-12 while closing the W9906 fence hole on
`loan_owed_at_dates` (`followup_fence_loan_owed_at_dates.md`). Deliberately NOT fixed there: it is a
distinct concern (DRY / performance, not the fence), it needs its own design decision, and folding it
in would have made a behavior-preserving refactor unreviewable.

**Not a correctness bug.** Every resolution returns the same answer -- the loan resolver is
deterministic and all four paths seed from the same genesis ledger. This is waste, not a wrong
number. It is filed because redundant producer calls in one request are a DRY violation by this
project's standard, and because the resolver is the single most expensive thing on the page.

---

## The measurement

Instrumented `resolve_account_loan` / `resolve_loan_seeded` / `generate_debt_schedules` and drove one
real `compute_dashboard_data(user_id)` against the dev database (user 1: **Mortgage** + **Van Loan**):

```text
=== ONE compute_dashboard_data(/savings) render ===
  generate_debt_schedules (per call)         5
  resolve_loan_seeded     (per loan)         2
  resolve_account_loan    (per loan)         6
```

`resolve_account_loan` itself calls `resolve_loan_seeded`, so that is **8 full resolver runs for 2
loans = 4 resolutions per loan, per render.**

Each run is not cheap: it loads the loan context (payments, escrow, rate changes), replays the rate
periods, seeds from the confirmed ledger, and projects the amortization forward to payoff -- for a
30-year mortgage, ~360 rows.

## Where the four come from

```text
1. _account_balance_maps        -> balance_at.build_maps -> _assemble_inputs -> generate_debt_schedules
2. build_account_net_worth_maps -> balance_at.build_maps -> _assemble_inputs -> generate_debt_schedules
3. _build_trend_window          -> generate_debt_schedules            (direct; the honest-history gate
                                                                       needs each loan's FIRST payment
                                                                       date, which the balance maps do
                                                                       not carry)
4. _retirement_investment_bands -> load_projection_batch -> balance_at.build_maps -> generate_debt_schedules
5. _liability_band              -> balance_at.liability_owed_at_dates -> loan_owed_at_dates
                                                                       (was the W9906 hole; now behind
                                                                        the seam, but still its own
                                                                        resolution)
```

(Five `generate_debt_schedules` calls, but two of them are handed loan-free account subsets, hence 6
`resolve_account_loan` calls rather than 10.)

Plus `_compute_loan_account` in `_projections.py`, which resolves each loan directly via
`resolve_loan_seeded` for the loan TILE (current balance, payment, rate, payoff) -- deliberately, and
documented as such, because the tile wants the rich `LoanState`, not a balance map.

## The root cause

The `balance_at` seam is **stateless by design**: every entry re-assembles its own inputs
(`_assemble_inputs`) so that single-account and batch reads cannot drift. That is the right call for
correctness, and it is what makes the seam safe to call from anywhere. The cost is that N seam calls
in one request do N input assemblies -- including N loan resolutions.

Nothing in the request threads the already-resolved `DebtSchedule` set from one consumer to the next.

## Options (needs a decision before building)

1. **Request-scoped resolution cache.** Memoize `generate_debt_schedules` / `resolve_account_loan` per
   `(account_id, scenario_id, today)` for the life of a request (Flask `g`, or an explicit cache
   object threaded by the orchestrator). Smallest diff, biggest win, and every existing call site
   keeps its current shape. Risk: a cache keyed on `date.today()` is a footgun in a long-lived
   process or a test that freezes time; it also hides the redundancy rather than removing it. The
   services layer is Flask-free (`CLAUDE.md`), so it must NOT reach for `flask.g` -- the cache would
   have to be an explicit parameter, which erodes the "call the seam from anywhere" property.
2. **Thread the resolved schedules through the savings orchestrator.** The orchestrator already loads
   core data once and passes it down; it could resolve the debt schedules ONCE and hand them to the
   seam (an optional `debt_schedules=` input on `_assemble_inputs`). Honest -- the redundancy is
   removed, not hidden -- and keeps the seam Flask-free. Larger diff, and it widens the seam's
   signature, which the Level-1 plan deliberately kept narrow.
3. **Do nothing, document it.** The page is not slow enough to be a complaint today. Revisit if the
   loan count or the render time grows.

Recommendation: **(2)**, because it removes the waste instead of masking it and respects the
service-boundary rule -- but it is a real design change to the seam's input contract and should be
decided deliberately, not slipped into an unrelated commit.

## Pointers

- Orchestrator: `app/services/savings_dashboard_service/_orchestrator.py`
  (`_compute_net_worth_section`, `_build_trend_window`).
- Seam input assembly: `app/services/balance_at/_inputs.py` (`_assemble_inputs`).
- Resolver entry: `app/services/loan_resolution.py` (`resolve_account_loan` -> `resolve_loan_seeded`).
- The loan tile's deliberate direct resolution: `savings_dashboard_service/_projections.py`
  (`_compute_loan_account`).
