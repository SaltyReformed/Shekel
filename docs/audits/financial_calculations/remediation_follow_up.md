# Financial-Calculation Audit -- Remediation Follow-up Work

Tracks structural improvements identified during the audit's remediation
commits that are deliberately out of scope for the remediation plan
itself.  Each entry is a self-contained refactor that improves
maintainability without changing financial behavior.  Add work here when
it surfaces; pick from here after Commit 37 (the remediation final
gate) closes.

Cross-references:

- Remediation plan: `remediation_plan.md`
- Commit prompts: `remediation_commit_prompts.md`
- Findings register: `08_findings.md`

---

## F-1. Split `app/routes/accounts.py` into per-domain blueprint modules

- **Surfaced during:** Commit 7 (`fix(accounts): route /accounts checking
  detail through canonical producer (E-25)`), commit `6c09ae8`.
- **Status:** not started; defer until after Commit 37.

### Problem

`app/routes/accounts.py` is 1,499 lines and breaks the project's 1,000-
line per-module ceiling (pylint `C0302 too-many-lines`, pre-existing
warning).  The file holds 21 endpoints spanning five distinguishable
sub-domains separated by banner comments today, which makes the file
hard to read end-to-end and creates merge-conflict surface for every
audit commit that touches it.

### Current sub-domains (banner-delimited in the file)

| Sub-domain | Routes | Lines (approx) | Templates | External coupling |
|---|---|---|---|---|
| Account CRUD | `list_accounts`, `new_account`, `create_account`, `edit_account`, `update_account`, `archive_account`, `unarchive_account`, `hard_delete_account` | ~525 | `accounts/list.html`, `accounts/form.html` | Heavy: `account_service`, `transfer_service`, `pay_period_service`, optimistic-lock contract, the `_validate_update_account` helper, the `_account_type_is_visible` helper |
| Inline anchor edit (list) | `inline_anchor_update`, `inline_anchor_form`, `inline_anchor_display` | ~145 | `accounts/_anchor_cell.html` | Shares anchor-history idempotency machinery with grid `true_up` (F-103 / C-22) |
| Account Type CRUD | `create_account_type`, `update_account_type`, `delete_account_type` | ~160 | `settings/*` | Independent (C-28 multi-tenant guard only); does not touch `Account` rows |
| Anchor true-up (grid) | `true_up`, `anchor_form`, `anchor_display` | ~180 | `grid/_anchor_edit.html` | Shares the same idempotency helper and history-row machinery as `inline_anchor_update`; HX-Trigger to `balanceChanged` |
| Detail pages | `interest_detail`, `update_interest_params`, `checking_detail` | ~255 | `accounts/checking_detail.html`, `accounts/interest_detail.html` | Now routed through `balance_resolver` (Commit 7); Commits 8-10 touch sibling detail surfaces |

### Shared module state to re-home

- `_ANCHOR_HISTORY_UNIQUE_INDEX` constant (the F-103 / C-22 idempotency
  backstop), referenced by both anchor-update endpoints.
- `_visible_account_types`, `_owned_account_type`,
  `_validate_update_account`, `_account_type_is_visible` helpers (the
  C-28 multi-tenant ownership machinery).
- Six Marshmallow schema singletons (`_anchor_schema`, `_create_schema`,
  `_update_schema`, `_type_create_schema`, `_type_update_schema`,
  `_interest_params_schema`).
- The `accounts_bp` blueprint -- all 21 routes register against it.

### Two split options

- **Option A (single blueprint, file split by import).** Estimated
  effort: 1-2 days.  Keep one `accounts_bp` blueprint; split routes
  across `app/routes/accounts/__init__.py` (registers the blueprint),
  `accounts/crud.py`, `accounts/anchor.py`, `accounts/types.py`,
  `accounts/detail.py`.  Each file does `from . import accounts_bp` and
  registers its own decorators.  URLs unchanged; no `url_for` call
  sites edited.  This is the recommended option.
- **Option B (per-sub-domain blueprints with new `url_prefix`s).**
  Estimated effort: 3-4 days.  Adds the cost of updating every
  `url_for("accounts.X")` reference in routes, templates, JS, and
  tests.  ~50 references in routes, ~100-150 in templates and tests.
  High blast radius, all mechanical, but no organisational gain over
  Option A.

### Effort breakdown (Option A)

- 4-6h: factor the shared anchor-history idempotency helper (the
  `IntegrityError + uq_anchor_history_account_period_balance_day`
  handler that lives inline in both `inline_anchor_update` and
  `true_up`) into one named helper in `app/services/entry_service.py`
  or a new `app/services/anchor_service.py`.  The DRY win lands here;
  can be verified before any file moves.
- 2-3h: move helpers and schema singletons into
  `app/utils/account_validation.py` (or similar) and update imports.
- 3-4h: physically split into the per-sub-domain files.
- 1-2h: pylint, run the full suite, fix any forgotten internal
  references (e.g. `app.routes.accounts.<symbol>` test imports).

### Why defer until after Commit 37

1. The split is mostly mechanical movement, but every remaining audit
   commit that touches this file would have to be rebased through the
   split if it lands first -- doubling the merge-conflict surface for
   limited benefit.
2. The first step of the split (extracting the shared anchor-history
   idempotency helper) is a finding-adjacent DRY win and could be
   folded into Commit 16 (loan principal true-up) where the same
   shape is implemented; landing the highest-value extraction without
   taking on the full file split.
