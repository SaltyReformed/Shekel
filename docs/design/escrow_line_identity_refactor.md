# Escrow line identity refactor (proposed -- review before building)

Status: PROPOSED, not started. Written 2026-07-06 as the agreed "very next piece of work" after the
loan-detail balance/split fix. Review this in a fresh session before implementing.

## TL;DR

`budget.escrow_components` uses the mutable display `name` string as the identity key for a logical
escrow line across its temporal versions. A rename therefore looks like a brand-new line, not a new
version of the same line. This is a normalization weakness (a mutable attribute is doing the job of
a stable key) and it is what let the temporal-escrow migration double-count a renamed line on one
real loan. The proposal: give each escrow line a stable surrogate identity (`line_id`), separate
from its display name, and support renaming in place. This is a cleaner, fully normalized model; it
is NOT required for correctness going forward (see "Why this is not urgent").

## Background: how escrow is modeled today

`budget.escrow_components` (see `app/models/loan_features.py::EscrowComponent`) is an
effective-dated / temporal table. Each row is one VERSION of an escrow line, valid over the
half-open range `[effective_date, end_date)` (open range = still active). The monthly figure on a
date is the sum of `annual_amount / 12` over every version active on that date
(`app/services/escrow_calculator.py::calculate_monthly_escrow`, fed by
`app/services/loan_loaders.py::load_all_escrow_components` + `EscrowComponent.is_active_on`).

Each row DOES have a primary key `id` -- but `id` identifies a single ROW (a single version), not
the logical line across its versions. The thing that ties versions of the same line together is the
**`name` string**:

- The uniqueness rule is `uq_escrow_components_account_name_active` -- a partial unique on
  `(account_id, name) WHERE end_date IS NULL` ("at most one ACTIVE version per name").
- The add route (`app/routes/loan/escrow_rates.py::add_escrow`) rejects a new component whose `name`
  matches an existing active one.
- There is NO edit/rename route. A "change amount" or "rename" is expressed as delete
  (`delete_escrow` stamps `end_date = today`) + add (`add_escrow` inserts a new row starting today).
  For an AMOUNT change the two rows share a name; for a RENAME they do not.

So the display `name` is doing double duty: it is both the human label AND the logical line's
identity key.

## The concrete defect this caused

On the real Mortgage (dev account id 3; the prod account id may differ), the operator renamed the
escrow line from "Property Tax & Insurance" to "Tax and Insurance" (same $7,403.88/yr = $616.99/mo).
In the pre-temporal (`is_active` boolean) model that was harmless: the old row was
`is_active = false` and never counted; only the active row counted, so escrow was $616.99.

The temporal-escrow migration
`migrations/versions/d1e7c4a2f9b3_escrow_components_effective_dating.py` then backfilled EVERY row's
`effective_date` to the loan's origination date and stamped the formerly-inactive row's
`end_date = updated_at`. Result: two rows, same account, same amount, DIFFERENT names, both starting
at origination:

- id 1 "Property Tax & Insurance" -- `[2018-12-01, 2026-04-03)`
- id 2 "Tax and Insurance"        -- `[2018-12-01, open)`

They now OVERLAP for every date on/before 2026-04-03, so any payment then sums BOTH ($1,233.98) -- a
double-count. The migration's docstring even assumed "on real data there are no inactive
components," which was false for this loan.

This was corrected by the data-only migration in the loan-detail fix work
(`migrations/versions/f2a7c1e9b4d3_escrow_components_fix_rename_duplicate_overlap.py`), which
collapses a closed version that is fully date-subsumed by another same-account, same-amount,
same-start version to an empty range. See that migration and `docs/design/loan_audit.md` / the loan
balance-architecture audit.

## Why the display-name-as-identity model is the root weakness

- **A rename splits one line into two.** Because versions are grouped by name, changing the name
  breaks the chain. The temporal history can no longer say "this one line was called X, then Y"; it
  shows two independent lines.
