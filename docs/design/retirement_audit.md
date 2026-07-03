# Retirement Page Audit

Static + live diagnosis of the retirement planning page (`/retirement`) ahead of its Fable 5
rebuild. This is the Step 1 artifact of the overhaul plan's per-screen process: per surface, what it
should show, what the code actually produces, the divergence if any, and a keep / fix / remove
verdict that feeds the developer's Gate A ruling. No code was changed.

Last evaluated: 2026-07-02.

## Method and scope

- Read in full: `app/routes/retirement.py`, `app/services/retirement_dashboard_service.py`,
  `app/services/retirement_gap_calculator.py`, `app/services/pension_calculator.py`, the growth
  conventions in `app/services/growth_engine.py`, all five templates under
  `app/templates/retirement/`, `app/templates/settings/_retirement.html`,
  `app/static/js/retirement_gap_chart.js`, and `app/static/js/chart_slider.js`.
- **Live figures were measured, not inferred**: `compute_gap_data(1)` and `compute_slider_defaults`
  were run inside the dev container against the dev database (a prod clone) on 2026-07-02, and the
  growth-engine reproduction in finding D1 was executed directly. Findings marked "confirmed" follow
  from code plus these measurements.
- The developer's framing for this screen (recorded 2026-07-02): the page "has a lot of data on it
  but it isn't easy to follow and it doesn't tell me how I'm doing or what to do. It has two sliders
  that aren't all that helpful at least on their own."

## The screen's job

The page must answer one question:
**"Am I on track to retire when I plan to -- and if not, what should I change?"**

One-question breakdown the rebuild must answer:

1. **How am I doing?** One verdict figure (funded ratio / surplus-or-shortfall) as the hero.
2. **What will retirement look like?** Income needed vs income covered (pension + savings
   withdrawals), stated in consistent dollars and a consistent tax basis.
3. **What can I do about it?** The levers, costed in the app's native unit -- the pay period:
   contribute $X more per paycheck, retire N periods later, adjust assumptions.
4. **What is this built on?** The assumptions (raise extrapolation, return rates, SWR, employer
   match, tax basis), each visible and each linked to where it is set.

Today the page answers only question 2, partially and across mixed conventions; 1 is buried in the
last row of a nine-row table, 3 and 4 are absent.

## Source of truth

`retirement.dashboard` (route) calls `retirement_dashboard_service.compute_gap_data(user_id)` and
`compute_slider_defaults(data)`, then renders `retirement/dashboard.html`. The two sliders drive one
HTMX endpoint, `retirement.gap_analysis`, which re-renders `_gap_analysis.html` (table + chart) and
OOB-swaps the accounts table with slider overrides applied. Pension CRUD lives on
`retirement/pension_form.html`; the SWR / planned-retirement-date / estimated-tax-rate settings live
on a different page entirely (Settings > Retirement, posting to `retirement.update_settings`).

## Live data snapshot (2026-07-02, prod-clone data)

Inputs:

- Salary $91,675; raises 3.0% every July + 2.5% every January, both recurring.
- Pension "State Pension": 1.85%/yr, 4 high years, hired 2016-05-31, planned retirement 2046-06-01.
- SWR 4%; estimated retirement tax rate **unset**.
- Three accounts, all at 10.5% assumed return: Roth IRA $27,332.33, Traditional IRA $11,675.48,
  401(k) $31,070.06 (employer flat 5%).