3. After Commit 37 the split becomes a single self-contained refactor
   PR with no audit-fix interaction.

### Remaining remediation commits that still touch this file

Quoted here so the split planner knows what conflicts with what:

- Commit 8 -- does NOT touch this file (year-end / net-worth /
  investment / retirement live in their own modules).
- Commit 11 -- test-only; consumes `/accounts/<id>/checking` through
  the test client, no source edit here.
- Commit 15 -- demotes loan param columns; touches loan consumers in
  `app/routes/loan.py`, not this file (no cross-leakage confirmed).
- Commit 16 -- loan principal edit as a dated true-up; implementation
  in `loan.py`.  The existing checking-anchor true-up machinery in
  this file is the reference implementation Commit 16 reads.
- Commit 21 -- semantic `is_settled` hard-delete guard.  Touches
  `archive_helpers` and the hard-delete path; the `hard_delete_account`
  route here is a consumer of those helpers and may need a touch-up.
- Commit 24 -- Marshmallow / DB CHECK reconciliation.  The schema
  singletons here would be re-read against the constraint changes.

### Acceptance criteria for the eventual split PR

- `pylint app/routes/accounts*` shows no `C0302` warnings.
- Every `url_for("accounts.X")` reference still resolves (Option A:
  trivially true; Option B: requires audit of all references).
- The full pytest suite passes with no test edits.
- The F-103 / C-22 anchor-history idempotency handler lives in exactly
  one place (the DRY violation that motivated the split is resolved).

---

## F-2. Remove dead anchor-None branches in `calendar_service`

- **Surfaced during:** Commit 9 (`fix(calendar): month-end balance via
  canonical balance-as-of-date (E-27)`), commit `9871bf7`.
- **Status:** not started; defer until after Commit 37.

### Problem

Three branches in `app/services/calendar_service.py` short-circuit on
the pre-E-19 NULL-anchor state and return zeroed
`MonthSummary` / `YearOverview` objects:

- `get_month_detail` (`:115-120`) -- `if account is None: return
  _empty_month(...)` and `if scenario is None: return
  _empty_month(...)`.
- `get_year_overview` (`:156-162`) -- the matching pair returning
  `_empty_year(year)`.
- `_empty_month` (`:482`) -- builds the zeroed summary used by both
  routes, including a hardcoded `projected_end_balance=Decimal("0")`
  (the "D6-02 fifth anchor-None behavior" the audit named at
  `08_findings.md:751-753`).

After Commit 3 (`fix(anchor): backfill origination anchor`) plus
Commit 4 (`feat(balance): date-anchored anchor resolver`), every
account row has a resolvable anchor and `resolve_anchor` either
returns a valid `AnchorPoint` or raises `RuntimeError`.  The
`account is None` / `scenario is None` paths can only fire when the
upstream resolvers (`resolve_analytics_account`,
`get_baseline_scenario`) themselves return `None`, which would
indicate a separate ownership / scenario-setup defect.  Treating
that case as a silently-zeroed calendar masks the upstream bug; the
zeroed `MonthSummary` ships a `Decimal("0")` calendar to the user
with no error.

### Recommended direction

The remediation plan's locked answer (E-19, Q-16/A-16) is "one anchor
resolver, NULL anchor unreachable."  The calendar should adopt the
same contract: `resolve_analytics_account` raises (or the route
short-circuits with a 404) when the account is gone; missing
scenario likewise.  Concretely:

1. Convert `get_month_detail` / `get_year_overview` to raise a
   specific exception (e.g. `AccountNotResolvableError`) when the
   account / scenario resolvers return `None`.  Routes catch the
   exception and surface a 404 -- matching the project's "404 for
   both 'not found' and 'not yours'" security rule.
2. Delete `_empty_month` and `_empty_year` (no remaining caller).
3. Update tests: any test that relied on the implicit zeroed return
   becomes an explicit 404 assertion or sets up a valid account /
   scenario.

### Why defer

The plan's "anchor-None family" cleanup (D6-02 -> CRIT-01) was
scoped to the balance producers (Commits 3-8) and the calendar
month-end (Commit 9 just landed).  The per-route surfacing of the
unreachable state -- including the calendar's `_empty_*` factories
plus any sibling unreachable-state branches in
`savings_dashboard_service`, `dashboard_service`,
`year_end_summary_service`, etc. -- is a separate sweep, best done
in one PR after Commit 37 so the audit-fix commits and the dead-code
removal do not interleave.

### Acceptance criteria for the eventual PR

- `git grep -n "if.*is None.*return.*_empty_" app/services/` shows
  no matches.
- `git grep -nF 'return Decimal("0")' app/services/calendar_service.py`
  shows no matches (the hardcoded zero is gone with `_empty_month`).
- Calendar / year-overview routes return 404 for an unresolvable
  account or scenario; targeted route tests assert the 404.
- Full pytest suite passes.

---

## F-3. Calendar per-day filter: all-status vs grid Projected-only (W-065)

- **Surfaced during:** Commit 9 (`fix(calendar): month-end balance via
  canonical balance-as-of-date (E-27)`), commit `9871bf7`.
- **Status:** not started; defer until after Commit 37.

### Problem

