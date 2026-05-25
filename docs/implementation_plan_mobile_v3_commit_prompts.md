# Mobile-First v3 -- Commit Prompts

- Companion to: `docs/implementation_plan_mobile_v3.md`
- Required reading for every prompt:
  `docs/audits/financial_calculations/remediation_follow_up_common.md`
- Purpose: one ready-to-paste session prompt per productive commit (26 total;
  Commits 15 and 26 are reserved buffers in the plan and have no prompt here) so
  each commit can be executed in its own fresh session.
- Audience: future Claude Code sessions (and the developer reading what each
  session was asked to do).

## How to use this document

1. Wait until every prerequisite commit listed under "Prereqs on dev" has been
   merged to `dev` (and `main`, via the PR-gated workflow in CLAUDE.md). Each
   prompt depends only on the state of `dev`, not on any prior session context.
2. Start a fresh Claude Code session at the project root with `dev` checked out.
3. Copy the entire fenced block under the commit's heading. Paste it as the first
   message in the new session. Do not edit it.
4. The session will read the canonical plan section for this commit, re-verify
   against current code, do the work, run the gates, and stop with a structured
   work summary that ends by asking whether to commit and push. **No commit or
   push happens without your explicit go-ahead.**
5. After the commit lands on `dev` and CI is green, open a PR `dev` -> `main`.
   After merge, resync `dev`
   (`git fetch origin && git checkout dev && git merge origin/main && git push origin dev`)
   before starting the next prompt.
6. If a session reports drift between the plan and current code, stop and
   reconcile (edit the plan or adjust the prompt) before continuing. The plan
   is the floor, not a free-floating wish list.

The prompts are ordered to match the plan's commit numbering (Section 8 checklist

- Section 9 detailed). Read `implementation_plan_mobile_v3.md` Section 7
(Dependency Analysis) once before starting; the prereqs in each prompt below encode it but the
picture is easier to hold from the DAG.

Work summary note: every commit in this plan is template / Jinja / JS / CSS work, not
financial-logic work. The A-M labels still apply verbatim, but expect:

- **G. Migrations:** "n/a" on every commit (no schema changes in the v3 plan).
- **D. Re-pinned tests:** "none" on every commit (no financial assertion changes).
- **H. Invariants:** the financial-correctness invariants (no new reads of
  `Account.current_anchor_*` / `LoanParams.current_principal`) stay green by
  construction; `tests/test_static_guards.py` is the lock and is named
  explicitly in commits that touch routes (Commits 2 and 13).

---

## Phase 1 -- DRY foundation (zero visible change)

### Commit 1 -- `feat(grid): add render_row_cells + render_row_card macros`

**Prereqs on dev:** none. **Touches:** new file only; no callers yet.

```text
You are executing Commit 1 of the Shekel mobile-first v3 implementation in a
fresh session. Work in the project root on the dev branch.

Required reading -- read each in full BEFORE anything else (use @path so they
are fetched, do not summarize from memory or training):
- @docs/implementation_plan_mobile_v3.md (Sections 0-8 for context; Section 9
  "Commit 1 -- add render_row_cells + render_row_card macros" for the A-H
  specification; Sections 2 D-B, 3 R-1, R-2, R-4, R-5, R-7, and 4 for the
  shape both macros must support across owner desktop, owner mobile, and the
  companion read-only consumer)
- @docs/audits/financial_calculations/remediation_follow_up_common.md (apply
  rules and work summary format -- mandatory; these project-wide rules govern
  every commit)
- @CLAUDE.md (Rules, Definition of Done)
- @docs/coding-standards.md (HTML / Jinja2 Templates section especially)
- @docs/testing-standards.md (test run guidelines)
- @app/templates/grid/grid.html (read in full; verify the income block at
  140-189 and expense block at 216-263 against current dev HEAD before
  authoring)
- @app/templates/grid/_mobile_grid.html (read in full; income block 64-130
  and expense block 151-216, plus the warning comment at lines 5-7)
- @app/templates/grid/_transaction_cell.html (the desktop cell partial both
  blocks include verbatim)
- @app/templates/grid/_transaction_empty_cell.html (the empty cell partial)

Objective: Phase 1 foundation. Add two Jinja macros in a NEW file
app/templates/grid/_grid_row_macros.html. No callers yet -- the macros sit
alongside the existing inline matching loops; Commits 3 and 4 switch the
templates to call them.

  render_row_cells(rk, periods, matched_by_row_period, entry_sums,
                   txn_type_id, account, today)
    Emits the desktop <th scope="row"> row label + one <td class="text-end
    cell"> per period. Looks up matched_by_row_period[(rk.category_id,
    rk.template_id, rk.txn_name, period.id)] (default []), renders
    grid/_transaction_cell.html for matched txns and
    grid/_transaction_empty_cell.html for the empty case. Wraps each matched
    txn in <div id="txn-cell-{{ txn.id }}"> (preserve the current
    grid.html:177-178 DOM-id convention exactly).

  render_row_card(rk, period, matched_by_row_period, entry_sums,
                  can_edit=True)
    Emits the mobile <li class="list-group-item ... mobile-txn-card"
    data-mobile-txn-id="{{ txn.id }}" ...> shape per matched txn (preserve
    _mobile_grid.html:99-127 verbatim). can_edit=False (used by companion in
    Commit 13) is allowed to drop bottom-sheet-opening data attributes but
    keep the Mark Paid path. Use `txn.is_transfer_shadow` to preserve the
    `data-mobile-xfer-id` attribute case at _mobile_grid.html:101.

Re-read the four duplicated blocks in full before writing. The macro bodies
are EXTRACTED from current Jinja text-for-text -- no rewrite from scratch
(CLAUDE.md rule 10). Both macros live in ONE file so they are visible
together.

Files this commit touches:
- app/templates/grid/_grid_row_macros.html (NEW)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md (sections
"Apply these rules (every commit)" and "Work summary format"). End the session
with the work summary using labels A through M verbatim. G is "n/a"
(template-only commit, no migrations).

Specific verification gates for this commit:
- `ls app/templates/grid/_grid_row_macros.html` -- file exists.
- `grep -nE "^\\{% macro (render_row_cells|render_row_card)" app/templates/grid/_grid_row_macros.html`
  returns exactly two lines.
- `pylint app/ --fail-on=E,F` clean (no Python changed, run as a safety check).
- `./scripts/test.sh tests/test_routes/test_grid.py -v` green -- the existing
  inline loops still drive output.
- Full suite green via `./scripts/test.sh`.
- Manually load /grid at 1920x1080 in a browser -- HTML output identical to
  pre-commit (the macros exist but no template imports them yet; pure addition).

If anything is unclear, ASK.
```

---

### Commit 2 -- `feat(grid): precompute matched_by_row_period in index route`

**Prereqs on dev:** Commit 1 merged. **Touches:** app/routes/grid.py only; context-only addition, no
consumer yet.

```text
You are executing Commit 2 of the Shekel mobile-first v3 implementation in a
fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/implementation_plan_mobile_v3.md (Sections 0-8 for context; Section 9
  "Commit 2 -- precompute matched_by_row_period in index route" for the A-H
  specification; Section 1 rule 2 and Section 4 for why this is the SOLE
  Python touch to grid routing in the entire plan)
- @docs/audits/financial_calculations/remediation_follow_up_common.md (apply
  rules and work summary format -- mandatory)
- @CLAUDE.md (Rules; especially rule 2 "Read before you write")
- @docs/coding-standards.md (Python type-hint rules; specific exceptions; DRY)
- @docs/testing-standards.md (test run guidelines; tests/test_static_guards.py
  is the financial-correctness lock referenced in test C2-4 of the plan)
- @app/routes/grid.py (read in full -- 389 lines; especially lines 165-389
  for index, lines 267-269 for the existing txn_by_period build, line 274
  for the entry_sums build that follows, lines 68-162 for _build_row_keys,
  and the existing import of is_cancelled at line 29)
- @app/templates/grid/grid.html (verify the matching predicate at lines
  162-172 income / 235-245 expense one more time before mirroring it in
  Python; the predicate is text-for-text the source-of-truth)
- @app/utils/balance_predicates.py (`is_cancelled` -- use this; do NOT
  compare against STATUS_CANCELLED directly in Python)
- @app/models/budget.py (Transaction model; confirm is_income / is_expense /
  is_deleted / template_id / name / category_id columns and shapes)

Objective: produce the dict matched_by_row_period ONCE in the route,
mirroring the predicate of the Jinja loops text-for-text. The macros from
Commit 1 read from this dict; Commit 3 (desktop) and Commit 4 (mobile) are
the consumers. This commit only adds context; no template change yet.

Key dict: keyed by 4-tuple (category_id, template_id, txn_name, period_id),
value is a list[Transaction]. Predicate:
  - txn.category_id == rk.category_id
  - is_income_section -> txn.is_income; otherwise txn.is_expense
  - not txn.is_deleted
  - not is_cancelled(txn)
  - if rk.template_id is not None and txn.template_id is not None:
      txn.template_id == rk.template_id
    else: txn.name == rk.txn_name

Build the dict after the existing txn_by_period build (currently lines
267-269 -- re-grep before editing) and pass it via render_template kwargs.
Type hint the dict explicitly:
  dict[tuple[int, int | None, str, int], list[Transaction]]

Files this commit touches:
- app/routes/grid.py (insert the precomputation between the txn_by_period
  build and the entry_sums build; add matched_by_row_period to the
  render_template kwargs)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End the
session with the work summary using labels A through M verbatim. G is "n/a".

Specific verification gates for this commit:
- `grep -nE "current_anchor_(balance|period_id)|current_principal|interest_rate" app/routes/grid.py`
  shows no NEW reads vs the pre-commit baseline (the financial-correctness
  guard from Section 1 rule 2 of the plan).
- `./scripts/test.sh tests/test_routes/test_grid.py tests/test_static_guards.py -v`
  green; the static-guards test must stay green (Test C2-4 of the plan).
- `pylint app/ --fail-on=E,F` clean. No new warnings.
- Full suite green via `./scripts/test.sh`.
- Manually load /grid -- HTML output identical to pre-commit (the precomputed
  dict exists in context but no template reads it yet; unused context keys
  are Jinja no-ops).

If anything is unclear, ASK.
```

---

### Commit 3 -- `refactor(grid): desktop grid uses render_row_cells macro`

**Prereqs on dev:** Commits 1 and 2 merged. **Touches:** app/templates/grid/grid.html.

```text
You are executing Commit 3 of the Shekel mobile-first v3 implementation in a
fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/implementation_plan_mobile_v3.md (Sections 0-8 for context; Section 9
  "Commit 3 -- desktop grid uses render_row_cells macro" for the A-H
  specification; Section 3 R-1, R-5, R-6 (the scenario-controls-slot div at
  grid.html:6 is NOT dead code; do NOT touch it))
- @docs/audits/financial_calculations/remediation_follow_up_common.md (apply
  rules and work summary format -- mandatory)
- @CLAUDE.md (Rules)
- @docs/coding-standards.md (HTML / Jinja2 Templates section)
- @docs/testing-standards.md
- @app/templates/grid/grid.html (read in full; especially lines 1-50 for
  block setup, 140-189 for income inline matching loop, 191-201 for the
  income subtotal row that STAYS inline, 215-263 for expense, 266-288 for
  expense subtotal + net cash flow rows that STAY inline; verify current
  line numbers via grep before any edit)
- @app/templates/grid/_grid_row_macros.html (the macros added by Commit 1)
- @app/routes/grid.py (verify matched_by_row_period exists in the
  render_template kwargs from Commit 2)

Objective: replace the desktop grid.html income and expense matching loops
with calls to render_row_cells. The output must be byte-identical on desktop
modulo Jinja whitespace from the macro expansion -- this is the
zero-visible-change refactor that locks Phase 1's invariant.

The group_name banner row logic (currently around lines 141-149 income and
218-226 expense; re-grep) stays inline -- it is iteration scaffolding, not
matched-row content. The subtotal rows (Total Income / Total Expenses / Net
Cash Flow) read from subtotals[period.id] and stay inline.

Add `{% from "grid/_grid_row_macros.html" import render_row_cells %}` at the
top of the content block. Inside each `{% for rk in income_row_keys %}` /
`{% for rk in expense_row_keys %}` loop, after the group-header `<tr>`
block, replace the inline matching `<tr>...</tr>` (income lines 151-189 /
expense lines 228-263 -- re-grep) with one call:

  {{ render_row_cells(rk, periods, matched_by_row_period,
                       entry_sums, TXN_TYPE_INCOME, account, today) }}

Use TXN_TYPE_EXPENSE for the expense block. These Jinja globals are already
injected -- verify in the route's render_template kwargs.

DO NOT touch:
- The scenario-controls-slot <div> at grid.html:6 (R-6).
- The subtotal rows.
- The Add Transaction modal at lines 303-362 (that is Commit 14's scope).

Files this commit touches:
- app/templates/grid/grid.html (income + expense matching loops only)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End the
session with the work summary using labels A through M verbatim. G is "n/a".

Specific verification gates for this commit:
- `grep -nE "\\{% for txn in period_txns %\\}" app/templates/grid/grid.html`
  returns no matches (inline matching loops are gone).
- `grep -nE "render_row_cells" app/templates/grid/grid.html` returns at
  least two matches (one in income, one in expense; the import counts as one
  more).
- `./scripts/test.sh tests/test_routes/test_grid.py -v` green.
- `pylint app/ --fail-on=E,F` clean.
- Full suite green.
- Manually: capture rendered HTML of /grid at 1920x1080 pre-commit (diff
  target). After this commit, re-render and diff. Only acceptable
  differences are whitespace artifacts from macro expansion (Jinja inserts
  newlines at macro call sites). Numbers, status badges, progress
  indicators, balance row, subtotals all byte-identical.
- Each txn-cell-<id> appears exactly once in the response (Test C3-6).

If anything is unclear, ASK.
```