| Figure (as rendered) | Value |
| -------------------- | ----- |
| Projected Pre-Retirement Income (monthly) | $16,171.31 |
| Projected Monthly Pension (gross) | $11,936.54 |
| Monthly Income Gap | $4,234.77 |
| Required Savings (4.0% rule) | $1,270,431.00 |
| Projected Retirement Savings | $718,266.71 |
| **Shortfall (the page's verdict)** | **-$552,164.29** (red) |
| Slider defaults | SWR 4.00%, return 10.50% |
| Chart bars: pension / investment income / "Gap" | $11,936.54 / $2,394.22 / $1,840.55 |
| Per-account projected: Roth / Trad IRA / 401(k) | $173,688.32 / $74,193.85 / $470,384.54 |

Decomposition of the
$16,171.31 headline (all verified arithmetic): the two recurring raises compound to a $258,087.44
high-4-year salary average by 2046; final-year gross biweekly is
$9,926.44; scaled by today's effective take-home rate (~75.2%) that is $7,463.68 net biweekly, times
26/12 =
$16,171.31/month **in 2046 nominal dollars**. The developer's actual current net monthly is about $5,917.
The page's headline income row is 2.7x his lived take-home, and nothing on the page (outside one
popover's "projected to retirement with raises applied") says why. This one number is most of "hard
to follow."

## Summary table

| # | Surface | Template | Verdict | Severity |
| - | ------- | -------- | ------- | -------- |
| 1 | Sensitivity sliders | `dashboard.html` | rework | medium |
| 2 | Income gap analysis table | `_gap_analysis.html` | fix | high |
| 3 | Gap chart | `_gap_analysis.html` + `retirement_gap_chart.js` | fix or fold into hero | medium |
| 4 | Pension benefit details | `dashboard.html` | keep (fix latent multi-pension) | low |
| 5 | Accounts projection table | `_retirement_account_table.html` / `_rows` | keep + fix | medium |
| 6 | Disclaimer alert | `dashboard.html` | keep, tighten | low |
| 7 | Empty state | `_gap_analysis.html` | dead code -- fix | medium |
| 8 | Pension management page | `pension_form.html` | keep (visual pass only) | low |
| 9 | Settings > Retirement (adjacent) | `settings/_retirement.html` | fix IA | medium |

Data-correctness findings D1-D7 are their own section below; D1 is a confirmed money-math defect in
the shared growth engine, not just a presentation problem.

## Surface 1: Sensitivity sliders

- **Should show:** the analysis's sensitivity to its two rate assumptions, in a way that helps the
  user decide something.
- **Actually does:** two slider+number pairs (SWR 2.0-6.0 step 0.25; return 3.0-12.0 step 0.5). On
  change (debounced 300ms) they fire one HTMX GET to `retirement.gap_analysis`, which re-renders the
  gap table, the chart, and (via OOB swap) the accounts table. The return override applies uniformly
  to every account. Defaults: stored SWR (4.00%); balance-weighted average of per-account configured
  returns (10.50%).
- **Divergence (confirmed):** the what-if is ephemeral and unanchored. There is no way to persist an
  adjusted value from here (SWR persists only via Settings > Retirement; per-account return only via
  each account's /investment params form -- neither is linked from this card). There is no delta
  display ("at 6% instead of 10.5%, your shortfall grows by $X"), no marker showing where the
  default came from, and no connection to any decision. Moving a slider just morphs nine numbers at
  once. The developer's "not all that helpful at least on their own" is accurate: the sliders are
  the only interactive lever on the page, and they lever nothing the user can act on.
- **Verdict: rework.** Sensitivity is worth keeping, but as part of a visible assumptions panel (see
  "What the page never answers"), with the changed-vs-baseline delta made explicit and a
  save-or-reset affordance for each assumption that has a home.

## Surface 2: Income gap analysis table

- **Should show:** income needed in retirement vs income covered, and the resulting savings
  requirement, in one consistent frame.
- **Actually does:** a flat 7-row table (9 with the tax rate set): pre-retirement income, gross
  pension, (after-tax pension), income gap, required savings, projected savings, surplus/shortfall,
  (after-tax savings + after-tax surplus). Every explanation lives in a click-to-open popover. The
  verdict row (surplus/shortfall) is the last row, styled like the rest plus color.
- **Divergence (confirmed):**
  - The rows mix three frames without saying so: 2046 nominal dollars (income, pension), a post-tax
    basis (income) vs pre-tax basis (pension, when no tax rate is set -- see D4), and today-anchored
    projections (savings). A figure and its caption never disagree -- here the captions are absent,
    hidden in popovers.
  - Four distinct questions (income? coverage? requirement? verdict?) share one undifferentiated
    table; the number that answers "how am I doing" has no visual rank. Principle 1 (the number is
    the hero) is violated head-on.
  - "Monthly Income Gap" ($4,234.77) and the chart's "Gap" bar ($1,840.55) are different concepts
    with the same name (post-pension vs post-pension-and-investment residual). Confirmed from
    `_build_chart_data` (`retirement_dashboard_service.py:675`) vs the table row.
- **Verdict: fix.** The underlying quantities are the right ones; the presentation needs a hero
  verdict, grouped sub-questions, on-surface captions (not popovers), and one declared dollar/tax
  frame.

## Surface 3: Gap chart

- **Should show:** how retirement income needs are covered (pension + withdrawals + remainder).
- **Actually does:** a single horizontal stacked bar ("Monthly Income"): pension
  ($11,936.54) + SWR investment income ($2,394.22) + residual "Gap"
  ($1,840.55), against the $16,171.31 target. Hidden when pre-retirement income is zero. Re-rendered
  on every slider change.
- **Divergence (confirmed):** the "Gap" label collision with the table (Surface 2). The `data-gap`
  attribute is written by the template but never read by `retirement_gap_chart.js` (dead data,
  `_gap_analysis.html:84`). A single thin stacked bar at aspect-ratio 8 spends a card answering what
  one sentence ("pension + withdrawals cover 89% of projected income") says better.
- **Verdict: fix or fold into the hero.** Coverage composition is a good candidate for the screen's
  hero visual (the dashboard precedent: chart-centric health check), but not in this form.

## Surface 4: Pension benefit details

- **Should show:** how the pension benefit is derived, since it is the largest single input to the
  analysis.
- **Actually does:** years of service (30.00), high salary average
  ($258,087.44), annual benefit ($143,238.53 = 1.85% x 30.00 x high avg, verified), monthly benefit
  ($11,936.54), plus the 4-year high-salary window list. Data is correct for the single-pension
  case.
- **Divergence (confirmed, latent):** with two or more qualifying pensions, this card shows only the
  LAST pension's derivation (`_compute_pension_benefit` keeps the last `benefit`,
  `retirement_dashboard_service.py:426-466`) while the gap table's pension row shows the SUM across
  pensions -- the card and the table would silently disagree. Harmless with today's one pension; a
  trap the moment a second is added. The high-years window also lists bare years with no "these are
  projected salaries, not history" caption.
- **Verdict: keep.** Fix the latent multi-pension mismatch (render per-pension, or aggregate
  honestly) and caption the projection basis during the rebuild.

## Surface 5: Retirement & investment accounts table

- **Should show:** each account's part in the plan: today's balance, what it is assumed to do, and
  what it becomes by retirement.
- **Actually does:** desktop table / mobile cards: name (links to the account's /investment
  dashboard), Traditional-vs-Roth badge, current balance (model-from-anchor via the `balance_at`
  seam -- agrees with /savings by construction), annual return, projected-at-retirement.
- **Divergence (confirmed):** the table hides the contribution story entirely. In the real data,
  employee contributions are zero on all three accounts: no paycheck deduction targets any account,
  and zero transfer contributions exist (verified by query). The 401(k)'s growth also includes a
  flat 5% employer contribution the page nowhere mentions. A reader cannot tell that the Roth's
  $173,688 projection assumes he never contributes another dollar, while the 401(k)'s $470,385
  quietly includes ~$273k of compounded employer money (reproduced by hand: $181.59 per period x 520
  periods + seed growth = $470,459 ~= $470,385). The single most consequential per-account fact --
  what flows in each period -- is invisible.
- **Verdict: keep + fix.** Add per-period contribution and employer columns (or an equivalent
  per-account caption), and make each row's assumption editable-or-linked (the /investment params
  form already exists).

