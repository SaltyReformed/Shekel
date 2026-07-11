# Fable 5 UI/UX Overhaul -- Plan and Status Ledger

The roadmap and durable status record for the full UI/UX overhaul (visual rebuild + UX/workflow/IA +
functionality -- NOT a reskin). This document answers "what is the plan, what is done, what is
next"; it deliberately contains no design content of its own.

Last updated: 2026-07-03.

## Where things live

| Concern | Document |
| ------- | -------- |
| Design language, principles, hard constraints, committed Steel Ink theme | `fable5-design-language.md` |
| Per-screen diagnosis + locked rebuild decisions | `<screen>_audit.md` (grid, dashboard so far) |
| The screenshot-driven design process (Loop A mockups, Loop B real app) | `visual_loop.md` |
| CSS architecture: app.css split plan + theme-selector feasibility (assessed 2026-06-11, decisions pending) | `css_architecture_audit.md` |
| This roadmap and status ledger | `overhaul_plan.md` |
| Project-wide phase status (all workstreams, not just UI) | `../progress.md` |

The prior `docs/ui_ux_audit.md` / `ui_ux_remediation_plan.md` are historical reference only, NOT
this overhaul's backlog.

## Provenance

The overhaul began as a proof-step plan ("Fable 5 UI/UX Overhaul - Proof Step: Diagnose and Rebuild
the Dashboard") authored in the original planning session that produced PR #29. That plan was never
committed -- it lived only in that session -- which is how this ledger came to be created later. Its
still-live content is folded in below (the dashboard playbook, the provisional rollout order, the
navbar/IA area). Superseded by events since it was written:

- The first rebuild target moved from the dashboard to the GRID (developer call, 2026-06-10); the
  grid rebuild is complete on `feat/grid-rebuild`.
- The browser loop uses `tests/manual/shoot.py` (the repo's Python Playwright), not the Node
  Playwright MCP the plan specified -- there is no Node on this machine (`visual_loop.md`).
- Its "Gate B" stack decision was settled during the grid direction rounds: Bootstrap 5 + tokens +
  HTMX stays; the Svelte 5 grid island is the recorded upgrade path.
- The `app.css` tokens consolidation (its Step 2a) landed with the Steel Ink theme commit; the
  design brief + shekel-design skill (its Step 2b) landed in PR #29.

## Process per screen

1. **Audit** (shekel-design skill Step 1): per-surface diagnosis + the developer's lived workflow ->
   `<screen>_audit.md`.
2. **Loop A**: scratch mockups in /tmp (never committed -- anti-anchoring rule in `visual_loop.md`),
   screenshot rounds, developer picks/iterates.
3. **Decisions locked**: recorded in a "Rebuild decisions" section of the screen's audit.
4. **Loop B**: build into the real templates in gated phases; full test suite per phase; live
   verification against the dev app INCLUDING mutation paths.
5. **Acceptance**: the developer drives it with real data before the PR merges.

Model discipline: Fable 5 for visual/template/CSS/JS work; Opus 4.8 (session or subagent) for
`app/services` / `app/routes` / test-assertion changes.

## Cross-screen decisions already locked

- **Theme: "Steel Ink"**, app-wide, dark first-class (token table in the design language).
- **Stack: Bootstrap 5 + HTMX + vanilla JS stays.** The stack ROI gate ran 2026-06-11
  (React/Vue/Svelte/Solid/Angular/Qwik/Astro/Tailwind considered); recorded upgrade path: a Svelte 5
  island for the desktop grid (direction I "Live Sheet": range select, bulk ops, type-in-cell)
  re-gated only if daily use shows the need.
- **Component vocabulary** established by the grid, intended to spread: status chips (filled =
  settled/credit, plain = projected), anchored action card, one-click + keyboard actions, Ctrl+K
  command palette, self-refreshing HTMX fragments over full reloads.

## Status

| Screen / workstream | Status |
| ------------------- | ------ |
| Foundation (design language, skill, dashboard audit) | DONE -- merged via PR #29 |
| Steel Ink theme (app-wide token swap) | MERGED to `dev` via PR #31 (2026-06-11) |
| **Grid** (first rebuild target) | MERGED to `dev` via PR #31 (2026-06-11, CI green). All 6 Loop B phases + audit fix-list items 1-5 done; decisions in `grid_audit.md`. Ships to prod with the next `dev` -> `main` PR |
| **Dashboard** (second target) | IN PROGRESS -- playbook steps 1-3 DONE 2026-06-12. Step 1: audit re-verified ("Re-verification (2026-06-12)" in `dashboard_card_audit.md`). Step 2: Gate A locked ("Rebuild decisions": keep+fix cards 1-6, REMOVE Spending Comparison, as-of-today balance headline, transfers excluded from spending / included in runway). Step 3: data-correctness pass COMMITTED to dev (`308b49f` pay-period guard, `0ef7ba6` dashboard; full suite 6063, pylint 10.00/10; all fixes live-verified). Step 4: UX/IA pass DONE, Gate B LOCKED 2026-06-12 ("UX/IA pass (step 4)" section in `dashboard_card_audit.md`): B1-B7 all approved -- pulse strip consolidating Balance + Payday + Alerts + the E2 horizon; bills still-due totals on the REMAINING basis with transfer shadows INCLUDED; position tier; copy/consistency fold-ins. AMENDED same day by the data-value pass ("Data-value pass (Gate B amendments)" in the audit, after Loop A round 1 read "about the same"): chart-centric health check (13-period end-balance chart + trough stat; the hero IS the current period's projected end balance under reservation semantics), due-soon list replaces the full bills list, runway and the payday card dropped (a "next paycheck" caption survives), design-language Differentiation re-scoped to the grid. Developer framing recorded: the dashboard is "a quick easy to read health check"; the two most-tracked values are still due + projected end balance; grid-as-homepage stays the fallback. Step 5 Loop A COMPLETE same day after SIX rounds; Loop B BUILT, B-4 ACCEPTED through a live developer drive (direction refined Q -> R -> P during acceptance: Due Soon card removed, stat row beside the hero, Highest Point chip added), SSOT verified (code trace + live 13/13 grid reconciliation), and **COMMITTED to dev as `7adcfff` (2026-06-12)** -- incl. the low_balance_threshold NOT NULL migration and the grid negative-formatting adoption of the shared money macro. Durable anatomy + every ruling in the audit. OPEN: the two revisit-later presentational deviations; the anchor UTC-day-vs-Eastern bucketing ruling (recorded in the audit + parity memory). Ships to prod with the next `dev` -> `main` PR; run `/update-docs` after it merges |
| **Accounts** (third target; unifies `/savings` + `/accounts`) | IN PROGRESS -- direction **B** "Net Worth Cockpit" built on `dev` across Loop B P0-P4 (per-slice ledger in `accounts_audit.md` "Process and next steps"): mini-sprint (Property type + equity + analytics fix, PR #42 on prod), P1 net-worth producer, P2 cockpit shell + click-to-edit, P3 trend / allocation bar / sparklines, P4 retired the `/accounts` table (redirect + hard-delete moved to the edit-form danger zone, decision 12). Full suite 6314, pylint 10.00/10. P5 live verification DONE 2026-06-26 (desktop + mobile both themes, SSOT to the cent, 7/7 non-destructive interaction checks). Two follow-up cleanups landed: the UI-orphan inline editor removed (a99ecc7) and the always-blank "Change this period" chip removed (f59f301, decision 13). NEXT: the `dev` -> `main` PR covering all Loop B P2-P4 + the cleanups (opening now). |
| **Account detail pages** (fourth target; the tabular per-account pages) | MERGED to `dev` via PR #55 (2026-07-03) after developer acceptance. Audit + all 7 gate rulings in `account_detail_audit.md` (2026-07-02): ONE `accounts.cash_detail` page (band grammar: balance hero + honest `as_of_date` anchor caption + 3/6/12-month delta chips + interest health chip + Chart.js trend with Today marker) serves checking, interest, AND the previously page-less plain types (Savings / Credit Card); the per-period table DROPPED by developer ruling (exact figures re-homed to the chart tooltip; grid = checking's detail home, with an Open Grid CTA); parameters in a card below the band; property page rebuilt in the same grammar (equity hero, corrected copy). Old `/checking` + `/interest` URLs are ownership-checked redirect stubs; cockpit macros extracted to `_acct_macros.html`; shared chart helpers extracted to `chart_theme.js`. Full suite 6863, pylint 10.00/10, live-verified on prod-clone data (both themes/viewports; as-built section in the audit). Investment/retirement dashboard explicitly OUT (own later target). |
| **Retirement** (parallel fourth target) | MERGED to `dev` via PR #56 (2026-07-03, merge `71485b2b`, lint-and-test green) after two same-day developer acceptance rounds. Audit + Gate A rulings + the Loop A direction-D lock + Loop B as-built in `retirement_audit.md` (commits `44511df0`, `676582fd`; closeout `3273907f`). The audit's confirmed money bug D1 (the growth engine credited 13 of every 14 period days) was fixed separately via PR #53 so the correction ships regardless of the redesign. Anatomy: readiness hero + assumptions rail with what-ifs, the two close-the-gap levers (per-paycheck contribution and retire-later solvers), income-in-retirement panel, accounts table projected to the retirement year, per-pension derivation lines; Settings > Retirement consolidated into the page. Acceptance-round fixes: the date row became a real write-through lever updating its owning pension; Chart.js re-init after htmx settle; progress_bar.js OOB widths. Final suite 6956. Ships to prod with the next `dev` -> `main` PR |
| **Salary** (fifth target) | MERGED to `dev` via PR #57 (2026-07-03, merge `2d0e2606`, lint-and-test + polyglot-lint green) after developer acceptance. Audit + the four Gate A rulings + the three-round Loop A record + Loop B as-built in `salary_audit.md` (commits `f7d6593b` docs, `d24ae9d6` rebuild). `/salary` is now the salary COCKPIT for the primary active profile (the profile-list landing page REMOVED; a switcher renders only with 2+ active profiles): net-per-paycheck hero with calibration provenance, five chips, and a full-width net-pay staircase (regular-paycheck net only so raise steps read instantly; 3rd paychecks as ringed dots on stems off the line; solid history / dashed projection / Today marker). Composition spine with HTMX period stepping (OOB deductions bar-list, sorted per timing group), salary-path sparkline over the raise rules, calibration strip. Projection ledger kept as its own page with summary chips (next raise / next 3rd / per-year nets) and token row tints; the edit form is the profile settings surface with a danger zone (deactivate moved from the dead list page + a NEW reactivate route closing the one-way-deactivation trap); old breakdown URLs are ownership-checked redirect stubs; the dead `tax_config.html` deleted. Producer gotcha recorded in the audit: raise-event RUN COLLAPSE (the calculator marks every period of a raise month; the first period of a run of identical labels is THE event). Full suite 7004, pylint 10.00/10, biome clean. Deliberately light: calibrate/confirm pages kept the plain form idiom. Ships to prod with the next `dev` -> `main` PR |
| Remaining screens | PROVISIONAL ORDER from the original plan (confirm per screen at each start): analytics, investment, loan, settings. The app-wide navbar/IA rework is its own area. Screens the original list omits (recurring, transfers, obligations, companion, calendar) slot in per developer call. |

## Dashboard playbook (carried from the original proof-step plan)

The dashboard is NOT a safe reskin: some cards show wrong information and the correct ones are
unhelpful (developer's verdict), so the work crosses into `dashboard_service.py`. Steps:

1. **Diagnosis** -- already done: `dashboard_card_audit.md` (per card: intended vs actually rendered
   vs divergence vs keep/fix/remove verdict). Re-verify line references against current code before
   acting; the audit predates the grid rebuild.
2. **Gate A (developer): per-card keep / fix / remove**, decided from the audit BEFORE any code. Do
   not fix data for a card slated for removal.
3. **Data-correctness fixes** (Opus scope; committed with tests regardless of which visual direction
   later wins -- bug fixes are direction-independent): root causes in `dashboard_service.py` / the
   partials. Known seeds: the Spending Comparison card's `hx-get` to `dashboard.bills_section` with
   `hx-swap="none"` (fetches and discards), the alert links hardcoded to `/`, the balance figure vs
   "as of" caption mismatch, and the `_get_balance_info` hardcoded `staleness_days = 14` that can
   disagree with `settings.anchor_staleness_days` (verified 2026-06-10, missed by the audit). New or
   changed assertions only after the developer hand-confirms corrected values (rule 5), arithmetic
   shown in comments. Targeted suites: `tests/test_services/test_dashboard_service.py`,
   `tests/test_routes/test_dashboard.py`, `tests/test_routes/test_dashboard_entries.py`; then the
   full suite.
4. **UX/IA pass** for surviving cards: what the user is trying to do on the dashboard, what each
   card should show, what to consolidate, reorder, or drop.
5. **Loop A directions** (2-3, scratch mockups in /tmp) honoring the existing HTMX refresh contract
   (`balanceChanged from:body`; the old `dashboardRefresh` event was deleted in the step 3 data
   pass), then the direction gate, then Loop B -- same process as the grid.

## Small follow-ups (not screen-sized)

- CSS architecture (`css_architecture_audit.md`): app.css split into 7 files DONE 2026-06-11
  (verified pixel-identical; dashboard rebuild work now lands in `dashboard.css`). Theme selector:
  developer chose Scope B (multiple palettes, per-palette token files); Steel Ink is the default and
  only palette until more are built -- the selector UI/persistence is future work. The audit's open
  defects were FIXED 2026-06-12: chart_theme.js factory API (function options survive + dataset
  colors re-resolve on theme toggle) and content-hash `v=` static URL versioning (cache busting in
  both deploy modes). Only residue: manifest icon paths are unversioned strings; meta theme-color
  deferred to the Scope B selector work.
- Accounts P4 orphan (created 2026-06-25, DONE 2026-06-25): retiring `accounts/list.html` left the
  old inline balance editor and its `accounts/_anchor_cell.html` partial UI-orphaned. Removed in
  full. The real scope was ~2.5x this note's first estimate of "5 tests + 2 docstrings": the partial
  cannot be deleted while its two GET partners (`inline_anchor_form` / `inline_anchor_display`)
  still render it, so all three inline routes went, plus the partial, 12 test methods + 3
  parametrized `test_auth_required` matrix rows + 5 `TEST_PLAN.md` rows, 6 now-unused imports (4 in
  `anchor.py`, 2 in `test_access_control.py`), and 7 stale docstring `:func:` cross-references
  across 6 live files (the `anchor.py` module docstring + its `true_up` comment,
  `accounts/__init__.py`, `account.py`, `crud.py`, `loan/params.py`, `anchor_service.py`, plus the
  `test_anchor_service.py` docstring). The live grid + cockpit editor (`true_up` / `anchor_form` /
  `anchor_display` + the revert helpers) and the 9 grid/dashboard/cockpit tests in the mixed
  `TestAnchorTemplatesEmitVersionPin` class were kept. Sweep clean (zero references in any tracked
  file), `pylint app/` 10.00/10, full suite 6318; net -462 lines. The gitignored
  `tests/.fixture-profile/main.csv` timing cache was left as-is (it regenerates).
- A5 (grid audit): quick-create has no name field for ad-hoc rows -- Opus scope (create
  schema/route).
- D1/D2 (grid audit, noted-not-prioritized): period-nav simplification; friendlier
  invalid-status-transition errors. RULED 2026-07-11: D1 dropped, D2 scheduled (both halves) -- see
  `grid_audit.md` D1/D2 and `../plans/implementation_plan_ui_closeout.md` session 4.
- After the grid PR merges: run `/update-docs` so `../progress.md` and the README reflect the
  overhaul.

## Shipping

Grid: PR `feat/grid-rebuild` -> `dev` (CI validates on the PR), then the normal `dev` -> `main` flow
per CLAUDE.md. Dev-data note: the dev DB was deliberately mutated during Loop B mutation testing and
will be resynced from prod; the clone procedure's privilege caveats are handled by
`scripts/init_db_role.sql` self-heal plus the one manual dev-only
`GRANT CREATE ON DATABASE shekel TO shekel_app`.