---

### Commit 4 -- `refactor(grid): mobile grid uses render_row_card macro`

**Prereqs on dev:** Commits 1, 2, and 3 merged. **Touches:** app/templates/grid/_mobile_grid.html.

```text
You are executing Commit 4 of the Shekel mobile-first v3 implementation in a
fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/implementation_plan_mobile_v3.md (Sections 0-8 for context; Section 9
  "Commit 4 -- mobile grid uses render_row_card macro" for the A-H
  specification; Section 3 R-1, R-2, R-5 for the warning-comment removal
  rationale)
- @docs/audits/financial_calculations/remediation_follow_up_common.md (apply
  rules and work summary format -- mandatory)
- @CLAUDE.md (Rules)
- @docs/coding-standards.md (HTML / Jinja2 Templates)
- @docs/testing-standards.md
- @app/templates/grid/_mobile_grid.html (read in full -- 250 lines;
  warning comment at lines 5-7, income matching loop at 64-130, expense at
  151-216, group headers at 84-89 income / 171-176 expense that STAY inline,
  Net Cash Flow bar at 224-231, Projected Balance card at 233-247; verify
  line numbers via grep before editing)
- @app/templates/grid/_grid_row_macros.html (the macros)

Objective: symmetric to Commit 3 for the mobile partial. Replace inline
matching loops with render_row_card calls. Remove the duplicate-warning
comment at lines 5-7 once both sides point at the same precomputed dict.
Output must be byte-equivalent on mobile.

Steps:
1. Remove the IMPORTANT warning comment at lines 5-7.
2. Add `{% from "grid/_grid_row_macros.html" import render_row_card %}` at
   the top of the file.
3. In the income section inside the period panel: keep the
   `{% for rk in income_row_keys %}` outer loop; keep the group-header
   `<li>` block; replace the inline `<li class="... mobile-txn-card">`
   matching/rendering loop with a `render_row_card(...)` call. The macro
   iterates the matched list itself per Commit 1's signature.
4. Same treatment for the expense section.
5. Preserve every data attribute the current cards emit:
   data-mobile-txn-id, data-mobile-xfer-id (transfer-shadow special case at
   line 101), role="button", aria-label, etc. The macro from Commit 1 must
   carry these forward; if the macro is missing any, fix the macro in this
   commit (note as a discovered refinement in the work summary section I).

DO NOT touch the Net Cash Flow bar, the Projected Balance card, or the
period-nav arrows.

Files this commit touches:
- app/templates/grid/_mobile_grid.html

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End the
session with the work summary using labels A through M verbatim. G is "n/a".

Specific verification gates for this commit:
- `grep -n "MUST be applied to both files" app/templates/grid/_mobile_grid.html`
  returns no matches.
- `grep -nE "\\{% for txn in period_txns %\\}" app/templates/grid/_mobile_grid.html`
  returns no matches.
- `grep -n "render_row_card" app/templates/grid/_mobile_grid.html` returns
  at least two matches.
- `./scripts/test.sh tests/test_routes/test_grid.py -v` green.
- `pylint app/ --fail-on=E,F` clean.
- Full suite green.
- Manually: capture rendered HTML of /grid at 375x812 (Firefox responsive
  mode, iPhone XS) pre-commit. After this commit re-render and diff. Mobile
  cards byte-identical modulo whitespace.
- Manually tap a card in Firefox responsive mode -- the existing
  bottom-sheet open still works (data-mobile-txn-id preserved by macro;
  mobile_grid.js:65-75 still binds to it).
- Transfer-shadow case: tap a transfer card -- still opens the transfer
  bottom sheet (data-mobile-xfer-id preserved).

If anything is unclear, ASK.
```

---

## Phase 2 -- Mobile grid UX rewrite

### Commit 5 -- `feat(mobile-grid): nav-pills tab scaffold for This Period / Plan`

**Prereqs on dev:** Commits 1-4 merged (Phase 1 complete). **Touches:**
app/templates/grid/_mobile_grid.html.

```text
You are executing Commit 5 of the Shekel mobile-first v3 implementation in a
fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/implementation_plan_mobile_v3.md (Sections 0-8 for context; Section 9
  "Commit 5 -- nav-pills tab scaffold for This Period / Plan" for the A-H
  specification; Section 2 D-A for the tab layout decision; Section 1 rule 7
  for the 44 px touch-target floor)
- @docs/audits/financial_calculations/remediation_follow_up_common.md (apply
  rules and work summary format -- mandatory)
- @CLAUDE.md (Rules)
- @docs/coding-standards.md (HTML / Jinja2 Templates; CSS section)
- @docs/testing-standards.md
- @app/templates/grid/_mobile_grid.html (read in full as it stands after
  Commit 4 -- the body at lines 22-249 is what wraps into the "Plan" tab in
  this commit; the macros now drive the matching)
- @app/templates/base.html (verify Bootstrap 5 bundle loads at line 283 so
  tab JS is available)

Objective: structural setup. Wrap the existing _mobile_grid.html body in a
Bootstrap nav-pills tab container with two tab-panes:
  - "This Period" -- a placeholder for now (Commit 6 populates it).
  - "Plan" -- contains the existing flow verbatim (indented one level).
Default active tab in this commit is "Plan" (preserves the existing flow as
the user-visible default until Commit 6 ships the new partial).

No JS change. mobile_grid.js's existing handlers continue to work because the
"Plan" tab renders the existing markup with the same IDs (Bootstrap's
tab.show() toggles display:none on the inactive pane; IDs remain valid).

Tab markup template (re-verify Bootstrap class names against the local
Bootstrap version before pasting):

  <div class="d-md-none" id="mobile-grid">
    <ul class="nav nav-pills nav-fill mb-3" role="tablist">
      <li class="nav-item" role="presentation">
        <button class="nav-link" id="mobile-tab-this-period"
                data-bs-toggle="tab" data-bs-target="#mobile-this-period"
                type="button" role="tab"
                aria-controls="mobile-this-period" aria-selected="false">
          This Period
        </button>
      </li>
      <li class="nav-item" role="presentation">
        <button class="nav-link active" id="mobile-tab-plan"
                data-bs-toggle="tab" data-bs-target="#mobile-plan"
                type="button" role="tab"
                aria-controls="mobile-plan" aria-selected="true">
          Plan
        </button>
      </li>
    </ul>

    <div class="tab-content">
      <div class="tab-pane fade" id="mobile-this-period" role="tabpanel"
           aria-labelledby="mobile-tab-this-period">
        <p class="text-muted text-center py-5">
          Loading current period...
        </p>
      </div>
      <div class="tab-pane fade show active" id="mobile-plan"
           role="tabpanel" aria-labelledby="mobile-tab-plan">
        {# existing _mobile_grid.html body lines 22-249, indented one level #}
      </div>
    </div>
  </div>

Touch targets on the nav-link buttons must be >= 44x44 (Section 1 rule 7);
Bootstrap's default nav-pill padding is below this floor on small viewports.
Add a min-height: 44px rule in app.css (mobile media query) only if visual
check confirms the tab buttons are short of the floor.

Files this commit touches:
- app/templates/grid/_mobile_grid.html
- app/static/css/app.css (only if needed to lift tab buttons to 44 px;
  verify before writing)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End the
session with the work summary using labels A through M verbatim. G is "n/a".

Specific verification gates for this commit:
- `grep -nE "<ul class=\"nav nav-pills nav-fill" app/templates/grid/_mobile_grid.html`
  returns one match.
- `grep -n "data-bs-target=\"#mobile-this-period\"" app/templates/grid/_mobile_grid.html`
  returns one match; same for `#mobile-plan`.
- Default active tab is "Plan": the mobile-tab-plan button carries `active`
  and aria-selected="true"; the mobile-plan tab-pane has `show active`.
- `./scripts/test.sh tests/test_routes/test_grid.py -v` green.
- `pylint app/ --fail-on=E,F` clean.
- Full suite green.
- Manually: open /grid at 375x812 (Firefox responsive mode). Two tabs at the
  top of the mobile grid; "Plan" active. Tap "This Period" -- placeholder
  visible. Tap "Plan" -- existing flow returns.
- Swipe gestures on the "Plan" tab still work (period nav unchanged).
- Bottom-sheet tap-to-edit on cards still works.

If anything is unclear, ASK.
```

---

### Commit 6 -- `feat(mobile-grid): _mobile_this_period.html partial with arrows`

**Prereqs on dev:** Commit 5 merged. **Touches:** new partial + tab activation flip in
_mobile_grid.html.

```text
You are executing Commit 6 of the Shekel mobile-first v3 implementation in a
fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/implementation_plan_mobile_v3.md (Sections 0-8 for context; Section 9
  "Commit 6 -- _mobile_this_period.html partial with arrows" for the A-H
  specification; Section 2 D-A for the layout decision; Section 2 D-B for the
  shared-with-companion contract that Commit 13 builds on; Section 4 for the
  Pattern -> canonical implementation map row for "Row -> mobile card")
- @docs/audits/financial_calculations/remediation_follow_up_common.md (apply
  rules and work summary format -- mandatory)
- @CLAUDE.md (Rules; especially rule 2 "Read before you write" and rule 10
  "Understand before you change")
- @docs/coding-standards.md (HTML / Jinja2; CSS)
- @docs/testing-standards.md
- @app/templates/grid/_mobile_grid.html (read in full as it stands after
  Commit 5; the "This Period" tab-pane placeholder and the "Plan" tab-pane's
  existing content are both in scope context; lines 42-247 of the original
  per-period panel structure are the source layout for the new partial)
- @app/templates/grid/_grid_row_macros.html (render_row_card from Phase 1)
- @app/routes/grid.py (verify all_periods exists in render_template kwargs;
  if not, add it as a context entry with the same shape as periods but the
  full window the periodicity selector would expose -- typically the route
  already passes this for the existing desktop selector; re-grep)
- @app/templates/grid/grid.html (verify how the existing desktop period
  selector at lines 24-49 builds its [<] [>] hrefs so the new mobile arrows
  match the convention)
- @app/static/js/mobile_grid.js (read in full -- 85 lines; the existing
  period-nav handlers stay; the new arrows in the partial render as
  hash-routed <a> links so no new JS is required in this commit)

Objective: a new partial that renders ONE pay period as a single panel,
defaulting to the current period. Used inside the "This Period" tab of
_mobile_grid.html; the partial will also be included by the companion route
in Commit 13.

The partial:
  - period nav header: [<] (link to /grid?periods=1&offset=N-1) + label +
    date range + [>] (link to /grid?periods=1&offset=N+1). Hrefs use the
    same offset convention as the existing desktop selector. Add #this-period
    fragment so the page lands on the right tab after the GET (a JS handler
    in mobile_grid.js -- can be inlined here or deferred -- reads
    location.hash on DOMContentLoaded and calls Bootstrap's
    `Tab.getOrCreateInstance(button).show()` for the matching pill).
  - income section -- a collapsible card listing income rows via
    render_row_card (matched_by_row_period scoped to the single period).
  - expense section -- mirrored expense card.
  - net cash flow bar (copy from _mobile_grid.html:224-231).
  - projected balance card (copy from _mobile_grid.html:233-247).

Expected Jinja context (document at the top of the partial in a
`{# ... #}` comment block per CLAUDE.md docstring expectations):
  periods (list[PayPeriod] -- partial uses periods[0])
  income_row_keys, expense_row_keys (list[RowKey])
  matched_by_row_period (dict from Phase 1)
  entry_sums (dict[txn_id -> entry summary])
  subtotals (dict[period_id -> PeriodSubtotal]) -- optional; gracefully
    omit the section if undefined (Section 12 Q-2; preserves the companion
    contract)
  balances (dict[period_id -> Decimal]) -- optional; same treatment
  all_periods (list[PayPeriod] -- only used by Commit 10's <select>; the
    partial can read it as already-present context but does not require it
    for Commit 6)
  can_edit (bool, default True)

The graceful-omit behavior for subtotals/balances is `{% if subtotals is
defined %}...{% endif %}`. Companion (Commit 13) passes neither.

Then in _mobile_grid.html: replace the "This Period" placeholder from
Commit 5 with `{% include "grid/_mobile_this_period.html" %}`. Switch the
default-active tab from "Plan" to "This Period" (move `active`/`show active`
class accordingly and update aria-selected on both buttons).

No financial logic in JS; numbers come from server-rendered partials with
{{ "{:,.0f}".format(...) }} formatting (Section 1 rule 3).

Files this commit touches:
- app/templates/grid/_mobile_this_period.html (NEW)
- app/templates/grid/_mobile_grid.html (placeholder replacement + default
  active tab flip)
- app/static/js/mobile_grid.js (optional: add the hash-routing handler if
  not feasible to inline as a small `{% block scripts %}` snippet -- prefer
  the external JS path per CLAUDE.md and CSP)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End the
session with the work summary using labels A through M verbatim. G is "n/a".

Specific verification gates for this commit:
- `ls app/templates/grid/_mobile_this_period.html` -- file exists.
- `grep -n "_mobile_this_period" app/templates/grid/_mobile_grid.html`
  returns the include match.
- The default active tab is now "This Period" (mobile-tab-this-period has
  `active` + aria-selected="true"; mobile-this-period pane has
  `show active`).
- `./scripts/test.sh tests/test_routes/test_grid.py -v` green.
- `pylint app/ --fail-on=E,F` clean.
- Full suite green.
- Manually: open /grid mobile (375x812) -- "This Period" tab default active;
  shows ONE period (the current one) with income, expenses, net cash flow,
  projected balance.
- Tap [>] -- navigates to /grid?periods=1&offset=1#this-period; lands on the
  "This Period" tab.
- Tap "Plan" tab -- existing flow still works.
- Confirm desktop /grid unaffected (the partial is only included in the
  mobile branch of _mobile_grid.html).

