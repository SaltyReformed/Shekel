# Implementation plan: property equity chart rebuild (date-anchored axis)

Single implementation plan for **Option 3** (the full correct rebuild) of the property detail
equity-over-time chart, adopting **(a) contractual back-projection** for a loan's pre-tracking debt
(developer ruling, 2026-07-12).

Findings and reproductions this plan closes: `docs/design/property_detail_followups.md` (H1 paid-off
fallback unreachable, H2 front-aligned merge breaks hero reconciliation, H3 past-dated projected
months get a fabricated market value, D1 double resolution). Producer under rebuild:
`app/services/property_equity_chart.py`.

## 1. Root cause and target

The current producer reasons in **loan-schedule-index space** and assumes "today" is the
confirmed/projected boundary. It must reason in **calendar-date space anchored at `today`**. Every
per-month decision keys off a calendar date, not a schedule index:

- The debt line spans a loan's **origination .. payoff** on a shared monthly calendar axis.
- The value line holds today's anchor flat for every month `<= today` and compounds strictly after.
- "No outstanding secured debt" is decided by **future** balance, not schedule emptiness.
- Multiple loans sum **by calendar month**, never front-aligned at index 0.

The rebuild also makes the producer a **pure function** fed pre-resolved loan data (folds in D1):
the route resolves each loan once and feeds both the equity hero and the chart from that one pass.

## 2. Per-loan debt series (the (a) contractual back-projection)

For one secured loan, build a full monthly debt series across its own
**origination-first-payment .. payoff** span, in five regions. `state` is the loan's resolved
`LoanState` (`resolve_account_loan(...).schedule == history_rows + committed_forward`), and
`tracking_start` is `state.schedule[0].payment_date` (the first row the resolver produced from the
latest anchor).

| Region (by calendar month `m`) | Balance | Confidence tier |
| --- | --- | --- |
| `m < origination_first_payment` | `0.00` (the loan did not exist) | n/a (excluded from this loan's span) |
| `origination_first_payment <= m < tracking_start` | **contractual back-projection** from origination terms | `estimated` |
| `tracking_start <= m <= today` | actual confirmed balance (`history_rows`) | `confirmed` |
| `today < m <= payoff` | committed forward balance (`committed_forward`) | `projected` |
| `m > payoff` | `0.00` (loan gone) | n/a |

**Contractual back-projection producer.** New thin producer (reuses existing primitives, forks no
math): amortize the contractual schedule from origination and clip it to the pre-`tracking_start`
months.

- Inputs: the loan's `LoanParams` (`origination_date`, `original_principal`, `term_months`,
  `payment_day`) and its rate-change feed.
- IMPLEMENTED (commit 1) as
  `loan_resolution.contractual_schedule_from_origination(loan_params, rate_changes)`: seeds
  `loan_resolver.compute_payoff_scenarios` with a synthesized ORIGINATION anchor
  (`loan_loaders.synthesize_origination_anchor`, which forces the origination opening even for a
  tracking-start loan) and an empty payment feed, evaluated as of `origination_date`, and returns
  its `original_forward`. Chosen over a direct `project_forward` call so the schedule inherits the
  resolver's EXACT first-payment-date and remaining-term convention (`next_pay_date` one month after
  origination on `payment_day`, full `term_months` remaining) -- the back-projection and the
  resolved schedule land on the same monthly grid and cannot drift at the seam. Forks no
  amortization math. Pure (the caller supplies `rate_changes` via `loan_loaders.load_rate_changes`).
- Return the rows with `payment_date < tracking_start`.
  **No-op when `tracking_start` is already at or before origination's first payment** (a loan with
  only an origination anchor and no tracking- start true-up already has its full contractual span
  inside `state.schedule`), so back-projection fills a gap only for tracking-start / true-up loans
  -- exactly the real-mortgage case.

**The tracking-start seam is honest, not smoothed.** The back-projection ends at the contractual
balance the month before `tracking_start`; `state.schedule[0]` opens at the actual anchor balance.
They differ whenever the loan was not exactly on schedule before tracking began. That gap is the
truth ("what the contract implies" vs "what you recorded"), so the producer does NOT reconcile it
away. The data layer emits the `estimated` tier so Loop B can render the pre-tracking region
distinctly (see 8); it is never drawn as if it were recorded fact.

## 3. Value series (date-keyed flat / compound)

Replace `index <= current_index` with a **date** test. For each calendar month `m` on the merged
axis:

- `m <= today`: value is the flat anchor `market_value` (its past is unknown; today's anchor is the
  only honest present value -- unchanged design intent, corrected trigger).
- `m > today`: compound `market_value` from `today` to `m` via the one appreciation primitive
  `growth_engine.period_return_rate` (verified consistent with the app's period-tiling growth
  model).

