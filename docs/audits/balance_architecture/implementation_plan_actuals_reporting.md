# Implementation plan: Actuals reporting (Build-Order Step 5)

Date: 2026-07-03. Branch: `feat/actuals-reporting` (worktree off `dev` at `3273907f`).
Companion to `level1_level2_scope_and_fitness.md` (the architecture of record; this is Build-Order
Step 5, the final built step -- "6. Stop.") and to
`adversarial_review_balance_architecture_2026-07-02.md`, whose open items M3 (reader-contract
half), L9 (display-timezone tax attribution, decided 2026-07-03), R9 (this step), and F-1 (the
`from app.models import Posting` fence blind spot) are closed here.

This plan was adversarially reviewed BEFORE approval (a second pass attacking the draft against
live code). Its CRITICAL finding -- a period-granular anchor walk that contradicted the engine's
moment-of-assertion anchor semantics -- is fixed in Section 3; its MEDIUM findings (blast-radius
enumeration, the non-loan liability sign rule, the account-type-change guard) and LOWs are folded
in throughout. Developer approval: 2026-07-03.

## Status banner (as-built ledger; updated per commit)

- C1 (L9 helper + Schedule-A switch) -- NEXT
- C2 (ref rows) -- pending
- C3 (index re-key + lookup hardening) -- pending
- C4 (anchor-equity chart resolver) -- pending
- C5 (walk + reconcile, pure) -- pending
- C6 (lifecycle wiring + oracle supersession) -- pending
- C7 (backfill + boundary migration) -- pending
- C8 (write-side oracle) -- pending
- C9 (reporting service) -- pending
- C10 (routes + templates) -- pending
- C11 (CSV export) -- pending
- C12 (enforcement: W9908 + F-1) -- pending
- C13 (statements oracle + docs close-out + full suite) -- pending

Gates for every commit: targeted tests green + `pylint` 10.00 on touched files + an adversarial
`code-reviewer` pass on the staged diff BEFORE committing; migrations tested up AND down; the full
suite (run alone) is the final gate in C13.

---

## 1. What Step 5 delivers

