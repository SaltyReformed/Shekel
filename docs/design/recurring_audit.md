# Recurring / Transfers / Obligations Audit

Per-surface diagnosis of the recurring-definitions cluster (`/templates`, `/transfers`,
`/obligations`) for the Fable 5 overhaul, per the shekel-design skill Step 1. These three navbar
items are audited as ONE target because they are three views of the same concept: definitions of
recurring money movement (in, out, between). Status: diagnosis complete 2026-07-04.
**Gate A LOCKED 2026-07-04** (rulings in "Rebuild decisions"). **Loop A COMPLETE 2026-07-04** (both
rounds locked; record below). Next: Loop B (build plan below), sequenced after the analytics slices
per developer priority. Line references are as of `dev` @ `009f0ad4` (2026-07-04); re-verify before
acting on them.

## The cluster's job (proposed)

Define and manage every recurring money movement, and see what those definitions commit you to each
month. The grid shows the instances; this cluster owns the blueprints.

## Inventory

| Surface | Route file | Lines | Templates | Tests |
| ------- | ---------- | ----- | --------- | ----- |
| Recurring | `app/routes/templates.py` | 799 | `templates/list.html` (303), `templates/form.html` (225), `_recurrence_macros.html` (45) | `test_templates.py` (2004), `test_template_flags.py` (871), engine suite (2725) |
| Transfers | `app/routes/transfers/templates.py` | 773 | `transfers/list.html` (235), `transfers/form.html` (194) | `test_transfers.py` (2912), `test_transfer_recurrence.py` (1692) |
| Obligations | `app/routes/obligations.py` | 470 | `obligations/summary.html` (245) | `test_obligations.py` (613), aggregator + projection suites |

Shared machinery: `RecurrenceRule` (one table, both template kinds point at it), the paired engines
`recurrence_engine` (783 lines) / `transfer_recurrence` (298, shares `resolve_generation_plan` /
`compute_due_date` / `match_periods`), shared `_recurrence_form_helpers` (510) and
`_commit_helpers`, shared `recurrence_form.js` (133).

Styling state: ALL THREE surfaces are plain Bootstrap - zero Steel Ink tokens, no HTMX on the list
pages, and both list pages duplicate their entire markup as a desktop table plus a separate mobile
card list. No money arithmetic in Jinja anywhere in the cluster (pinned by
`test_template_no_money_arithmetic.py`).

## Surface 1 -- Recurring (`/templates`)

**Actually produces:** an active/archived split list (Name with envelope/companion badges, Category,
Income/Expense badge, Amount, Recurrence via the shared `recurrence_cell` macro, Edit/Archive/Delete
actions as plain POST forms). Create/edit form: name, amount, type, account, category, tracking
flags (`is_envelope`, `companion_visible`), the 8-pattern recurrence picker (single select; JS
shows/hides interval / day-of-month / due-day / month / start-period / end-date) with a LIVE preview
fed by `templates.preview_recurrence`, edit-only `effective_from`, optimistic-lock `version_id`.

**Propagation semantics (solid, keep):** create fans out instances via `generate_for_template`;
update calls `regenerate_for_template(effective_from)` which deletes only auto-generated
non-overridden future rows and preserves overridden/deleted rows as conflicts; settled periods are
skipped; renames propagate to all non-deleted instances. Archive soft-deletes projected instances
and unarchive restores + regenerates; hard-delete is blocked when settled history exists.

**Divergence:** no grouping, filtering, or sorting beyond active/archived; no monthly-equivalent
cost anywhere; the list answers "what exists" but not "what does it cost me"; visual identity
predates the overhaul entirely.

**Proposed verdict: KEEP + REBUILD as the host of a unified Recurring surface.**

## Surface 2 -- Transfers (`/transfers`)