`app/services/calendar_service.py::_assign_transactions_to_days`
classifies and totals every non-deleted transaction returned by
`_query_transactions_for_range`, with NO Projected-only gate.  The
grid, by contrast, sums only Projected items into its per-period
subtotal and excludes Settled / Cancelled / Credit rows from the
balance-contributing set (E-15 / Commit 2's
`balance_contributing_clause`).  The audit named this drift in
HIGH-02 / W-065:

> "per-day totals use all-status vs grid Projected-only (W-065
> DEFINITION_DRIFT, F-004 cross-ref)" -- `08_findings.md:731`,
> `03_consistency.md:6044`

Worked example: on a single calendar day with one Projected $500
expense and one Settled $500 actual paid on the same date, the
calendar day-cell shows $1,000 of expenses while the grid's
period subtotal counts $500.  Same data, two surfaces, different
totals.  Commit 9 fixed the month-end *balance* (E-27) but left
the per-day classification untouched -- the day-cell display path
was explicitly out of scope.

The relevant code:

- `_assign_transactions_to_days` (`:270-310`) -- no status gate;
  every transaction with a `due_date` in the range hits the
  `day_map` and the `total_income` / `total_expenses` totals.
- `_query_transactions_for_range` (`:194-237`) -- the query also
  returns all-status (only `is_deleted=False` is enforced); a
  status filter would need to land here too to keep the SQL
  predicate aligned with the Python summation predicate (the same
  "SQL and Python must agree" principle that motivated
  `balance_contributing_clause` in Commit 2).

### Recommended direction

Apply the locked E-15 / E-25 predicate to the calendar per-day
classification so the same rule that governs grid subtotals,
`/savings`, `/accounts`, and the dashboard governs calendar day
cells too.  Two design choices remain for the developer to confirm:

- **Choice 1: Projected-only (matches grid).**  Settled / Received
  rows do not appear on calendar day cells.  Loses visibility of
  realized payments at the calendar surface.
- **Choice 2: balance-contributing (Projected + Settled, excludes
  Credit / Cancelled).**  Settled rows appear on the calendar at
  their settled date; Credit / Cancelled hidden.  More aligned
  with what a user expects of a "calendar of activity," but the
  per-day totals will then differ from the grid's
  Projected-only subtotal.

Either choice resolves the W-065 drift; the developer must pick
which surface defines the canon.  The plan's locked E-25 names
the balance producer's Projected-only predicate; the calendar's
per-day *display* may legitimately want a wider filter
(Choice 2), but the choice must be made explicit and documented.

### Implementation sketch

1. Reuse `app.utils.balance_predicates.balance_contributing_clause`
   (Commit 2) in `_query_transactions_for_range` for the SQL
   filter.
2. Re-apply the same predicate in `_assign_transactions_to_days`
   (Python loop) so SQL + Python agree.
3. Tests: pin per-day totals against hand-computed values for a
   fixture with mixed Projected / Settled / Cancelled / Credit
   rows; assert the calendar day-total equals the grid
   period-subtotal (or the documented Choice-2 difference, if the
   developer picks Choice 2).
4. Update `_calendar_month.html` Jinja template if the chosen
   filter changes which entries reach the template (otherwise
   no template change).

### Why defer

W-065 is a definition-drift issue on a display surface; no
developer-reported wrong dollar is bound to it (HIGH-02's
displayed-wrong-dollar evidence is the balance facet, fixed by
Commit 9).  It requires a product decision (Choice 1 vs Choice 2)
which is not on the locked E-NN list, so it should not be folded
into the audit-fix commit chain.

### Acceptance criteria for the eventual PR

- Calendar per-day totals are computed with a documented,
  named status predicate (no inline status-filter logic in
  `_assign_transactions_to_days`).
- The chosen predicate is locked by a test that fails if the
  filter regresses (e.g. a Settled row toggled to Projected
  changes the day total).
- `git grep -nF 'is_deleted.is_(False)' app/services/calendar_service.py`
  shows the calendar query reuses the shared predicate, not an
  inline filter.
- Full pytest suite passes; calendar route tests still green.

---

## F-4. Plan drift: Commit 10's obligations cross-reference is stale

- **Surfaced during:** Commit 10 (`fix(grid): period_subtotal through
  canonical producer (Q-10, E-25)`).
- **Status:** documentation-only; the underlying refactor is already
  scoped as Commit 23 (`refactor(obligations): one monthly-equivalent
  aggregator (E-24, HIGH-05)`).

### Problem

`remediation_plan.md` Section 9 Commit 10 names
`app/routes/obligations.py` as a target for routing through
`balance_resolver.period_subtotal`:

> `app/routes/obligations.py`: the manual subtotal at ~`:331-408`
> also routed through it (same concept).

The current obligations route does NOT compute a per-period
transaction subtotal.  It aggregates `amount_to_monthly(...)`
across `TransactionTemplate` / `TransferTemplate` rows (a monthly-
equivalent rollup, with no `(account, scenario_id, period)`
parameter set).  `period_subtotal` is the wrong API for that
operation: forcing it in would require throwing away the
template-level recurrence math and re-deriving it from the
generated transactions, with no semantic gain.

The audit's own structure already separates the two: F-004
(`period_subtotal`) explicitly cross-links Q-12 for the
obligations-path subtotal and calls it "out of P3-a balance scope;
cross-link only" (`03_consistency.md:405-407`).  The obligations
monthly aggregator is Q-12 / E-24 / Commit 23 territory.