- **A mutable field is the key.** This is the exact pattern the project forbids for ref tables ("IDs
  for logic, strings for display", `.claude/rules/coding.md`). Escrow names are free-text labels,
  not a ref table, so the rule does not mechanically apply -- but the spirit does: logic keyed on a
  user-editable string is fragile.
- **No rename-in-place.** The only "rename" path is delete + add, which is what manufactured the
  duplicate rows in the first place.

## Why this is NOT urgent (do not skip the review to rush it)

Going forward, the temporal model already prevents the double-count WITHOUT this refactor: a rename
today is delete (`end_date = today`) + add (`effective_date = today`), which produces ADJACENT,
non-overlapping ranges, so no date is ever double-counted. The account-3 defect was a one-time
artifact of the temporal MIGRATION backfilling a pre-existing deactivated row to overlap its
successor; the live app cannot reproduce it. So this refactor is a normalization/robustness
improvement and a prerequisite for a proper rename UX -- not a fix for a live correctness bug.

## Proposed solution (options to decide in the review session)

Add a stable identity for an escrow LINE, separate from its display name, and key temporal
versioning + the overlap invariant on that identity.

### Option B1 -- surrogate column on the existing table

Add `line_id` (a per-line surrogate, e.g. an integer or UUID) to `budget.escrow_components`.
Versions of one line share a `line_id`; `name` becomes a per-version display attribute that may
change across versions (a rename = new version, same `line_id`, new name -- OR an in-place name edit
on the active version). Temporal grouping and the overlap constraint key on `(account_id, line_id)`.

### Option B2 -- parent/child tables (textbook normalized)

A parent `budget.escrow_lines` (`id` PK, `account_id`, current display name) and child version rows
in `budget.escrow_components` FK-ing to it (`line_id`, `annual_amount`,
`[effective_date, end_date)`, `inflation_rate`). The line is a first-class entity; versions belong
to it. Cleanest separation of "the line" (identity + current name) from "a version" (amount over a
date range).

Decide B1 vs B2 in the review session. B2 is more normalized; B1 is a smaller migration.

### Companion changes (either option)

- **Overlap guard on the stable key.** A PostgreSQL exclusion constraint (needs the `btree_gist`
  extension) keyed on `line_id`. This WOULD prevent an overlap across a rename -- which a
  `(account_id, name)` constraint cannot. Distinct lines (tax + insurance + PMI) get distinct
  `line_id`s and coexist freely. The DDL:

  ```sql
  ALTER TABLE budget.escrow_components ADD CONSTRAINT ex_escrow_components_line_no_overlap
    EXCLUDE USING gist (
      account_id WITH =, line_id WITH =,
      daterange(effective_date, COALESCE(end_date, 'infinity'::date), '[)') WITH &&
    );
  ```

- **Rename-in-place flow.** Add an edit route/schema so renaming updates the name (on the active
  version, or on the parent line) instead of delete + add. This removes the mechanism that created
  the duplicate.
- **Update readers/consumers.** `escrow_calculator`, `loan_loaders.load_active_escrow_components` /
  `load_all_escrow_components`, the add/delete/edit routes, `build_escrow_display`, the loan
  dashboard escrow list template, and the savings dashboard escrow surfaces.

### The backfill challenge (important)

A migration CANNOT auto-detect that two differently-named rows are the same line -- that requires
the operator's knowledge (as it did for account 3). So the `line_id` backfill would, by default,
assign one `line_id` per existing `(account_id, name)` group (each distinct name = one line).
Account 3's two rows were already merged into one effective line by the loan-detail data-fix
migration (the older name collapsed to an empty range), so the backfill sees a single active line
there. Any future differently-named duplicate would again need a manual merge. Document this
limitation in the backfill migration.

## Blast radius / files (for the estimate in the review session)

- `app/models/loan_features.py` (EscrowComponent: new column / relationship + constraint)
- new `budget.escrow_lines` model if B2
- `app/services/escrow_calculator.py`, `app/services/loan_loaders.py`
- `app/routes/loan/escrow_rates.py` (add/delete + new rename/edit), `app/routes/loan/_helpers.py`
  (schema)
- templates: `loan/_escrow_list.html` and any escrow display partials
- `app/services/loan_posting_service/_walk.py` reads escrow only through
  `load_all_escrow_components` + `is_active_on`, so it needs no change if the loaders keep their
  signatures -- verify.
- migration(s): add column/table, backfill `line_id`, add the exclusion constraint (+ `btree_gist`)
- tests: escrow temporal tests, escrow route tests, a new rename-in-place test, the overlap
  constraint test, and the loan-payment split/oracle tests (escrow must remain penny-correct).

## Related

- `docs/design/loan_audit.md` -- the loan-detail overhaul that surfaced this.
- `docs/audits/balance_architecture/implementation_plan_temporal_escrow.md` -- the temporal-escrow
  design whose migration introduced the overlap.
- The loan-detail fix plan:
  `~/.claude/plans/after-completing-docs-design-loan-audit-magical-engelbart.md`.