**Actually produces:** a parallel CRUD list (Name, From, To, Amount, Recurrence, actions) and a
parallel form. A "one-time transfer" is a template with the ONCE pattern (the default); truly ad-hoc
transfers live on the grid (`POST /transfers/ad-hoc`), not here. Grid cell/quick- edit/full-edit
partials belong to the grid surface and are out of scope here. All mutations route through
`transfer_service` so the five shadow-transaction invariants hold; the list page never shows
shadows.

**Divergences:**

- The form loads `recurrence_form.js` but has NO live preview div, so the preview silently no-ops -
  the one capability gap vs the transaction form (besides `due_day_of_month`, which transfers
  legitimately lack).
- `TransferTemplate` is structurally `TransactionTemplate` minus type/flags plus
  from/to/`derive_from_loan`, with a UNIQUE(user_id, name) the transaction side lacks - parallel
  models, parallel engines, parallel routes, parallel list pages. The duplication is contained by
  shared helpers but the USER-FACING split is artificial: both are "recurring definitions."

**Proposed verdict: MERGE into the unified Recurring surface** (one list, kind = Income / Expense /
Transfer; the transfer form stays its own form variant with the preview gap fixed). The `/transfers`
list URL becomes a redirect; the Transfers navbar item retires.

## Surface 3 -- Obligations (`/obligations`)

**Actually produces:** a read-only page: a cash-flow projection card (now / ~12 months / end, via
`obligations_projection` reusing `balance_at.grid_balance_view`) and three tables (recurring
expenses / transfers / income) re-listing the same active templates as the other two pages, adding a
monthly-equivalent column (`obligations_aggregator`, also used by /savings), an APPROXIMATE
next-occurrence date, and per-section monthly subtotals.

**Divergences:**

- Near-total duplication: name/account/category/amount/frequency re-list `/templates` and
  `/transfers` content with different frequency wording (`_frequency_label` says "Biweekly" /
  "Semi-Annual" where `recurrence_cell` says "Every paycheck" / "Every 6 months").
- `_next_occurrence` (obligations.py:100) is an admitted "informational approximation" that
  re-implements period matching instead of calling `recurrence_engine.match_periods` - a
  figure-caption honesty risk (its date can disagree with the grid).
- The projection card overlaps the dashboard's end-balance chart and the grid's footer, both rebuilt
  surfaces that own that question.

**Unique value worth saving:** the monthly-equivalent committed totals (the aggregator is canonical
and shared with /savings). Everything else is duplication.

**Proposed verdict: RETIRE the page.** Its monthly-equivalent totals move into the unified Recurring
surface as a summary band; the projection card is dropped (dashboard and grid own projection);
next-occurrence, if kept as a column, is recomputed from `match_periods`, not the approximation.
`/obligations` becomes a redirect; the navbar item retires.

## Cross-cutting findings

1. **Orphaned conflict-resolution capability.** Both engines ship `resolve_conflicts()` with
   docstrings describing an update-conflict UI ("present the options, call resolve_conflicts") that
   was never built; the routes call `handle_recurrence_conflict`, which only logs and flashes a
   Phase-1 "kept your overrides" advisory. 44 test references, zero route callers. Gate call: build
   the keep-vs-regenerate chooser in Loop B, or ratify auto-keep-overrides as the product behavior
   and delete the orphan.