### Recommended direction

Treat the Commit 10 line as a doc-only drift that Commit 23 will
naturally cover.  No code change is needed to close this entry.

### Acceptance criteria for the eventual doc PR

- `remediation_plan.md` Commit 10 section is corrected to drop the
  obligations bullet, or Commit 23's section is updated to
  explicitly reference E-24 instead of period_subtotal.
- No code under `app/routes/obligations.py` is changed by Commit 10.

### Why defer

The plan's prose is wrong but the actual remediation pipeline
already routes the work correctly through Commit 23.  Closing
this entry is a single-paragraph doc edit best folded into the
final remediation pass (Commit 35 / 37) where other doc-drift
corrections are batched.

---

## F-5. Dead `_skip_user_bootstrap_period` global flag in `tests/conftest.py`

- **Surfaced during:** Commit 11 (`test(integration): cross-page balance-
  equality regression lock (HIGH-01)`), commit `4674e7e`.
- **Status:** not started; trivial cleanup, can be folded into any
  conftest-touching commit.

### Problem

`tests/conftest.py:844` declares `global _skip_user_bootstrap_period`
and the `bare_user` fixture toggles it (`:853` -> True, `:857` -> False)
around its `db.session.flush()`, but a project-wide grep
(`grep -rn '_skip_user_bootstrap_period' /home/josh/projects/Shekel/`)
returns matches only inside that one fixture; no `event.listens_for`
or other listener consumes the flag.  Reading the flag's role: it
was intended to suppress an `after_insert` listener on `User` that
inserts a bootstrap pay period via `auth_service.create_user`-style
machinery, so `bare_user` could yield a truly bare user (no period,
no account anchor) for `pay_period_service` tests.  The bootstrap
period is now inserted inline in the `bare_user` body itself rather
than through a listener, so the flag has no reader.

### Recommended direction

Delete the `global` declaration and both assignments; verify the
`bare_user` fixture still yields the bare-user-with-no-period state
the dependent tests expect.

### Why defer

Dead code in a test fixture; zero functional impact.  Out of scope
for HIGH-01 (no financial-calculation correctness signal), and the
removal needs a targeted run of every test that consumes `bare_user`
to confirm no implicit dependency.

---

## F-6. Cross-page balance lock readers could parse rendered HTML directly

- **Surfaced during:** Commit 11 (`test(integration): cross-page balance-
  equality regression lock (HIGH-01)`), commit `4674e7e`.
- **Status:** not started; defer until after Commit 37.

### Problem

In `tests/test_integration/test_cross_page_balance_equality.py`, the
grid and /accounts-checking surface readers (`_grid_value`,
`_accounts_checking_value`) replicate the
`balance_resolver.balances_for(...)` call that the routes make
internally, rather than driving the route via `auth_client.get(...)`
and parsing the rendered HTML for the displayed Decimal.  Both
routes are exercised at the route level for status 200, but the
Decimal extraction itself goes through the producer, so a
hypothetical regression that bypasses `balance_resolver` in the
route handler (e.g. someone re-introduces a hand-rolled balance
loop in `app/routes/grid.py` and forgets to delete the
`balance_resolver.balances_for(...)` call that the test reader
re-runs) would not be caught by the equality assertion.

The four other surface readers (dashboard, /savings, year-end,
calendar) DO call the surface's public service function, so they
catch divergence between the route's exposed value and the
canonical producer.  The grid and /accounts paths are the
remaining gap.

### Recommended direction

Two options, in order of cost:

- **(a) Static lock on the route source** (cheap).  Add a grep-
  style guard in `tests/test_routes/test_grid.py` and
  `tests/test_routes/test_accounts.py` that fails when the route's
  balance-computation block contains anything other than a
  `balance_resolver.balances_for(...)` call -- modeled on the
  existing `test_grid_inline_subtotal_loop_removed`
  (`tests/test_routes/test_grid.py:3794`).
- **(b) HTML parsing in the cross-page readers** (more authentic
  but fragile).  Replace `_grid_value` and `_accounts_checking_value`
  with HTML scans of the route response: grid renders
  `${:,.0f}` in `app/templates/grid/_balance_row.html:26`; the
  checking-detail template renders the same.  HTML parsing across
  these specific templates needs a robust regex anchored to the
  enclosing class (`.balance-row-summary`, the period cell index)
  to avoid false matches.

### Why defer