If anything is unclear, ASK.
```

---

### Commit 7 -- `feat(mobile-grid): _mobile_plan.html + inline card action bar`

**Prereqs on dev:** Commit 6 merged. **Touches:** two new partials, one macro update,
_mobile_grid.html, mobile_grid.js.

```text
You are executing Commit 7 of the Shekel mobile-first v3 implementation in a
fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/implementation_plan_mobile_v3.md (Sections 0-8 for context; Section 9
  "Commit 7 -- _mobile_plan.html + inline card action bar" for the A-H
  specification; Section 2 D-C for the inline-action-bar rationale; Section
  3 R-7 for the can_edit=False companion contract)
- @docs/audits/financial_calculations/remediation_follow_up_common.md (apply
  rules and work summary format -- mandatory)
- @CLAUDE.md (Rules; HTMX patterns; CSRF requirement on state-changing forms)
- @docs/coding-standards.md (HTMX Patterns subsection)
- @docs/testing-standards.md
- @app/templates/grid/_mobile_grid.html (the multi-period flow that the new
  "Plan" partial extracts)
- @app/templates/grid/_grid_row_macros.html (render_row_card; this commit
  extends it to emit the action-bar slot)
- @app/static/js/mobile_grid.js (read in full; especially the existing
  tap-to-edit handler at ~lines 65-75 which is removed in this commit)
- @app/routes/transactions.py (verify mark_done endpoint at line 491 and
  inline endpoint at line 909 are stable; the action bar's hx-post targets
  them by url_for)
- @app/static/js/grid_edit.js (especially the txn-expand-btn delegated
  click handler around lines 482-486 -- the [Open Full] button reuses this)
- @app/utils/auth_helpers.py (ownership helpers context -- not modified but
  important to understand mark_done's auth model)

Objective: extract the multi-period scroll view into _mobile_plan.html, and
introduce per-card inline action bars. Each card gains a tap-to-toggle
action bar with three buttons:
  - [Mark Paid] -- when txn.status_id is not STATUS_DONE / STATUS_SETTLED.
    Posts to /transactions/<id>/mark-done with hx-target=#txn-cell-<id>
    hx-swap=outerHTML.
  - [Edit Amount] -- when can_edit. hx-get the existing inline-edit endpoint
    (/transactions/<id>/quick-edit per the existing route), targets the same
    cell.
  - [Open Full] -- when can_edit. Triggers the existing bottom-sheet via
    grid_edit.js's txn-expand-btn delegated handler (data-txn-id on the
    button).

The existing tap-to-edit-card behavior in mobile_grid.js is REPLACED: tap
on a .mobile-txn-card no longer opens the bottom sheet; it expands the
action bar. The bottom sheet is now reached explicitly via [Open Full].

CSRF: every action-bar form must include `{{ csrf_token() }}` directly OR
rely on the existing htmx:configRequest handler in app/static/js/app.js for
HTMX requests (Section 1 rule 6). If a hidden CSRF input is needed, use
the same helper the existing inline forms use -- re-grep before writing.

render_row_card change: emit a wrapper <div class="mobile-card-wrapper">
containing the card <li> followed by an
`{% include "grid/_mobile_card_actions.html" %}` for each matched txn. The
include passes `txn`, `can_edit`, and the URL context the form needs.

mobile_grid.js change (~30 lines): delegated click handler on
.mobile-txn-card that toggles the sibling .mobile-card-action-bar collapse;
closes any other open action bar first. Taps inside the action bar do not
re-toggle (`e.target.closest('.mobile-card-action-bar')` guard). Existing
swipe handlers for period navigation are unchanged (this commit does NOT
add the swipe-left-to-mark-paid -- that is Commit 9).

Files this commit touches:
- app/templates/grid/_mobile_plan.html (NEW; renders the multi-period flow
  via render_row_card)
- app/templates/grid/_mobile_card_actions.html (NEW; ~30 lines)
- app/templates/grid/_grid_row_macros.html (render_row_card emits the
  mobile-card-wrapper + action bar include)
- app/templates/grid/_mobile_grid.html (replace inline Plan tab body with
  the new include)
- app/static/js/mobile_grid.js (remove the previous tap-to-edit handler;
  add the tap-to-toggle-action-bar handler)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End the
session with the work summary using labels A through M verbatim. G is "n/a".

Specific verification gates for this commit:
- `ls app/templates/grid/_mobile_plan.html app/templates/grid/_mobile_card_actions.html`
  -- both exist.
- The action-bar partial includes <form hx-post=... mark_done ...> only
  when txn.status_id is not STATUS_DONE and not STATUS_SETTLED.
  Verify via two render fixtures: one projected txn (Mark Paid present)
  and one settled txn (Mark Paid absent).
- can_edit=False suppresses [Edit Amount] and [Open Full] but keeps
  [Mark Paid] (Test C7-5 of the plan).
- `grep -n "openFullEdit\\|openTransferFullEdit" app/static/js/mobile_grid.js`
  -- the previous tap-to-edit caller paths are removed; the bottom sheet
  is now reached via the [Open Full] button's existing
  txn-expand-btn handler in grid_edit.js (Tests C7-3, C7-4, C7-6).
- Touch-target check: each action-bar button has style="min-height: 44px"
  or equivalent CSS (Section 1 rule 7).
- `./scripts/test.sh tests/test_routes/test_grid.py tests/test_routes/test_transactions.py -v`
  green.
- `pylint app/ --fail-on=E,F` clean.
- Full suite green.
- Manually (Firefox responsive 375x812): tap a projected expense card --
  action bar slides in beneath it; three buttons visible. Tap [Mark Paid]
  -- card updates with done badge; the projected-balance card in "This
  Period" reflects the change (the existing balanceChanged HX-Trigger
  refreshes it).
- Tap another card -- previous bar collapses, new one opens.
- Tap [Edit Amount] -- inline quick-edit input replaces the card content;
  Enter -- card returns updated.
- Tap [Open Full] -- bottom sheet opens (existing flow).
- Switch to "Plan" tab -- same action-bar behavior.

If anything is unclear, ASK.
```

---

### Commit 8 -- `feat(mobile-grid): bottom-sheet drag-to-dismiss + iOS keyboard avoidance`

**Prereqs on dev:** Commits 5-7 merged. **Touches:** app.css + grid_edit.js mobile branch only.

```text
You are executing Commit 8 of the Shekel mobile-first v3 implementation in a
fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/implementation_plan_mobile_v3.md (Sections 0-8 for context; Section 9
  "Commit 8 -- bottom-sheet drag-to-dismiss + iOS keyboard avoidance" for the
  A-H specification; Section 2 D-G for the design decision; Section 3 R-8 on
  passive listener semantics)
- @docs/audits/financial_calculations/remediation_follow_up_common.md (apply
  rules and work summary format -- mandatory)
- @CLAUDE.md (Rules; especially rules 1 "Do it right, not fast" and 13
  "No gold-plating")
- @docs/coding-standards.md (CSS section)
- @docs/testing-standards.md
- @app/static/js/grid_edit.js (read in full -- 583 lines; especially
  positionPopover at line 143, showPopover at line 176, closeFullEdit at
  line 327, and the existing mobile branch gated on window.innerWidth <
  768; verify line numbers via grep before editing)
- @app/static/css/app.css (read the mobile bottom-sheet block at lines
  821-843 and the form-control sizing at 815-819; the @media (max-width:
  767.98px) block that wraps these is the home for the new rules)
- @app/static/js/app.js (verify the existing htmx:configRequest handler and
  any global event delegation context)

Objective: add a draggable handle to the bottom sheet on mobile so it can
be dismissed by swiping down; add iOS keyboard avoidance via the
visualViewport API so the sheet floats above the on-screen keyboard.

CSS additions in the existing mobile-only @media block:

  .txn-full-edit-popover .bottom-sheet-handle {
    width: 32px;
    height: 4px;
    background: var(--bs-secondary-bg);
    border-radius: 2px;
    margin: 8px auto;
    cursor: grab;
    touch-action: none;
  }
  .txn-full-edit-popover {
    transform: translateY(0);
    transition: transform 200ms ease-out;
  }
  .txn-full-edit-popover.dragging {
    transition: none;
  }

JS additions in the mobile branch of positionPopover / showPopover:
  - Inject a new <div class="bottom-sheet-handle"> as the first child of
    the popover element.
  - Attach passive touchstart / touchmove / touchend listeners (R-8:
    passive matches the existing project convention; the listeners do not
    need preventDefault). Track dy relative to startY; clamp dy >= 0 (only
    drag down). Apply transform: translateY(dy + 'px') during the drag.
  - On touchend: if dy > popoverHeight * 0.30, call closeFullEdit();
    otherwise restore transform: translateY(0).
  - Attach a visualViewport.resize listener that adjusts popover.style.bottom
    = (window.innerHeight - visualViewport.height -
       visualViewport.offsetTop) + 'px'.
  - Store the listener ref on popover._adjustForKeyboard so closeFullEdit
    can remove it during tear-down.

closeFullEdit additions: remove the visualViewport listener if attached;
clear popover.style.transform and popover.style.bottom.

Mobile branch is gated on `window.innerWidth < 768` -- desktop must be
unaffected. Verify both branches at edit time.

Files this commit touches:
- app/static/css/app.css (mobile @media block; new rules listed above)
- app/static/js/grid_edit.js (positionPopover, showPopover, closeFullEdit
  mobile branches only)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End the
session with the work summary using labels A through M verbatim. G is "n/a".

Specific verification gates for this commit:
- `grep -n "bottom-sheet-handle" app/static/css/app.css` returns matches
  inside the @media (max-width: 767.98px) block.
- `grep -n "visualViewport" app/static/js/grid_edit.js` returns at least
  three matches (the resize listener attach, the remove call in
  closeFullEdit, and the offsetTop arithmetic).
- `grep -n "passive: true" app/static/js/grid_edit.js` confirms the new
  touch listeners use passive (R-8).
- `./scripts/test.sh` full suite green (no Python change but safety check).
- `pylint app/ --fail-on=E,F` clean.
- Manually (Firefox responsive 375x812 iPhone XS): open /grid; tap a card
  -> [Open Full] -- sheet opens with a 32x4 px handle pill at the top.
- Drag the handle down 50 px and release -- sheet snaps back.
- Drag the handle past 30 % of the sheet's height -- sheet dismisses.
- Open the sheet again; focus the amount input; toggle the keyboard
  emulation on. Sheet repositions above the keyboard; save / cancel
  buttons reachable.
- Real device test (iPhone XS + iPhone 16 Plus in Firefox iOS): repeat the
  drag and keyboard scenarios end to end (WebKit ships visualViewport).
- Desktop unaffected: open /grid at 1920x1080, open popover -- no drag
  handle present, behavior identical to pre-commit.

If anything is unclear, ASK.
```

---

### Commit 9 -- `feat(mobile-grid): swipe-left reveals Mark Paid button on cards`

**Prereqs on dev:** Commit 7 merged. **Touches:** app.css, mobile_grid.js, _grid_row_macros.html.

```text
You are executing Commit 9 of the Shekel mobile-first v3 implementation in a
fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/implementation_plan_mobile_v3.md (Sections 0-8 for context; Section 9
  "Commit 9 -- swipe-left reveals Mark Paid button on cards" for the A-H
  specification; Section 2 D-D for the gesture design; Section 1 rule 10
  for the non-gesture-equivalent requirement; Section 3 R-7 (companions can
  mark paid) and R-8 (passive listener convention))
- @docs/audits/financial_calculations/remediation_follow_up_common.md (apply
  rules and work summary format -- mandatory)
- @CLAUDE.md (Rules; CSRF requirement)
- @docs/coding-standards.md (CSS; JS)
- @docs/testing-standards.md
- @app/static/js/mobile_grid.js (read in full as it stands after Commit 7;
  the existing period-nav swipe at lines 47-59 is the reference for passive
  touch handling)
- @app/static/css/app.css (mobile media query block)
- @app/templates/grid/_grid_row_macros.html (render_row_card -- this commit
  extends it to emit the swipe-action button as a sibling to the card <li>)
