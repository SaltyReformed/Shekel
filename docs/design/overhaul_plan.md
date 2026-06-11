# Fable 5 UI/UX Overhaul -- Plan and Status Ledger

The roadmap and durable status record for the full UI/UX overhaul (visual rebuild +
UX/workflow/IA + functionality -- NOT a reskin). This document answers "what is the plan,
what is done, what is next"; it deliberately contains no design content of its own.

Last updated: 2026-06-11.

## Where things live

| Concern | Document |
| ------- | -------- |
| Design language, principles, hard constraints, committed Steel Ink theme | `fable5-design-language.md` |
| Per-screen diagnosis + locked rebuild decisions | `<screen>_audit.md` (grid, dashboard so far) |
| The screenshot-driven design process (Loop A mockups, Loop B real app) | `visual_loop.md` |
| This roadmap and status ledger | `overhaul_plan.md` |
| Project-wide phase status (all workstreams, not just UI) | `../progress.md` |

The prior `docs/ui_ux_audit.md` / `ui_ux_remediation_plan.md` are historical reference only,
NOT this overhaul's backlog.

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
| Steel Ink theme (app-wide token swap) | BUILT -- on `feat/grid-rebuild` |
| **Grid** (first rebuild target) | BUILT, pending developer acceptance -- branch `feat/grid-rebuild` pushed (9 commits, full suite green). All 6 Loop B phases + audit fix-list items 1-5 done; decisions in `grid_audit.md` |
| **Dashboard** (second target) | NEXT -- audit exists (`dashboard_card_audit.md`); leading direction: the "E2" horizon strip + alert line from the grid's Loop A rounds; includes data fixes (mis-wired spending card, hardcoded alert links, balance caption mismatch, and the `_get_balance_info` staleness-days inconsistency found 2026-06-10) |
| Remaining screens (recurring, accounts, salary, transfers, obligations, retirement, analytics, settings, companion, calendar) | UNSEQUENCED -- ordered per developer call after the dashboard; each starts at Process step 1 |

## Small follow-ups (not screen-sized)

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
