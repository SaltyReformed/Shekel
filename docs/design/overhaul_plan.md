# Fable 5 UI/UX Overhaul -- Plan and Status Ledger

The roadmap and durable status record for the full UI/UX overhaul (visual rebuild +
UX/workflow/IA + functionality -- NOT a reskin). This document answers "what is the plan,
what is done, what is next"; it deliberately contains no design content of its own.

Last updated: 2026-06-12.

## Where things live

| Concern | Document |
| ------- | -------- |
| Design language, principles, hard constraints, committed Steel Ink theme | `fable5-design-language.md` |
| Per-screen diagnosis + locked rebuild decisions | `<screen>_audit.md` (grid, dashboard so far) |
| The screenshot-driven design process (Loop A mockups, Loop B real app) | `visual_loop.md` |
| CSS architecture: app.css split plan + theme-selector feasibility (assessed 2026-06-11, decisions pending) | `css_architecture_audit.md` |
| This roadmap and status ledger | `overhaul_plan.md` |
| Project-wide phase status (all workstreams, not just UI) | `../progress.md` |

The prior `docs/ui_ux_audit.md` / `ui_ux_remediation_plan.md` are historical reference only,
NOT this overhaul's backlog.

## Provenance

The overhaul began as a proof-step plan ("Fable 5 UI/UX Overhaul - Proof Step: Diagnose and
Rebuild the Dashboard") authored in the original planning session that produced PR #29. That
plan was never committed -- it lived only in that session -- which is how this ledger came to
be created later. Its still-live content is folded in below (the dashboard playbook, the
provisional rollout order, the navbar/IA area). Superseded by events since it was written:

- The first rebuild target moved from the dashboard to the GRID (developer call, 2026-06-10);
  the grid rebuild is complete on `feat/grid-rebuild`.
- The browser loop uses `tests/manual/shoot.py` (the repo's Python Playwright), not the Node
  Playwright MCP the plan specified -- there is no Node on this machine (`visual_loop.md`).
- Its "Gate B" stack decision was settled during the grid direction rounds: Bootstrap 5 +
  tokens + HTMX stays; the Svelte 5 grid island is the recorded upgrade path.
- The `app.css` tokens consolidation (its Step 2a) landed with the Steel Ink theme commit;
  the design brief + shekel-design skill (its Step 2b) landed in PR #29.

## Process per screen

1. **Audit** (shekel-design skill Step 1): per-surface diagnosis + the developer's lived
   workflow -> `<screen>_audit.md`.
2. **Loop A**: scratch mockups in /tmp (never committed -- anti-anchoring rule in
   `visual_loop.md`), screenshot rounds, developer picks/iterates.
3. **Decisions locked**: recorded in a "Rebuild decisions" section of the screen's audit.
4. **Loop B**: build into the real templates in gated phases; full test suite per phase;
   live verification against the dev app INCLUDING mutation paths.
5. **Acceptance**: the developer drives it with real data before the PR merges.

Model discipline: Fable 5 for visual/template/CSS/JS work; Opus 4.8 (session or subagent)
for `app/services` / `app/routes` / test-assertion changes.

## Cross-screen decisions already locked

- **Theme: "Steel Ink"**, app-wide, dark first-class (token table in the design language).
- **Stack: Bootstrap 5 + HTMX + vanilla JS stays.** The stack ROI gate ran 2026-06-11
  (React/Vue/Svelte/Solid/Angular/Qwik/Astro/Tailwind considered); recorded upgrade path:
  a Svelte 5 island for the desktop grid (direction I "Live Sheet": range select, bulk
  ops, type-in-cell) re-gated only if daily use shows the need.
- **Component vocabulary** established by the grid, intended to spread: status chips
  (filled = settled/credit, plain = projected), anchored action card, one-click +
  keyboard actions, Ctrl+K command palette, self-refreshing HTMX fragments over full
  reloads.

## Status

| Screen / workstream | Status |
| ------------------- | ------ |
| Foundation (design language, skill, dashboard audit) | DONE -- merged via PR #29 |
| Steel Ink theme (app-wide token swap) | MERGED to `dev` via PR #31 (2026-06-11) |
| **Grid** (first rebuild target) | MERGED to `dev` via PR #31 (2026-06-11, CI green). All 6 Loop B phases + audit fix-list items 1-5 done; decisions in `grid_audit.md`. Ships to prod with the next `dev` -> `main` PR |
| **Dashboard** (second target) | IN PROGRESS -- playbook steps 1-3 DONE 2026-06-12. Step 1: audit re-verified ("Re-verification (2026-06-12)" in `dashboard_card_audit.md`). Step 2: Gate A locked ("Rebuild decisions": keep+fix cards 1-6, REMOVE Spending Comparison, as-of-today balance headline, transfers excluded from spending / included in runway). Step 3: data-correctness pass COMMITTED to dev (`308b49f` pay-period guard, `0ef7ba6` dashboard; full suite 6063, pylint 10.00/10; all fixes live-verified). NEXT: step 4 UX/IA pass, then step 5 Loop A directions -- leading candidate: the "E2" horizon strip + alert line from the grid's Loop A rounds |
| Remaining screens | PROVISIONAL ORDER from the original plan (confirm per screen at each start): accounts, savings, salary, analytics, retirement, investment, loan, settings. The app-wide navbar/IA rework is its own area. Screens the original list omits (recurring, transfers, obligations, companion, calendar) slot in per developer call. |

## Dashboard playbook (carried from the original proof-step plan)

The dashboard is NOT a safe reskin: some cards show wrong information and the correct ones are
unhelpful (developer's verdict), so the work crosses into `dashboard_service.py`. Steps:

1. **Diagnosis** -- already done: `dashboard_card_audit.md` (per card: intended vs actually
   rendered vs divergence vs keep/fix/remove verdict). Re-verify line references against
   current code before acting; the audit predates the grid rebuild.
2. **Gate A (developer): per-card keep / fix / remove**, decided from the audit BEFORE any
   code. Do not fix data for a card slated for removal.
3. **Data-correctness fixes** (Opus scope; committed with tests regardless of which visual
   direction later wins -- bug fixes are direction-independent): root causes in
   `dashboard_service.py` / the partials. Known seeds: the Spending Comparison card's
   `hx-get` to `dashboard.bills_section` with `hx-swap="none"` (fetches and discards), the
   alert links hardcoded to `/`, the balance figure vs "as of" caption mismatch, and the
   `_get_balance_info` hardcoded `staleness_days = 14` that can disagree with
   `settings.anchor_staleness_days` (verified 2026-06-10, missed by the audit). New or
   changed assertions only after the developer hand-confirms corrected values (rule 5),
   arithmetic shown in comments. Targeted suites:
   `tests/test_services/test_dashboard_service.py`, `tests/test_routes/test_dashboard.py`,
   `tests/test_routes/test_dashboard_entries.py`; then the full suite.
4. **UX/IA pass** for surviving cards: what the user is trying to do on the dashboard, what
   each card should show, what to consolidate, reorder, or drop.
5. **Loop A directions** (2-3, scratch mockups in /tmp) honoring the existing HTMX refresh
   contracts (`dashboardRefresh from:body`, `balanceChanged from:body`), then the
   direction gate, then Loop B -- same process as the grid.

## Small follow-ups (not screen-sized)

- CSS architecture (`css_architecture_audit.md`): app.css split into 7 files DONE
  2026-06-11 (verified pixel-identical; dashboard rebuild work now lands in
  `dashboard.css`). Theme selector: developer chose Scope B (multiple palettes,
  per-palette token files); Steel Ink is the default and only palette until more are
  built -- the selector UI/persistence is future work. The audit's open defects were
  FIXED 2026-06-12: chart_theme.js factory API (function options survive + dataset
  colors re-resolve on theme toggle) and content-hash `v=` static URL versioning
  (cache busting in both deploy modes). Only residue: manifest icon paths are
  unversioned strings; meta theme-color deferred to the Scope B selector work.
- A5 (grid audit): quick-create has no name field for ad-hoc rows -- Opus scope
  (create schema/route).
- D1/D2 (grid audit, noted-not-prioritized): period-nav simplification; friendlier
  invalid-status-transition errors.
- After the grid PR merges: run `/update-docs` so `../progress.md` and the README
  reflect the overhaul.

## Shipping

Grid: PR `feat/grid-rebuild` -> `dev` (CI validates on the PR), then the normal
`dev` -> `main` flow per CLAUDE.md. Dev-data note: the dev DB was deliberately mutated
during Loop B mutation testing and will be resynced from prod; the clone procedure's
privilege caveats are handled by `scripts/init_db_role.sql` self-heal plus the one manual
dev-only `GRANT CREATE ON DATABASE shekel TO shekel_app`.