## Surface 6: Disclaimer alert

- **Should show / actually does:** "projections are estimates" boilerplate.
- **Verdict: keep, tighten.** Once the assumptions panel exists (below), the disclaimer should point
  at it instead of vaguely gesturing at "assumed rates."

## Surface 7: Empty state (dead code)

- **Should show:** a first-run path: what to configure, in what order, to light the page up.
- **Actually does:** never renders. `calculate_gap` unconditionally returns a
  `RetirementGapAnalysis` dataclass, which is always truthy, so `{% if gap_analysis %}` (both the
  slider guard at `dashboard.html:23` and the table guard at `_gap_analysis.html:3`) is always true
  and the "Configure your salary profile and retirement settings..." message is unreachable. A user
  with nothing configured sees the sliders card plus a table of $0.00 rows.
- **Verdict: fix.** Design real empty/partial states (no salary profile; no retirement date; no
  accounts; no pension) as part of the rebuild; each names its missing input and links to where it
  is set.

## Surface 8: Pension management page

- **Should show / actually does:** list + create/edit/deactivate forms for pension profiles, with
  proper validation, IDOR checks, and double-submit protection. Solid.
- **Verdict: keep.** Visual-language pass only (tokens, spacing) when the screen is rebuilt.
  Consider whether "Manage Pensions" deserves the page's only primary button (it is an edit-inputs
  action, not the page's job).