This deletes the H3 degenerate-span path: no month `<= today` ever reaches the compounding branch,
so `period_return_rate` is never handed a non-positive span. **Additionally** harden the
appreciation call so a non-positive span returns factor `1` (no elapsed growth) rather than
borrowing `period_return_rate`'s 14-day inverted-period clamp -- that clamp is a pay-period
safeguard and must never fabricate appreciation in this context. Keep it as belt-and-suspenders even
though the date test makes it unreachable.

## 4. Fallback decision (future balance, not empty schedule)

A loan contributes to the debt line only if it has outstanding balance on or after `today`
(`state.current_balance > 0`, or equivalently its schedule has a `payment_date >= today` row with a
non-zero balance). When no secured loan is outstanding (none linked, none configured, or
**all paid off**), return the loan-less fallback (120-month forward appreciation arc, `no_loans`
state, `debt` and `equity` empty). This makes the paid-off case reach the fallback (closes H1).

## 5. Multi-loan merge (date-aligned)

Merge on a shared monthly calendar axis spanning
`min(origination_first_payment over loans) .. max(payoff over loans)`. For each month, each loan
contributes its region balance from 2 (0.00 before its origination and after its payoff). Sum per
calendar month into one debt line. The per-month debt **confidence tier** is the least-confident
contributing loan's tier (`estimated` < `confirmed` < `projected`... rendered so a month mixing an
estimated loan and a confirmed loan reads as estimated). The single-mortgage case reduces to one
series and three contiguous tiers.

## 6. Reconciliation guarantees (pin them with tests)

- **Chart vs hero at today.** The `today` column sums each loan's confirmed balance at today, which
  is `state.current_balance`, so `debt[today_index] == home_equity.total_debt` to the cent, and
  `equity[today_index] == home_equity.equity`. This is the promise the current producer's docstring
  makes but does not keep; make it a test (the reconciliation gap flagged in the review).
- **Value continuity.** `value[today_index] == market_value` exactly (flat region includes today).
- **Internal identity.** `equity[i] == value[i] - debt[i]` for every `i` (already holds; keep).

## 7. Purity and single resolution (folds D1)

Split I/O from math:

- **Route / thin orchestrator (I/O).** Resolve each secured loan **once** via
  `resolve_account_loan(loan.id, scenario_id, today)`; skip `None`. Build each loan's contractual
  back-projection prefix (2). Read `market_value` and `appreciation_rate` once. Use ONE `today`.
- **`home_equity_service`.** Feed the already-resolved `state.current_balance` list into the
  existing pure `compute_home_equity(market_value, balances)` -- no second resolution.
- **`build_property_equity_chart` becomes pure.** New signature (rows + scalars in, series out):
  takes, per loan, `(back_projection_rows, schedule_rows)` plus `market_value`, `appreciation_rate`,
  `today`; returns the `PropertyEquityChart`. No `resolve_account_loan` import, no DB. The three
  reproductions become fast pure unit tests.

Do NOT change `resolve_account_loan`'s return shape (traced consumers: net worth, year-end, debt
strategy, home equity, loan card, recurrence sync -- rule 7). The back-projection producer loads its
own light rate-change feed; only the heavy resolve is done once.

## 8. Shared axis primitive (generalize, keep the loan band bit-identical)

`app/utils/chart_series.build_chart_series` currently front-aligns at index 0 -- correct for the
loan band (its `original` / `committed` / `accelerated` series share one start month and cadence)
but wrong for cross-loan merges. Generalize it to **date-align on the union of month dates**:

- For same-start series (the loan band), the union of dates equals the longest series' dates and
  each shorter series pads at the tail exactly as today -> output is **bit-identical**. Gate: the 53
  loan-chart tests in `tests/test_routes/test_loan.py` must stay green unchanged.
- For different-start series (the property merge), date-alignment places each loan by calendar
  month.

