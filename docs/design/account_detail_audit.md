# Account Detail Pages Audit (Fable 5 overhaul)

Per-surface diagnosis of the type-specific account detail pages, the fourth rebuild target of the
Fable 5 UI/UX overhaul (after grid, dashboard, accounts cockpit). Written 2026-07-02 from a code
trace of the routes, templates, and the cockpit's `detail_endpoint` routing; line references are to
the code as of `dev` at PR #52 (`2d81705`).

## Scope

The cockpit card's Details action routes to five destinations
(`app/templates/savings/_cockpit.html:34-41`). This audit covers the three that live under
`app/routes/accounts/detail.py` and are "largely tabular data":

| Route | Template | Serves |
| ----- | -------- | ------ |
| `/accounts/<id>/checking` | `accounts/checking_detail.html` | Checking |
| `/accounts/<id>/interest` | `accounts/interest_detail.html` | any `has_interest` type (HYSA, Money Market, CD, HSA) |
| `/accounts/<id>/property` | `accounts/property_detail.html` | any `has_appreciation` type (Property) |

Out of scope, confirmed at the gate below: `loan.dashboard` (amortizing types; developer excluded
loans from this pass) and `investment.dashboard` (retirement / investment types; its own screen in
the overhaul's provisional order, and it is chart-bearing, not tabular).

**Coverage gap (in scope as a decision):** Savings, Credit Card, and any plain custom type have NO
detail page at all -- `detail_endpoint` falls through to an empty branch, so their cockpit cards
render no Details link (this same gap already forced hard-delete into the edit form's danger zone;
`accounts_audit.md` decision 12).

## The screens' jobs

- **Checking / interest detail:** "Where is this account's balance heading, period by period, from
  its anchored reality?" -- plus, for interest-bearing types, "what is it earning?"
- **Property detail:** "What is this property worth, and how much of it do I own once its mortgage
  is netted out?"

Data is already sound: balances flow through the balance-at seam (`balance_at.cash_balance_map` /
`balance_map` + `net_worth_kernel.interest_by_period_for_account`), the anchor resolves via the
dated `AccountAnchorHistory` source of truth, and equity comes from `home_equity_service` (the same
producer as the cockpit's debt figures). The F-6 static guard pins the seam usage. This is a
presentation rebuild; the two data-adjacent findings are #2 (anchor caption) and the route additions
if the gate approves them.

## Surface 1: Shared page chrome (all three pages)

- **Should show:** where you are, one way back, the account's identity in the cockpit's visual
  vocabulary.
- **Actually does:** a breadcrumb ("Accounts Dashboard / name") AND a "Back to Accounts" button on
  the same header row, both to `savings.dashboard` (`checking_detail.html:5-20`, same in the other
  two). Header icons diverge from the cockpit's `acct_glyph` vocabulary: interest pages use
  `bi-bank` (cockpit uses `bi-piggy-bank` for interest types), property uses `bi-houses` (cockpit:
  `bi-house-door`). The `setup=1` post-create alert (interest, property) is useful onboarding.
- **Divergence:** duplicated navigation (two CTAs to the same place); icon vocabulary drift against
  the cockpit cards the user just clicked.
- **Verdict: fix.** One navigation affordance; reuse the cockpit glyph mapping; keep the setup
  alert.

## Surface 2: Account Summary card (checking + interest)

- **Should show:** the account's headline balance as the hero, its anchor provenance, and where the
  balance is heading at a glance.
- **Actually does:** a half-width card (`col-md-6`; on checking the right half of the row is simply
  empty) of small label/value rows: Type, Anchor Date (checking) or APY + Compounding (interest),
  then Current Balance as an `fs-5` row styled `text-accent`, then a "Projected:" list of 3 / 6 /
  12-month figures in `small` (`checking_detail.html:22-63`, `interest_detail.html:29-71`).
- **Divergence (confirmed):**
  1. **The number is not the hero** (principle 1). The balance is one row among rows, at `fs-5`,
     while rebuilt screens give the headline figure hero scale (cockpit `nw-hero__num`).
  2. **"Anchor Date" caption disagrees with its figure** (principle 2). It renders
     `anchor_period.start_date` -- the anchor PERIOD's start -- not the anchor event's date. The
     resolver already returns the real thing (`AnchorPoint.as_of_date`,
     `balance_resolver.py:104-119`), and the cockpit's "as of" display uses it. A user who trued up
     mid-period reads the wrong date here.
  3. **Accent used as money color.** `text-accent` on the balance breaks the Steel Ink rule that the
     accent is the only non-money chroma (controls / brand), never the money figure itself; the
     cockpit hero is plain `font-mono` with state color only when negative.
- **Verdict: fix.** Balance becomes the hero with an honest "anchored <as_of_date>" caption;
  horizons become scannable chips (the cockpit `pulse-chip` pattern); type/APY/compounding become
  quiet metadata.

## Surface 3: Parameters forms (interest APY + compounding; property appreciation rate)

- **Should show:** set-and-forget configuration, editable but not competing with the numbers.
- **Actually does:** a full half-width card ("Interest Parameters" / "Appreciation") with a percent
  input, a compounding `<select>` (rendered by ref id -- correct), and a submit that full-page
  redirects with a flash (`interest_detail.html:73-106`, `property_detail.html:59-87`). CSRF
  present, POST used, schema-validated percent-domain conversion handled in the route/schema.
- **Divergence:** prominence only -- configuration occupies the same visual rank as the money it
  parameterizes. Mechanics are sound and simple (a full reload is fine for a rare edit).
- **Verdict: keep + demote.** Same fields and POST endpoints, restyled as a quiet, collapsed or
  side-rail config surface. No service changes.

## Surface 4: Balance Projection table (checking + interest)

- **Should show:** the trajectory (the trend IS the answer) plus exact per-period figures (tabular
  money).
- **Actually does:** a flat `table-sm` of every period in the rolling window (dozens of rows, about
  two years of biweekly periods), columns Period / Balance (+ Interest Earned on the interest page),
  no chart, no "today" marker, no grouping, rendered oldest-first so the current period is buried
  mid-list (`checking_detail.html:65-94`, `interest_detail.html:109-146`).
- **Divergence (confirmed):**
  1. **No trend presentation.** The design language routes "a trend IS the answer" screens through a
     chart first (the dashboard's 13-period end-balance chart is canonical); here the user
     scroll-scans dozens of rows to see a shape.
  2. **No current-period anchor point.** Nothing marks "you are here"; the first rows are past
     periods.
  3. **Raw Bootstrap theming.** `thead class="table-light"` hardcodes a light header band that
     ignores `data-bs-theme` (nothing in the token layer remaps it -- verify visually in Loop B),
     and the interest column uses `text-success` instead of the token-mapped state treatment
     (`--shekel-done`).
  4. Interest "+$x.xx" pairs sign and color correctly (color not the only signal) -- keep that.
- **Verdict: fix.** Chart-first trend with the full tabular list preserved beneath it (principle 3:
  no figure the user relies on is dropped), current-period marker, token-based table styling shared
  with the grid vocabulary.

## Surface 5: Property Summary + Home Equity (property page)

- **Should show:** market value, appreciation assumption, and the equity story (value minus secured
  debt, LTV), with working guidance for updating the value.
- **Actually does:** summary card (Type / Appreciation Rate / Market Value at `fs-5 text-accent`),
  the appreciation form, then a full-width Home Equity card: Market Value, Secured Debt, Equity
  (green/red via `text-success` / `text-danger`), LTV, and a "Secured by" loan list linking to
  `loan.dashboard` with a good empty state (`property_detail.html:31-146`).
- **Divergence (confirmed):**
  1. **Stale copy pointing at a retired screen.** "Update it from the accounts list to true up the
     home's value" (`property_detail.html:51-54`) -- the `/accounts` list was retired in cockpit P4
     and now redirects; the live edit surface is the cockpit card's click-to-edit balance. Breaks
     principle 4 (every call to action goes somewhere useful).
  2. Market Value repeats in both cards; the summary card's copy of it is the redundant one.
  3. Same raw-Bootstrap state colors and accent-as-money issues as above.
  4. Equity is the page's real headline but renders at the same `fs-5` rank as its supporting
     figures.
- **Verdict: fix.** Equity becomes the hero with market value and secured debt as its supporting
  pair (a figure minus a figure), LTV as a chip, corrected copy (or an in-place click-to-edit for
  market value, gate call), tokens throughout, keep the "Secured by" list and empty state.

## Surface 6: The missing detail page (Savings, Credit Card, plain custom types)

- **Should show:** the same "where is this balance heading" answer every other cash account gets.
- **Actually does:** nothing -- no route, no link on the cockpit card. The type's balance still
  projects (the cockpit sparkline and `savings_dashboard_service` already compute it via the same
  producers), so the data exists; only the page is missing.
- **Divergence:** an entire account class has no drill-down, which is why the cockpit card's primary
  click affordance silently does nothing for them.
- **Verdict: add (gate decision).** The checking page IS the plain-cash detail page in all but its
  type guard; generalizing it covers these types with no new financial logic. Route work is
  Opus-scope per the model discipline.

## Cross-cutting notes

- `checking_detail.html` and `interest_detail.html` are about 80 percent the same template; the
  interest page adds APY/compounding rows, the params form, and one table column. DRY argues for one
  shared cash-detail template (and possibly one route) with the interest surfaces conditional on
  `has_interest`.
- The money macro is used throughout (good; negative formatting standardized). No inline styles or
  scripts. No template money math. Display-only `.name` usage only. CSRF everywhere.
- Empty states: with no scenario or no periods the pages render the summary card with "--" and no
  table -- acceptable, but the rebuild should say why ("no pay periods yet") rather than showing a
  bare card.
- Nothing here mutates balances; the cockpit remains the true-up surface (unless the gate moves
  click-to-edit onto the detail page too).

## Rebuild decisions (Gate A)

Decided 2026-07-02 (developer ruling). Locked.

1. **One cash-detail template.** `checking_detail.html` and `interest_detail.html` merge into a
   single cash-account detail page; the APY/compounding metadata, the params form, and the interest
   table column render only for `has_interest` types. One route family serves it.
2. **Coverage extends to the missing types.** Savings, Credit Card, and plain custom types get the
   same cash detail page: the checking route's type guard generalizes to all plain cash types and
   the cockpit `detail_endpoint` macro gains their branch. No new financial logic (their projections
   already flow from the same producers); the route/guard edit is an Opus-scope commit per the model
   discipline.
3. **Chart-first, table preserved.** A trend chart with a today marker and 3/6/12-month horizon
   chips answers the trajectory question; the complete per-period table remains below it, so no
   exact figure is dropped (principles 3 and 5). **AMENDED at the Loop A round-1 pick (2026-07-02,
   developer ruling): the per-period table is DROPPED.** The developer's lived workflow: per-period
   detail tracking happens on the GRID, and only for checking (the volatile account); the detail
   pages' job is trend, health, and easy parameter management. Exact per-period values are re-homed
   to the trend chart's hover tooltip (the app's standard chart behavior), so the figures remain
   reachable -- removal of the table is the explicit product decision principle 3 requires, not a
   silent drop. Health signals (e.g. projected interest over the next year, computed in the route
   with Decimal) join the chip row.
4. **Investment/retirement dashboard stays out.** It remains its own later rebuild target per the
   overhaul's provisional order.
5. **Property page** rebuilds per the Surface 5 verdict (equity hero, supporting value/debt pair,
   LTV chip, corrected copy, tokens). Whether market value gets in-place click-to-edit here or the
   copy simply points at the cockpit's editor is a Loop A presentation call.
6. **Layout direction: A "Cockpit band"** (Loop A round-1 pick, 2026-07-02): hero + horizon chips
   - trend chart in one banded canvas, continuing the dashboard/cockpit hero-band grammar.
   Round 2 explores where the promoted parameters surface lives (resident card below the band vs inline
   in the band) and gives the checking variant an "Open grid" call to action, since the grid is
   checking's detail home.
7. **Parameters live in a card BELOW the band** (Loop A round-2 pick, 2026-07-02): the in-band
   variant was rejected as too crowded. Final anatomy, whole family: band = hero + honest anchored
   caption, horizon chips (with the done-tinted "Interest, next 12 mo" chip for `has_interest`
   types), and the trend chart with its TODAY marker; below the band, a Parameters card (APY and
   compounding, or the property appreciation rate). The plain-cash variant (Checking / Savings /
   Credit Card) has no parameters card and carries an "Open grid" action in the band instead; the
   property page keeps its Secured-by card below alongside Parameters. Loop A CLOSED; the scratch
   mockups (`cash_detail_r2.html`, `property_detail_explore.html`) stay in /tmp per the
   anti-anchoring rule and are deleted when Loop B completes.

## Loop B as-built (2026-07-03)

Built on `feat/account-detail-rebuild` (worktree off `dev` at PR #52). All seven rebuild decisions
shipped as specified; deltas and additions worth recording:

- **Route shape:** one `GET /accounts/<id>/details` (`accounts.cash_detail`) serving every cash
  kind; the old `/checking` and `/interest` URLs remain as ownership-checked redirect stubs (an IDOR
  probe of a not-yours stub URL 404s BEFORE redirecting -- the full suite caught the stub initially
  answering 302, fixed at the root in `_redirect_to_cash_detail`).
- **Per-kind producer paths preserved verbatim** (interest: `balance_at.balance_map` + kernel
  interest accessor; plain: `balance_at.cash_balance_map`); the F-6 static guard was re-pointed and
  pins both seam entries on the merged route.
- **Anchor caption fixed** (audit finding #2): the hero caption renders `AnchorPoint.as_of_date` --
  the true-up event date -- never the anchor period's start date.
- **Horizon chips carry Decimal deltas** computed in the route; the interest health chip sums
  `interest_by_period` over periods `[current+1, current+26]`.
- **Chart:** `data-chart` JSON (labels / balance floats / current_index) mirroring the cockpit
  serializer's conventions; solid-history vs dashed-projection and the Today marker come from shared
  `ShekelChart` helpers extracted from `net_worth_cockpit.js` into `chart_theme.js` (`hexToRgba` /
  `splitSegment` / `todayMarkerPlugin`), so the two trend charts cannot drift. Long series (> 26
  periods) drop resting point dots and thin the x labels to ~13 (`maxTicksLimit`); below-zero points
  stay visible as danger-colored warning dots.
- **Cockpit macros extracted** to `_acct_macros.html` (glyph + detail endpoint shared across the
  cockpit and detail pages); every cash card now has a Details destination.
- **Live-verified 2026-07-03** on prod-clone dev data (worktree app on :5001, both themes, desktop
  and mobile):
  - Money Market: hero, chips, chart, and the $279 projected-interest chip (3.29% APY) reconcile.
  - Checking: the volatile sawtooth, a NEGATIVE 3-month horizon chip (danger-colored, signed), and
    below-zero danger dots.
  - Property: $350,000.00 minus $177,554.69 = $172,445.31 equity to the cent, LTV 50.7%.
- **Deferred / follow-ups:** in-place market-value editing on the property page (copy points at the
  cockpit's click-to-edit balance instead; small follow-up reusing the anchor editor if wanted). The
  `setup=1` post-create banner and all params POST endpoints unchanged.

## Property equity chart (2026-07-06)

Enrichment of the Surface 5 property page: its band is the only one in the detail family with no
chart (the slot holds helper copy). The developer wants the equity story over time -- market value
appreciating, secured debt amortizing, equity as the difference. New surface work, so it re-enters
the protocol here: this audit section, then Loop A mockups, then Loop B.

### The surface's job

"How much of this property do I own, and where is that share heading?" The answer is an arc, not a
window: the value line climbs at the appreciation rate, the debt line falls to zero at payoff, and
equity -- the gap -- widens from both edges until it is the whole property. The chart must make that
arc legible; the hero and chips already carry the as-of-today snapshot.

### First attempt, rejected (2026-07-06)

A pay-period-window chart (the cash-detail axis reused verbatim) was built out of protocol and
rejected on a live drive. Findings, which the redesign must answer:

1. **Flat lines.** A ~2-year biweekly window barely moves a mortgage balance, and a property whose
   appreciation rate is still the zero sentinel has a dead-flat value line by construction. Two
   horizontal lines answer nothing. The equity story lives on the decades scale.
2. **Ambiguous x labels.** The family's `%b %-d` (month + day, no year) label format is unreadable
   on any multi-year axis; the reader cannot tell what span they are looking at.
3. **Dead space.** A zero-based y-axis with a top-anchored value line and a mid-chart debt line left
   the bottom third of the plot as empty ink.

### Gate A rulings (developer, 2026-07-06 -- LOCKED)

1. **Horizon: to payoff.** Monthly axis ending at the last secured loan's payoff -- the loan detail
   band's own axis grammar. (Fallback horizon for a paid-off or loan-less property is an open Loop A
   item below.)
2. **Include loan history.** Confirmed history renders solid, the forward projection dashed, with
   the Today marker at the boundary (the loan band's splice). The value line flat-carries backward
   from today's anchor -- its past is unknown -- and that flat carry must be captioned honestly.
3. **Fill style and the remaining visual forks are NOT ruled.** They are Loop A material, judged on
   rendered mockups, not descriptions.

### Open Loop A forks

- **Fill style:** stacked shares (debt region tinted from zero as the bank's share, equity band
  tinted between debt and value as the owner's share) vs equity-fill-only (only the gap tinted) vs a
  single equity line. The dead-space finding argues for the stacked reading; the mockups decide.
- **Value-history caption:** how the flat-carried value history announces itself.
- **Label cadence:** `%b %Y` labels thinned to a readable count (the loan chart's convention); exact
  tick density on a decades axis.
- **Legend / tooltip composition:** exact figures live in the hover tooltip per the family rule
  (decision 3 amendment); what the legend and tooltip rows/footer carry.
- **The zero-rate state:** how a flat value line (appreciation rate still the 0% sentinel) is
  captioned and pointed at the Parameters card so it reads as "unset," not "broken."
- **Paid-off / loan-less fallback horizon:** proposed 10 years of appreciation-only projection;
  confirm or adjust at the Loop A lock.

### Loop B sketch (finalized at the Loop A lock)

Data (Opus scope): per secured loan, the monthly confirmed-history + committed-forward rows the loan
band already charts (`build_baseline_scenarios` / `compute_payoff_scenarios` with
`confirmed_loan_view`); the multi-loan merge and post-payoff zero padding follow the
`_build_chart_series` pattern in `app/routes/loan/_helpers.py` (hoist to a shared home, do not
duplicate). The value line compounds today's anchor at each month via the growth engine's
`(1 + rate) ** (days / 365)` primitive (`growth_engine.period_return_rate`) so there is exactly one
appreciation formula in the codebase; equity is the per-month `Decimal` difference. Visual (Fable
scope): the band canvas + a `property_detail.js` renderer on the shared `chart_theme.js` helpers, to
whatever direction Loop A locks.