## Surface 9: Settings > Retirement (adjacent surface)

- **Should show:** n/a (different page) -- but it owns three of this page's core assumptions: SWR,
  planned retirement date (fallback when no pension date), estimated retirement tax rate.
- **Divergence (confirmed):** the retirement page never links to it. The estimated tax rate -- which
  flips the analysis's whole tax basis (D4) -- is invisible from the page it affects. SWR is a
  slider here and a setting there with no cross-reference.
- **Verdict: fix IA.** The rebuild should surface these assumptions on the retirement page itself
  (read-only with an edit affordance is fine); whether the settings section remains is a Gate A
  call.

## Data-correctness findings (the step-3 seeds)

These are money-math findings, independent of any visual direction, in confirmed-severity order. Per
CLAUDE.md they are reported here for the developer's ruling; none were fixed in this audit.

### D1 (confirmed defect): the growth engine credits 13 days of growth per 14-day period

`growth_engine._period_return_rate` (line 253) computes `(end_date - start_date).days`, but both
real pay periods and `generate_projection_periods` synthetics (line 639) use inclusive end dates: a
2026-06-18..2026-07-01 period spans 14 calendar days and the next period starts 07-02, yet
`(end - start).days` = 13. Every period therefore compounds `(1+r)^(13/365)-1`, and consecutive
periods leave 1 day in 14 (26 days/year, about 7.1%) entirely uncompounded. Measured: a
$27,332.33 seed at 10.5% over the 520 synthetic periods to 2046 returns $173,688.32 -- a 6.354x
factor, exactly `(1.105)^(520*13/365)`, where 14-day crediting gives 7.324x. The assumed 10.5%
behaves as an effective ~9.69%; at the page's scale the projected total
($718,266.71) is understated by roughly $110k. This engine also serves the /investment growth chart
and the savings/year-end services, so the fix and its hand-computed test assertions cross this
screen's scope: **developer ruling required** (fix = day count `+1`, then re-derive affected
assertions by hand per rule 5; check `balance_at` / net-worth kernel use the same convention so
cross-page figures stay identical).

### D2 (confirmed, data + design): employee contributions are modeled as $0 everywhere

No paycheck deduction row targets any account (`target_account_id` NULL on all 12), and no transfer
contributions into accounts 4/5/6 exist. The projections therefore assume zero future employee
contributions -- silently. Real-world 401(k)/IRA contributions that happen outside Shekel's model
make every projection figure structurally pessimistic, in the same analysis whose income target is
structurally aggressive (D3/D5 below). Two invisible, opposite-direction biases is the worst kind of
"precise-looking" number. Design half: the page must state the contribution assumption per account
(Surface 5). Data half: the "what to do" path -- linking a deduction or recurring transfer to each
account -- exists in the app but is not reachable from this page.

### D3 (confirmed): the employer contribution base is frozen at today's gross

The 401(k)'s flat 5% employer contribution is computed on `salary_gross_biweekly` =
`get_current_gross_biweekly` -- today's
$3,631.74 -- held constant for all 520 periods to 2046. The same analysis compounds the SALARY to $258k
by 2046 for the pension and the income target. One number says his pay nearly triples; the sibling
number says it never changes. Internally inconsistent; direction: understates the 401(k) projection.

### D4 (confirmed): default tax basis compares net income against gross pension

