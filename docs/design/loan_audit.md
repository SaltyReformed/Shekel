# Loan Detail Audit

Per-surface diagnosis of the loan detail page (`/accounts/<id>/loan`) for the Fable 5 overhaul, per
the shekel-design skill Step 1. Status: diagnosis complete 2026-07-04. **Gate A LOCKED 2026-07-04**
(rulings in "Rebuild decisions"). **Loop A COMPLETE 2026-07-04** (two rounds, record below). Next:
Loop B (build plan below). Line references are as of `dev` @ `ced1feca` (2026-07-04); re-verify
before acting on them.

## The screen's job (proposed)

For one loan: where do I stand (balance, rate, payoff trajectory), what is each payment actually
doing (principal / interest / escrow), what would change the trajectory (extra payment, refinance),
and manage the loan's parameters. The cockpit answers "how are my debts overall"; this page answers
"this loan, in depth."

## Inventory

| Concern | Files | Lines |
| ------- | ----- | ----- |
| Routes | `app/routes/loan/` package (dashboard, params, escrow_rates, calculators, payment_transfer, helpers) | 2360 |
| Templates | `app/templates/loan/` (dashboard.html 456, setup.html 105, 7 partials) | 1229 |
| Producers | `loan_resolver` package (state/payoff/periods), `loan_payment_service` (697), `loan_loaders` (486), `escrow_calculator`, `amortization_engine` | - |
| JS | `payoff_chart.js`, `chart_slider.js` (+ chart_theme vendor stack) | - |
| Tests | `tests/test_routes/test_loan.py` | 5641 (24 classes, 184 methods) |

The page is functionally RICH: six nav pills (Overview, Escrow, Rate History for ARM, Amortization
Schedule, Payoff Calculator, Refinance Calculator), a three-series balance chart, payment-allocation
stacked bar, escrow CRUD with temporal close, rate history, payoff and refinance calculators,
payment-transfer creation (`derive_from_loan`), collateral linking, and the dated true-up form.
Current principal is already LEDGER-AUTHORITATIVE via the resolver (`current_principal_display` =
`LoanState.current_balance`). No Jinja money arithmetic anywhere (schedule totals and percentages
precomputed server-side per MED-04/E-16).

## What the code actually produces vs what it should

**Styling is pre-overhaul.** Zero Steel Ink / pulse / band tokens (grep count 0); 31 bare Bootstrap
card/pill usages. The rebuilt `accounts/cash_detail.html` band grammar (pulse-canvas / nw-hero /
stat chips / acctd-*) is the committed vocabulary for account detail pages; the loan page predates
it entirely.

**The ledger's best facts are computed but never shown:**

1. **Actual interest paid this year** - `confirmed_loan_interest_in_year`
   (`loan_posting_service/_reader.py:318`) returns real, paid-date-attributed interest from the
   genesis ledger (the Schedule A figure; its only consumer is the year-end service). The page
   instead shows only life-of-loan PROJECTED total interest. For the developer's tax story (the
   analytics Taxes tab consumes exactly this figure) the number exists and is invisible here.
2. **Payment history** - `get_payment_history` (`loan_payment_service.py:184`) returns confirmed
   `PaymentRecord`s (actual cash, dates); consumed only by tests. The user cannot see the list of
   real payments anywhere on the page.