The current readers achieve the HIGH-01 lock's stated goal -- catch
math-layer divergence (CRIT-01 / F-009 / symptom #1, #5) -- and the
route plumbing is guarded by the status-200 assertion plus the
existing `test_grid_inline_subtotal_loop_removed` static lock.  A
hypothetical route-handler bypass of `balance_resolver` is a
narrower regression than the math-layer one HIGH-01 closes, and
strengthening the readers is gold-plating relative to that scope.
Option (a) above is the lower-risk follow-up if a real regression
in this gap surfaces.

### Acceptance criteria for the eventual PR

- Either the grid and /accounts-checking route handlers carry a
  static-grep guard equivalent to
  `test_grid_inline_subtotal_loop_removed`, OR
- `_grid_value` and `_accounts_checking_value` parse the rendered
  HTML for the balance Decimal and assert the test still bites
  for the symptom-tuple data.
- Full pytest suite passes.

---

## F-7. Jinja-globals ID exposure list duplicated between `app/__init__.py` and `tests/conftest.py`

- **Surfaced during:** Commit 11 (`test(integration): cross-page balance-
  equality regression lock (HIGH-01)`), commit `4674e7e`.  The
  duplication itself predates this commit and carries an inline
  "out of scope for C-28" rationale (unrelated to the financial-
  calculations audit C-28 is a different remediation effort and
  does not own this debt going forward).
- **Status:** not started; defer until after Commit 37.

### Problem

The ID-derived Jinja global registration list lives in two places
that must stay byte-identical:

- `app/__init__.py:168-219` -- 40+ `app.jinja_env.globals[...] =
  ref_cache.<lookup>(...)` lines inside the `create_app` factory.
- `tests/conftest.py:2086-2124` -- the same 40+ lines inside
  `_refresh_ref_cache_and_jinja_globals`, which the `db` fixture
  invokes once per test to re-seat the cache after the per-test
  drop+reclone (Phase 3b).

A missing entry on the test-side breaks every template that
references the omitted constant at request time, with a confusing
Jinja `UndefinedError`; a missing entry on the production side
breaks the same templates in dev / prod.  The current inline
docstring in `_refresh_ref_cache_and_jinja_globals` justifies the
duplication ("`app/__init__.py` runs inside `create_app()` which
is called once per test session, while this helper runs once per
test"), but the project's DRY/SOLID rule
(`feedback_dry_solid_normalized` in user-memory) flags any
two-source-of-truth pattern as a regression risk.

### Recommended direction

Two options, in order of cost:

- **(a) Extract to a shared function** (cheap).  Move the list of
  `(name, lookup_callable)` pairs (or a single function that takes
  an `app` and registers them) into a new helper module, e.g.
  `app/jinja_globals.py`, and have both `create_app` and the
  conftest helper import and call it.  Single source of truth;
  the per-test re-seat still happens but reads from the same list.
- **(b) Refactor `create_app` so the registration is callable
  standalone** (more involved).  Extract a
  `register_ref_id_globals(app)` function inside `app/__init__.py`
  that both `create_app` and the conftest helper call; the
  enums import already pulls from `app.enums`.  Same DRY outcome,
  slightly different layout.

Either way, the acceptance criterion is that adding a new
``ref.<table>`` enum requires editing exactly one list.

### Why defer

The duplication is a quality / DRY violation, not a correctness
bug -- both lists are currently in sync (verified this session via
side-by-side read of `app/__init__.py:169-218` and
`tests/conftest.py:2088-2124`).  A test would have caught any
drift the next time a Jinja Undefined surfaced.  Out of scope for
HIGH-01 (which targets balance-producer correctness, not Jinja
plumbing); best folded into a focused infrastructure PR after
Commit 37 alongside any other test-harness DRY items.

### Acceptance criteria for the eventual PR

- `git grep -n "STATUS_PROJECTED.*ref_cache.status_id" app/ tests/`
  shows exactly one match (the canonical registration site).
- Both `create_app` and the conftest's
  `_refresh_ref_cache_and_jinja_globals` route through the shared
  helper.
- Full pytest suite passes; in particular, the dev server still
  renders templates that reference any of the 40+ constants
  without raising `UndefinedError`.

---

## F-8. Engine anchor-reset clobbers the contractual payment for fixed-rate loans

- **Surfaced during:** Commit 13 (`feat(loan): pure event-derived loan
  resolver (E-18)`), this commit.
- **Status:** not started; defer until after Commit 37.  Currently
  unreachable.

### Problem

`app/services/amortization_engine.generate_schedule` honors the
``anchor_balance`` / ``anchor_date`` parameters by snapping the
running balance and unconditionally re-computing
``monthly_payment`` at the snap:

```python
if (anchor_balance is not None and anchor_date is not None
        and not anchor_applied and pay_date > anchor_date):
    balance = anchor_balance
    anchor_applied = True
    months_left = max_months - month_num + 1
    monthly_payment = calculate_monthly_payment(
        balance, current_annual_rate, months_left,
    )
```

This is correct for ARM loans (``using_contractual`` is False, so
``max_months = remaining_months``), where re-amortization at the
anchor is the intended behavior.

For fixed-rate loans (``using_contractual`` is True), the recompute
is structurally wrong: ``max_months = remaining_months + term_months``
(a generous upper bound for the early-payoff case), so
``months_left`` at the anchor reset is roughly ``2 * term_months``,
which drives ``calculate_monthly_payment`` to produce a payment
about half the correct contractual amount.  Every subsequent row in
the schedule then uses that wrong payment, and the projected
balances drift accordingly.

### Why this is unreachable today

Every production call site that passes ``anchor_balance`` /
``anchor_date`` (Commit-13 resolver included) explicitly skips the
anchor when the loan is fixed-rate -- see
`app/routes/loan.py:476-489` (``floor_anchor_bal``) and the
analogous "is_arm guards" elsewhere.  Commit 13's resolver mirrors
the same pattern by passing ``anchor_balance=None`` for fixed-rate
loans, so the buggy branch stays dormant.

The gap surfaces only when a user records a dated balance trueup
(D-C, Commit 16) on a fixed-rate loan whose anchor balance diverges
from the engine's from-origination projection -- e.g. a borrower
who prepaid principal.  In that case:

- The resolver-derived ``current_balance`` is correct because
  ``_replay_balance_from_anchor`` operates on the primary data
  (anchor + confirmed payments) without depending on the engine.
- The resolver-derived ``monthly_payment`` is correct because
  the fixed-rate branch returns the contractual payment
  unconditionally.
- The resolver-returned ``schedule`` (for display) would have
  post-anchor rows projected from the wrong payment if the
  resolver passed the anchor to the engine.

Commit 13 avoids the bug by passing ``anchor_balance=None`` for
fixed-rate, so post-anchor rows for the fixed-rate trueup case use
the from-origination projection (slightly off from reality in
trueup-corrected balance terms, but correct in P&I-split terms).

### Fix

Gate the payment recompute on ``not using_contractual``:

```python
if (anchor_balance is not None and anchor_date is not None
        and not anchor_applied and pay_date > anchor_date):
    balance = anchor_balance
    anchor_applied = True
    if not using_contractual:
        months_left = max_months - month_num + 1
        monthly_payment = calculate_monthly_payment(
            balance, current_annual_rate, months_left,
        )
```

This is a two-line targeted change with full backward
compatibility for current callers (none of which exercise the
buggy combination) and no behavior change for ARM loans.  After
the fix, the resolver can pass the anchor universally and the
schedule's post-trueup rows for fixed-rate loans will project
from the correct balance with the correct payment.

### Acceptance criteria for the eventual PR

- `app/services/amortization_engine.py`: anchor reset gated on
  ``not using_contractual`` (or equivalent).
- A new engine test: with anchor at origination,
  ``original_principal`` set, no rate changes, the produced
  ``monthly_payment`` for every row equals
  ``calculate_monthly_payment(original_principal, rate, term)``.
- The Commit 13 resolver gains a fixed-rate trueup pathway:
  ``engine_anchor_balance`` / ``engine_anchor_date`` are set for
  both ARM and fixed-rate.
- A new resolver test: fixed-rate loan with mid-loan user_trueup
  whose balance diverges from the engine's projection; the
  schedule's post-trueup rows project from the trueup balance,
  not from the from-origination projection.

---

## F-9. No origination LoanAnchorEvent on new-loan creation

- **Surfaced during:** Commit 14 (`test(loan): settled transfer reduces
  resolved principal (symptom #3)`), this commit.
- **Status:** **resolved by Commit 15.** `app/routes/loan.py:create_params`
  now writes the origination :class:`LoanAnchorEvent` inline after the
  :class:`LoanParams` insert, mirroring `account_service.create_account`'s
  paired-row pattern.  Test fixtures across `tests/test_routes/`,
  `tests/test_services/`, and `tests/test_integration/` were updated
  to use `tests._test_helpers.insert_origination_event` after their
  direct :class:`LoanParams` constructions.  Centralisation into a
  `loan_service.create_loan` helper (option 2 in the original
  direction) remains a possible future cleanup if more callers
  appear; it is not required since the only production write site
  is now compliant.

### Problem

Commit 12 (`feat(loan): append-only loan_anchor_events table +
backfill (E-18)`) inserts an origination event for every
:class:`LoanParams` row that exists at migration time.  No
application code path creates an origination event when a new loan
is created post-migration:

- `app/services/account_service.create_account` is generic across
  account types and does not know about :class:`LoanAnchorEvent`.
- `app/routes/loan.py` has no shared "create-loan" service; the
  loan dashboard setup form writes :class:`LoanParams` directly and
  does not append an event.
- The Commit-12 migration backfill ran once against the existing
  data set and is not re-run on new inserts.

The Commit-13 resolver explicitly requires a non-empty event list
(``_select_latest_anchor`` raises ``ValueError`` when ``anchor_events``
is empty, citing "Commit 12 backfill should have produced an
origination event for every loan").  Once Commit 15 routes consumers
through the resolver, the loan card / debt strategy / net-worth
liability for a freshly created loan will raise instead of rendering.

Verified by ``grep -rn "LoanAnchorEvent" app/routes/ app/services/``
(empty -- only the resolver module references the class) and by the
integration tests in
``tests/test_integration/test_loan_principal_settles.py`` which
must create the origination event explicitly via the helper
``_create_mortgage`` (a production code path would need to do the
same work).

### Recommended direction

Two options:

- **Inline at the existing :class:`LoanParams` insert sites**: each
  route or service that creates a :class:`LoanParams` row also
  inserts an origination :class:`LoanAnchorEvent` in the same
  transaction.  Low touch; matches the pattern of "create the
  paired row inline" used elsewhere.
- **Centralize via a new ``loan_service.create_loan`` helper**: one
  service-level entry point creates :class:`LoanParams` plus the
  origination event together (mirrors
  :func:`account_service.create_account` which writes the
  :class:`Account` plus the origination
  :class:`AccountAnchorHistory` row).  Higher up-front cost,
  better DRY when more callers appear.

The second option also slots cleanly into Commit 16 if the same
service ends up owning user-trueup appends (already extracted into
``anchor_service.apply_anchor_true_up`` per R-7).

### Why defer

Commit 14 is test-only by charter (test that confirmed transfers
reduce principal once the resolver is used).  Adding new-loan
creation logic would mix scopes and obscure the symptom-#3 lock
this commit installs.  Commit 15 must address the gap before it
routes the loan card through the resolver, or it must defer the
loan-card routing until this is fixed.

### Acceptance criteria for the eventual PR

- Every code path that inserts :class:`LoanParams` also inserts an
  origination :class:`LoanAnchorEvent` in the same transaction
  (``anchor_date = origination_date``,
  ``anchor_balance = original_principal``,
  ``source_id = ORIGINATION``).
- A new integration test creates a loan via the production route
  and asserts ``loan_resolver.resolve_loan`` succeeds (does not
  raise ``ValueError`` for empty anchors).
- The Commit 14 integration test helper ``_create_mortgage`` is
  updated to call the shared service path so the test exercises
  the production code path rather than duplicating it.

---

## F-10. Engine internals still read `LoanParams.current_principal` / `.interest_rate`

- **Surfaced during:** Commit 15 (`refactor(loan): demote
  current_principal/interest_rate; route all consumers (E-18)`).
- **Status:** not started; documented as the explicit Commit-17
  follow-up.  Three engine-side functions remain after Commit 15
  routes all display paths through the resolver:

  - `app/services/amortization_engine.py:913` --
    `get_loan_projection(params, ...)` reads
    `params.current_principal` / `params.interest_rate`.  Orphaned
    in production after Commit 15 (zero callers); kept alive by
    its substantial test surface in
    `tests/test_services/test_amortization_engine.py`
    (`TestGetLoanProjection`, `test_get_loan_projection_*`, and
    several ARM tests).
  - `app/services/balance_calculator.py:226` --
    `calculate_balances_with_amortization(...)` reads
    `loan_params.current_principal`.  Orphaned in production after
    Commit 8 routed the savings dashboard through `balance_resolver`;
    kept alive by `tests/test_services/test_balance_calculator_debt.py`.
  - `app/services/loan_payment_service.py:252` --
    `compute_contractual_pi(params)` reads
    `params.current_principal` / `params.interest_rate` to compute
    the "above-P&I excess" boundary used by
    `prepare_payments_for_engine`'s escrow subtraction step.  Still
    actively called by `load_loan_context`, which the resolver
    consumes transitively for its payment feed.

### Problem

The Commit-15 verification gate is
``grep -rn "\.current_principal" app/ | grep -v migrations |
grep -v loan_resolver | grep -v loan_anchor_event`` shows ONLY
write/seed paths.  The three sites above are reads, not writes, and
they sit outside the resolver/event modules.  Commit 15's scope
(per `remediation_plan.md` Section 9 C) explicitly lists only
`routes/loan.py`, `routes/debt_strategy.py`,
`services/savings_dashboard_service.py`, and
`services/year_end_summary_service.py`; the engine internals are
out of scope.

Pragmatically these reads are no longer display contracts -- they
are either dead production code (the first two) or internal
helpers the resolver consumes (the third) -- so they cannot drive
the symptom-#5 family of bugs Commit 15 fixed.  They violate the
strict letter of the gate but not its intent.

### Recommended direction

Commit 17 (`fix(loan): unify per-period/interest/payoff figures via
resolver+round_money (HIGH-08)`) is the natural home:

- **`get_loan_projection`**: delete the function and its
  `LoanProjection` dataclass; rewrite the
  `tests/test_services/test_amortization_engine.py` tests that
  exercise it to drive `loan_resolver.resolve_loan` directly.
- **`calculate_balances_with_amortization`**: delete the function
  and the `test_balance_calculator_debt.py` test class.  Production
  no longer calls it; the canonical balance producer is
  `balance_resolver`.
- **`compute_contractual_pi`**: replace the
  `params.current_principal` read with the resolver's
  `state.current_balance` at the same call site
  (`load_loan_context`).  Currently impossible because
  `load_loan_context` is itself in the resolver's input chain
  (chicken-and-egg); the fix is to compute the escrow-boundary
  heuristic from `params.original_principal` (always available,
  never null) and the BASE rate, which gives the SAME boundary as
  today for fixed-rate loans and a slightly less precise (but
  still safe) boundary for ARMs.

### Why defer

Commit 15's charter is "route all loan consumers through the
resolver and demote the stored columns to nullable seed."  The
engine-internal reads do not break that charter -- they are not
display reads -- and rewriting them touches `amortization_engine`,
`balance_calculator`, and `loan_payment_service`, which are
explicitly out of Commit 15's Section 9 C scope.  Doing the work
here would inflate the diff substantially and mix concerns Commit
17 is purpose-built to address.

### Acceptance criteria for the eventual PR

- `grep -rn "\.current_principal" app/ | grep -v migrations | grep
  -v loan_resolver | grep -v loan_anchor_event | grep -v models/`
  is empty (no engine-internal reads remain).
- The `LoanProjection` dataclass and `get_loan_projection` function
  are deleted from `amortization_engine.py`.
- The `calculate_balances_with_amortization` function is deleted
  from `balance_calculator.py` and `test_balance_calculator_debt.py`.
- `compute_contractual_pi` computes the escrow boundary from
  `original_principal` (no `current_principal` read).
- Targeted + full pytest pass with the same green count.

---

## F-11. Truthiness `or Decimal("0")` on the per-account balance read in `compute_slider_defaults`

- **Surfaced during:** Commit 20 (`fix(retirement): zero is a value not
  missing (E-12, CRIT-04)`).
- **Status:** not started; pick up alongside any future MED-02 sweep of
  inline truthiness on financial values.

### Problem

`app/services/retirement_dashboard_service.py:362` (post-Commit-20 line
number) reads the per-account balance for the weighted-return loop as

```python
bal = proj.get(
    "current_balance", acct.current_anchor_balance
) or Decimal("0")
```

The trailing ``or Decimal("0")`` is truthiness on a monetary value -- the
same pattern Commit 20 removed from the SWR resolver and the
weighted-return gate.  A genuine zero-balance account behaves the same
either way (the account contributes ``0 * rate = 0`` to the numerator
and ``0`` to the denominator regardless), so the line is behaviourally
inert today, but it violates the post-Commit-20 invariant
"no truthiness on financial values" in spirit and is the kind of latent
hazard that surfaces only after an upstream refactor changes what
``proj.get`` can return.

### Why defer

Commit 20's charter is "fix the two specific truthiness sites the audit
cited (CRIT-04 / F-042 / PA-04 / PA-05)."  This third site is not
called out in any finding and is behaviourally inert.  Folding it in
would expand scope and require a separate verification that
``proj.get("current_balance", ...)`` cannot in practice return ``None``
upstream (verified empirically today but not statically enforced).

### Acceptance criteria for the eventual PR

- Replace with an explicit ``is None`` guard or remove the ``or
  Decimal("0")`` once the upstream `_project_retirement_accounts`
  contract is documented as always returning a non-None Decimal.
- Add a unit-test pinning the upstream contract so the guard's removal
  is safe in perpetuity.

---

## F-12. Stylistic truthiness `if params and projection_periods:` in `_project_retirement_accounts`

- **Surfaced during:** Commit 20 (`fix(retirement): zero is a value not
  missing (E-12, CRIT-04)`).
- **Status:** not started; pick up alongside F-11.

### Problem

`app/services/retirement_dashboard_service.py:444` (post-Commit-20 line
number) gates the per-account growth simulation with

```python
if params and projection_periods:
    ...
```

The first conjunct (``params``) is truthiness on a SQLAlchemy
`InvestmentParams` instance, which Python evaluates as ``is not None``
for any non-None object (no ``__bool__`` override on the class).  So
this is not a bug -- it produces the same behaviour as ``params is not
None`` -- but it is stylistically inconsistent with the post-Commit-20
``params is not None`` gate immediately above (in `compute_slider_defaults`)
and could mislead a future reader into thinking truthiness is the
project's convention here.

### Why defer

Behaviourally a no-op; the audit did not cite it.  Worth a one-line edit
in a future style-pass commit but does not justify expanding Commit 20's
diff.

### Acceptance criteria for the eventual PR

- Replace ``if params and projection_periods:`` with ``if params is not
  None and projection_periods:`` so the gate matches the post-Commit-20
  convention in the same file.
- No behaviour change; existing tests stay green.

---

## F-13. `retirement_gap_calculator` does not validate `safe_withdrawal_rate >= 0`

- **Surfaced during:** Commit 20 (`fix(retirement): zero is a value not
  missing (E-12, CRIT-04)`).
- **Status:** not started; product decision required (validation vs.
  clamping vs.  status quo).

### Problem

`tests/test_services/test_retirement_gap_calculator.py:309-311` carries
a pre-existing in-code TODO that pre-dates Commit 20:

```
# BUG: Source does not validate SWR > 0 -- negative SWR silently
# accepted. Should raise ValidationError.
# TODO: Source should validate safe_withdrawal_rate > 0.
```

`app/services/retirement_gap_calculator.calculate_gap` guards the
division by zero (``if safe_withdrawal_rate > 0:``) but silently treats
a negative SWR the same as zero -- ``required_retirement_savings``
collapses to ``ZERO``.  Existing test
``test_safe_withdrawal_rate_negative`` pins that behaviour
intentionally.  The dashboard's `_resolve_swr_fraction` (added in
Commit 20) reads the column as-is, and the column's CHECK constraint
(`ck_user_settings_valid_safe_withdrawal`) admits ``0 <= rate <= 1``
only, so a negative value cannot reach the calculator through the
normal storage path.  The hazard is the slider-override path
(`retirement.gap_analysis` route accepts any float and divides by 100),
where a negative ``swr=`` query parameter would flow through to the
calculator.

### Why defer

Commit 20's charter is CRIT-04 (truthiness on financial values).  The
"validate or clamp negative SWR" decision is a product / UX call that
deserves its own design discussion -- the right answer might be a
Marshmallow validator on the route, a database column CHECK on the
override channel (there isn't one yet), or status quo with a comment
explaining why silent-clamp-to-zero is acceptable.

### Acceptance criteria for the eventual PR

- Decide: reject (422), clamp (silently floor to 0), or document (keep
  current behaviour and write a comment naming the constraint at the
  route layer).
- If reject: add Marshmallow `Range(min=0)` validator on the
  `swr` query parameter; update or remove the
  ``test_safe_withdrawal_rate_negative`` and matching dashboard-route
  tests.
- Remove the ``# BUG:`` / ``# TODO:`` lines in
  ``test_retirement_gap_calculator.py:309-311``.

---

<!-- Add new follow-up entries above this line, numbered F-2, F-3, ... -->