1. **The trial balance closes app-wide.** Every NON-loan account (checking, savings, investment,
   property, interest-bearing) posts an OPENING equity correction for its earliest
   `AccountAnchorHistory` row and a TRUE-UP correction per later row, mirroring the shipped loan
   genesis pattern. After this, every linked ledger sums to an ABSOLUTE balance and
   `assets == liabilities + equity` is checkable end to end. An anchor assertion is a FACT;
   modeled growth/appreciation/interest between assertions is a derivation and is never posted
   (Option D's fact-versus-derivation line, unchanged).
2. **Three reports on the confirmed ledger**: an income statement (pay-period AND calendar
   month/year windows), a balance sheet (as-of date) with a trial-balance tie-out footer, and CSV
   export -- two new tabs on `/analytics`.
3. **The reader contract** (Section 2) -- the ledger-wide attribution rule M3 required deciding
   once, recorded durably and implemented by every reporting reader, including the L9
   display-timezone rule. The year-end Schedule-A mortgage-interest figure switches to the same
   basis.
4. **Enforcement**: a new W9908 `shekel-ledger-model-bypass` import fence for the ledger models,
   and the test-side F-1 detector closure.

Non-goals: NO cash read switch (app balance screens stay on the `balance_at` seam; the engine
remains the projection authority; the stale-anchor warning is unchanged); M6/R8 multi-scenario
gaps stay deferred (reports read the baseline scenario only); no charts on the report tabs; no
category-group accordion nesting in v1.

### What the ledger comes to MEAN for cash

The engine computes only PROJECTED balances: settled items are excluded everywhere because the
anchor is assumed to already reflect them (`balance_calculator.py:13-14, 103-107`), and a settle
without a fresh true-up merely raises the stale-anchor warning. The app therefore has NO computed
"confirmed cash balance" today -- the anchor itself is the closest thing. Step 5's ledger DEFINES
it: **confirmed balance = the latest anchor assertion + settled facts recorded after that
assertion moment.** At every assertion moment the ledger equals the asserted bank truth; between
assertions it extends forward with settled facts. A later true-up whose delta is $0 means the app
tracked reality perfectly; a non-zero delta is the checking "deliberate cheat" made visible as an
explicit equity adjustment. This is the loan-anchor discipline generalized, not a new invention.

---

## 2. The reader contract (normative; M3's open half)

Every Step-5+ reporting reader follows these rules. Recorded here as the durable rule M3
(`adversarial_review_balance_architecture_2026-07-02.md:352-379`) required.

- **C-1 Whole-entry inclusion.** A reader includes or excludes journal entries whole (in practice
  whole SOURCES, which is stronger), never lone legs. Every entry sums to zero (the deferred
  balanced trigger), so any window's included-leg sum is exactly zero and
  `assets = liabilities + equity` (with derived retained earnings) is an identity, not a
  computation that can drift.
- **C-2 Pay-period windows** filter `JournalEntry.pay_period_id` directly. The R2 storage rule
  (reversal/delta entries carry the pay period of the postings they reverse) already makes
  per-period nets honest with no reader-side compensation.
- **C-3 Calendar windows and as-of dates** group entries by SOURCE and attribute each source's
  per-ledger-account NET to the source's CURRENT paid date, converted to the DISPLAY-timezone
  (America/New_York) civil date -- the decided L9 rule -- falling back to the source's pay period
  `start_date` when `paid_at` is NULL:
  - transaction-linked entries (sources `transaction` and `loan_payment`, both carrying
    `transaction_id`): by `Transaction.paid_at`;
  - transfer-linked entries: by the transfer's INCOME shadow's `paid_at` (Transfer Invariant 3
    mirrors `paid_at` onto both shadows; `posting_service._entry_date` already dates transfer
    entries off exactly this shadow);
  - sourceless corrections (`loan_opening` / `loan_trueup` / `account_opening` /
    `account_trueup`): by `entry_date`;
  - hard-delete residue (an FK-bearing source kind whose concrete FK was SET-NULLed): DROPPED, as
    whole entries -- each sums to zero, so the identity survives the drop exactly; the
    reverse-before-delete discipline nets residue to zero per account, and the statements oracle
    asserts that independently.
- **C-4 Presentation** by natural balance via `ref_cache.ledger_class_is_debit_normal` (its first
  runtime consumer): debit-normal (Asset/Expense) as-is; credit-normal (Liability/Income/Equity)
  negated. `Decimal` only. **Non-loan liability sign rule (stated, not silently normalized):** the
  statements present the ledger faithfully. A non-loan liability whose anchors follow the
  owed-as-negative convention renders as a positive Liabilities line; a positively-anchored one
  would render negative -- unlike `net_worth_kernel.sum_net_worth_at_period`, which
  `-abs()`-normalizes both conventions. The statements oracle pins a negatively-anchored liability
  case; the prod-clone verification checks real data for positively-anchored non-loan liabilities
  and, if any exist, the developer decides data cleanup versus a documented presentation note. No
  `-abs` band-aid.
- **C-5 Retained earnings is derived, never posted** (no closing entries, ever): the Equity
  section carries a computed line equal to the negated cumulative Income+Expense net through the
  as-of date.

Storage stays UTC: `journal_entries.entry_date` keeps the `_civil_settle_date` UTC rule; the
display-timezone conversion happens only in readers, via `app.utils.dates.to_display_civil_date`
(which composes the existing `to_display_date`).

Known, accepted divergences (documented, oracle-pinned): an early-settled source appears in its
(future) pay-period window under C-2 and in its actual paid year under C-3 -- both honest answers
to different questions; the balance sheet is the POSTED ledger's statement and excludes modeled
growth between true-ups (Net Worth's job, via the seam).

---

## 3. Write side: anchor-equity postings for all non-loan accounts

New package `app/services/account_posting_service/` mirroring `loan_posting_service`
(`_walk.py`, `_anchors.py`, `_sync.py`, `__init__.py`); journal entries written only through
`posting_service._emit_balanced_entry`; ledger rows only via the new
`ledger_account_service.get_or_create_anchor_equity_account(user_id, account_id)`.

### 3.1 Chart shape: per-account `anchor_equity` kind (index re-key)

New `LedgerAccountKindEnum.ANCHOR_EQUITY` row shape: `account_id` set, Equity class, snapshot name
`"<account.name> -- Opening"`. Compatible with every existing CHECK (verified;
`ck_ledger_accounts_loan_shape` is `loan_account_id IS NULL OR ...`). The partial unique
`uq_ledger_accounts_account` re-keys to `(account_id, kind_id)` so the linked row and its equity
twin coexist. Rejected: reusing `loan_account_id` (every name/guard says "loan"); one equity
account per user (byte-identical to the deliberately-non-unique orphan shape, racy, loses the
loan-parallel per-account presentation).

Load-bearing hardening that must land BEFORE any writer: `posting_reads._ledger_account_for`
(`.one_or_none()` raises `MultipleResultsFound` the moment a twin exists) and
`ledger_account_service.create_ledger_account_for_account`'s idempotency lookup both gain a
LINKED-kind filter. `archive_helpers.account_has_ledger_postings` deliberately stays kind-agnostic
(correct for Guard 5 -- the twin's legs appear and disappear pairwise with the linked row's).

### 3.2 The walk (MOMENT-granular)

Engine semantics (verified): settled items are excluded from every period's sum -- the anchor is a
moment-of-assertion fact that already reflects all settled activity known at that moment
(`balance_calculator.py:13-14, 103-107`; `apply_anchor_true_up` docstring: "the user is declaring
'my real checking is now $X' -- every past-dated debit purchase is already in that number"). A
period-granular walk would mis-state the balance sheet by every pre-true-up settle in the anchor
period (the pre-approval review's CRITICAL-1). Per (account, scenario):

- **Anchor facts**: all `AccountAnchorHistory` rows ordered by `(created_at, id)` -- matching
  `resolve_anchor`'s max-`created_at` pick. First row = OPENING, rest = TRUE-UPs. Assertion
  instant = the row's `created_at` (UTC).
- **Source facts, read back from the ledger** (never re-derived from transaction rows --
  timing-proof against reverse-before-delete, future-proof for new source kinds): group the
  account's linked-ledger postings, excluding `account_opening`/`account_trueup` sources, by
  SOURCE; each source contributes its CURRENT net and its CURRENT attribution instant = the
  source's `paid_at` (transfers: the income shadow's), falling back to its pay period
  `start_date` at midnight UTC when NULL. A reverted source nets to zero and drops out regardless
  of partition.
- **Per anchor, in order**: `ledger_before = prior correction deltas + SUM(net of sources whose
  attribution instant <= this assertion instant)`; `delta = anchor_balance - ledger_before`
  (ledger-native sign -- holds for Asset AND Liability non-loan accounts; the engine never
  branches on class); legs `{linked: +delta, equity: -delta}`, posting kinds `opening`/`trueup`
  (REUSED -- the journal SOURCE distinguishes account from loan corrections).
- **Why this is correct**: at the true-up chokepoint the fresh delta equals
  `asserted - current live linked total` (every settled source's instant precedes "now"), so the
  go-forward wiring is a plain reconcile-to-target and the pure walk reproduces it after the fact
  (`created_at` and the ledger are immutable). Pre-anchor settles in ANY period are absorbed by
  the opening/true-up deltas; post-assertion settles ride on top. A source reverted after a
  true-up self-heals to the engine's answer. Zero-delta corrections book nothing (a fresh $0
  account mints no entries and no equity row, staying hard-deletable); a stale posted correction
  reverses via its empty-target key.
- **`entry_date` = the anchor's UTC civil date** (the `AnchorPoint.as_of_date` convention and the
  loan-correction precedent). `pay_period_id` = the history row's period (NOT NULL FK; satisfies
  R2 -- the period of what it corrects). Reconcile key `(source_kind_id, entry_date)`; two
  same-day same-kind anchors merge to one target landing on the later value (mirrors the F-103
  unique-index semantics and the loan merge behavior). The late-evening-ET/UTC-day edge on
  `entry_date` is identical to shipped loan corrections; documented.
- Delta emission, posted-leg read-back keyed by `(source_kind_id, entry_date)`, and
  flush-never-commit mirror `loan_posting_service/_anchors.py`. Shared primitives `delta_legs` /
  `summed_posting_legs` move from `loan_posting_service/_common.py` to a new
  `app/services/_posting_reconcile.py`; the loan package re-points.

### 3.3 Lifecycle wiring (exhaustive anchor-writer inventory)

1. `account_service.create_account` (after the ledger pairing): sync all scenarios; loud log +
   skip when no baseline scenario exists (test fixtures only; production users get a baseline at
   registration).
2. `anchor_service.apply_anchor_true_up`: inside the existing try, after
   `clear_entries_for_anchor_true_up` (verified side-effect-free for posted amounts: it flips
   `is_cleared` on PROJECTED parents only and the Step-3 effect formula never reads `is_cleared`),
   before `commit()`.
3. `routes/accounts/crud.py update_account` (the direct history write): extend the guarded
   callable that already runs on `anchor_changed`.
4. `pay_period_admin.reset_pay_periods`: `resync_user_account_anchor_postings(user_id)`
   immediately after the R7 loan resync, same transaction. Post-reset is clean by construction
   (the wipe CASCADEd old entries + history; `_reanchor_accounts` staged one fresh row per
   account; the zero-settled gate guarantees no effects).
5. Effect-time self-heal INSIDE `posting_service` at the tails of `sync_transfer_postings` /
   `sync_transaction_postings` (covers `reverse_postings_before_delete`): when deltas were emitted
   for a non-loan account, resync that (account, scenario) iff `min(source's current attribution
   instant, source's pay-period start-of-day) <= the account's latest anchor instant` -- the
   period-start arm catches revert/move cases whose OLD attribution preceded the anchor.
   Deliberately conservative, cheap (one indexed latest-anchor lookup), oracle-pinned.
   Function-local import with the standard rationale (reverse dependency).
6. `routes/grid.py create_baseline` (the recovery path for baseline-less users): resync the
   user's accounts after minting the baseline so openings are not silently stranded.
7. **Type-change guard**: `account_validation._validate_update_account` refuses an
   `account_type_id` change that crosses the AMORTIZING boundary or flips the linked ledger's
   Asset/Liability class while the account has ledger postings (the Guard-5 pattern). Without it a
   re-typed account strands one correction family and double-counts under the other. Pre-existing
   latent gap; Step 5 makes it visible money, so Step 5 closes it.

Scenario enumeration for account-global events: `{baseline} UNION {scenario_ids of entries with a
posting on the account's linked ledger}` -- the cash analog of the loan rule; R8 owns the residual
multi-scenario policy.

### 3.4 Migrations (three) + deploy hook

1. **Ref rows**: INSERT `ref.posting_sources` `account_opening` / `account_trueup` +
   `ref.ledger_account_kinds` `anchor_equity` (no new posting kinds -- `opening`/`trueup` are
   reused). Enum members + `ref_seeds.py` dual-seed + parity test. Downgrade deletes the rows
   (safe: the boundary migration downgrades first; RESTRICT FKs enforce the order).
2. **Index re-key** (destructive; developer-approved via this plan; `Review:` docstring line):
   drop `uq_ledger_accounts_account`, create `uq_ledger_accounts_account_kind (account_id,
   kind_id) WHERE account_id IS NOT NULL`; model `__table_args__` + taxonomy docstring updates
   (8 -> 9 kinds; the COALESCE display-rule caveat; the CASCADE-impossibility paragraph).
   Downgrade fails loud (RuntimeError with the diagnostic SELECT) if any `anchor_equity` twin
   still exists -- unreachable via the linear chain, defense against out-of-band runs.
3. **Data boundary** (mirror `f3d6b1a8c2e4`): upgrade = documented no-op (forward population is
   the go-forward wiring + deploy hook; the walk needs `ref_cache`/services and the migration
   host runs `create_app(init_ref_cache=False)`). Downgrade deletes `account_opening` /
   `account_trueup` entries (legs cascade) then `anchor_equity` ledger rows, resolving ref ids by
   name, fail-loud.

Deploy hook: extend `scripts/init_database.py` with `backfill_all_account_anchor_postings()`
after the loan backfill -- backfill == go-forward by construction (same sync code); idempotent,
including on a database where go-forward corrections already exist; existing-database path only.

### 3.5 Accepted behavior changes

- Periods holding a non-zero opening/true-up flip lock reason `ACCOUNT_ANCHOR` ->
  `LEDGER_POSTINGS` (the gate keys on `pay_period_id` and precedes `ACCOUNT_ANCHOR`); UI badge
  text shifts accordingly.
- Any account with a non-zero anchor becomes archive-only (Guard 5) the moment its opening posts
  -- identical to shipped loan behavior; $0-anchor accounts stay hard-deletable.
- Rolling edge: an all-future schedule anchors to a future period; its correction can
  `LEDGER_POSTINGS`-lock that period, and a future-dated opening is excluded from an as-of-today
  balance sheet (whole-entry exclusion keeps the tie-out green). Rare; oracle-pinned.

---

## 4. Read side: statements, routes, enforcement

### 4.1 `app/services/ledger_report_service/` (package)

- `_types.py`: frozen dataclasses `StatementWindow`, `StatementLine`, `TrialBalanceTieOut`,
  `IncomeStatementReport`, `BalanceSheetReport`. Decimal-only.
- `_attribution.py`: the shared core BOTH statements consume (shared code is what makes
  articulation automatic): `dated_account_nets(user_id, scenario_id)` -> per-(ledger account,
  attribution date) nets from three batched queries -- transaction-linked (join `Transaction` +
  `PayPeriod`), transfer-linked (two-step C10 shape: grouped nets, then one batched income-shadow
  date load), sourceless (grouped by `entry_date`). No per-account loops.
- `_income_statement.py`: `compute_income_statement(user_id, window)`. Pay-period window = one
  grouped query on `pay_period_id` (uses `idx_journal_entries_user_scenario_period`); calendar
  windows filter the attribution core. Lines = Income/Expense-class ledger accounts only
  (category rows, the Uncategorized fallback, orphans, per-loan interest/escrow rows), sorted by
  label.
- `_balance_sheet.py`: `compute_balance_sheet(user_id, as_of)`. Fold nets dated <= as_of;
  sections by class; `retained_earnings` derived (C-5); tie-out = assets vs
  liabilities-plus-equity AND the mechanical ledger-net == 0; `in_balance` requires both.
- Display names: one chart load with `joinedload(category)`; LIVE `category.display_name` for
  category rows (renames reflected); the snapshot `LedgerAccount.name` for orphan / fallback /
  per-loan / equity rows; branch on `kind_id` via ref_cache, never NULL patterns.
- `get_baseline_scenario` first; `None` -> empty report with a green tie-out.
- Template footnote + this doc record the honesty boundary: this is the POSTED ledger's
  statement; modeled growth between true-ups is Net Worth's job (the seam).
- W9906 posture: the report functions are statement aggregates, not balance-at-T producers; that
  exclusion rationale is recorded in the checker header. W9908 fences the models; the statements
  oracle fences the numbers.

### 4.2 Routes / templates / CSV

- `app/routes/analytics.py`: `income_statement_tab()` (params
  `window`/`period_id`/`month`/`year`/`format`; IDOR-check `period_id` via
  `_validate_owned_or_abort`; generalize `_resolve_variance_params` -> `_resolve_window_params`
  shared with the variance tab) and `balance_sheet_tab()` (param `as_of` ISO date,
  parse-or-today, clamped to [2000-01-01, today]); CSV branch before the HTMX guard; non-HTMX
  redirects to `analytics.page`.
- `analytics/analytics.html`: two new pills (same hx-get / hx-target / hx-indicator pattern). No
  new JS; CSP holds by construction.
- `analytics/_income_statement.html` / `_balance_sheet.html`: money macro, card + table
  `text-end font-mono`, window toggle cloned from `_variance.html`, a date input wired like the
  year select, the tie-out footer card with a success/danger badge, empty states.
- `csv_export_service.export_income_statement_csv` / `export_balance_sheet_csv` following
  `export_year_end_csv`; filenames per the variance convention.

### 4.3 The L9 switch (Schedule-A)

New pure helper `app.utils.dates.to_display_civil_date(paid_at, fallback)` composing
`to_display_date` (the display-timezone counterpart of `posting_service._civil_settle_date`,
which stays UTC as the storage rule). `confirmed_loan_interest_in_year` switches to it; docstring
updated. No existing test assertion moves (attribution tests use noon-UTC instants); NEW boundary
tests pin an 8:05pm-ET Dec-31 settle to the earlier year.

### 4.4 Enforcement

- **W9908 `shekel-ledger-model-bypass`** (new checker in the `tools/pylint` plugin): an IMPORT
  fence -- modules outside the allowlist may not import the ledger models by module path
  (`app.models.journal_entry`, `app.models.ledger_account`) or by name from `app.models`
  (`Posting`, `JournalEntry`, `LedgerAccount` -- the F-1 shape). Allowlist (grep-verified
  complete against every current importer + the new packages): `app.models`,
  `app.services.posting_service`, `app.services.posting_reads`,
  `app.services._posting_reconcile`, `app.services.loan_posting_service` (prefix),
  `app.services.account_posting_service` (prefix), `app.services.ledger_account_service`,
  `app.services.ledger_report_service` (prefix), `app.services.pay_period_admin`,
  `app.utils.archive_helpers`. Reuses the fail-closed `_module_in_allowlist`. W9906's
  reader-name list is NOT extended.
- Gate lockstep (the gate-consistency test forces every occurrence): CI `--fail-on` (both
  lines), `.pre-commit-config.yaml` (all three occurrences), `scripts/hooks/post-edit-python.sh`,
  `_CANONICAL_FAIL_ON` in `tools/pylint/tests/test_fail_on_gate_consistency.py`, the CLAUDE.md
  lists.
- **Test-side F-1 closure**: the loan oracle's two import-fence detectors also match
  `from app.models import Posting/JournalEntry/LedgerAccount`; the negative control extends to
  prove it.

---

## 5. Tests

- **Write-side oracle** `tests/test_integration/test_posting_ledger_account_anchor_reconciliation.py`:
  the ABSOLUTE invariant per non-loan account (`account_posting_total == latest anchor balance +
  SUM(current nets of sources attributed after the latest assertion instant)`, cross-checked
  against independently-summed transaction-table effects); ledger-through-each-assertion-instant
  == that asserted balance; pre-true-up settle absorbed / post-true-up settle rides on top (the
  CRITICAL-1 regression case); revert-after-true-up self-heal; pre-anchor absorption; same-day
  anchor merge; a NEGATIVELY-anchored liability account; zero-delta books nothing;
  scenario/owner isolation; backfill == go-forward; a non-vacuity injection.
- **Statements oracle** `tests/test_integration/test_posting_ledger_statements.py` (the fourth
  reconciliation oracle): hand-computed income statement + balance sheet on a rich fixture
  (categories, fallback, loan interest+escrow, transfers, an anchor true-up, a cross-year revert,
  the 8:05pm-ET Dec-31 settle, an early-settled future-period source, NULL-`paid_at` fallback,
  hard-delete residue nets-zero-and-dropped); `A = L + E` at >= 4 as-of dates including one
  inside an anchor period before and after the assertion date; articulation (income net +
  windowed equity corrections == equity delta between bounding sheets); period-vs-calendar
  agreement on an aligned fixture; transfers never touch the income statement; a
  liability-account line signs correctly; orphan/live-rename labels; scenario/owner isolation; a
  tie-out tamper injection (raw single leg -> red).
- **Unit/service tests**: walk + reconcile (hand-computed deltas incl. the moment partition), the
  anchor-equity resolver guards, the type-change guard, ref-seed parity, checker tests
  (`tools/pylint/tests/`), route tests (auth, HTMX partial, non-HTMX redirect, CSV
  content/filename, param clamps, IDOR 404).
- **Existing tests that legitimately change** (behavior change; the developer approved this plan
  as the confirmation), enumerated up front rather than discovered at the full-suite gate:
  per-account changes-only invariants -> the superseded absolute form in
  `test_posting_ledger_reconciliation.py` (:343-346, :441-444, :593-596) and
  `test_posting_ledger_cash_reconciliation.py` (:625-641, :949-952); `account_posting_total`
  assertions against seeded accounts in `test_transfer_posting_lifecycle.py`,
  `test_transaction_posting_lifecycle.py`, `test_posting_service.py`,
  `test_loan_posting_wiring.py`, `test_loan_posting_backfill.py`, `test_pay_period_truncate.py`;
  chart-shape assertions (`len(ledger_accounts_for_account) == 1` and siblings) in
  `test_ledger_account_backfill.py:168,188,206,232-233` and the other `test_ledger_account*.py`
  files; `tests/_test_helpers.create_account_of_type` ($100 sentinel) now mints a twin + opening
  everywhere; `seed_user` reorders (baseline Scenario BEFORE `create_account`, matching
  production -- the seeded Checking then carries its $1000 opening) and
  `_drop_seed_user_bootstrap` resyncs; pay-period lock-reason tests flip `ACCOUNT_ANCHOR` ->
  `LEDGER_POSTINGS` (`test_pay_period_admin.py:134-143`, `test_pay_period_truncate.py:340,:380`);
  reset tests gain one opening entry per account; hard-delete route tests on non-zero-anchor
  accounts now hit Guard 5.

---

## 6. Commit sequence

1. **C1 -- L9 helper + Schedule-A switch** (independent): `to_display_civil_date`; the
   `confirmed_loan_interest_in_year` switch; boundary tests; the L9 annotation in the review doc.
2. **C2 -- Ref rows**: enums + ref_seeds + inline-seed migration + parity tests.
3. **C3 -- Index re-key + lookup hardening**: the `(account_id, kind_id)` unique migration
   (fail-loud downgrade guard); model docstrings; the two LINKED-kind filter fixes;
   `test_ledger_account*.py` shape-test updates. Must precede any writer.
4. **C4 -- Anchor-equity chart resolver** + guards + tests.
5. **C5 -- Walk + reconcile (pure, unwired)**: `_posting_reconcile.py` extraction (loan package
   re-pointed); `account_posting_service/{_walk,_anchors,_sync}.py` + unit tests.
6. **C6 -- Lifecycle wiring + oracle supersession** (one commit -- the fixture reorder breaks the
   old invariants, so they move together): the seven wiring points incl. the type-change guard
   and `create_baseline`; the `seed_user` reorder + `_drop_seed_user_bootstrap` resync; the
   enumerated test-file updates from Section 5.
7. **C7 -- Backfill**: `backfill_all_account_anchor_postings` + the deploy-hook extension + the
   data-boundary migration; idempotence / no-double-post / downgrade-scope tests.
8. **C8 -- Write-side oracle.**
9. **C9 -- Reporting service** + service tests.
10. **C10 -- Routes + templates + tabs** + route tests.
11. **C11 -- CSV export** + tests.
12. **C12 -- Enforcement**: W9908 + gate lockstep wiring + checker tests; the test-side F-1
    detector fix; the F-1 annotation.
13. **C13 -- Statements oracle + docs close-out**: `test_posting_ledger_statements.py`; annotate
    M3 (reader half) / R9 / the header note in the review doc; update
    `level1_level2_scope_and_fitness.md` Status. Final gate: full suite alone + `pylint app/
    scripts/` 10.00, output shown.

## 7. Verification (end-to-end)

1. Per-commit: `SKIP_DB_RESTART=1 ./scripts/test.sh <targeted files> -v`; final
   `./scripts/test.sh` (full suite, run alone) with pass counts shown.
2. Migration round-trip on the rebuilt template: `flask db upgrade` / `downgrade` x2, then
   `python scripts/build_test_template.py`.
3. **Prod-clone verification** (developer-gated): clone prod -> dev DB, run migrations + the
   deploy hook, then assert by hand: trial balance still 0; every non-loan account's
   `account_posting_total == latest anchor + post-assertion settled effects`; the balance sheet
   tie-out green on real data; NO positively-anchored non-loan liabilities exist (else the C-4
   decision point returns to the developer); spot-check the income statement against the
   year-end spending totals for a known year.
4. Live-drive the dev container: open `/analytics`, exercise both tabs (window toggles, as-of
   date, CSV downloads); settle a transaction then true-up the anchor and watch the balance
   sheet land exactly on the asserted balance (the CRITICAL-1 scenario); both themes.
5. PR `feat/actuals-reporting` -> `dev` after developer review; prod ship follows the
   established dev -> main pipeline separately.