With `estimated_retirement_tax_rate` unset (the live state), `calculate_gap` compares post-tax
pre-retirement income ($16,171 net) against the GROSS pension ($11,936)
(`retirement_gap_calculator.py:142-150`). Pension income is taxable; the default comparison
overstates pension coverage and understates the gap. The after-tax path exists but only activates
via a setting buried on another page (Surface 9). The default should be honest or loudly labeled.

### D5 (confirmed, ruling needed): recurring raises extrapolate 20 years

Two recurring raises (3% + 2.5%) compound to ~5.6%/yr for 20 years, tripling salary by 2046 and
driving the high-4 average ($258k), the pension ($11.9k/mo), the income target
($16.2k/mo), and therefore the $1.27M requirement. This is the user's own configured input behaving
as documented -- but the page nowhere shows that the entire analysis hangs on it. Whether raise
rules should extrapolate indefinitely for retirement projections (vs a horizon or a long-run
growth-rate assumption) is a product call for Gate A; at minimum the assumption must be visible.

### D6 (confirmed, latent): multi-pension derivation card mismatch

See Surface 4: details card shows the last qualifying pension; the gap row sums all of them.

### D7 (confirmed, minor): collected-but-unused and written-but-unread fields

`earliest_retirement_date` is collected and validated on the pension form but never consumed by any
analysis (a retire-earlier what-if is an obvious use). The chart's `data-gap` attribute is written
but never read by the JS.

## Live render verification (2026-07-02)

The page was rendered against the dev app (prod-clone data) in both themes and both viewports after
the static analysis above. Every figure in the live-data snapshot matched the rendered page exactly
(headline income, pension, gap, required, projected, shortfall, slider defaults, all three account
rows) -- the audit's measured numbers are the page's numbers. Rendering also surfaced visual defects
the code read alone did not:

- **V1 (confirmed, both themes): the gap chart collapses on mobile.** `aspectRatio: 8` is fixed in
  `retirement_gap_chart.js`, so at a 390px viewport the canvas is ~49px tall: the bar is invisible
  and the "Monthly Income" axis label overlaps the legend text. The chart card renders as broken
  fragments on a phone.
- **V2 (confirmed): chart legend labels are illegible in light mode.** The color swatches render but
  the label text ("Pension", "Investment Income (SWR)", "Gap") does not resolve a readable color
  against the paper background on this page.
- **V3 (confirmed): raw Bootstrap state styling instead of Steel Ink tokens, in four spots.** The
  accounts table `<thead class="table-light">` renders as a stark white band in dark mode; the type
  badges use `bg-info`/`bg-success` (cyan/green that carry no money-state meaning, violating the
  color-maps-to-money-state rule); "Monthly Income Gap" uses `text-warning` (low-contrast yellow on
  the light theme); the surplus/shortfall row uses `text-success`/`text-danger` classes rather than
  the token-mapped money-state treatment shared with the grid.
- **V4 (confirmed): the slider number inputs truncate on mobile** (`input-rem-7` renders "4.0" and
  "10." at 390px), and the slider track is near-invisible in light mode.

V1-V4 fold into the Surface 1/3/5 rebuild work; none change a verdict, all strengthen the "fix"
rulings.

## What the page never answers (the unrealized potential)

The developer's brief -- "doesn't tell me how I'm doing or what to do" -- maps to concrete,
buildable surfaces, all within the existing stack and mostly with existing data:

1. **A verdict hero.** Funded ratio (projected / required = 56.5% today) or the surplus/shortfall,
   stated as the page's largest figure with a one-sentence caption in plain dollars-and-dates ("On
   your current path you cover 57% of the savings your 2046 retirement needs"). Everything else on
   the page supports this number.
2. **The per-paycheck lever.** Shekel's whole identity is the pay period, and retirement saving is a
   per-paycheck act. "Contributing $X more per period from now until 2046-06-01 closes the gap" is
   one solve against the existing growth engine (which already has a reverse-projection primitive)
   -- and it converts the shortfall from an alarming abstraction into the exact number the user
   asked the page for. This is the single highest-value missing surface.