- @app/routes/transactions.py (mark_done route, line 491 -- the swipe-
  action POSTs to the same endpoint as the inline action bar's [Mark Paid])

Objective: add a swipe-left-to-reveal pattern to .mobile-txn-card. Card
translates -80 px on a horizontal swipe past 50 px threshold; reveal a
[Paid] button positioned absolutely under the card. Tap the button to
commit Mark Paid (same HTMX form as Commit 7's inline action bar). The
gesture is a SHORTCUT; the inline action bar is the non-gesture path
(Section 1 rule 10).

CSS:

  .mobile-card-wrapper {
    position: relative;
    overflow: hidden;
  }
  .mobile-txn-card {
    position: relative;
    transition: transform 150ms ease-out;
    background: var(--bs-body-bg);
  }
  .mobile-txn-card.swiped {
    transform: translateX(-80px);
  }
  .swipe-action-mark-paid {
    position: absolute;
    top: 0;
    right: 0;
    width: 80px;
    height: 100%;
    background: var(--bs-success);
    color: white;
    display: flex;
    align-items: center;
    justify-content: center;
    border: none;
    font-size: 0.85rem;
  }

The action button must clear the 44 px minimum touch target. width: 80px is
fine on the horizontal axis; verify the card height in browser DevTools
(it must be >= 44 px; if a row is shorter on a long list, consider a
min-height on .mobile-txn-card -- if so, fold that into the work summary
section I as a discovered refinement).

JS (mobile_grid.js): delegated touchstart / touchmove / touchend listeners
with passive: true (R-8). Match the 50 px threshold used by the existing
period-nav swipe at lines 47-59. The Math.abs(dy) > Math.abs(dx) guard
cancels swipe-tracking on dominantly-vertical movement so vertical scroll
still works. On commit, add the .swiped class; on tap outside or
swipe-right past 50 px, remove it. Only one card swiped at a time -- close
any other .swiped before adding.

render_row_card change in _grid_row_macros.html: for each matched txn,
emit:

  <div class="mobile-card-wrapper">
    <button class="swipe-action-mark-paid"
            hx-post="{{ url_for('transactions.mark_done', txn_id=txn.id) }}"
            hx-target="#txn-cell-{{ txn.id }}"
            hx-swap="outerHTML"
            aria-label="Mark {{ txn.name }} paid">
      <i class="bi bi-check2"></i> Paid
    </button>
    <li class="list-group-item ... mobile-txn-card" ...>
      ... existing card content (including the Commit-7 action bar
          include) ...
    </li>
  </div>

CSRF is handled by HTMX's existing htmx:configRequest handler. Verify by
inspecting an outgoing request in DevTools after the manual gates below.

Important: the reveal button must be present even for companion mode
(can_edit=False) -- companions can mark paid per R-7 and existing
companion blueprint precedent.

Files this commit touches:
- app/static/css/app.css (mobile media query)
- app/static/js/mobile_grid.js (new swipe handlers)
- app/templates/grid/_grid_row_macros.html (render_row_card emits the
  swipe-action button sibling)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End the
session with the work summary using labels A through M verbatim. G is "n/a".

Specific verification gates for this commit:
- `grep -n "swipe-action-mark-paid" app/templates/grid/_grid_row_macros.html`
  returns one match (the button emitted by render_row_card).
- `grep -n "passive: true" app/static/js/mobile_grid.js` shows the new
  listeners use passive (R-8 alignment).
- `grep -nE "50" app/static/js/mobile_grid.js` includes both the existing
  period-swipe threshold and the new card-swipe threshold (Test C9-4 of
  the plan).
- companion fixture (Commit 13 prereq is NOT required; you can simulate
  can_edit=False by rendering the macro with that flag directly in a
  fixture): swipe-action button present (R-7).
- `./scripts/test.sh tests/test_routes/test_grid.py tests/test_routes/test_transactions.py -v`
  green.
- `pylint app/ --fail-on=E,F` clean.
- Full suite green.
- Manually (Firefox responsive 375x812): swipe-left on a projected card --
  card slides -80 px; green Paid button revealed.
- Tap the Paid button -- card updates; balance refreshes (the existing
  balanceChanged HX-Trigger).
- Swipe-left on another card -- the first one un-swipes automatically.
- Tap outside a swiped card -- it un-swipes.
- Swipe-right on a swiped card -- it un-swipes.
- Vertical scroll test: swipe-diagonally (down-and-left) on a card -- the
  swipe must NOT register; vertical scroll wins.
- Real device (iPhone XS + 16 Plus in Firefox iOS): repeat all gestures.
- Desktop unaffected: open /grid at 1920x1080 -- no swipe handlers fire
  on mouse; cards render normally.

If anything is unclear, ASK.
```

---

### Commit 10 -- `feat(mobile-grid): jump-to period <select> in This Period header`

**Prereqs on dev:** Commit 6 merged. **Touches:** _mobile_this_period.html (optionally
mobile_grid.js for the CSP-strict fallback).

```text
You are executing Commit 10 of the Shekel mobile-first v3 implementation in
a fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/implementation_plan_mobile_v3.md (Sections 0-8 for context; Section 9
  "Commit 10 -- jump-to period <select> in This Period header" for the A-H
  specification; Section 2 D-E for the native-select decision)
- @docs/audits/financial_calculations/remediation_follow_up_common.md (apply
  rules and work summary format -- mandatory)
- @CLAUDE.md (Rules; especially the CSP note on inline JS handlers; rule 6
  on CSRF -- GET forms do not need CSRF tokens)
- @docs/coding-standards.md (HTML / Jinja2)
- @docs/testing-standards.md
- @app/templates/grid/_mobile_this_period.html (read in full as it stands
  after Commit 6)
- @app/routes/grid.py (verify all_periods is in render_template kwargs;
  verify the offset semantics by reading the desktop selector at
  grid.html:24-49 -- the new <select> matches its href pattern)
- @app/templates/grid/grid.html (the existing desktop period selector at
  lines 24-49 -- the convention reference)

Objective: add a native <select> below the [<] [>] arrow row in
_mobile_this_period.html that lists every period in all_periods. Picking a
non-current option submits a GET form to /grid?periods=1&offset=N. Lands
back on the "This Period" tab via the same hash-routing from Commit 6.

Markup:

  <form action="{{ url_for('grid.index') }}" method="get" class="mb-3">
    <input type="hidden" name="periods" value="1">
    <select name="offset"
            class="form-select form-select-sm"
            aria-label="Jump to pay period">
      {% for p in all_periods %}
        {% set p_offset = p.period_index - current_period.period_index %}
        <option value="{{ p_offset }}"
                {{ 'selected' if p.id == period.id else '' }}>
          {{ p.label }} ({{ p.start_date.strftime('%-m/%-d/%y') }})
        </option>
      {% endfor %}
    </select>
  </form>

Submit behavior: prefer a delegated change listener in mobile_grid.js over
an inline onchange="this.form.submit()" handler. CSP allows inline event
handlers under the current policy, BUT the delegated listener is the
documented project convention per CLAUDE.md "No inline scripts." Use:

  // in mobile_grid.js
  document.addEventListener('change', function (e) {
    if (e.target.matches('select[name="offset"]') &&
        e.target.closest('#mobile-this-period')) {
      e.target.form.submit();
    }
  });

Files this commit touches:
- app/templates/grid/_mobile_this_period.html (insert the form/select)
- app/static/js/mobile_grid.js (delegated change handler)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End the
session with the work summary using labels A through M verbatim. G is "n/a".

Specific verification gates for this commit:
- `grep -n "select name=\"offset\"" app/templates/grid/_mobile_this_period.html`
  returns one match.
- Option list count matches `all_periods | length` for a known fixture
  (Test C10-2).
- The current period's option carries `selected` (Test C10-3).
- `grep -n "select\\[name=\"offset\"\\]" app/static/js/mobile_grid.js`
  returns the delegated-handler match.
- `./scripts/test.sh tests/test_routes/test_grid.py -v` green.
- `pylint app/ --fail-on=E,F` clean.
- Full suite green.
- Manually (Firefox responsive 375x812): tap the <select> -- native picker
  opens; pick a non-adjacent period -- page navigates and lands on the
  "This Period" tab showing that period.
- CSP check: confirm the browser console shows no CSP violation when the
  delegated listener fires.

If anything is unclear, ASK.
```

---

### Commit 11 -- `feat(forms): inputmode="decimal" on 10 monetary inputs`

**Prereqs on dev:** none (independent of other Phase 2 commits). **Touches:** the 10 sites listed in
plan Section 6.4.

```text
You are executing Commit 11 of the Shekel mobile-first v3 implementation in
a fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/implementation_plan_mobile_v3.md (Sections 0-8 for context; Section 9
  "Commit 11 -- inputmode=\"decimal\" on 10 monetary inputs" for the A-H
  specification; Section 6.4 for the authoritative list of 10 sites;
  Section 1 rule 8 for the floor)
- @docs/audits/financial_calculations/remediation_follow_up_common.md (apply
  rules and work summary format -- mandatory)
- @CLAUDE.md (Rules)
- @docs/coding-standards.md (HTML / Jinja2)
- @docs/testing-standards.md
- @app/templates/grid/_transaction_full_edit.html (estimated_amount at
  line 29, actual_amount at line 38 -- re-grep)
- @app/templates/grid/_transaction_full_create.html (estimated_amount at
  line 36, actual_amount at line 44)
- @app/templates/grid/_transaction_quick_edit.html (amount at line 14)
- @app/templates/grid/_transaction_quick_create.html (amount at line 23)
- @app/templates/grid/_anchor_edit.html (anchor_balance at line 25)
- @app/templates/grid/_transaction_entries.html (entry create amount at
  line 43, entry edit amount at line 147)
- @app/templates/grid/grid.html (Add Transaction modal estimated_amount at
  line 324)

Objective: add `inputmode="decimal"` to each of the 10 monetary inputs in
Section 6.4 of the plan. iOS uses a numeric keypad that does NOT include a
decimal point under the default type="number"; inputmode="decimal" fixes
this. Desktop is unaffected (the attribute is ignored on non-touch).

Before editing, re-grep to confirm the 10 sites:

  grep -nE '<input type="number"[^>]*step="0\\.01"' \\
    app/templates/grid/_transaction_*.html \\
    app/templates/grid/_anchor_edit.html \\
    app/templates/grid/grid.html

If the grep returns MORE than 10 sites (someone added a new monetary input
between plan-write and commit-land), include the additional sites and call
them out in the work summary section I (discovered refinements folded in).
If FEWER than 10, stop and report -- a site has moved or been removed and
the plan needs reconciling.

Edit each input to add the attribute exactly once:

  <input type="number" step="0.01" name="..." ...>
  -- becomes --
  <input type="number" step="0.01" inputmode="decimal" name="..." ...>

Do not change any other attribute on these inputs. No CSS, no JS.

Files this commit touches:
- app/templates/grid/_transaction_full_edit.html
- app/templates/grid/_transaction_full_create.html
- app/templates/grid/_transaction_quick_edit.html
- app/templates/grid/_transaction_quick_create.html
- app/templates/grid/_anchor_edit.html
- app/templates/grid/_transaction_entries.html
- app/templates/grid/grid.html

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End the
session with the work summary using labels A through M verbatim. G is "n/a".

Specific verification gates for this commit:
- `grep -cE 'inputmode="decimal"' app/templates/grid/_transaction_*.html app/templates/grid/_anchor_edit.html app/templates/grid/grid.html`
  totals to exactly 10 across those files (the precise per-file counts
  should be auditable: _transaction_full_edit.html = 2,
  _transaction_full_create.html = 2, _transaction_quick_edit.html = 1,
  _transaction_quick_create.html = 1, _anchor_edit.html = 1,
  _transaction_entries.html = 2, grid.html = 1).
- `grep -nE '<input[^>]*step="0\\.01"' app/templates/grid/_transaction_*.html app/templates/grid/_anchor_edit.html app/templates/grid/grid.html | grep -v 'inputmode="decimal"'`
  returns empty (no monetary input without inputmode).
- `./scripts/test.sh tests/test_routes/test_grid.py tests/test_routes/test_transactions.py -v`
  green.
- `pylint app/ --fail-on=E,F` clean.
- Full suite green.
- Manually on a real iPhone (XS or 16 Plus in Firefox iOS): open the full-
  edit form, focus the amount input -- keypad includes the decimal point.
  Repeat for the Add Transaction modal, quick-edit, anchor balance,
  entries create/edit.

If anything is unclear, ASK.
```

---

## Phase 3 -- Companion + grid-adjacent forms

### Commit 12 -- `feat(mobile-sheet): sticky action footer in full-edit popover`

**Prereqs on dev:** Phase 2 complete (Commits 5-11). **Touches:** two templates + app.css.

```text
You are executing Commit 12 of the Shekel mobile-first v3 implementation in
a fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/implementation_plan_mobile_v3.md (Sections 0-8 for context; Section 9
  "Commit 12 -- sticky action footer in full-edit popover" for the A-H
  specification; Section 2 D-G context for the keyboard-avoidance pairing
  with Commit 8's drag handle)
- @docs/audits/financial_calculations/remediation_follow_up_common.md (apply
  rules and work summary format -- mandatory)
- @CLAUDE.md (Rules)
- @docs/coding-standards.md (CSS section; HTML / Jinja2)
- @docs/testing-standards.md
- @app/templates/grid/_transaction_full_edit.html (locate the existing
  button group -- it carries Save, Cancel, Mark Done, Mark Credit, Cancel
  Transaction depending on status)
- @app/templates/grid/_transaction_full_create.html (same shape for the
  create-side popover)
- @app/static/css/app.css (mobile @media block where the bottom-sheet
  rules live)

Objective: wrap the action-button group in each of the two full-edit
popover templates in a new `<div class="popover-action-footer">`. On
mobile-only (< 768 px), the footer is `position: sticky; bottom: 0;` so
buttons stay reachable above the iOS on-screen keyboard. Desktop is
unchanged (no sticky rule).

CSS:

  @media (max-width: 767.98px) {
    .popover-action-footer {
      position: sticky;
      bottom: 0;
      padding: 8px 16px;
      padding-bottom: calc(8px + env(safe-area-inset-bottom));
      background: var(--bs-body-bg);
      border-top: 1px solid var(--bs-border-color);
    }
  }

Do NOT change any button class, handler, or HTMX attribute. Wrap only.

Files this commit touches:
- app/templates/grid/_transaction_full_edit.html (wrap button group)
- app/templates/grid/_transaction_full_create.html (wrap button group)
- app/static/css/app.css (new .popover-action-footer rule inside mobile
  @media)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End the
session with the work summary using labels A through M verbatim. G is "n/a".

Specific verification gates for this commit:
- `grep -n "popover-action-footer" app/templates/grid/_transaction_full_edit.html`
  returns the wrapper match.
- Same in _transaction_full_create.html.
- `grep -n "popover-action-footer" app/static/css/app.css` shows the rule
  is INSIDE the @media (max-width: 767.98px) block (not a top-level rule).
- `./scripts/test.sh tests/test_routes/test_transactions.py tests/test_routes/test_grid.py -v`
  green.
- `pylint app/ --fail-on=E,F` clean.
- Full suite green.
- Manually (Firefox responsive 375x812): open bottom sheet from a card's
  [Open Full]. Focus amount input -- keyboard emulation up. Action footer
  remains visible above the keyboard.
- Real iPhone XS + 16 Plus: same scenario.
- Desktop unaffected: open popover at 1920x1080 -- no sticky footer; the
  buttons render in their pre-commit position.

If anything is unclear, ASK.
```

---

### Commit 13 -- `refactor(grid): extract grid_view_service + companion uses This Period partial + swipe.js shared`

**Prereqs on dev:** Commits 1-12 merged. **Touches:** new service module, new shared JS module, grid

+ companion routes, two templates, optional deprecation of one companion template.

```text
You are executing Commit 13 of the Shekel mobile-first v3 implementation in
a fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/implementation_plan_mobile_v3.md (Sections 0-8 for context; Section 9
  "Commit 13 -- extract grid_view_service + companion uses This Period
  partial + swipe.js shared" for the A-H specification; Section 2 D-B for
  the shared-helper rationale; Section 3 R-4 and R-7 for the companion
  contract; Section 4 for the canonical implementation map row for the
  swipe-action utility; Section 12 Q-2 for the subtotals/balances
  graceful-omit pattern)
- @docs/audits/financial_calculations/remediation_follow_up_common.md (apply
  rules and work summary format -- mandatory)
- @CLAUDE.md (Rules; Architecture; Reference Tables)
- @docs/coding-standards.md (Python; SQL; HTML)
- @docs/testing-standards.md
- @app/routes/grid.py (read in full; especially _build_row_keys at
  lines 68-162 and the Commit-2 matched_by_row_period precomputation that
  this commit refactors into a service module)
- @app/routes/companion.py (read in full; index at 82-123 and period_view
  at 126-163; the existing transactions/period context that the partial
  expects)
- @app/templates/companion/index.html (60 lines; the inline card loop at
  lines 36-38 is replaced with the partial include)
- @app/templates/companion/_transaction_card.html (94 lines; deprecated
  after this commit -- left in place to ease rollback; not deleted)
- @app/templates/grid/_mobile_this_period.html (the partial that companion
  now includes; verify the can_edit=False branch behaves correctly)
- @app/templates/grid/_grid_row_macros.html (render_row_card with
  can_edit=False)
- @app/static/js/mobile_grid.js (the swipe handler from Commit 9, which is
  factored out here)
- @app/static/js/companion.js (25 lines; gains the shared swipe call)
- @app/models/budget.py (Category model; verify the columns used by
  build_row_keys are read-only here)

Objective: extract the row-keys building and matching dict construction
into a NEW pure service module `app/services/grid_view_service.py`. Both
the grid route and the companion route call into it. Companion's index
view replaces its inline card loop with `{% include
"grid/_mobile_this_period.html" with context %}` passing can_edit=False
and omitting subtotals/balances (the partial's graceful-omit branches
handle the latter -- verify Commit 6 ships those branches; if not, add
them here as a discovered refinement and note in section I).

Also factor the swipe-action touch logic from `mobile_grid.js` (added in
Commit 9) into a new shared `app/static/js/swipe.js` exporting a single
helper `attachSwipeAction(root, { onLeftSwipe, threshold = 50 })`. Both
`mobile_grid.js` and `companion.js` call it once on DOMContentLoaded.

The service module must have NO Flask imports -- it takes plain data and
returns plain data (CLAUDE.md "Services are isolated from Flask"). Move:
  - `RowKey` namedtuple (currently in grid.py around lines 39-47)
  - `build_row_keys(transactions, categories, is_income_section)` (the
    extracted _build_row_keys; rename to drop the leading underscore as it
    is now a public service API)
  - `build_matched_by_row_period(transactions, periods, row_keys)` (the
    Commit 2 precomputation, parameterized)

Companion subtotals/balances: per Section 12 Q-2 resolution (c), the
companion route passes NO `subtotals` or `balances`, and the partial
gracefully omits those sections (`{% if subtotals is defined %}...`).
Verify the partial supports this before passing reduced context.

Companion route additions (in app/routes/companion.py::index):

  from app.services import grid_view_service

  # after the existing transactions / period setup:
  all_categories = (
      db.session.query(Category)
      .filter_by(user_id=current_user.linked_owner_id)
      .order_by(Category.group_name, Category.item_name)
      .all()
  )
  income_row_keys = grid_view_service.build_row_keys(
      transactions, all_categories, is_income_section=True,
  )
  expense_row_keys = grid_view_service.build_row_keys(
      transactions, all_categories, is_income_section=False,
  )
  matched_by_row_period = grid_view_service.build_matched_by_row_period(
      transactions, [period], income_row_keys + expense_row_keys,
  )
  return render_template(
      "companion/index.html",
      periods=[period],
      current_period=period,
      income_row_keys=income_row_keys,
      expense_row_keys=expense_row_keys,
      matched_by_row_period=matched_by_row_period,
      entry_sums=entry_data,
      can_edit=False,
  )

Files this commit touches:
- app/services/grid_view_service.py (NEW; pure service, no Flask)
- app/routes/grid.py (replace _build_row_keys + Commit 2 inline
  precomputation with service calls)
- app/routes/companion.py (add the service calls; build the new
  render_template context)
- app/templates/companion/index.html (replace the inline loop at 36-38 with
  the partial include)
- app/templates/companion/_transaction_card.html (deprecated -- leave as-is
  but no longer reachable; do NOT delete unless explicitly asked)
- app/static/js/swipe.js (NEW; the attachSwipeAction helper)
- app/static/js/mobile_grid.js (replace the inlined swipe handler with a
  single attachSwipeAction call)
- app/static/js/companion.js (one new attachSwipeAction call)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End the
session with the work summary using labels A through M verbatim. G is "n/a".

Specific verification gates for this commit:
- `ls app/services/grid_view_service.py app/static/js/swipe.js` -- both
  exist.
- `grep -nE '^(from|import)\\s+flask\\b|\\b(request|session|current_app|render_template)\\b' app/services/grid_view_service.py`
  returns empty (no Flask in services).
- `grep -nE "(quantize|round_money|Decimal\\()" app/services/grid_view_service.py`
  returns empty (the service is pure-template-data; no monetary arithmetic).
- `grep -nE "current_anchor_(balance|period_id)|current_principal|interest_rate" app/routes/grid.py app/routes/companion.py app/services/grid_view_service.py`
  shows NO new reads.
- `grep -n "grid_view_service" app/routes/grid.py app/routes/companion.py`
  shows both routes import + call.
- `grep -n "attachSwipeAction" app/static/js/mobile_grid.js app/static/js/companion.js`
  shows both JS files call it once.
- `./scripts/test.sh tests/test_routes/test_grid.py tests/test_routes/test_companion.py tests/test_static_guards.py -v`
  green.
- `pylint app/ --fail-on=E,F` clean. No new warnings vs baseline.
- Full suite green.
- Manually (Firefox responsive 375x812):
  - /grid mobile -- looks identical to post-Commit-9 state (refactor only).
  - /companion/ as a companion user -- now renders the same card layout as
    the owner mobile grid. Tap a card -- action bar shows only [Mark Paid]
    (no [Edit Amount] or [Open Full]).
  - Swipe-left on a companion card -- the [Paid] reveal still works.
- Desktop /grid unaffected.

If anything is unclear, ASK.
```

---

### Commit 14 -- `feat(mobile-modal): Add Transaction modal-fullscreen-sm-down`

**Prereqs on dev:** Commit 11 merged (inputmode sweep already covers the modal's amount input).
**Touches:** grid.html + app.css.

```text
You are executing Commit 14 of the Shekel mobile-first v3 implementation in
a fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/implementation_plan_mobile_v3.md (Sections 0-8 for context; Section 9
  "Commit 14 -- Add Transaction modal-fullscreen-sm-down" for the A-H
  specification; Section 2 D-F for the rationale)
- @docs/audits/financial_calculations/remediation_follow_up_common.md (apply
  rules and work summary format -- mandatory)
- @CLAUDE.md (Rules)
- @docs/coding-standards.md (HTML / Jinja2; CSS)
- @docs/testing-standards.md
- @app/templates/grid/grid.html (read in full; especially the Add
  Transaction modal at lines 303-362; re-grep current line numbers)
- @app/static/css/app.css (mobile @media block)

Objective: convert the Add Transaction modal to Bootstrap's
`modal-fullscreen-sm-down` layout so it takes over the viewport at
< 576 px. Pin the modal footer to the bottom via `position: sticky` on
mobile-only so the Save button stays reachable above the iOS keyboard.

Markup change (one class addition on the dialog):

  <div class="modal-dialog">
    -- becomes --
  <div class="modal-dialog modal-fullscreen-sm-down">

CSS (in app.css; placed inside the existing < 576 px or mobile @media
block):

  @media (max-width: 575.98px) {
    .modal-fullscreen-sm-down .modal-footer {
      position: sticky;
      bottom: 0;
      background: var(--bs-body-bg);
      padding-bottom: calc(0.75rem + env(safe-area-inset-bottom));
      border-top: 1px solid var(--bs-border-color);
    }
  }

The modal amount input was covered by Commit 11; verify
inputmode="decimal" is present on `<input ... name="estimated_amount" ...>`
at line 324.

Files this commit touches:
- app/templates/grid/grid.html (one-line class addition)
- app/static/css/app.css (new < 576 px rule)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End the
session with the work summary using labels A through M verbatim. G is "n/a".

Specific verification gates for this commit:
- `grep -n "modal-fullscreen-sm-down" app/templates/grid/grid.html` returns
  one match.
- `grep -n "modal-fullscreen-sm-down" app/static/css/app.css` shows the
  rule inside the < 576 px @media block.
- `grep -n 'inputmode="decimal"' app/templates/grid/grid.html` confirms the
  Commit 11 attribute is present on the modal's estimated_amount input.
- `./scripts/test.sh tests/test_routes/test_grid.py -v` green.
- `pylint app/ --fail-on=E,F` clean.
- Full suite green.
- Manually (Firefox responsive 375x812): tap "Add Transaction" -- modal
  takes the viewport. Save button at bottom, sticky. Focus amount input;
  keyboard up -- Save still reachable.
- Real iPhone XS + 16 Plus: same scenario.
- Desktop unaffected: tap "Add Transaction" at 1920x1080 -- centered
  dialog, no fullscreen behavior.

If anything is unclear, ASK.
```

---

## Phase 4 -- Settings + list pages (independent of P3, can land in parallel)

### Commit 16 -- `feat(mobile-settings): sidebar -> shekel-scroll-pills on mobile`

**Prereqs on dev:** none (Phase 4 is independent of Phases 2-3). **Touches:**
settings/dashboard.html.

```text
You are executing Commit 16 of the Shekel mobile-first v3 implementation in
a fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/implementation_plan_mobile_v3.md (Sections 0-8 for context; Section 9
  "Commit 16 -- settings sidebar -> shekel-scroll-pills on mobile" for the
  A-H specification; Section 2 D-K for the rationale; Section 4 for the
  Pattern -> canonical implementation map row for "Settings sidebar ->
  scroll-pills")
- @docs/audits/financial_calculations/remediation_follow_up_common.md (apply
  rules and work summary format -- mandatory)
- @CLAUDE.md (Rules)
- @docs/coding-standards.md (HTML / Jinja2; CSS)
- @docs/testing-standards.md
- @app/templates/settings/dashboard.html (read in full; identify the
  current sidebar block at col-md-3 and the section-link list inside it;
  identify the variable name(s) used to enumerate sections and mark the
  current section)
- @app/static/css/app.css (verify `.shekel-scroll-pills` is defined --
  expected lines ~876-890 per v1 commit 463b188; do not redefine)
- @app/routes/settings.py (verify section routing/key conventions; the
  pills row href targets the same URL pattern as the sidebar links)

Objective: on mobile, replace the stacked sidebar with a horizontal
scroll-pills row above the section content. Desktop is unchanged.

Steps:
1. Wrap the existing `<div class="col-md-3">` sidebar block in a
   `d-none d-md-block` so it disappears on mobile. (If the wrapper class
   uses `col-md-3 col-12` or similar, keep desktop classes and only ADD
   `d-none d-md-block`.)
2. Above the row (or as a sibling at the top of the content area, before
   the row begins), add a `d-md-none mb-3` block containing:

     <ul class="nav nav-pills shekel-scroll-pills" role="tablist">
       {% for section in sections %}
         <li class="nav-item">
           <a class="nav-link {{ 'active' if section.key == current_section else '' }}"
              href="{{ url_for('settings.dashboard', section=section.key) }}">
             {{ section.label }}
           </a>
         </li>
       {% endfor %}
     </ul>

   (Adjust loop / key / label names to match the actual context shape --
   re-read settings/dashboard.html to confirm.)
3. The `.shekel-scroll-pills` class is already defined in app.css from v1
   commit 463b188. Do NOT redefine. Pills are >= 44 px tall by inheriting
   the existing rule -- verify via DevTools and add a min-height: 44px
   inline only if the rule is missing the floor.

Files this commit touches:
- app/templates/settings/dashboard.html

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End the
session with the work summary using labels A through M verbatim. G is "n/a".

Specific verification gates for this commit:
- `grep -n "shekel-scroll-pills" app/templates/settings/dashboard.html`
  returns the pills-row match.
- The sidebar block carries `d-none d-md-block`; the pills row carries
  `d-md-none`.
- Active pill carries `active` for the matching section.
- `./scripts/test.sh tests/test_routes/test_settings.py -v` green.
- `pylint app/ --fail-on=E,F` clean.
- Full suite green.
- Manually (Firefox responsive 375x812): open /settings -- pills row at
  top; content below. Scroll the pills horizontally if N > visible.
- Tap a pill -- navigates to that section.
- Desktop unaffected: open /settings at 1920x1080 -- sidebar visible,
  pills row hidden.

If anything is unclear, ASK.
```

---

### Commit 17 -- `feat(mobile-accounts): cards on mobile in accounts/list.html`

**Prereqs on dev:** none. **Touches:** accounts/list.html.

```text
You are executing Commit 17 of the Shekel mobile-first v3 implementation in
a fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/implementation_plan_mobile_v3.md (Sections 0-8 for context; Section 9
  "Commits 17-20 -- list pages card-on-mobile" for the shared A-H
  specification with per-commit specifics for accounts; Section 2 D-L for
  the per-page-commit rationale (bisectability); Section 4 for the
  Pattern -> canonical implementation map row for "List page -> card layout
  on mobile")
- @docs/audits/financial_calculations/remediation_follow_up_common.md (apply
  rules and work summary format -- mandatory)
- @CLAUDE.md (Rules)
- @docs/coding-standards.md (HTML / Jinja2)
- @docs/testing-standards.md
- @app/templates/accounts/list.html (read in full; identify the table
  structure, the row template, the columns to keep prominent on mobile,
  and the action buttons (Edit / Archive / Detail))
- @app/routes/accounts.py (verify the context shape -- account list, any
  permission flags; do NOT modify)

Objective: convert accounts/list.html to a card layout on mobile while
preserving the desktop table verbatim. The mobile card prominently shows
account name + current balance; secondary fields (account type) below;
actions in a small icon row or dropdown at the bottom.

Steps:
1. Wrap the existing `<table class="table">` (or its `table-responsive`
   container) in `<div class="d-none d-md-block">`.
2. Add a sibling `<div class="d-md-none">` containing one card per row:

     {% for account in accounts %}
       <div class="card mb-2">
         <div class="card-body py-2 px-3">
           <div class="d-flex justify-content-between align-items-baseline">
             <h6 class="card-title mb-0">{{ account.name }}</h6>
             <span class="font-mono fw-bold">
               ${{ "{:,.2f}".format(account.current_balance) }}
             </span>
           </div>
           <small class="text-muted">{{ account.account_type.name }}</small>
           <div class="mt-2 d-flex gap-2">
             <a class="btn btn-sm btn-outline-secondary" style="min-height: 44px;"
                href="{{ url_for('accounts.detail', account_id=account.id) }}">
               Detail
             </a>
             ... Edit / Archive ...
           </div>
         </div>
       </div>
     {% endfor %}

   Adjust loop variable / template URL names to match the actual file --
   re-read before writing.

3. Touch targets: every actionable element in the card must be >= 44 px
   tall (Section 1 rule 7). Use `style="min-height: 44px;"` on buttons
   that Bootstrap's default sizing doesn't reach.

Mobile cards must NOT compute monetary values in Jinja beyond the existing
formatting helpers; the value comes from `account.current_balance` which
is sourced from balance_resolver per Section 1 rule 2.

Files this commit touches:
- app/templates/accounts/list.html

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End the
session with the work summary using labels A through M verbatim. G is "n/a".

Specific verification gates for this commit:
- `grep -n "d-none d-md-block" app/templates/accounts/list.html` returns
  matches on the table wrapper.
- `grep -n "d-md-none" app/templates/accounts/list.html` returns matches
  on the card-list wrapper.
- Each card's prominent fields match the corresponding table row (Test
  C17-3).
- `./scripts/test.sh tests/test_routes/test_accounts.py -v` green.
- `pylint app/ --fail-on=E,F` clean.
- Full suite green.
- Manually (Firefox responsive 375x812): cards readable, no horizontal
  scroll, actions tappable.
- Desktop unaffected at 1920x1080: table renders byte-identical to
  pre-commit.

If anything is unclear, ASK.
```

---

### Commit 18 -- `feat(mobile-salary): cards on mobile in salary/list.html`

**Prereqs on dev:** none. **Touches:** salary/list.html.

```text
You are executing Commit 18 of the Shekel mobile-first v3 implementation in
a fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/implementation_plan_mobile_v3.md (Sections 0-8 for context; Section 9
  "Commits 17-20" shared A-H; Section 9 per-commit specifics line for 18
  including the v1 mobile action dropdown that must be preserved -- commit
  a3e9467 is the reference)
- @docs/audits/financial_calculations/remediation_follow_up_common.md (apply
  rules and work summary format -- mandatory)
- @CLAUDE.md (Rules)
- @docs/coding-standards.md (HTML / Jinja2)
- @docs/testing-standards.md
- @app/templates/salary/list.html (read in full; identify the existing
  mobile action dropdown from v1 commit a3e9467 -- it lives somewhere in
  the row markup and MUST be preserved in the new card layout)
- @app/routes/salary.py (verify list context shape; do NOT modify)

Objective: same pattern as Commit 17 (accounts) for salary profiles. Card
shows profile name + estimated net biweekly prominently; annual salary +
filing status below; actions in the existing v1 dropdown.

Verify the prominent-field choice against the user's mental model: a
biweekly app's most important number on a salary card is the per-paycheck
net, not the annual gross. The hand-computation reference (net biweekly =
annual_gross * (1 - effective_tax_rate) / PAY_PERIODS_PER_YEAR) is NOT
done in the template -- read the value from the existing profile model
field or service-computed context. Re-grep the route to confirm which
field carries net biweekly.

Steps:
1. Wrap the existing salary `<table>` in `<div class="d-none d-md-block">`.
2. Sibling `<div class="d-md-none">` with one card per profile. Preserve
   the v1 mobile action dropdown by emitting it inside the card's action
   area (the same Bootstrap dropdown markup -- copy / paste the existing
   block, do not invent a new one).
3. Touch targets 44 px floor on every interactive element.

Files this commit touches:
- app/templates/salary/list.html

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End the
session with the work summary using labels A through M verbatim. G is "n/a".

Specific verification gates for this commit:
- `grep -n "d-none d-md-block" app/templates/salary/list.html` -- table
  wrapper.
- `grep -n "d-md-none" app/templates/salary/list.html` -- card list.
- The v1 dropdown markup is present in the new card layout.
- `./scripts/test.sh tests/test_routes/test_salary.py -v` green.
- `pylint app/ --fail-on=E,F` clean.
- Full suite green.
- Manually mobile: cards readable; dropdown menu opens; actions tappable.
- Desktop unaffected.

If anything is unclear, ASK.
```

---

### Commit 19 -- `feat(mobile-templates): cards on mobile in templates/list.html`

**Prereqs on dev:** none. **Touches:** templates/list.html.

```text
You are executing Commit 19 of the Shekel mobile-first v3 implementation in
a fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/implementation_plan_mobile_v3.md (Sections 0-8 for context; Section 9
  "Commits 17-20" shared A-H; Section 9 per-commit specifics for 19)
- @docs/audits/financial_calculations/remediation_follow_up_common.md (apply
  rules and work summary format -- mandatory)
- @CLAUDE.md (Rules)
- @docs/coding-standards.md (HTML / Jinja2)
- @docs/testing-standards.md
- @app/templates/templates/list.html (read in full)
- @app/routes/templates.py (verify the list context shape, the actions
  (Edit / Archive / Delete), and recurrence pattern field; do NOT modify)

Objective: same pattern as Commit 17 for transaction templates. Card shows
template name + amount prominently; recurrence pattern + category below;
actions.

Steps:
1. Wrap the existing table in `<div class="d-none d-md-block">`.
2. Sibling `<div class="d-md-none">` card list.
3. Recurrence pattern display: read the existing label rendering from the
   table row (it routes through enums via ref_cache per Section 1 rule 2 of
   CLAUDE.md "Reference Tables") -- do NOT compare against string `name`
   columns or invent new labels.
4. Amount: use the existing `{{ "{:,.2f}".format(...) }}` formatting.

Files this commit touches:
- app/templates/templates/list.html

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End the
session with the work summary using labels A through M verbatim. G is "n/a".

Specific verification gates for this commit:
- `grep -n "d-none d-md-block" app/templates/templates/list.html`
- `grep -n "d-md-none" app/templates/templates/list.html`
- `grep -nE 'recurrence(_pattern)?\\.name ==' app/templates/templates/list.html`
  returns empty (no string-name comparisons; use IDs per CLAUDE.md
  Reference Tables).
- `./scripts/test.sh tests/test_routes/test_templates.py -v` green.
- `pylint app/ --fail-on=E,F` clean.
- Full suite green.
- Manually mobile: cards readable, actions tappable.
- Desktop unaffected.

If anything is unclear, ASK.
```

---

### Commit 20 -- `feat(mobile-transfers): cards on mobile in transfers/list.html`

**Prereqs on dev:** none. **Touches:** transfers/list.html.

```text
You are executing Commit 20 of the Shekel mobile-first v3 implementation in
a fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/implementation_plan_mobile_v3.md (Sections 0-8 for context; Section 9
  "Commits 17-20" shared A-H; Section 9 per-commit specifics for 20)
- @docs/audits/financial_calculations/remediation_follow_up_common.md (apply
  rules and work summary format -- mandatory)
- @CLAUDE.md (Rules; especially Transfer Invariants -- do NOT touch
  budget.transfers data or shadow logic)
- @docs/coding-standards.md (HTML / Jinja2)
- @docs/testing-standards.md
- @app/templates/transfers/list.html (read in full)
- @app/routes/transfers.py (verify the list context shape; the transfer
  service handles all data; this commit is template-only)

Objective: same pattern as Commit 17 for transfers. Card shows transfer
name + amount prominently; from -> to + recurrence below; actions.

Steps:
1. Wrap the existing table in `<div class="d-none d-md-block">`.
2. Sibling `<div class="d-md-none">` card list.
3. From/to display reads existing source_account.name -> destination_account.name
   (already rendered in the desktop table).
4. Amount: use the existing format helper.

Do NOT touch the transfer service or any path that mutates budget.transfers
/ shadow transactions -- this is a list-page rendering change only
(CLAUDE.md Transfer Invariants).

Files this commit touches:
- app/templates/transfers/list.html

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End the
session with the work summary using labels A through M verbatim. G is "n/a".

Specific verification gates for this commit:
- `grep -n "d-none d-md-block" app/templates/transfers/list.html`
- `grep -n "d-md-none" app/templates/transfers/list.html`
- Transfer Invariants (CLAUDE.md): no code path mutates a shadow directly;
  this is template-only.
- `./scripts/test.sh tests/test_routes/test_transfers.py -v` green.
- `pylint app/ --fail-on=E,F` clean.
- Full suite green.
- Manually mobile: cards readable.
- Desktop unaffected.

If anything is unclear, ASK.
```

---

### Commit 21 -- `feat(mobile-retirement): cards + popover tooltips on retirement account table`

**Prereqs on dev:** none. **Touches:** retirement/_retirement_account_table.html.

```text
You are executing Commit 21 of the Shekel mobile-first v3 implementation in
a fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/implementation_plan_mobile_v3.md (Sections 0-8 for context; Section 9
  "Commit 21 -- retirement account table cards + popovers" for the A-H
  specification; the assessment context at
  docs/mobile_friendliness_assessment.md:82-86)
- @docs/audits/financial_calculations/remediation_follow_up_common.md (apply
  rules and work summary format -- mandatory)
- @CLAUDE.md (Rules)
- @docs/coding-standards.md (HTML / Jinja2; CSS)
- @docs/testing-standards.md
- @app/templates/retirement/_retirement_account_table.html (read in full;
  identify every `title="..."` hover tooltip on info icons that needs
  conversion to a Bootstrap popover)
- @app/static/js/app.js (verify the existing global popover-init handler
  from v1 commit 921de65; the popover Bootstrap component requires this
  init -- no new JS needed if the handler already runs on HTMX swaps)

Objective: two changes in one commit, scoped to this template:
1. Same card-on-mobile pattern as Commits 17-20 for the retirement account
   table.
2. For every `title="..."` on an info icon, replace with:
     data-bs-toggle="popover" data-bs-trigger="click focus"
     data-bs-title="..."
   (Hover-only `title` tooltips are inaccessible on touch.)

The popover init JS at app/static/js/app.js already initializes popovers
globally on HTMX swaps (v1 commit 921de65) -- no JS change required. If
adding a new HX-swap target, verify the init still fires.

Files this commit touches:
- app/templates/retirement/_retirement_account_table.html

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End the
session with the work summary using labels A through M verbatim. G is "n/a".

Specific verification gates for this commit:
- `grep -n "d-none d-md-block" app/templates/retirement/_retirement_account_table.html`
- `grep -n "d-md-none" app/templates/retirement/_retirement_account_table.html`
- `grep -nE 'title="[^"]+"' app/templates/retirement/_retirement_account_table.html | grep -v 'data-bs-toggle="popover"'`
  returns empty (every title-bearing info icon converted to popover).
- `./scripts/test.sh tests/test_routes/test_retirement.py -v` green.
- `pylint app/ --fail-on=E,F` clean.
- Full suite green.
- Manually (Firefox responsive 375x812): /retirement -- cards readable;
  tap an info icon -- popover opens; tap elsewhere -- popover closes.
- Desktop unaffected at 1920x1080.

If anything is unclear, ASK.
```

---

### Commit 22 -- `feat(mobile-dashboard): order Bills Due first + loan schedule columns + mark-paid disposition`

**Prereqs on dev:** none. **Touches:** dashboard/dashboard.html + loan/_schedule.html +
(potentially) dashboard mark-paid markup depending on user disposition.

```text
You are executing Commit 22 of the Shekel mobile-first v3 implementation in
a fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/implementation_plan_mobile_v3.md (Sections 0-8 for context; Section 9
  "Commit 22 -- dashboard mobile ordering + loan schedule column hides +
  dashboard mark-paid disposition" for the A-H specification; Section 2
  D-M for the ASK protocol; Section 12 Q-1 for the open question)
- @docs/audits/financial_calculations/remediation_follow_up_common.md (apply
  rules and work summary format -- mandatory)
- @CLAUDE.md (Rules)
- @docs/coding-standards.md (HTML / Jinja2; CSS)
- @docs/testing-standards.md
- @app/templates/dashboard/dashboard.html (read in full; map every
  col-lg-* split to its mobile stacking order; identify the Bills Due card
  and decide its order-first ordinal vs the lg-N current position)
- @app/templates/dashboard/_bill_row.html (read in full; this is where
  the mark-paid form lives -- the disposition decision in step 1 below
  determines whether it changes)
- @app/templates/loan/_schedule.html (read in full; identify the Escrow,
  Extra, and Rate columns that hide on small screens)
- @docs/mobile_friendliness_assessment.md:240 (the assessment line that
  v1 did not complete)

CRITICAL FIRST STEP -- ASK BEFORE EDITING ANY DASHBOARD CODE.

Memory entry project_dashboard_redesign_or_remove.md flags the dashboard
mark-paid feature as redesign-or-remove. Before touching any dashboard
mark-paid markup, USE the AskUserQuestion tool with these options:

  (a) KEEP IT. Apply the same swipe-left-mark-paid pattern as the grid
      cards (~80 lines of additional JS+CSS, mostly reusing swipe.js).
  (b) REDESIGN IT. The bill row becomes a tappable card that opens a
      small confirmation modal with "Mark Paid" plus an "Actual Amount"
      override; aligns with the grid's mark_done route.
  (c) REMOVE IT. Delete the mark-paid form from _bill_row.html; the user
      uses the grid for mark-paid going forward.

Do NOT proceed with sub-problem 3 until the user picks (a), (b), or (c).
Sub-problems 1 and 2 (Bills Due ordering and loan schedule column hides)
can land regardless of the disposition; do them first if the disposition
is still under discussion.

Sub-problems (after disposition is known):

1. Bills Due first on mobile. Add `order-first order-lg-N` to the Bills
   Due card's column wrapper, where N is its current desktop ordinal
   position in the row. Other cards keep their desktop order.

2. Loan schedule columns. In loan/_schedule.html, add `d-none
   d-lg-table-cell` to the Escrow, Extra, and Rate column header `<th>`s
   AND every corresponding `<td>` in the row template. Verify the
   row-iteration loop covers exactly the three columns named in
   docs/mobile_friendliness_assessment.md:240.

3. Dashboard mark-paid -- per the user's disposition from the ASK above:
   - (a) Wire swipe.js (the shared helper from Commit 13) into the bill
         row; emit a `.swipe-action-mark-paid` sibling targeting the
         dashboard's existing mark-paid endpoint via HTMX.
   - (b) Convert the bill row to a card; add a small confirmation modal
         with mark-paid + actual amount override (modal-fullscreen-sm-down
         per D-F).
   - (c) Delete the mark-paid form block from _bill_row.html.

Files this commit touches:
- app/templates/dashboard/dashboard.html (Bills Due ordering)
- app/templates/loan/_schedule.html (column hide classes)
- app/templates/dashboard/_bill_row.html (only under dispositions (a),
  (b), or (c) -- if the user defers the disposition, do not touch this
  file in this commit and note in the work summary section K)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End the
session with the work summary using labels A through M verbatim. G is "n/a".
Section K (Open questions / assumptions) MUST name the disposition the
user picked AND any sub-decisions that flowed from it (e.g., what endpoint
the dashboard mark-paid posts to under (a) -- match the grid's
transactions.mark_done if and only if the dashboard list is sourced from
the same txn ids).

Specific verification gates for this commit:
- `grep -n "order-first" app/templates/dashboard/dashboard.html` returns
  the Bills Due card.
- `grep -n "d-none d-lg-table-cell" app/templates/loan/_schedule.html`
  returns matches on Escrow, Extra, Rate columns (in both header and
  cell positions).
- `./scripts/test.sh tests/test_routes/test_dashboard.py tests/test_routes/test_loan.py -v`
  green.
- `pylint app/ --fail-on=E,F` clean.
- Full suite green.
- Manually (Firefox responsive 375x812): dashboard -- Bills Due at the
  top of the stack.
- Loan schedule -- only Date, Payment, Principal, Interest, Balance
  visible (or whichever the assessment specifies); Escrow / Extra / Rate
  hidden.
- Dashboard mark-paid: per the picked disposition, verify (a) swipe
  reveals mark-paid, (b) tap opens modal, or (c) the mark-paid form is
  gone.
- Desktop unaffected.

If anything is unclear, ASK.
```

---

## Phase 5 -- Nav offcanvas + remaining dashboards + service worker

### Commit 23 -- `feat(mobile-nav): navbar -> offcanvas drawer at <md`

**Prereqs on dev:** none (independent). **Touches:** base.html + app.css.

```text
You are executing Commit 23 of the Shekel mobile-first v3 implementation in
a fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/implementation_plan_mobile_v3.md (Sections 0-8 for context; Section 9
  "Commit 23 -- navbar -> offcanvas drawer at <md" for the A-H
  specification; Section 2 D-H for the rationale; Section 1 rule 7 for the
  44 px touch-target floor on nav items)
- @docs/audits/financial_calculations/remediation_follow_up_common.md (apply
  rules and work summary format -- mandatory)
- @CLAUDE.md (Rules)
- @docs/coding-standards.md (HTML / Jinja2; CSS)
- @docs/testing-standards.md
- @app/templates/base.html (read in full; the navbar lives at lines 39-149;
  toggler at line 44; navbar-collapse at line 48; theme toggle + logout
  form at 127-148; the Bootstrap bundle script tag at line 283 ships the
  offcanvas JS already)
- @app/static/css/app.css (find a sensible place for the offcanvas rules;
  the @media (max-width: 767.98px) block already exists)

Objective: replace the collapsing navbar's <md behavior with a Bootstrap
offcanvas drawer (slide from the left). Above md the offcanvas markup
behaves as a regular inline nav -- Bootstrap handles the transition; the
desktop nav looks unchanged.

Markup restructure (verify against current base.html before pasting; the
exact existing classes and IDs may differ):

  <nav class="navbar navbar-expand-md navbar-dark sticky-top">
    <div class="container-fluid">
      <a class="navbar-brand" href="...">Shekel</a>
      <button class="navbar-toggler" type="button"
              data-bs-toggle="offcanvas"
              data-bs-target="#mainOffcanvas"
              aria-controls="mainOffcanvas"
              aria-expanded="false"
              aria-label="Toggle navigation">
        <span class="navbar-toggler-icon"></span>
      </button>

      <div class="offcanvas offcanvas-start" id="mainOffcanvas"
           tabindex="-1" aria-labelledby="mainOffcanvasLabel">
        <div class="offcanvas-header">
          <h5 class="offcanvas-title" id="mainOffcanvasLabel">Shekel</h5>
          <button type="button" class="btn-close btn-close-white"
                  data-bs-dismiss="offcanvas" aria-label="Close"></button>
        </div>
        <div class="offcanvas-body">
          <ul class="navbar-nav me-auto">
            {# existing nav-item <li> blocks from current lines 54-126
               verbatim #}
          </ul>
          {# existing theme toggle + logout form from current lines 127-148
             verbatim, OR moved here so they live inside the drawer on
             mobile and remain accessible inline on desktop #}
        </div>
      </div>
    </div>
  </nav>

CSS (in app.css; mobile @media block):

  @media (max-width: 767.98px) {
    .offcanvas-start {
      width: 280px;  /* Bootstrap default 400px is too wide on a 375px viewport */
    }
    .offcanvas-body .nav-link {
      min-height: 44px;
      display: flex;
      align-items: center;
    }
  }

The `navbar-expand-md` class is preserved on the outer <nav>. Above md the
offcanvas markup is rendered inline by Bootstrap; below md it slides over.
Verify this behavior in the browser -- if Bootstrap's expand-md does NOT
render the offcanvas inline at lg+ in your local version, adjust the
markup (e.g., wrap the existing navbar-nav in two containers, one for the
collapsing/static desktop path and one for the offcanvas; preserve a
single source of truth via Jinja include if needed).

Theme toggle and logout: every existing handler must keep working in the
offcanvas-body context. Verify by tapping each on mobile and at lg+.

Files this commit touches:
- app/templates/base.html (navbar restructure)
- app/static/css/app.css (offcanvas styling)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End the
session with the work summary using labels A through M verbatim. G is "n/a".

Specific verification gates for this commit:
- `grep -n "offcanvas offcanvas-start" app/templates/base.html` returns
  the offcanvas container match.
- `grep -n 'data-bs-toggle="offcanvas"' app/templates/base.html` returns
  the toggler match.
- The number of nav-items inside .offcanvas-body matches the pre-commit
  collapse navbar (Test C23-3).
- Theme toggle + logout form both present inside .offcanvas-body
  (Test C23-4).
- `./scripts/test.sh` full suite green.
- `pylint app/ --fail-on=E,F` clean.
- Manually (Firefox responsive 375x812): tap hamburger -- drawer slides
  from left. Tap a link -- navigates; drawer closes.
- Tap outside the drawer -- closes.
- Theme toggle inside drawer works.
- Logout works.
- Desktop unaffected at 1920x1080: nav items visible inline; hamburger
  hidden.

If anything is unclear, ASK.
```

---

### Commit 24 -- `refactor(mobile-dashboards): analytics/loan/retirement/investment/debt audit`

**Prereqs on dev:** Commits 17-22 ideally (the table-to-card pattern is established) but technically
independent. **Touches:** multiple dashboard templates.

```text
You are executing Commit 24 of the Shekel mobile-first v3 implementation in
a fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/implementation_plan_mobile_v3.md (Sections 0-8 for context; Section 9
  "Commit 24 -- analytics / loan / retirement / investment / debt audit"
  for the A-H specification; Section 6.3 for the dashboard files in scope;
  docs/mobile_friendliness_assessment.md for the assessment baseline)
- @docs/audits/financial_calculations/remediation_follow_up_common.md (apply
  rules and work summary format -- mandatory)
- @CLAUDE.md (Rules)
- @docs/coding-standards.md (HTML / Jinja2; CSS; JS)
- @docs/testing-standards.md
- @app/templates/analytics/analytics.html and every analytics partial
  loaded by it (identify each Chart.js init site)
- @app/templates/loan/dashboard.html (read in full; identify the
  rate-history sub-table)
- @app/templates/retirement/dashboard.html (verify v1 removed the
  hardcoded width: 7rem on slider inputs from the assessment line 84)
- @app/templates/investment/dashboard.html (verify width-removal fixes
  still in place)
- @app/templates/debt_strategy/dashboard.html (same)

Objective: a small audit-and-fix pass across five dashboards. Each is a
discrete fix; do them in one commit because they are small and grouped.

Sub-tasks:

1. Analytics charts. Locate each Chart.js init call. Ensure the chart
   options object includes `maintainAspectRatio: false`, and the
   containing `<div>` has a non-default `min-height` (250-300 px is a
   reasonable floor; the existing project precedent should be re-greppable).

2. Retirement dashboard. `grep -n "width: 7rem" app/templates/retirement/dashboard.html`
   -- if any matches, replace with responsive Bootstrap utility classes
   (e.g., `class="w-100 w-md-auto"` on the input or its container). If no
   matches, leave the file untouched; note "no fix needed" in the work
   summary section A (verification).

3. Loan rate-history sub-table. Apply the same d-none d-md-block + d-md-none
   card pattern as Commits 17-20.

4. Investment dashboard. Same width-removal check as retirement.

5. Debt strategy dashboard. Same width-removal check.

Files this commit touches (only those that actually need a change):
- app/templates/analytics/analytics.html and analytics partials
- app/templates/loan/dashboard.html
- (potentially) app/templates/retirement/dashboard.html
- (potentially) app/templates/investment/dashboard.html
- (potentially) app/templates/debt_strategy/dashboard.html

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End the
session with the work summary using labels A through M verbatim. G is "n/a".

Specific verification gates for this commit:
- `grep -rn "maintainAspectRatio:\\s*false" app/templates/analytics/` --
  every chart init has the option.
- `grep -n "width: 7rem" app/templates/retirement/dashboard.html app/templates/investment/dashboard.html app/templates/debt_strategy/dashboard.html`
  returns empty.
- `grep -n "d-none d-md-block" app/templates/loan/dashboard.html` returns
  the rate-history table wrapper match.
- `./scripts/test.sh tests/test_routes/test_analytics.py tests/test_routes/test_loan.py tests/test_routes/test_retirement.py tests/test_routes/test_investment.py tests/test_routes/test_debt_strategy.py -v`
  green (skip any test file that does not exist; document in work summary).
- `pylint app/ --fail-on=E,F` clean.
- Full suite green.
- Manually open each dashboard at 375x812 + 1920x1080: charts render
  responsively, no hardcoded widths cropping inputs, rate-history is
  readable.

If anything is unclear, ASK.
```

---

### Commit 25 -- `feat(pwa): service worker + /sw.js passthrough route + registration`

**Prereqs on dev:** none. **Touches:** new sw.js, new route, app.js addition.

```text
You are executing Commit 25 of the Shekel mobile-first v3 implementation in
a fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/implementation_plan_mobile_v3.md (Sections 0-8 for context; Section 9
  "Commit 25 -- service worker + /sw.js passthrough route + registration"
  for the A-H specification; Section 0 "Consequence of getting this wrong"
  point 2 for the static-only caching invariant; Section 2 D-I for the
  design decision; Section 14 "Out of scope" item 1 for the explicit
  exclusion of offline-edit queuing)
- @docs/audits/financial_calculations/remediation_follow_up_common.md (apply
  rules and work summary format -- mandatory)
- @CLAUDE.md (Rules; especially Rule 2 "No financial logic in JS")
- @docs/coding-standards.md (JS section)
- @docs/testing-standards.md
- @app/routes/__init__.py (read in full; identify where new routes are
  registered; decide whether to inline /sw.js here or create
  app/routes/static_pass.py for the passthrough route)
- @app/static/js/app.js (read in full; the SW registration lands at the
  top inside a feature-check guard)
- @app/static/manifest.json (verify it exists; Commit 27 audits it but
  the SW route doesn't depend on its contents)

Objective: add a static-asset-only service worker. Cache static files for
faster repeat loads + installability; NEVER cache HTML or JSON
(financial-correctness invariant from Section 0 of the plan). Cross-
origin requests pass through.

CRITICAL invariant: the fetch handler is cache-first ONLY for
URL prefixes /static/vendor/, /static/css/, /static/js/, /static/img/,
/static/fonts/, /static/manifest.json. For everything else (HTML, JSON,
HTMX partials, even GET requests to app routes), the handler returns
WITHOUT calling event.respondWith() -- network-only pass-through, no
stale fallback. A user offline sees an honest connection error, not
yesterday's balances.

Files (NEW or modified):

  app/static/sw.js (NEW, ~80 lines)
  ----------------------------------
  const CACHE = 'shekel-static-v1';
  const STATIC_PREFIXES = [
    '/static/vendor/',
    '/static/css/',
    '/static/js/',
    '/static/img/',
    '/static/fonts/',
    '/static/manifest.json',
  ];

  self.addEventListener('install', function () {
    self.skipWaiting();
  });

  self.addEventListener('activate', function (event) {
    event.waitUntil(
      caches.keys().then(function (names) {
        return Promise.all(names
          .filter(function (n) {
            return n.startsWith('shekel-static-') && n !== CACHE;
          })
          .map(function (n) { return caches.delete(n); })
        );
      }).then(function () { return self.clients.claim(); })
    );
  });

  self.addEventListener('fetch', function (event) {
    if (event.request.method !== 'GET') return;
    var url = new URL(event.request.url);
    if (url.origin !== self.location.origin) return;
    var isStatic = STATIC_PREFIXES.some(function (prefix) {
      return url.pathname.startsWith(prefix);
    });
    if (!isStatic) return;

    event.respondWith(
      caches.open(CACHE).then(function (cache) {
        return cache.match(event.request).then(function (cached) {
          if (cached) return cached;
          return fetch(event.request).then(function (response) {
            if (response.ok) cache.put(event.request, response.clone());
            return response;
          });
        });
      })
    );
  });

  app/routes/__init__.py (or new app/routes/static_pass.py)
  ----------------------------------------------------------
  Add a passthrough route so the SW is served from /sw.js (scope /), not
  /static/sw.js (scope /static/):

    @app.route('/sw.js')
    def service_worker():
        """Serve sw.js from the static folder at the root scope.

        Required because the browser scopes the service worker to the
        directory containing the worker file.  Serving from
        /static/sw.js would scope to /static/, which excludes app
        routes; we need the scope to be /.
        """
        return send_from_directory(
            current_app.static_folder,
            'sw.js',
            mimetype='application/javascript',
        )

  Match the existing route-registration convention in __init__.py
  (blueprint vs. app-level decorator). If the existing convention uses
  blueprints exclusively, register the route on the main blueprint
  rather than inventing an app-level decorator.

  app/static/js/app.js (top, inside a feature-check guard)
  --------------------------------------------------------
    if ('serviceWorker' in navigator) {
      window.addEventListener('load', function () {
        navigator.serviceWorker.register('/sw.js').catch(function () {});
      });
    }

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End the
session with the work summary using labels A through M verbatim. G is "n/a".

Specific verification gates for this commit:
- `ls app/static/sw.js` -- file exists.
- `grep -n "navigator.serviceWorker.register" app/static/js/app.js`
  returns one match inside a feature-check guard.
- `grep -n "@app.route('/sw.js')" app/routes/__init__.py app/routes/static_pass.py`
  -- one match across the two files (the passthrough is registered).
- `grep -nE "html|/grid\\b|/dashboard\\b" app/static/sw.js | grep -i 'cache.put\\|cache.match'`
  returns empty (no HTML pattern in the cache code path).
- `curl -I http://localhost:5000/sw.js` -> 200 + Content-Type:
  application/javascript (manual gate after the dev server is running).
- `./scripts/test.sh tests/test_routes/ -v` green (the new route does not
  break existing tests; existing assertions on /static/ remain valid).
- `pylint app/ --fail-on=E,F` clean.
- Full suite green.
- Manually (any browser supporting SW): open any page; DevTools ->
  Application -> Service Workers shows scope `/`, status `activated`.
- Reload; DevTools -> Network: static assets show `(ServiceWorker)`; HTML
  / JSON requests show fresh network or disk cache (NOT ServiceWorker).
- DevTools -> Network -> Offline; reload -- HTML request fails
  (network error); static assets still load from cache.
- DevTools -> Application -> Cache Storage -> shekel-static-v1: every
  URL begins with /static/ (no HTML, no JSON).

If anything is unclear, ASK.
```

---

### Commit 27 -- `feat(pwa): manifest maskable icons + Apple-specific 180/167 sizes`

**Prereqs on dev:** Commit 25 merged (the service worker handles the manifest fetch). **Touches:**
manifest.json, new icon files, potentially base.html.

```text
You are executing Commit 27 of the Shekel mobile-first v3 implementation in
a fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/implementation_plan_mobile_v3.md (Sections 0-8 for context; Section 9
  "Commit 27 -- manifest maskable icons + Apple-specific 180/167 sizes"
  for the A-H specification; Section 2 D-J for the no-install-banner
  rationale)
- @docs/audits/financial_calculations/remediation_follow_up_common.md (apply
  rules and work summary format -- mandatory)
- @CLAUDE.md (Rules)
- @docs/coding-standards.md (HTML / Jinja2)
- @docs/testing-standards.md
- @app/static/manifest.json (read in full; current icon entries lack
  `purpose: "any maskable"`)
- @app/templates/base.html (look around line 27 for the
  `apple-touch-icon` link; verify whether it points at icon-180.png /
  icon-167.png; if those files do not exist, this commit creates them)
- @app/static/img/ (list contents; confirm icon-192.png and icon-512.png
  exist from v1)

Objective: audit manifest.json for PWA installability on iPhone and add
two Apple-specific icon sizes if the base.html link references them but
the files do not exist. iOS crops icons unless they declare
`purpose: "any maskable"`.

Steps:

1. Read manifest.json and verify the required keys: name, short_name,
   icons (at least 192 and 512), theme_color, background_color,
   display: standalone, start_url. Add `purpose: "any maskable"` to each
   icon entry. If any required key is missing, add it (use the values
   that v1 settled on -- re-grep git history if unsure).

2. Generate Apple-specific icons if missing. Use ImageMagick:

     convert app/static/img/icon-512.png -resize 180x180 app/static/img/icon-180.png
     convert app/static/img/icon-512.png -resize 167x167 app/static/img/icon-167.png

   If ImageMagick is not available locally, ASK the developer to run
   these commands; do not fall back to a placeholder image.

3. Verify base.html line 27 (re-grep). If the `<link rel="apple-touch-icon">`
   references one of the new sizes, the asset is now present. If it
   references only a 192 / 512 size that v1 generated, add new link
   tags for 180 and 167:

     <link rel="apple-touch-icon" sizes="180x180"
           href="{{ url_for('static', filename='img/icon-180.png') }}">
     <link rel="apple-touch-icon" sizes="167x167"
           href="{{ url_for('static', filename='img/icon-167.png') }}">

Files this commit touches:
- app/static/manifest.json (add purpose: any maskable on icon entries;
  fill in any missing required keys)
- app/static/img/icon-180.png (NEW)
- app/static/img/icon-167.png (NEW)
- app/templates/base.html (only if a new apple-touch-icon link is needed)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End the
session with the work summary using labels A through M verbatim. G is "n/a".

Specific verification gates for this commit:
- `python -c "import json; m=json.load(open('app/static/manifest.json')); assert all('any maskable' in i.get('purpose','') for i in m['icons']), m"`
  succeeds.
- `ls app/static/img/icon-180.png app/static/img/icon-167.png` -- both
  exist.
- `grep -n "apple-touch-icon" app/templates/base.html` returns at least
  one match (for 180 -- the iPhone canonical size).
- `./scripts/test.sh tests/test_routes/ -v` green (no route-test
  regressions).
- `pylint app/ --fail-on=E,F` clean.
- Full suite green.
- Manually: reload a real iPhone via "Add to Home Screen" -- icon
  updates on the next install. Confirm no crop, no white border around
  a transparent icon (the maskable purpose lets iOS pick the safe-zone).

If anything is unclear, ASK.
```

---

### Commit 28 -- `chore(release): mobile v3 full gate + verification appendix`

**Prereqs on dev:** all prior productive commits (1-14, 16-25, 27) merged. **Touches:**
docs/implementation_plan_mobile_v3.md (Section 11 appendix).

```text
You are executing Commit 28 of the Shekel mobile-first v3 implementation in
a fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/implementation_plan_mobile_v3.md (read the full plan; Section 11
  is the empty Verification Appendix that this commit fills in)
- @docs/audits/financial_calculations/remediation_follow_up_common.md (apply
  rules and work summary format -- mandatory)
- @CLAUDE.md (Rules; Definition of Done)
- @docs/coding-standards.md
- @docs/testing-standards.md (Zero Tolerance for Failing Tests; Test Output
  is Evidence)
- @tests/test_static_guards.py (the financial-correctness lock that must
  remain green)

Objective: final gate for the v3 mobile work. No code change beyond
appending Section 11 (Verification Appendix) of the plan with the actual
final state. The appendix records: which commits landed, screenshots /
DevTools captures taken, manual user-acceptance tests passed, OPT-* items
promoted (if any), open questions resolved or carried forward.

Steps:

1. Run the gates (in order):
   a. `pylint app/ --fail-on=E,F` -- must finish clean.
   b. `./scripts/test.sh` -- full suite at -n 12. Must end in
      `N passed`, zero failed / errors / xfailed.
   c. `./scripts/test.sh tests/test_static_guards.py -v` -- the static
      guards must remain green (no new direct reads of
      Account.current_anchor_* or LoanParams.current_principal).

2. Manually walk every workflow from Section 0 of the plan, at the
   following viewports / devices:
   - Firefox Desktop 1920x1080
   - Firefox Responsive iPhone XS 375x812
   - Firefox Responsive iPhone 16 Plus 430x932
   - Real iPhone XS in Firefox iOS
   - Real iPhone 16 Plus in Firefox iOS
   Workflows:
   - Mark a transaction paid (via inline action bar AND swipe-left)
   - Edit a transaction's actual amount (via [Edit Amount] AND
     [Open Full])
   - Add an ad-hoc transaction (via the now-fullscreen modal)
   - Review upcoming bills and projected end balance in "This Period"
     and "Plan" tabs

3. Capture DevTools evidence for the SW cache audit (Section 10 item 7):
   Application -> Cache Storage -> shekel-static-v1 -- every entry
   begins with /static/.

4. Append the Verification Appendix to Section 11 of the plan. Use the
   following sub-structure (or revise to fit what actually happened):

     ## 11. Verification Appendix (filled in at Commit 28)

     ### Commits landed
     - Commits 1-14, 16-25, 27: short title + commit SHA (resolved via
       `git log --oneline --reverse main..HEAD -- docs/implementation_plan_mobile_v3.md`
       or per-commit via the merged PR list).
     - Commits 15, 26: reserved buffers, unused.

     ### Final test gate
     - pytest: `<verbatim final summary line>`
     - pylint: `<verbatim final line>`

     ### Manual verification matrix
     - Desktop Firefox 1920x1080: PASS (no regression)
     - Firefox Responsive 375x812: PASS
     - Firefox Responsive 430x932: PASS
     - Real iPhone XS (Firefox iOS): PASS
     - Real iPhone 16 Plus (Firefox iOS): PASS

     ### SW cache audit
     - shekel-static-v1 entries: all /static/* (NN entries verified)

     ### OPT-* items promoted (if any)
     - OPT-MN: <name> -- folded into Commit N as per <rationale>.

     ### Open questions resolved
     - Q-1 (dashboard mark-paid): resolved at Commit 22 as <a/b/c>.

     ### Open questions carried forward
     - None / or list any.

Files this commit touches:
- docs/implementation_plan_mobile_v3.md (Section 11 appendix)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End the
session with the work summary using labels A through M verbatim. G is "n/a".
Section E must contain the verbatim final summary lines from pytest and
pylint -- this is the load-bearing evidence for the final gate.

Specific verification gates for this commit:
- pylint app/ --fail-on=E,F: clean (verbatim final line in Section F).
- `./scripts/test.sh`: ends in `N passed`, zero failed / errors / xfailed
  (verbatim in Section E).
- `./scripts/test.sh tests/test_static_guards.py -v`: green; no new
  direct anchor / loan-resolver reads since the v3 work began.
- Section 11 of the plan is populated (not the empty placeholder).
- Manual verification matrix records every viewport / device row.

If anything is unclear, ASK.
```

---

## Notes on running the sequence

- Phases 1 -> 2 -> 3 must be serial. Phase 4 (Commits 16-22) and Phase 5
  (Commits 23-27) can run in parallel with each other and with the tail
  end of Phase 3, per the dependency graph in Section 7 of the plan.
- Commits 15 and 26 are RESERVED in the plan -- they have no prompt here.
  If a cross-phase regression appears mid-execution, that work lands in
  the matching reserved slot; if unused, renumber Commits 16-28 down by
  one at Commit 28 time (the plan provides the option but does not
  require it; in practice keeping the numbering stable is simpler).
- One commit per session. The prompts assume a fresh Claude Code session
  for each commit so that re-grep / re-read steps land on current code,
  not on stale context.
- The test template does NOT need rebuilding by this plan (no schema
  changes, no app/ref_seeds.py or app/audit_infrastructure.py edits, no
  migrations -- see plan Section 13).
- Never silently re-pin a test. The plan calls out "Re-pinned tests:
  none" on every commit; if execution surfaces a test that needs
  re-pinning, name the finding and the hand arithmetic in a comment,
  per CLAUDE.md rule 5.