3. **True-up / anchor event history** - the Record Loan Balance form WRITES append-only
   `user_trueup` events, but there is no read surface: no event history, and no "anchored as of"
   caption on the balance (the cash detail page shows one; principle "a figure and its caption never
   disagree").
4. **Escrow past** - components are temporally modeled (effective_date / end_date) but only
   currently-active rows render; closed components vanish without history.

**Architectural smell carried by the page:** `GET /loan` commits a write
(`_update_transfer_end_date`, documented Risk R-4) syncing the payment transfer's recurrence
end_date to the projected payoff. A rebuild should move this off the GET path.

**IA smell:** six pills on a detail page is the old analytics shape. The calculators are what-if
LEVERS in cockpit terms (the retirement page's two close-the-gap levers are the precedent); Escrow
and Rate History are parameter/history cards; the Schedule is a statement table (the table IS the
artifact - the display ruling's exception applies).

## Navigation facts

Cockpit loan card links here (`detail_endpoint` maps amortizing accounts to `loan.dashboard`) and
already shows rate, click-to-edit balance, sparkline, monthly payment + payoff caption; the cockpit
also owns the Debt Summary block (total debt, weighted rate, DTI, debt-free date) and Home Equity
cards. Property detail links securing loans back here. Account creation redirects amortizing
accounts to `setup=1`.

## Investment detail (context, out of scope)

`investment.dashboard` exists (317-line route, 326-line template: summary, YTD limit bar, growth
chart with what-if slider, params form) and is likewise pre-overhaul Bootstrap. It remains its own
deferred target per the account-detail audit; nothing here changes that.

## Proposed shape (for Gate A)

Rebuild in the account-detail band grammar, cockpit rules:

- **Band:** balance hero (ledger-authoritative) with an honest "anchored / confirmed through"
  caption; chips: rate, monthly P&I, total with escrow, projected payoff, interest paid this year
  (measured); the existing three-series payoff curve as the band chart.
- **Levers:** extra-payment and refinance become inline what-if levers under the chart (retirement
  precedent), replacing two calculator tabs.
- **Sections:** payment allocation bar; payment history (measured, new); amortization schedule (kept
  as the statement table, Confirmed/Projected badges stay); escrow card (active + history); rate
  history card (ARM); parameters + Record Balance + anchor-event history consolidated into one
  settings card; collateral card.
- **Mechanical fixes ride along:** R-4 write-on-GET moved to the mutation path; Steel Ink tokens
  throughout; single responsive markup.

## Gate A questions for the developer

1. **IA:** collapse the six pills into the band + sections + levers shape above? Does the
   Amortization Schedule stay a full table (recommended - it is the artifact)?
2. **Measured facts:** surface actual interest paid this year (chip) and the confirmed payment
   history (section)? Recommended - it is the payoff of the whole ledger arc, and the interest
   figure is the same one the future Taxes tab reports.
3. **Anchor honesty:** add the "anchored / confirmed through" caption and a small true-up history?
   Recommended.
4. **Escrow history:** show closed components (timeline) or keep active-only with history behind a
   toggle?
5. **R-4:** move the transfer end-date sync off the GET path during Loop B (Opus scope)?
6. **Your lived workflow:** what do you actually check when you open the mortgage page today, and
   what do you wish it answered that it does not?

## Rebuild decisions (Gate A, 2026-07-04)

1. **IA: LOCKED.** Six pills collapse into band + levers + sections. The amortization schedule is
   DEMOTED - "an extra click or two to reach it" is acceptable; developer does not need the full
   table often.
2. **Measured facts: LOCKED.** Interest-paid-this-year chip (the Schedule A figure) and a confirmed
   payment-history section both surface.
3. **Anchor honesty: caption LOCKED** ("anchored / confirmed through" on the balance hero).
   **True-up history PENDING** - developer wants to SEE it rendered before deciding; the round-1
   mock must include it so the ruling can happen at the mock gate.
4. **Escrow: LOCKED** - active components only, closed-component history hidden behind a toggle.
5. **R-4 write-on-GET: LOCKED** - moved to the mutation path in Loop B (Opus scope).
6. **Lived workflow (recorded):** the developer checks the BALANCE-OVER-TIME chart and the PAYOFF
   CALCULATOR routinely; the amortization schedule was only ever a trust-verification tool ("I
   didn't trust the calculations") and the posting ledger has superseded that job. Design
   consequence: the chart is the band's centerpiece, the extra-payment lever gets top placement, and
   the schedule demotes without loss.

## Loop A record

- **Round 1 (2026-07-04): direction A "Levers below" LOCKED** (full-width band chart, two what-if
  levers beneath, sections in a two-column grid; B "lever rail" rejected). Developer caught a
  capability regression in the mock: the existing payoff calculator has TWO modes (extra payment AND
  payoff-by-date goal) and the lever only mocked one - round 2 adds a mode toggle to the
  extra-principal lever (extra amount -> payoff date, or target date -> required extra).
  **Balance anchors card (true-up history + drift column): ruling still PENDING.**
- **Round 2 (2026-07-04): LOCKED as presented** ("I like A as presented in this last iteration
  including the balance anchors card"). The payoff lever carries an Extra payment / Payoff-by-date
  mode toggle (both directions of the question: extra -> new payoff date and interest saved; target
  date -> required extra and interest saved). The
  **Balance anchors card is KEPT, WITH the drift column** (recorded figure vs the ledger's computed
  balance on that date) - it is the running scorecard of ledger-vs-reality that replaces the
  developer's old use of the amortization schedule as a trust check.

## Locked anatomy (Loop A complete, 2026-07-04)

- **Band:** current-principal hero + caption "ledger-confirmed / last payment / anchored"; chips:
  rate, P&I, total with escrow, payoff date, interest paid YTD (measured), principal paid YTD
  (measured).
- **Chart:** balance over time, full width - solid ledger-confirmed history, dashed committed
  schedule to payoff, green dashed extra-payment lever preview, Today marker, payoff labels.
- **Levers (below the chart):** "Pay off sooner" with Extra payment / Payoff by date modes;
  "Refinance" with rate/term/closing inputs and honest verdict chips (including negative verdicts:
  payoff extension, lifetime cost).
- **Sections:** this month's allocation bar (principal/interest/escrow); confirmed payment history
  table (cash + per-payment split, CONFIRMED badges, view-all link); balance anchors (true-up events
  - origination + drift column); escrow active components with closed history behind a toggle;
  Secured By (market value, equity, LTV); loan parameters card with Edit parameters / Record balance
  actions.
- **Amortization schedule:** demoted to a footer link (own page/fragment), Confirmed/Projected
  badges preserved.

## Loop B build plan

- **P1 -- data (Opus scope):** producers for the measured chips (reuse
  `confirmed_loan_interest_in_year`, add its principal sibling), payment-history surface
  (`get_payment_history` gets its first UI consumer), balance-anchors producer (anchor events joined
  with the ledger's computed balance at each event date for the drift column); move the R-4 transfer
  end-date sync off the GET path; schedule relocated to its own route/fragment; lever endpoints
  reuse the existing calculator services. Targeted tests then full suite (the 5641-line route suite
  will need re-pointing where pills/tabs die).
- **P2 -- page (Fable scope):** rebuild `loan/dashboard.html` in the band grammar per the locked
  anatomy, `loan.css` additions, chart via the chart_theme factory, both themes, shoot.py
  verification.
- **P3 -- acceptance:** developer drive on the real mortgage; as-built record here.

## Loop B P2 as-built (2026-07-05)

P2 (the band-grammar visual rebuild) is BUILT and green on `dev`, UNCOMMITTED as of 2026-07-05
(built by Opus, not a Fable session, per the developer's call). As-built matches the locked anatomy:

- `app/templates/loan/dashboard.html` rebuilt into the band grammar; new
  `app/templates/loan/_chips.html` macros (the band chips and their HTMX out-of-band swaps share one
  definition so they cannot drift); new `app/static/css/loan.css` (tokens only, loaded after
  `accounts.css`); new `app/static/js/loan_detail.js` (band chart via `ShekelChart.splitSegment` /
  `todayMarkerPlugin`, plus the pay-off-sooner lever's green dashed accelerated overlay on
  `htmx:afterSwap`).
- `app/routes/loan/_helpers.py`: `build_band_chart(scenarios, has_payments)` and
  `accelerated_overlay(scenarios)` (both reuse `_build_chart_series`; the overlay is forward-only
  with leading nulls over confirmed history, padded to the same contractual x-axis so it cannot
  drift from the committed line). `dashboard.py` context reshaped: measured chips plus the
  payment-history and balance-anchor sections, a single band `chart_json`, the dead FLOOR scenario
  and the schedule-tab context removed. `calculators._payoff_extra_payment_result` returns the
  overlay instead of a canvas.
- `_refinance.html` inlined into the dashboard and deleted; `payoff_chart.js` deleted.

Gates: full suite 7206 passed; `pylint app/` 10.00/10; independent `code-reviewer` clean (no
Critical/High, band/overlay serialization confirmed financially correct); both themes shot via
`shoot.py` on the real prod-clone Mortgage (account id 3).

### Deferred follow-ups (from the P2 adversarial review)

All three PRE-DATE the rebuild (not P2 regressions); recorded here so a future session can pick them
up. Re-verify symbol / line references before acting (CLAUDE.md rule 2).

1. **Band chart goes stale after an ARM rate-change OOB swap.** A rate change re-amortizes the loan,
   but `app/routes/loan/escrow_rates.py:add_rate_change` (via `_render_rate_history`) OOB-swaps only
   the rate chip (`#interest-rate-chip`); nothing re-renders `#loan-balance-chart`, so the band line
   reflects the pre-change rate until a full reload. The band is now always visible (not a hidden
   tab), so this is more noticeable than before; the non-HTMX `update_params` / `true_up_balance`
   forms reload the page and are fine. FIX DIRECTION: have the rate route additionally return fresh
   band `chart_json` and have `loan_detail.js` rebuild `#loan-balance-chart` from it on that swap.

2. **Chart series can exceed the labelled x-axis for a sub-PITI (underpayment) loan.**
   `app/routes/loan/_helpers.py:_build_chart_series` takes its labels from the FIRST series
   (`"original"`, the contractual baseline), and `_balances_for_chart` PADS shorter series up to
   that length but never TRUNCATES longer ones. A recurring payment BELOW the contractual P&I makes
   `committed_forward` (and the lever's `accelerated_forward`) longer than `original_forward`, so
   the band balance / overlay exceed `len(labels)` and the tail points plot past the last labelled
   tick. The values are correct; only the x-axis labelling falls short. Pre-existing (the old
   three-series chart used the same `"original"`-as-baseline selection). FIX DIRECTION: choose the
   LONGEST series as the label baseline, NOT a `[:target_len]` truncation band-aid (which would hide
   the real underpayment tail). Ties to the P1 note that a deliberately sub-PITI custom recurring
   payment gets a shorter initial shadow horizon.

3. **YTD chips use a UTC-derived year against display-tz attribution (New Year boundary).**
   `app/routes/loan/dashboard.py:_build_measured_context` passes `date.today().year` (backend UTC)
   into `confirmed_loan_interest_in_year` / `confirmed_loan_principal_in_year`, which attribute each
   payment by its America/New_York paid date. In the few hours around New Year when UTC and Eastern
   differ in year, the "Interest / Principal paid, YTD" figure selects the wrong civil year relative
   to the user's clock. KEPT DELIBERATELY at P2: `date.today().year` is the app-wide "current year"
   convention (tax config, retirement, salary), and using it keeps the interest chip consistent with
   the analytics Taxes tab (the same Schedule-A figure). Only worth revisiting as part of a
   cross-cutting "display-tz current year" decision, not in isolation (which would make this surface
   disagree with the Taxes tab).