If generalization proves to perturb the band, fall back to a property-local date-merge and leave
`build_chart_series` untouched (the band's front-align stays correct for its use). Prefer the
generalization (one primitive) but do not force it against a band regression.

## 9. Data contract (`PropertyEquityChart` -> route JSON -> `property_detail.js`)

- `labels: list[str]` (`%b %Y`), spanning `min origination .. max payoff` (or 120-month arc in the
  fallback).
- `value: list[float]`, `debt: list[float]`, `equity: list[float]` (route floats the `Decimal`
  series at the JSON boundary only; empty `debt`/`equity` in the fallback).
- `today_index: int` -- the flat/compound + solid/dashed boundary (the month `== today`, or the last
  month `< today`). Replaces `current_index`.
- `debt_tier: list[str]` -- per-month `"estimated" | "confirmed" | "projected"` for the debt line's
  three-way styling (empty in the fallback). Loop B renders estimated distinctly from confirmed
  (solid) and projected (dashed).
- `chart_state: "standard" | "zero_rate" | "no_loans"` (unchanged meaning).

## 10. Test plan

Convert the three reproductions in `property_detail_followups.md` into permanent regression tests
(they currently document the pre-fix trap; after the fix they assert the corrected behavior):

- **H1:** a property whose single secured loan is fully paid off -> `chart_state == "no_loans"`,
  `debt == []`, 120-month value arc.
- **H2:** a mortgage (old) + HELOC (young) -> every debt column equals the per-calendar-month sum (0
  before a loan's origination), and `debt[today_index] == hero.total_debt` to the cent.
- **H3:** a loan with past-dated projected months -> every `value[m <= today] == market_value`
  exactly; `today_index` is the last month `<= today`, never in the past.

Plus:

- **Contractual back-projection:** a loan with a tracking-start true-up after origination -> the
  pre-tracking debt rows equal an independent hand-computed contractual amortization from
  origination terms, tier `estimated`; the confirmed region tier `confirmed`; the seam discontinuity
  is present and equals `contractual_at(tracking_start - 1) - actual_anchor_balance` (assert the
  gap, do not hide it).
- **Reconciliation (new, closes the review gap):** `debt[today_index] == home_equity.total_debt` and
  `equity[today_index] == home_equity.equity` for a loan WITH confirmed history.
- **D1 single resolution:** a call-counting spy asserts exactly one `resolve_account_loan` per
  secured loan per `property_detail` GET, and one `date.today()`.
- Exact-`Decimal` assertions throughout (testing standard); loans dated relative to `today` so CI is
  date-robust, but now deliberately spanning past origination / tracking-start / paid-off, which the
  current suite never exercises.

Gates: `pylint app/` 10.00 with the full `--fail-on` set; the 53 loan-chart tests unchanged; full
suite green.

## 11. Sequencing (commits, model discipline)

Data first (Opus), visual second (Fable), each resumable from this plan.

1. **Contractual back-projection producer** + its unit tests (pure, reuses the resolver composer).
   DONE: `contractual_schedule_from_origination` + `synthesize_origination_anchor` +
   `tests/test_services/test_loan_resolution.py` (3 tests: exact 12-month amortization, 360-month
   span, tracking-start-independence). pylint 10.00; 329-test consumer regression green.
2 + 3. **Pure date-anchored producer + route resolve-once** (MERGED -- the producer's new pure
signature and its sole caller must change together, rule 7, to keep the tree green). DONE:
`build_property_equity_chart` rewritten pure/date-anchored (date-keyed value, future-debt fallback
owned by the producer, calendar-month merge, `today_index` + `debt_tier` contract, per-loan
`SecuredLoanSeries` input); `property_detail` resolves each loan ONCE and feeds both
`compute_home_equity` and the chart (one `today`); `_secured_loan_series` builds the clipped
back-projection. Tests (`tests/test_routes/test_property.py`, 16): fallback, H1
(zero-balance-with-nonempty-schedule -> fallback), H2 (date-aligned merge), H3 (past months flat),
value/equity identity, zero-rate, route context contract, resolve-once spy. Fixed a C7-2 dead
anchor-NULL fork the rewire briefly introduced. pylint 10.00; full route suite 2727 green. STILL
PENDING: the chart-vs-hero reconciliation test with real CONFIRMED history (needs settled loan
payments; the design guarantees it -- last confirmed row balance == `current_balance` -- and the
identity test already covers `equity == value - debt`) and the back-projection `estimated`-tier +
tracking-start-seam test (both need a settled-payment / tracking-start fixture).
2. **Shared primitive generalization** (8), gated by the unchanged loan-band suite. NOTE: the pure
   producer no longer imports `chart_series.build_chart_series` (it builds its own date-aligned
   merge), so this step is now DE-RISKED to "hoist the property merge as the shared date-aligned
   primitive and retire the loan band's front-align" rather than a mutation both depend on.
3. **Fable visual** (Loop B): render `debt_tier`'s estimated region distinctly; captions state the
   pre-tracking estimate honestly; both themes / viewports; live-verify.

## 12. Open sub-decisions (decide at the gate, do not block the plan)

- **Seam rendering (Loop B).** How the `estimated` pre-tracking region is styled (dotted vs a wash)
  and whether the seam discontinuity gets an annotated marker. Data contract is fixed (`debt_tier`);
  only the pixels are open.
- **Multi-loan mixed-tier month styling (Loop B).** How a month mixing `estimated` and `confirmed`
  loans reads. Default: least-confident tier wins.
- **Fallback caption for paid-off vs never-secured (Low).** The `no_loans` caption currently says
  "Nothing is secured by this property," which is inaccurate for a paid-off-but-still-linked loan;
  reword when H1 lands.
