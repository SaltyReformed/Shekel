# Implementation Plan: UI Overhaul + Polish Pass Closeout

Combined remaining-work list for the Fable 5 UI/UX overhaul (`docs/design/overhaul_plan.md`) and the
UI/UX polish pass (`docs/plans/implementation_plan_ui_ux_polish.md` plus its register at
`docs/design/ui_ux_polish_audit.md`). Written 2026-07-11 from both docs cross-checked against dev
git history; where a doc and the code disagreed, the code won. The polish register remains the SoT
for finding details and rulings; this plan governs only what is left and in what order.

## Status corrections (listed as open in the docs, verified DONE on dev)

The overhaul plan's status table stops at 2026-07-03. Complete since then:

- **Loan detail rebuild:** Loop B P1 (`39eafed0`) + P2 (`90b1116f`); all three deferred follow-ups
  resolved (`f361f1fb`); Extra-principal card placement (`f6467560`).
- **Escrow config redesign:** all 8 steps merged to dev via PR #60 (`b28a3cfa`).
- **Investment detail rebuild** (the last account-type page): band-grammar rebuild landed
  (`65730d0a`).
- **Recurring cluster** (templates + transfers + obligations -> one surface): Loop B P1
  (`60f0c96a`), P2 (`38152be7`), P3 conflict chooser (`9d14f005`), P4 shared form cards
  (`4f15ddd6`), P5 accepted 2026-07-05 (as-built in `recurring_audit.md`).
- **Navbar/IA rework:** decided and built 2026-07-06 (`navbar_audit.md` decision gate); silver-coin
  brand extended app-wide, gold logo deleted.
- **Polish sessions S1-S7 and S11-S16** complete (per-session records in the polish plan). Only S8,
  S9 (shrunk), and S10 remain.
- The dashboard row's "anchor UTC-day-vs-Eastern bucketing" fork: resolved by the timezone display
  policy (DB/backend UTC, America/New_York at presentation; shipped 2026-06-12).

## Remaining work

### A. Polish sessions still open (already specced in the polish plan)

1. **S8 - detail pages + anchor idiom** (Opus recommended, size S-M). P-DT2 loan allocation bar +
   "confirmed" badge off raw `bg-success/warning/info`; P-DT3 investment hero caption mono -> sans;
   P-DT5 casing normalization across the three siblings; P-DT6 investment half-width final row;
   P-DT7 ARM tag RC5 floor case; P-DT9 amortization link promoted to a real control; D14 port of the
   investment click-to-edit hero to loan and cash (cash has no on-page anchor recording, so the port
   may touch a route/form; ruled 2026-07-09, buildable).
2. **S9 remainder - accounts** (Fable preferred, size S). P-AC1 allocation-bar track + center tick
   (the judgment item); P-AC4 liability red on the loan-cell balances; verify the account edit form
   and archived list against the D5 archive convention (the cockpit has no on-card archive button
   left after D6-F).
3. **S10 - breadcrumb removal + back buttons (D1)** (Opus recommended, size M). Inventory the
   sub-pages whose only way back is the breadcrumb; add a top-right "Back to <parent>" on the
   amortization-schedule pattern; then delete breadcrumbs everywhere (~24 templates). Run solo.

### B. Flagged during polish sessions, not registered as findings

4. **Designed error-fragment convention for handled 4xx/5xx over HTMX** (Opus + one design ruling).
   htmx drops 4xx/5xx response bodies by default; S5 shimmed only the taxes checkpoint's 422 path
   (`tax_checkpoint.js`). Still dead UI: that card's handled-500 banner, and the grid's 422 error
   partials. Needs one ruled convention (which statuses swap, what the fragment looks like); the
   wiring afterward is mechanical.
5. **Analytics year-view month cards** (size S, mechanical). Raw `border/text-success/danger` + cyan
   `bg-info` badges; flagged in the S5 as-built as out-of-register residue; same-genus token sweep.

### C. Overhaul-plan small follow-ups (verified still open today)