2. **Duplicated frequency vocabulary:** `_frequency_label` vs `recurrence_cell` - one wording set
   must win (the macro's, presumably) regardless of direction.
3. **Mobile markup duplication:** both list pages maintain two parallel DOM trees
   (`d-none d-md-block` table + `d-md-none` cards); the rebuild should use one responsive structure.
4. **No Steel Ink anywhere in the cluster** - this is the last major navbar surface (with Settings)
   still wearing the pre-overhaul skin.

## Consolidation shape (proposed for Gate A)

One navbar item, **Recurring**, replacing three. Cockpit grammar per the display ruling
([[feedback_graphical_cockpit_display]] / the analytics audit's display grammar section):

- **Summary band (the obligations kernel):** committed monthly hero (expenses + transfers
  monthly-equivalent), income monthly-equivalent, net committed vs income chip - measured from the
  definitions themselves, no projection mixing.
- **One unified list:** all active definitions with kind chips (Income / Expense / Transfer),
  amount, monthly-equivalent, recurrence phrase (macro vocabulary), engine-backed next date,
  envelope/companion badges, archived section collapsed below. Grouping/sort options are a Loop A
  question (by kind, by category group, by monthly cost).
- **Forms:** the two form variants restyled into the shared form idiom, transfer preview gap fixed,
  pattern picker UX explored in Loop A.

## Gate A questions for the developer

1. **Unify Recurring + Transfers into one surface** (one navbar item, kind chips, two form
   variants)? Recommended: yes.
2. **Retire Obligations**, moving its monthly-equivalent totals into the unified surface's summary
   band? And is dropping the projection card acceptable (dashboard/grid own that question), or do
   you want a slim end-balance chip retained?
3. **Next-occurrence column:** replace the approximation with engine-backed dates (exactness fix,
   recommended regardless)?
4. **Conflict UI:** build the keep-vs-regenerate chooser (the orphaned `resolve_conflicts`
   capability) in this rebuild's Loop B, or ratify auto-keep-overrides as product behavior and
   remove the orphan?
5. **List organization default:** what do you reach for first on these pages today - finding a
   specific template to edit, or scanning what things cost? (Sets the default sort/grouping for Loop
   A.)

## Rebuild decisions (Gate A, 2026-07-04)

1. **Unify Recurring + Transfers: LOCKED.** One navbar item, one surface, kind chips, two form
   variants. `/transfers` list URL redirects; Transfers navbar item retires.
2. **Retire Obligations: LOCKED.** `/obligations` redirects; navbar item retires. CRITICAL workflow
   fact recorded with the ruling: the developer used Obligations as the MONTHLY lens on the budget -
   "an overview of monthly income and expenses to compare with the grid's per-paycheck view." The
   summary band is therefore the page's second job, not decoration: it must present
   monthly-equivalent income vs committed outflow (expenses + transfers) clearly enough to stand in
   for the retired page. Loop A should explore a monthly/per-paycheck unit toggle on the band, since
   the comparison against the grid is the stated use.
3. **Next dates: LOCKED** - engine-backed (`match_periods`), the approximation and the duplicate
   frequency vocabulary both retire.
4. **Conflict chooser: LOCKED - Option B, default KEEP.** Build the keep-vs-replace chooser wired to
   the existing `resolve_conflicts()` (per-instance rows, each defaulting to "keep my override").
   Developer rationale recorded: "I do occasionally edit a single instance and then will update the
   template to better reflect reality instead of continual one-offs" - i.e. the override is
   sometimes intentionally superseded by the template edit, so being asked matters, but keep must be
   the default. Opus scope in Loop B; the chooser UI gets its own Loop A mock round before build.
5. **List workflow facts:** add and edit are used about equally (both entry points stay prominent);
   developer wants sort/filter functionality - Loop A explores filter by kind / category / account
   plus text search, and sort by name / amount / monthly cost / next date.
6. **DRY/SOLID consolidation is explicit Loop B scope** (developer call): the unification should
   collapse the parallel list routes/templates into one, and share form scaffolding between the two
   variants where the merge itself makes them shared - consolidation that the design causes, not
   speculative abstraction (rule 13). The paired engines already share their core; route/form-helper
   duplication currently held down by documented pylint disables is the target. Opus scope.

### Conflict chooser option space (for ruling 4)

The scenario: a template's future instances are regenerated when the template is edited. Instances
the user hand-edited on the grid (`is_override=True`) are protected - the engine flags them as
conflicts instead of overwriting. Today the route auto-keeps every override and flashes a generic
"kept your overrides" advisory; the `resolve_conflicts()` service capability (which can apply
per-instance keep/replace decisions) was built and tested but no UI ever calls it.

- **Option A - ratify auto-keep:** current behavior becomes the product decision; the orphaned
  `resolve_conflicts` is deleted. Overrides always win; realigning an instance means re-editing it
  on the grid. Zero new UI.
- **Option B - build the chooser:** after a conflicting template edit, an interstitial lists each
  overridden instance (period, old/new amount) with per-row keep vs replace, wired to the existing
  `resolve_conflicts`. Full control, most new surface (Opus route work + a new template + tests).
- **Option C - auto-keep with specific receipts (recommended):** keep auto-keep as the behavior, but
  replace the generic advisory with an enumerated one - which instances were kept, their periods and
  amounts, each linking to its grid cell. Kills silent drift without adding a blocking decision
  point; `resolve_conflicts` is then deleted as orphaned.

## Loop A record

- **Round 1 (2026-07-04): direction B "Grouped by kind" LOCKED** for the unified list surface.
  Anatomy as mocked: summary band (Income / Expenses with %-of-income / Transfers out / Net margin
  verdict tile, captioned "from the rules, no projection") with a live Monthly / Per-paycheck unit
  toggle that swaps every figure on the page (band, section subtotals, equivalents column) - the
  developer's grid-comparison workflow built in; toolbar (search, kind filter pills, sort select, +
  New); Income / Expenses / Transfers sections with tinted banners and per-section subtotals (kind
  chips retire inside sections); rows = name with env/shared badges, category or From -> To, defined
  amount + recurrence phrase (macro vocabulary), monthly equivalent with share-of-committed bar
  (Spending tab's bar vocabulary), engine-backed next date, Edit/Archive; archived collapsed to one
  line. Direction A (flat cost-ranked with kind chips) rejected as the default landing state but its
  cost lens survives through the sort control.
- **Round 2 (2026-07-04): conflict chooser LOCKED as mocked** ("This looks good"). Anatomy: appears
  only when a template edit collides with hand-edited (`is_override`) upcoming instances; scenario
  framing sentence (what changed, new value, effective date); one row per conflicted instance = date
  - owning pay period, your value, new template value, and a two-state Keep/Use toggle DEFAULTING TO
  KEEP with the actual amounts in the button labels ("Keep 220.00" / "Use 190.00") so each decision
  reads without cross-referencing columns; bulk shortcuts (Keep all / Use new value for all); an info
  line making the blast radius explicit ("all N other upcoming instances regenerate at the new value;
  paid history is never touched"); Cancel edit abandons the whole template edit; Apply commits
  decisions through `resolve_conflicts()`.

## Loop B build plan

- **P1 -- data + consolidation (Opus scope):** unified list producer serving both template kinds
  with monthly AND per-paycheck equivalents (reuse `obligations_aggregator` as the monthly SSOT),
  engine-backed next dates via `match_periods`; one page route; `/transfers` and `/obligations`
  become redirects and their navbar items retire; delete `_frequency_label` / `_next_occurrence` and
  the obligations projection card path; targeted tests then full suite.
- **P2 -- page build (Fable scope):** summary band + unit toggle, toolbar (search/filter/sort),
  grouped sections, rows per the locked anatomy, `recurring.css`, both themes, shoot.py
  verification.
- **P3 -- conflict chooser (Opus scope):** wire `resolve_conflicts()` end to end: the update route
  returns the chooser when conflicts exist, chooser template + apply route, default keep, tests for
  keep/replace/mixed/cancel paths.
- **P4 -- forms (Fable + Opus touch):** restyle both form variants into the shared idiom, fix the
  transfer live-preview gap, plus the developer-flagged DRY pass: collapse the parallel list
  routes/templates the unification obsoletes and share form scaffolding the merge makes common.
- **P5 -- acceptance:** developer drive on real data; as-built record here.