3. **A timeline.** 19.9 years / ~520 paychecks to the planned date; earliest vs planned retirement
   date (D7's unused field); optionally "retiring one year later changes the verdict by $Y" as the
   second lever.
4. **Actual trajectory vs required path.** `budget.account_anchor_history` already records real
   balance updates over time; plotting actuals against the required-savings path turns the page from
   a static calculator into a tracked plan. (Candidate, not a commitment: data density needs a look
   first.)
5. **An assumptions panel.** One visible list -- SWR 4%, per-account returns 10.5%, raises
   extrapolated at ~5.6%/yr to 2046, employer 5% flat on frozen gross, employee contributions $0,
   nominal 2046 dollars, tax basis -- each with its edit-or-link affordance. The two sliders stop
   being orphans and become two of N levers in this panel; the popover copy migrates here.

## Open questions for Gate A

1. **D1 fix scope:** fix the day-count in the shared engine now (touches /investment, savings,
   year-end + hand-recomputed test assertions), or gate the retirement rebuild on it? (It changes
   every projected figure on this page by ~9-13%.)
2. **D4 default:** make the no-tax-rate default compare like-for-like (e.g. label the pension row
   gross and the verdict "pre-tax"), or prompt for the tax rate as a first-run step?
3. **D5 raises:** should recurring raises extrapolate to the horizon, or cap at a configurable
   long-run growth assumption for retirement math?
4. **The per-paycheck lever (missing surface 2):** in scope for this rebuild? It is the "what to do"
   answer and is Opus-scope service work (a contribution solver against the growth engine).
5. **Dollars frame:** keep everything nominal-at-2046 with explicit labeling, or add a
   today's-dollars toggle (requires an inflation assumption -- a new input)?
6. **Consolidation:** does Settings > Retirement survive as a separate section, or do its three
   fields move onto the retirement page's assumptions panel?
7. **Sliders:** confirm the rework direction (assumptions panel with deltas + save affordance)
   before Loop A explores it.

## Rebuild decisions

Gate A ruled by the developer 2026-07-02 (question numbers match "Open questions for Gate A"):

1. **D1 growth-engine day count: fix now, before the rebuild.** Expected-behavior change is
   developer-confirmed, which authorizes re-deriving the affected hand-computed test assertions
   (rule 5 exception), each with its arithmetic shown. Work runs on `fix/growth-engine-day-count`
   off `dev`, isolated from the in-flight `fix/ledger-period-attribution` branch.
2. **Tax basis: always distinguish gross from net; when a single value must stand alone, use NET.**
   Developer: net "more accurately reflects what is available to me." Resolves D4's default: the gap
   comparison and the verdict figure present after-tax values as primary, with gross visible but
   never headline.
3. **Raise extrapolation (D5): LOCKED 2026-07-02 -- type-aware merit horizon.** Cola-type raises
   extrapolate to the retirement date (nominal-frame consistency); merit-type and custom-type raises
   apply only for the next N years. N defaults to 5 and MUST be a user-facing parameter (developer
   condition), surfaced in the assumptions panel with a live delta. Applies only to the retirement
   salary projection; the 2-year paycheck/budget pipeline is untouched. Requires the missing
   `RaiseTypeEnum` + ref-cache accessor (ID-based rule) and a `UserSettings` column + migration for
   the horizon.
4. **The per-paycheck lever is IN SCOPE** for the rebuild (required additional contribution per pay
   period to close the gap by the retirement date).
5. **Dollars frame: everything stays nominal-at-retirement, explicitly labeled.** No today's-dollars
   toggle in this rebuild; no inflation input added.
6. **Settings > Retirement consolidates INTO /retirement.** The assumptions panel becomes the
   persistent home for SWR, planned retirement date, and estimated retirement tax rate; the settings
   section retires (route/template cleanup in scope).
7. **Sliders rework APPROVED: assumptions panel with explicit deltas + save affordance.**

### Loop A outcome: direction D locked (2026-07-03)

Three rounds (A "verdict ledger" / B "flight path" / C "mission control" in round 1; the A+B merge
as direction D in round 2; hero-row refinement in round 3). The developer locked D. Scratch mockups
stayed out of the repo per the anti-anchoring rule; this anatomy is the record.

Anatomy, top to bottom:

- **Page header:** title + "Planned date 2046-06-01 / 19.9 years / 519 paychecks away" caption; a
  single quiet "Pensions" button (no primary-button styling).
- **Readiness card (the hero):** "63% funded"-style hero figure with the one-sentence plain-dollars
  caption BESIDE it (round-3 refinement: stacking them squeezed the chart), then the savings
  flight-path chart -- "your path" (accent, 10% wash) vs "needed to retire" (gray), 2px lines, 8px
  end dots with surface rings, direct end labels, hairline gridlines. The caption states projected
  vs needed vs shortfall and the frame ("2046 dollars, net of estimated tax"). Shortfall wording
  carries the danger color.
- **Assumptions rail** (beside the readiness card): one row per assumption -- retirement date, SWR,
  assumed return, merit horizon, COLA note, est. retirement tax, dollars frame -- each with its
  value, provenance or edit affordance; a changed row highlights with a delta chip ("was 10.5% /
  funded 52% (-11)") and Save / Reset buttons. This panel is the persistent home that replaces
  Settings > Retirement (ruling 6) and absorbs the old sliders (ruling 7).
- **"Close the gap" card, two levers at equal rank:** contribution stepper ("+$305/paycheck from now
  to 2046") and retire-later stepper ("+14 months, no new contributions"), each with its funded /
  shortfall outcome line.
- **Income-in-retirement card:** slim sequential meter (two validated accent-ramp steps over a pale
  track; NOT categorical hues) + four rows: net pension, SWR withdrawals, uncovered (danger), income
  to replace -- all labeled net-of-tax, 2046 dollars.
- **Accounts table:** account (link), type chip, today's balance, **contributions/period** ("you
  $X / employer $Y"; "none linked" in credit-amber), return, at-retirement; footer note states the
  $0-contribution assumption with a link-a-deduction CTA (finding D2's design half).
- **Pension footer:** the whole derivation as one auditable line (years x multiplier x high-4
  average = gross/mo -> net/mo).

Chart/meter palette committed (validated with the dataviz six-checks script): dark `#2878A8` /
`#4A9ECC` on `#14161A` (dE 14.0); light `#0A5A96` / `#3E92C2` on `#FBFAF7` (dE 21.5). Red stays
reserved for verdict / uncovered states; identity always paired with labels, never color-alone.

## Loop B plan (drafted 2026-07-03)

Gated phases; full suite green at each gate. Model discipline: P1/P2 are Opus 4.8 scope (services,
routes, migrations, test assertions); P3 is Fable scope (templates/CSS/JS). Work branch:
`feat/retirement-rebuild` off `dev` AFTER PR #53 (growth day-count) and PR #54 (balance remediation)
have both merged -- the rebuild's figures depend on the corrected engine. Service work runs in a
worktree; P4 live verification needs the branch in the main checkout (the dev app bind-mounts it),
coordinated with any session holding that tree.

### P1 -- model foundations (Opus)

- **P1a merit horizon (ruling 3).** New `RaiseTypeEnum` + ref-cache accessor (IDs, never name
  strings). `UserSettings.merit_raise_horizon_years`: Integer, NOT NULL, server_default 5, CHECK
  0-50, migration tested both directions. `project_salaries_by_year` gains the horizon behavior:
  through the cutoff year (current year + N) all raises apply; after it only cola-type recurring
  raises continue to compound from the cutoff salary -- merit and custom stop applying but their
  earned effect persists. Verified 2026-07-03: the only consumers are the two call sites in
  `retirement_dashboard_service`; the 2-year paycheck pipeline uses `apply_raises` directly and is
  untouched. Re-verify with the rule-7 grep before building.
- **P1b employer-base fix (finding D3).** The growth engine's employer-contribution base becomes
  time-varying for the retirement projection: retirement passes the cola-grown projected salary
  series; every other engine consumer (savings, year-end, investment) keeps today's constant-base
  behavior in this pass. Mechanism fork F3 below.
- **P1c readiness producer.** A producer for direction D's data: net-frame gap per ruling 2 (net
  income target vs after-tax pension; after-tax projected savings vs required); funded ratio =
  after-tax projected / required; the chart's two series ("your path" = summed per-account
  projections downsampled to <= 48 points; "needed path" = `reverse_project_balance` from the
  required target back to today under the same returns/contributions); countdown facts (periods
  remaining, years, date); per-account contribution facts (employee $/period, employer $/period,
  none-linked flag). Unset tax rate handling is fork F1.

### P2 -- the two levers (Opus)

- **P2a contribution solver.** Additional per-period contribution closing the shortfall: closed-form
  (shortfall at retirement / annuity factor of the remaining synthetic periods at the blended
  return) -- no iteration; solved in the after-tax frame treating new contributions as Roth-basis
  money, and the caption says so (fork F2). If the solution exceeds remaining contribution-limit
  headroom, the producer reports that honestly rather than silently capping.
- **P2b retire-later solver.** Smallest month offset (binary search, cap +180) where the full
  recomputation -- salary path, pension years/high-average, growth horizon, required target --
  reaches funded >= 100%. Degenerate states surfaced: "already funded", "not within 15 years".
- **P2c lever fragment endpoint.** One HTMX GET recomputing both lever outcome lines from stepper
  values, schema-validated like the existing `RetirementGapQuerySchema` pattern.

### P3 -- page rebuild to direction D (Fable)

- Rewrite `retirement/dashboard.html` + new partials (readiness, assumptions rail, levers, income
  composition, accounts, pension footer); per-screen `retirement.css`; new
  `retirement_path_chart.js` through the ShekelChart factory. Kills V1 (no fixed aspect-ratio), V2
  (factory-themed legend), V3 (tokens replace `table-light` / `bg-info` / `text-warning` / raw state
  classes), V4 (no truncating inputs).
- Assumptions panel: per-field HTMX save/reset posting through an evolved
  `retirement.update_settings`; server-computed deltas; Settings > Retirement section retires
  (ruling 6) with the settings page pointing here.
- Empty/partial states replace the dead `{% else %}` branch: each missing input (salary profile,
  retirement date, accounts, tax rate) named and linked.
- Accounts table gains the contributions column + none-linked CTA wired to the existing deduction
  form and `investment.create_contribution_transfer`; finding D6 fixed by rendering the pension
  footer per pension; `data-gap` cruft and `retirement_gap_chart.js` retire. `chart_slider.js` STAYS
  (investment + loan dashboards consume it; verified 2026-07-03).
- Popovers become on-surface captions. Both themes, desktop + mobile, CSP-clean.

### P4 -- live verification + acceptance

Branch into the main checkout (coordinated), drive on dev data: SSOT cross-checks (funded ratio
recomputed by hand; per-account balances equal /savings to the cent), mutation paths (assumption
save + reset persistence, both levers, pension CRUD, settings redirect), both themes and viewports.
Developer acceptance drive, then the PR to `dev`.

### Out of scope (recorded)

Actual-trajectory overlay from anchor history (revisit after daily use); consuming
`earliest_retirement_date` in analysis (the retire-later lever supersedes it; field stays
display-only); today's-dollars toggle (ruling 5); per-account contribution targeting in the solver.

### Forks: RATIFIED as recommended (developer, 2026-07-03)

All four forks were ratified with the recommended option; the alternatives are recorded for
provenance only.

- **F1 -- unset estimated tax rate: RATIFIED** -- treat as an explicit 0% with an "assumption
  missing -- set your estimate" state on the assumptions row. (Alternative not taken: blocking the
  verdict behind a first-run prompt.)
- **F2 -- contribution solver frame: RATIFIED** -- blended-return, Roth-basis approximation with an
  honest caption. (Alternative not taken: per-account targeting with limit enforcement.)
- **F3 -- employer-base mechanism: RATIFIED** -- the engine accepts an optional per-period salary
  basis defaulting to the current constant; other consumers unchanged in this pass. (Alternative not
  taken: migrating every consumer to salary series at once.)
- **F4 -- horizon column shape: RATIFIED** -- `merit_raise_horizon_years` NOT NULL default 5.
  (Alternative not taken: nullable-means-extrapolate-forever.)