6. **A5 - grid quick-create name field** (Opus: schema/route/template). Verified 2026-07-11:
   `_transaction_quick_create.html` still posts `estimated_amount` plus hidden ids only, so an
   ad-hoc row cannot be named at the Tier-1 entry point.
7. **Grid D1/D2 (noted-not-prioritized in `grid_audit.md`):** period-nav simplification (three
   mechanisms); friendlier invalid-status-transition errors (pairs naturally with item 4's
   fragments). Decide at the acceptance drive: schedule or formally drop.
8. **Manifest icon versioning residue** (size XS). `css_architecture_audit.md`: manifest icon paths
   are unversioned strings (the icons themselves were re-baked silver 2026-07-06).

### D. Housekeeping

9. **Test-suite idle-timeout pin.** `.env` carries `IDLE_TIMEOUT_MINUTES=10080` (the 7-day dev value
   from `1bdf0485`) and neither `scripts/test.sh` nor the fixtures pin the test value, so
   `test_stale_activity_rejected` fails suite-wide unless run with `IDLE_TIMEOUT_MINUTES=720`. Root
   fix: pin the session-lifetime config in the test fixture so the suite is independent of the
   ambient `.env`.

### E. Ship

10. **Developer acceptance drive** on dev over everything unshipped: dashboard S2, accounts D6-F
    cells, spending D7 cockpit, analytics S5, statements D8, retirement/salary S7, settings S11,
    plus whatever A-D adds.
11. **dev -> main PR** (57 commits pending as of 2026-07-11) -> prod deploy -> resync dev.
12. **Docs refresh after the merge:** run `/update-docs`; bring `overhaul_plan.md`'s status table
    current (the corrections above); mark the polish plan and register closed out.

## Proposed sessions

| # | Session     | Contents                          | Model           | Size     |
|---|-------------|-----------------------------------|-----------------|----------|
| 1 | S8          | item 1                            | Opus            | S-M      |
| 2 | S9+         | items 2, 5, 8                     | Fable preferred | S        |
| 3 | S10         | item 3                            | Opus            | M (solo) |
| 4 | Errors + A5 | items 4, 6, 9 (+ D2 if kept)      | Opus            | M        |
| 5 | Ship        | items 10-12 (+ the D1/D2 call)    | developer-led   | -        |

Sessions 1, 2, and 4 are order-free among themselves; session 3 runs solo (it brushes ~24 templates,
so nothing else mid-flight); session 5 is last. Session 4's error-fragment convention needs its
design ruling before build (a STOP, not a license to build).

## Open developer decisions

- **Error-fragment convention (item 4):** which handled statuses swap a designed fragment, and what
  the fragment looks like. Blocks the error half of session 4.
- **Grid D1/D2 (item 7):** schedule or formally drop.

## Adjacent open items (outside these two docs; triage at the acceptance drive)

From the grid/entries review session (recorded in session memory, not in a doc): mobile-cell
breakpoint staleness, add-form refocus, and the `detail.target` detached-node trap. The same
review's 422 error partials are item 4 above. Verify each still reproduces, then register or drop.

## Parked (recorded, no action)

- Theme selector Scope B (palettes beyond Steel Ink, selector UI + persistence, meta theme-color).
- Svelte 5 grid island: re-gated only if daily use shows the need.
- The two dashboard presentational deviations (persistent negative-point chart labels; the tracks
  target-date tick): REVISIT LATER by ruling, pending daily-use feedback.
- `investment_dashboard_service` and `balance_at` at the C0302 module-size ceiling: split when next
  touched.

## Session protocol

Unchanged from the polish plan: load the `shekel-design` skill; before/after shots in both themes (+
mobile where the surface renders there); targeted tests per change with the full suite once as the
session gate (until item 9 lands, run it with `IDLE_TIMEOUT_MINUTES=720`); logical commits per
finding; update the register; model discipline (`services/`/`routes/`/test-assertion edits happen in
an Opus context).
