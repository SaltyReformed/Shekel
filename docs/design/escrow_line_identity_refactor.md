# Escrow config redesign: line identity, effective dates, date-aware cash, overpayment

Status: COMPLETE on branch `feat/escrow-config-redesign` -- ALL steps (1, 2a, 2b, 4, 3, 5, 8, 7, 6)
SHIPPED; ready for the PR to `dev`. Steps 7 (inflation fix, commit `83be140c`) and 6 (merge tool,
commit `b7bfe00b`) shipped 2026-07-07 (Section 17); step 8 (the resolver-seam fix that resolved the
step-5 review's one open finding, cross-surface payoff consistency) shipped 2026-07-07 (Section 16).
Written 2026-07-06. **Supersedes** the earlier "escrow line identity refactor" proposal that this
file used to hold (see git history) -- an adversarial review of that proposal in a fresh session
expanded the scope from "add a `line_id` column" to the full, correct redesign below.
**Decisions A-D approved by the operator 2026-07-06 (Section 12).**

## 0. Why this exists (plain language, no jargon)

An escrow account (impound) is where the mortgage servicer collects money each month for property
tax, insurance, and PMI on top of principal and interest. Each of those is an escrow **line**. Two
things about escrow lines change over the life of a loan:

1. **The amount changes** -- almost always once a year, when the servicer re-runs its escrow
   analysis (property tax reassessed, insurance premium moved). For this operator's mortgage (dev
   account id 3) it changes annually, effective on a stated future date.
2. **The label changes, lines are added, lines drop off** -- PMI falls away at 20% equity; a line
   gets renamed ("Property Tax & Insurance" -> "Tax and Insurance").

Today the app models an escrow line's identity by its **display name string**. That is fragile (a
rename looks like a brand-new line), it forces every change to be "delete + re-add" (there is no
edit route), and it pins a change's effective date to *the day you happen to click*. That last one
is the real operator pain: when the annual letter arrives on, say, Nov 15 announcing a new amount
effective Jan 1, there is no way to enter "effective Jan 1" -- you must remember to come back and
click during a specific window in December, or the December payment silently picks up the new
amount.

This spec fixes all of that, and closes an unrelated gap the operator asked for:
**paying extra principal while still auto-deriving the payment** (today those are mutually
exclusive).

## 1. What "correct" means here (the invariant everything serves)

A confirmed loan payment's real economic split is:

```text
principal = cash - interest - escrow
```

computed by the genesis ledger walk (`app/services/loan_posting_service/_walk.py:207`). For that
principal to be right, the **escrow figure the split subtracts must equal the escrow figure that was
built into that payment's cash**. Call this the **cash==split invariant**. Everything below is in
service of making that invariant hold *by construction* -- because both sides read escrow from ONE
shared "escrow as of date D" function -- rather than by luck or by the operator clicking on the
right day.

This is squarely the root-cause doctrine
(`docs/audits/balance_architecture/recurring_loan_balance_root_cause.md` Sec. 3): escrow is a
**dated config input** (Fowler *Temporal Property*), the loan payment split is the
**materialized posting**, and we never let the same question ("escrow on date D") be answered two
different ways.

## 2. The model: supersession-based temporal escrow (the operator's chosen normalization)

### 2.1 Two tables (fully normalized: identity separate from versions)

**`budget.escrow_lines`** -- the logical line (its identity and current label):

| column | type | notes |
|--------|------|-------|
| `id` | int PK | the stable line identity (surrogate; never the name) |
| `account_id` | int FK -> `budget.accounts.id` | via `AccountScopedMixin`, as `escrow_components` uses today |
| `name` | `String(100)` NOT NULL | current display label ONLY; a rename edits this in place |
| timestamps | | `TimestampMixin` |

Partial unique `uq_escrow_lines_account_name_active` on `(account_id, name)` filtered to non-removed
lines (a line is "removed" iff its latest version `is_removed`; see 2.3) -- at most one active line
per name per account, so the add route still rejects a duplicate active name, while a removed line's
name may be reused. (Because "removed" is derived from the latest version, this predicate cannot be
a raw partial index on `escrow_lines`; enforce it in the add/rename route with a matching non-unique
index for lookup speed, OR carry a denormalized `is_removed` mirror on `escrow_lines` maintained in
the same transaction. **Resolved (decision C): route-enforced, with a matching non-unique index for
lookup speed; no denormalized mirror that could drift.**)

**`budget.escrow_component_versions`** -- one immutable version per (line, effective date):

| column | type | notes |
|--------|------|-------|
| `id` | int PK | one row = one version |
| `line_id` | int FK -> `budget.escrow_lines.id` ON DELETE CASCADE | the version belongs to a line |
| `effective_date` | `Date` NOT NULL | the domain date this version takes effect |
| `annual_amount` | `Numeric(12,2)` NOT NULL, `CHECK >= 0` | stored annual; monthly = `/12` |
| `inflation_rate` | `Numeric(5,4)` NULL, `CHECK IS NULL OR (0..1)` | forward-projection only (Sec. 8) |
| `is_removed` | `Boolean` NOT NULL default false | a tombstone version: "this line contributes nothing from here" |

Unique `uq_escrow_versions_line_effective` on `(line_id, effective_date)` -- at most one version per
line per date (a same-day correction edits that row; it does not append a second). Index
`ix_escrow_versions_line_effective` on `(line_id, effective_date)` for the as-of lookup.

**There is NO `end_date`.** This is the operator's chosen posture and it is the right one:

### 2.2 Why supersession (no `end_date`) kills the whole bug class

A version is active from its `effective_date` until the
**next version of the same `line_id` supersedes it**. "Escrow as of date D for a line" = the version
with the greatest `effective_date <= D`. Because activeness is defined by the
*ordering of a line's own versions*, there is no end date to set wrong and
**two versions of one line can never overlap -- the overlapping-range state is not representable.**
That is strictly stronger than the earlier proposal's "keep ranges, add a Postgres exclusion
constraint to *catch* overlaps": here the illegal state cannot exist, so we need no `btree_gist`
extension and no exclusion constraint (the codebase has zero of either today; verified by grep).
This is the exact "make the bad state unrepresentable" principle the root-cause doc argues for.

The original `d1e7c4a2f9b3` double-count on account 3 happened precisely because the range model let
two rows both start at origination and overlap. Under supersession that cannot occur: two versions
of one line are a strict sequence; two *different* lines are independent and never summed against
each other by identity.

### 2.3 Removal is a tombstone version, not a state flag

To drop a line (PMI falls off on a date): append a version with `is_removed = true` at that
effective date. "Escrow as of D" for that line then resolves to the tombstone and contributes 0. A
later real version revives it (a re-add). `is_removed` is NOT the old mutable `is_active` boolean we
deleted: `is_active` was per-line **current state** that could drift from the ranges; `is_removed`
is a per-version **immutable event** ("removal happened, effective this date"). Same reason
`LoanAnchorEvent` records a dated event rather than mutating a balance column.

## 3. The single "escrow as of date" function (the DRY heart)

One pure function, the ONLY way any surface computes an escrow figure for a date:

```text
escrow_monthly_as_of(lines_with_versions, on_date: date) -> Decimal
```

For each line: pick the version with the greatest `effective_date <= on_date`; if none, or if that
version `is_removed`, the line contributes 0; else contribute `annual_amount / 12`. Sum across
lines, then round (preserving the existing sum-then-round boundary in
`escrow_calculator.calculate_monthly_escrow`, `escrow_calculator.py:160-185`, and the per-row
largest-remainder allocation in `_allocate_monthly_amounts`).

**This one function is called by BOTH the split and the cash builder**, which is what makes the
cash==split invariant hold by construction rather than by coincidence. Concretely it replaces:

- the split's per-version `is_active_on` filter (`_walk.py:405-408`) -- which, note,
  **cannot survive as-is under supersession**: `is_active_on(D)` on a single row is no longer
  sufficient, because a version's activeness now depends on its siblings (the next one supersedes
  it). The walk must group by `line_id` and pick latest-effective-<=-D. (The earlier proposal's
  claim that the walk needs no change was true only for the range model; under supersession the walk
  changes here. This is the one place the operator's preferred model costs a bit more than ranges --
  worth it.)
- the cash builder's single-current-escrow figure (`loan_payment_service.py:120-121`).
- `calculate_monthly_escrow`'s callers that pass "the currently active set" -- they instead pass
  `on_date = today`.

`EscrowComponent.is_active_on` (`loan_features.py:246-264`) is deleted; the as-of resolution moves
into `escrow_monthly_as_of`.

## 4. Effective-date field (the operator's real ask)

### 4.1 The field

The escrow add/edit form gains an **Effective date** field (defaulting to today, so the common case
is unchanged). Entering "new amount, effective Jan 1" on Nov 15 writes a version with
`effective_date = 2026-01-01` under the existing line. No waiting, no click-timing.

### 4.2 The forward-only guard (this is what preserves settled-payment immutability)

An effective date that precedes an already-settled payment would retroactively change that payment's
`escrow_monthly_as_of`, desyncing it from the cash that already froze at settlement (Sec. 5.2). So:

> **Guard:** a new, edited, OR deleted escrow version's `effective_date` must be strictly after the
> pay-period start of the loan's latest **settled** payment.

**As built (correction to the original wording).** The boundary is the greatest
`pay_period.start_date` over settled payments -- `loan_loaders.latest_settled_payment_period_start`,
which shares the settled-shadow set (`_settled_income_shadows`) with the tracking-start guard so
both agree on "which payments are settled." It keys on the pay-period **start**, NOT the monthly
**due** date `_settled_payment_due_dates` derives: the split (`_walk._replay_events`) and the
settle-time cash freeze (`loan_payment_service._shadow_live_amount`) both resolve escrow at
`pay_period.start_date`, so that is the precise boundary; guarding on the due date would be
over-conservative. The guard applies uniformly to add-line, add-version, edit (both the version's
current and new date), delete-version, AND the line-level remove (its tombstone lands at today, so
today must clear the boundary). **The boundary can be in the FUTURE** -- an EARLY-settled payment
(settled before its pay period begins) puts it after today; an adversarial review caught that the
two delete paths originally guarded only on "today" and could corrupt a paid-ahead payment's split.
The route rejects a violating date with an actionable message naming the boundary; the escrow card
surfaces it (a 4xx does not HTMX-swap, so `escrow_card.js` projects the message into
`#escrow-error`).

### 4.3 Past corrections go through the existing loan true-up, NOT retroactive escrow

If a *past* escrow figure was genuinely wrong, the resulting balance error is corrected with a loan
**true-up** (`LoanAnchorEvent` `user_trueup`, the mechanism that already exists for exactly this).
That is deliberate and consistent with `project_loan_trueup_semantics`: true-ups are the sanctioned
correction for wrong/incomplete past loan data. We do NOT let escrow be retro-edited, because that
would create a second way to move a settled payment's split and reintroduce the dual-source drift
the whole architecture avoids.

### 4.4 Why we do NOT "freeze" the escrow onto each settled payment

A tempting alternative is to record the escrow figure onto each settled payment (or its posting) so
the split reads the frozen number. **Reject this.** The genesis ledger is deliberately
reconcile-to-target (it recomputes every split from inputs on each sync, which is what makes it
self-healing). A frozen per-payment escrow copy would be a SECOND source of truth for a settled
payment's escrow that could drift from the config -- exactly the class of bug this project keeps
fighting. Instead we keep ONE source (the versions) and make its inputs immutable for settled dates
via the 4.2 guard. That is more correct here, not merely easier.

## 5. Date-aware cash projection (mandatory once effective dates exist)

### 5.1 The change

Today the live recompute resolves ONE PITI per loan and maps it onto every projected shadow
(`loan_payment_service.live_loan_transfer_amounts`, `:684-696`; PITI built from a single current
escrow, `_resolve_loan_piti:612`). That is only safe because nothing can be future-dated today
(`implementation_plan_temporal_escrow.md:52-56`). With future-dated versions it is wrong: a December
projected payment and a January projected payment must use *different* escrow.

So `live_loan_transfer_amounts` becomes **per-shadow**: for each projected, non-overridden
derive-from-loan shadow, cash = `state.monthly_payment` (P&I resolved once per loan) +
`escrow_monthly_as_of(lines, shadow.pay_period.start_date)` + `extra_principal` (Sec. 6). The P&I
resolve stays once-per-loan; only the escrow term becomes per-shadow-date via the Sec. 3 function.

### 5.2 The two other date-unaware escrow consumers (must also move)

Traced 2026-07-06 -- both currently subtract or add a single current escrow to all payments:

- `load_loan_context` (`loan_payment_service.py:120-123`) sets `monthly_escrow` from
  `load_active_escrow_components` and hands it to `prepare_payments_for_engine` (`:161-164`), which
  subtracts that one figure from every historical payment to recover P&I for the resolver's
  schedule/replay. Under effective dating this must subtract each payment's as-of escrow.
- `_resolve_loan_piti` / `calculate_total_payment` (the cash default in `_resolve_transfer_amount`,
  `payment_transfer.py:64-70`) use current escrow for the DEFAULT amount at create time -- that is
  fine (a default captured at creation), but the *live* recompute is the authority thereafter (5.1).

Build step: audit every `calculate_monthly_escrow` / `load_active_escrow_components` caller
(enumerated in Sec. 10) and classify each as "today's set" (pass `on_date = today`) or "per-payment
as-of" (pass the payment's date). The split and the live cash recompute are the two as-of consumers;
the rest are today's-set.

## 6. Overpayment + auto-derive (NOT mutually exclusive)

### 6.1 The decomposition

Cash for a loan payment becomes:

```text
cash = base + extra_principal
  base           = auto-derived PITI (P&I + escrow-as-of-date)   [derive mode]
                   OR a fixed operator-entered base              [manual mode]
  extra_principal = a persisted recurring extra toward principal (default 0.00)
```

`extra_principal` is added in BOTH modes. So the operator can keep auto-derive ON (escrow/rate
changes flow through automatically) AND add a standing $X/month extra. The split needs no special
case: principal is the residual `cash - interest - escrow` (`_walk.py:207`), so the extra lands in
principal automatically -- verified in the worked proof (Sec. 9). Near payoff, an extra that would
overrun the balance caps to the balance with the surplus routed to Refund (`_split_one_payment`,
`_walk.py:208-215`) -- already correct, no change.

### 6.2 The projection must see the extra too

The extra must accelerate the committed-forward trajectory and payoff date, or the chart and the
cash debit disagree. The amortization engine ALREADY models an extra monthly payment -- the payoff
lever's `extra_monthly` (`compute_payoff_scenarios`, used at `_helpers.py:446-451` with
`extra_monthly=0`). Persisted `extra_principal` feeds that same parameter for the BASELINE committed
scenario (not just the what-if lever). Build step: thread `extra_principal` into
`build_baseline_scenarios` so `committed_forward` reflects the real plan; the lever then previews
*additional* extra on top.

### 6.3 Where `extra_principal` lives -- RESOLVED (decision B): new `loan_payment_settings` table

`extra_principal` is a loan-payment attribute of a recurring transfer.
**Decision: a new `budget.loan_payment_settings` table** keyed by `transfer_template_id`, holding
`derive_from_loan` + `extra_principal` (+ room for future loan-payment settings). This is fully
normalized AND retires the pre-existing smell of `derive_from_loan` living on the generic
`transfer_templates` (`transfer_template.py:85`). The rejected alternative was a second loan-only
column on `transfer_templates` (smaller change, but compounds the smell rather than fixing it).

Migration consequence: `derive_from_loan` moves off `transfer_templates` into
`loan_payment_settings` (a three-step column move -- add the new table + backfill from the existing
column, repoint every `derive_from_loan` reader, then drop the old column), and every current reader
(`payment_transfer.py`, `loan_payment_service.live_loan_transfer_amounts:675`) reads the new table.
This is a destructive move -> `Review:` docstring line + operator approval on that migration.
`ondelete=CASCADE` on `transfer_template_id` (settings die with their template).

## 7. Rename in place, and merge

- **Rename in place:** an edit route updates `escrow_lines.name`. It is display-only and
  **provably cannot move a cent of any settled payment**, because the split reads amount + date,
  never name (`_walk.py:207`, `escrow_monthly_as_of`). Pin this with a test: rename a line, re-run
  the split oracle, assert byte-identical postings.
- **Merge:** the operator action that repairs a line that history split in two (the account-3
  situation). It repoints the versions of line X onto line Y (or marks X merged into Y) and lets the
  reconcile re-derive. This is the capability the earlier proposal lacked -- a column alone does not
  give the operator back their unified history; the merge tool does. Merge must re-run the loan
  posting reconcile for the affected account afterward
  (`loan_posting_service.backfill_all_loan_postings` / the per-user resync).
- The add route keeps rejecting a duplicate **active** name (2.1); a rename to an existing active
  name is likewise rejected (offer merge instead).

## 8. Inflation fix (folded in, since we are rewriting this table anyway)

Today inflation compounds off `created_at` -- a technical insert timestamp -- not a domain date
(`escrow_calculator.py:166-180`), and it is still live on the loan dashboard's "next year" note
(`dashboard.py:118-135`). In the new model:

- Inflation is a **forward-projection display** concern ONLY (recorded past/present escrow is
  exact), as the temporal plan already established.
- The forward projection compounds the **latest-as-of-today version's** amount forward from
  **today** (a domain date), never from `created_at`. `created_at` is removed from the inflation
  math entirely.
- The "next year" note compares today's escrow to the same set projected to next Jan 1, both via the
  Sec. 3 function -- so an old line no longer shows a spurious multi-year jump.

## 9. Worked proof: cash == split at every step (the operator's Nov/Dec/Jan case, with an extra)

Real account-3 figures. Level P&I (fixed rate) = **$1,293.96/mo**. Old escrow **$616.99/mo** (stored
annual $7,403.88). New escrow **$666.99/mo** (annual $8,003.88), a +$50 annual analysis. Standing
**`extra_principal` = $100.00**. Illustrative interest on the December payment = $1,018.82 (interest
varies per payment as the balance amortizes; escrow is what we are proving consistent).

**Timeline.** Nov payment settles ~Nov 1 (old escrow). On **Nov 15** the operator opens the escrow
line and enters "amount $666.99, **effective 2026-01-01**." Guard check (Sec. 4.2): latest settled
payment is November -> its pay-period start is in early November -> Jan 1 is strictly after it ->
**allowed.**

Escrow line "Property Tax & Insurance" versions after the edit (supersession, no end dates):

| version | effective_date | monthly |
|---------|----------------|---------|
| v1 | 2018-12-01 (origination) | $616.99 |
| v2 | 2026-01-01 | $666.99 |

`escrow_monthly_as_of(line, D)` = greatest effective <= D: as-of any December date ->
**v1 $616.99**; as-of any January date -> **v2 $666.99**.

**December payment** (pay-period start early Dec), projected on Nov 15, settles ~Dec 1:

- Cash built (date-aware, Sec. 5): base = P&I $1,293.96 + `escrow_as_of(Dec) $616.99` = $1,910.95;
  - extra $100 -> **cash $2,010.95**. Frozen at settlement.
- Split (keyed to Dec start, Sec. 3): interest $1,018.82; `escrow_as_of(Dec) $616.99`; principal =
  2,010.95 - 1,018.82 - 616.99 = **$375.14** = scheduled $275.14 + extra $100. Extra lands in
  principal. ✓
- Cash escrow $616.99 **==** split escrow $616.99. ✓ **MATCH.**

**January payment** (pay-period start early Jan), projected on Nov 15, settles ~Jan 1:

- Cash built (date-aware): base = $1,293.96 + `escrow_as_of(Jan) $666.99` = $1,960.95; + extra $100
  -> **cash $2,060.95**. Frozen at settlement.
- Split (keyed to Jan start): interest = on the post-December balance; `escrow_as_of(Jan) $666.99`;
  principal = 2,060.95 - interest - 666.99 = (1,293.96 - interest) + 100. Extra in principal. ✓
- Cash escrow $666.99 **==** split escrow $666.99. ✓ **MATCH.**

**Contrast (why the field and date-aware cash are required):** Without them, entering the change on
Nov 15 pins the new version to Nov 15. December then wrongly bills the new escrow, while its cash
and split disagree over which figure applies. The shared as-of function plus the effective-date
field is what makes December provably stay at the old amount, and the invariant hold no matter when
the operator entered the change.

## 10. Blast radius (files, traced 2026-07-06)

- **Models:** new `app/models/escrow_line.py` (`EscrowLine`); rewrite
  `app/models/loan_features.py::EscrowComponent` -> `EscrowComponentVersion` (drop `name`, drop
  `end_date`, drop `is_active_on`; add `line_id`, `is_removed`). Update `app/models/__init__.py`
  registration.
- **Audit:** update `app/audit_infrastructure.py::AUDITED_TABLES` -- both new tables added, old
  table name replaced (both live in `budget`, so both need triggers per
  `.claude/rules/database.md`).
- **The escrow-as-of function + calculator:** `app/services/escrow_calculator.py`
  (`escrow_monthly_as_of` added; `calculate_monthly_escrow` becomes a thin `on_date=today` wrapper
  or is folded in; keep the sum-then-round + largest-remainder display allocation).
- **Loaders:** `app/services/loan_loaders.py` -- `load_active_escrow_components` -> "lines with
  their as-of-today version, non-removed"; `load_all_escrow_components` -> "lines with all versions"
  (join). Keep return shapes consumable by `escrow_monthly_as_of`.
- **Split walk:** `app/services/loan_posting_service/_walk.py:405-408,484` -> group by line, resolve
  as-of via the shared function.
- **Cash path:** `app/services/loan_payment_service.py` -- `live_loan_transfer_amounts` (`:684-696`)
  per-shadow escrow; `load_loan_context` (`:120-123`) + `prepare_payments_for_engine` call site
  (`:161-164`) date-aware; `_resolve_loan_piti` (`:612`).
- **Routes/schemas:** `app/routes/loan/escrow_rates.py` (add/edit/rename/remove/merge; the OOB
  summary tail), `app/routes/loan/_helpers.py` (`EscrowComponentSchema` gains `effective_date`;
  `_compute_total_payment`), `app/routes/loan/payment_transfer.py` (`_resolve_transfer_amount` ->
  base + `extra_principal`, both modes), `app/routes/loan/dashboard.py:118-135,161-163` (inflation
  note + breakdown via the shared function). `app/schemas/validation.py::EscrowComponentSchema`.
- **Overpayment home (decision B):** new `app/models/loan_payment_settings.py` +
  `budget.loan_payment_settings` table; move `derive_from_loan` off `transfer_templates` into it and
  repoint its readers (`payment_transfer.py`, `loan_payment_service.py:675`); add to
  `AUDITED_TABLES`.
- **Projection:** thread `extra_principal` into `build_baseline_scenarios` (`_helpers.py:410-451`).
- **Other escrow consumers:** `savings_dashboard_service/_data.py:88`,
  `savings_dashboard_service/_metrics.py:414` (classify as today's-set), and any escrow display
  partials (`loan/_escrow_list.html`).
- **Migration(s):** restructure `escrow_components` -> `escrow_lines` + `escrow_component_versions`;
  backfill (Sec. 11); (if B-ii) move `derive_from_loan` + add `extra_principal`. Destructive ->
  `Review:` docstring line + operator approval.
- **Tests:** Sec. 13.

## 11. Migration and backfill (byte-identical behavior on current data)

Restructure, not a column add -- and split into an **expand** phase (Commit 1) and a **contract**
phase (Commit 2) so every commit is independently green and the old table is never live-dropped in
the same commit that repoints its readers.

**Expand (Commit 1 -- SHIPPED as migration `c4f8a1b6e9d2`, additive only):**

1. Create `escrow_lines` and `escrow_component_versions`; leave `escrow_components` in place and
   unchanged.
2. For each existing `escrow_components` row, ensure an `escrow_lines` row exists for its
   `(account_id, name)` (one line per distinct name -- see the documented limitation below), then
   insert a non-removed version under it carrying `effective_date`, `annual_amount`,
   `inflation_rate`.
3. **Removal mapping:** a formerly-closed row (old `end_date` set, no adjacent successor) becomes a
   tombstone version `is_removed = true` at that `end_date`. A zero-length collapsed row (the
   account-3 `f2a7c1e9b4d3` fix, `effective_date == end_date`) contributed nothing and is DROPPED
   (no version). An **overlap guard** aborts the migration if any two non-collapsed same-line rows
   overlap in date (an unresolvable different-amount duplicate), failing safe rather than mangling
   data -- confirmed zero such overlaps on the dev prod clone.

**Contract (Commit 2 -- reader cutover):** repoint every escrow consumer onto the new tables via the
Sec. 3 `escrow_monthly_as_of` function, then DROP `escrow_components` (that migration is the
destructive one and carries its own `Review:` line).

- **Documented limitation (unchanged from the reviewed proposal, and correct):** a migration cannot
  know that two differently-named historical rows were the same renamed line -- that needs operator
  knowledge. So each distinct name backfills to its own line; the operator uses the new **merge**
  tool (Sec. 7) to rejoin any split history. Account 3's old name was already collapsed by
  `f2a7c1e9b4d3`, so it backfills to a single active line with no manual step.
- **Re-sync postings:** correcting nothing on current data, but the deploy hook
  (`scripts/init_database.py` -> `loan_posting_service.backfill_all_loan_postings`) re-splits after
  the chain reaches head, as `f2a7c1e9b4d3` documents (relevant once Commit 2 makes the ledger read
  the new escrow).

On current data every payment still sees $616.99 (one line, one version at origination), so temporal
escrow changes nothing until a *future* effective-dated version is added. **Downgrade** of the
expand migration simply drops the two new tables (their audit triggers cascade); `escrow_components`
was never touched, so it is a lossless reversal. The contract migration's downgrade reconstructs
`escrow_components` from lines + versions (`name` from the parent, `end_date` from the next
version's `effective_date` or the tombstone's date, `is_removed` tombstones back into closed
ranges), with the documented caveat that a post-upgrade merge is not losslessly reversible, per
`.claude/rules/database.md`.

## 12. RESOLVED DECISIONS (approved by the operator 2026-07-06)

- **A. Model -- APPROVED.** Supersession (no `end_date`, tombstone removal) as specified. The only
  cost vs. ranges: the split walk gains per-line as-of resolution (Sec. 3) instead of a per-row
  filter -- accepted.
- **B. Home for `extra_principal` (and `derive_from_loan`) -- APPROVED: new
  `budget.loan_payment_settings` table** (Sec. 6.3). Retires the existing smell rather than
  compounding it.
- **C. Active-name uniqueness -- APPROVED: route-enforced** (Sec. 2.1), no drift-prone mirror.
- **D. Manual-mode escrow caveat -- APPROVED/ACCEPTED.** In manual (typed-base) mode cash does not
  track escrow, so the operator owns any cash==split gap for that payment (the split still keys
  escrow as-of). Auto-derive is the default and the operator's preference, so this is an accepted
  edge.

## 13. Test plan

- **Unit -- `escrow_monthly_as_of`:** greatest-effective-<=-D per line; tombstone -> 0; multi-line
  sum; ordering independence; sum-then-round + display allocation preserved.
- **Invariant -- cash == split:** the Sec. 9 Nov/Dec/Jan case, hand-computed, asserting each
  payment's cash escrow equals its split escrow and the extra lands in principal; plus an
  effective-date-in-the-settle-window case proving the guard blocks the desync.
- **Guard:** an escrow effective date on/before the latest settled payment is rejected; strictly
  after is accepted (reuse the tracking-start guard fixtures).
- **Rename immutability:** rename a line, re-run the loan posting oracle, assert byte-identical
  postings.
- **Merge:** two split lines merged -> one line, versions reconciled, oracle reconciles.
- **Overpayment:** auto-derive + `extra_principal` -> cash includes extra, split principal += extra,
  committed-forward payoff accelerates; near-payoff extra caps to balance with surplus -> Refund.
- **Inflation:** forward note compounds latest version from today (not `created_at`); old line shows
  no spurious jump.
- **Migration up/down** + backfill derivations (one line per name, tombstone from old close,
  collapsed row dropped); full loan split/oracle suite stays penny-exact; `pylint app/` 10.00; full
  suite as the final gate.

## 14. Sequencing (each commit independently green: targeted tests + pylint 10.00 + code-reviewer)

1. **Escrow schema EXPAND** (create `escrow_lines` + `escrow_component_versions`, backfill from
   `escrow_components` which is KEPT live, audit wiring, up/down verified) -- additive, no behavior
   change. SHIPPED (migration `c4f8a1b6e9d2`).
2. **Reader cutover + CONTRACT:** `escrow_monthly_as_of` + loaders + split walk onto the shared
   function (parity: current data unchanged; the split oracle stays penny-exact), then DROP
   `escrow_components` (destructive migration, `Review:` line). `loan_payment_settings` (decision B)
   and its `derive_from_loan` move land with the overpayment work (step 5), not here. SHIPPED as
   **two commits** (operator's decision, honouring the Sec. 11 principle that the old table is never
   dropped in the same commit that repoints its readers): **2a** -- reader cutover with
   `escrow_components` KEPT live-but-unused (commit `75593bcd`); **2b** -- the destructive DROP
   migration `d7b2f9a4c1e6` (reconstruct-on-downgrade), model/loader/audit removal, and old-model
   test cleanup. Real account-3 data verified byte-identical ($616.99 per split) and the migration
   up/down/up round-trip ran clean on the dev DB.
3. **Effective-date field + forward-only guard + version drawer.** SHIPPED (commit
   `feat(escrow): effective-date field + version drawer with forward-only guard`). Built the
   operator-facing effective-date field, the forward-only guard (Sec. 4.2 as-built:
   `latest_settled_payment_period_start`, keyed on the pay-period start, boundary-can-be-future,
   applied to every write path incl. both deletes and the line remove), and -- per the operator's
   scope choice -- the loan-detail escrow card rebuilt into a per-line **version-history drawer**:
   `escrow_calculator.build_escrow_card` (reuses `resolve_active_lines` + `build_escrow_display`, so
   the cent-allocation invariant holds; shows not-yet-active lines so a scheduled line never
   silently vanishes; drops an orphaned empty line), routes `add_escrow_version` /
   `edit_escrow_version` / `delete_escrow_version` (scheduled-only) / `rename_escrow_line`
   (rename-in-place pulled forward from step 6; provably cannot move a split), Bootstrap-collapse
   drawer (CSP-safe), and `escrow_card.js` for guard-message surfacing. Additive -- no migration.
   ~27 tests (guard incl. max-not-min and the early-settle `boundary > today` regime on both delete
   paths, all CRUD, cross-account version IDOR, drawer display, upcoming-line + orphan cleanup);
   pylint 10.00; full suite green; adversarial reviewer CONFIRMED-FIXED (it caught the early-settle
   delete-guard gap, which was then closed).
4. **Date-aware cash.** SHIPPED (commit `05ecd956`, prior session): `live_loan_transfer_amounts`
   per-shadow via `_shadow_live_amount`; `prepare_payments_for_engine` per-payment as-of; plus
   capture-on-settle (Option A) freezing the live payment-date amount at settlement.
5. **Overpayment** (`extra_principal` home per decision B; both modes; projection threading).
   SHIPPED as **four commits**. **5a** (`d2e07f90`): the `budget.loan_payment_settings` table (1:1,
   carrying `derive_from_loan` and `extra_principal`), the `derive_from_loan` reader cutover, and
   the expand migration. **5b** (`954a8fa6`): dropping the old `transfer_templates.derive_from_loan`
   column. **5c** (`cb8a5ba8`): the standing extra into the LIVE cash (both modes),
   capture-on-settle, the create/edit routes, and the dashboard control. **5d**: the projection,
   where `project_forward` applies the extra to override months too (operator Q3 ratification),
   `compute_payoff_scenarios` and `target_date_outlook` gain an `extra_principal` parameter,
   threaded via `loan_standing_extra` into the dashboard band chart, the payoff lever, and the
   target-date calculator. cash==split-with-extra was proven to the cent; each commit was
   adversarially reviewed (5c fixed one Medium; 5d deferred one High, see Section 16, and addressed
   two Low). The one open follow-up is now planned as step 8 (Section 16).
6. **Merge (the operator action that rejoins a line history split in two). SHIPPED** (commit
   `b7bfe00b`, 2026-07-07; Section 17). Rename in place already shipped in step 3. As built: an
   invariant-checked planner (escrow-per-date byte-identical before/after) REPLACES the "repoint +
   reconcile" sketched here -- no reconcile is needed and it is strictly safer. Detail in Section
   17.
7. **Inflation fix. SHIPPED** (commit `83be140c`, 2026-07-07; Section 17). Projects one full annual
   step from **today** (operator decision), not the spec's literal "to next Jan 1"; `created_at` is
   removed from the inflation math entirely. Detail in Section 17. (`/update-docs` remains, to run
   after the PR to `dev`; the full suite is the final gate before the PR.)
8. **Plan-aware forward trajectory (resolver-seam fix). SHIPPED** (commit `a6ff83b6`, 2026-07-07).
   Detailed in Section 16. Made `LoanState` carry the committed (plan) trajectory so net worth,
   `/savings`, the schedule page, year-end, and the recurrence-`end_date` writer all reflect the
   standing extra; refinance stays contractual-vs-contractual. The seam-injection wrappers were
   extracted to `app/services/loan_resolution.py` (operator decision, to clear the
   `loan_payment_service` size ceiling). Independent of steps 6-7; a read-path fix, no migration.

## 16. Step 8: plan-aware forward trajectory (the resolver-seam fix)

**Status: SHIPPED to branch `feat/escrow-config-redesign` 2026-07-07** (implementation `a6ff83b6`;
plan doc `21974f0b`). Full suite 7293 passed; `pylint app/` 10.00; no migration. The optional
checker (8d) is deferred. See 16.7 for the as-built notes (including the extraction that was not in
the original plan). Supersedes the step-5 deferred follow-up (H1, "cross-surface payoff
consistency"), which a fresh-session adversarial review (2026-07-07) found under-scoped: the
divergence is not a payoff-date label, it is a **net-worth correctness defect** plus a
**cash-flow writer** that generates phantom post-payoff payments. This step fixed all of it at one
seam.

**The mechanism (traced 2026-07-07).** `compute_payoff_scenarios` (`loan_resolver/_payoff.py:342`)
already emits both stories from one replay: `original_forward` (pure contractual, the lender
minimum) and `committed_forward` (the real plan -- projected recurring payments routed through
`monthly_override` plus the standing `extra_principal`, `_payoff.py:464-468`). The bug is entirely
in consumption. `resolve_loan` -- documented as "the single-source-of-truth producer every
loan-touching surface reads through" (`_state.py:3,:132`) -- calls that engine with
**confirmed-only payments** (`_state.py:206-210,:234`) and **no `extra_principal`**, so its
`committed_forward` collapses to contractual and `LoanState.schedule / payoff_date / total_interest`
are contractual (`_state.py:239-278`). The loan **detail** page bypasses `LoanState` and calls the
engine itself with all payments plus the standing extra (`_helpers.build_baseline_scenarios`,
`:445-495`), so it alone is plan-aware. Every other surface reads the contractual `LoanState`.

### 16.1 Why this is a correctness bug, not a conservative baseline

The two legs of a projected loan payment are computed from two different stories, and they must not
be. The cash leg debits checking by the **live** amount, which **includes** the standing extra:
`live_loan_transfer_amounts` -> `_shadow_live_amount = P&I + escrow + extra_principal`
(`loan_payment_service.py:698`), threaded into the cash projection at `balance_resolver.py:419`. The
liability leg walks the **contractual** `state.schedule`
(`net_worth_kernel.generate_debt_schedules:181` ->
`account_projection.balance_from_schedule_at_date:196`), so the extra never reduces it.
`live_loan_transfer_amounts` states the split outright (`loan_payment_service.py:930-935`): "the
checking expense leg moves the checking balance; the loan income leg does not affect the loan
balance (that is resolver-derived)."

Per-month net-worth change under a standing extra `E`:

```text
today (contractual liability):  checking -(P&I + escrow + E),  loan -(P&I - interest)      => net -(escrow + interest + E)   WRONG
fixed (committed liability):    checking -(P&I + escrow + E),  loan -(P&I - interest + E)  => net -(escrow + interest)       CORRECT
```

Paying extra principal is net-worth-neutral (cash becomes home equity); today the app destroys `E`
of net worth every projected month. For the operator's $250 standing extra that is about -$3,000 one
year out and -$15,000 five years out, widening as the committed balance amortizes faster. The
"conservative baseline" defense is invalid: once the app projects the extra **leaving** checking,
projecting the debt contractually is not conservative, it is internally inconsistent. The only
self-consistent postures are project-both (committed) or project-neither; today projects cash but
not debt.

### 16.2 Decisions (locked)

- **A. `LoanState` carries the COMMITTED trajectory.** Its `schedule / payoff_date / total_interest`
  become plan-aware (real recurring payments plus the standing extra). A budgeting app projecting
  *your* finances defaults to *your* plan, not the lender minimum. This is a deliberate reversal of
  the confirmed-only choice at `_state.py:226-232`; that default was wrong for this app.
- **B. Contractual stays available from the SAME producer.** The pure-contractual reference is
  `compute_payoff_scenarios(...).original_forward`, which is unconditionally override-free and
  extra-free (`_payoff.py:459-463`) regardless of the extra passed. No second engine, no second
  boundary rule -- surfaces that want the baseline read this slice explicitly.
- **C. Refinance is contractual-vs-contractual (locked, operator 2026-07-07).** A like-for-like
  comparison holds the extra constant on both sides; minimum-vs-minimum is the honest baseline, so
  the current side reads `original_forward`, not `state.schedule`. See 16.5.
- **D. Debt-strategy is out of scope.** Its baseline deliberately means "pay minimums" and
  re-simulates from `state.monthly_payment` (`routes/debt_strategy.py:177`), not the schedule, so
  this change does not touch it. Whether that baseline should fold in a persisted standing extra is
  a separate product question (16.9).

### 16.3 The seam change, and why it is safe

In `resolve_loan` (`_state.py:125`): stop stripping payments to confirmed-only and thread the
standing extra into its existing `compute_payoff_scenarios` call, so it composes exactly as
`build_baseline_scenarios` does:

- pass the full `loan_inputs.payments` (the composer already partitions confirmed-pre-`as_of` into
  replay and everything else into the forward override, `_payoff.py:170-186`, so passing the full
  list is what routes projected recurring payments forward), and
- pass `extra_principal` (new resolve-time parameter, default `Decimal("0.00")`), forwarding it to
  `compute_payoff_scenarios(..., extra_principal=...)`.

`state.schedule = history_rows + committed_forward` is unchanged in shape (`_state.py:239-241`); it
simply becomes plan-aware, and `payoff_date` / `total_interest` follow from it (`:271-278`).

**Provably safe for the headline figures.** The three figures every card shows are derived
independently of the schedule and are untouched:

- `current_balance` = `_replay_from_anchor(loan_inputs, ...)` or `confirmed_view.balance`
  (`_state.py:253-257`) -- a separate call that already receives the full `loan_inputs` and does not
  read the composer's payment view. The resolver docstring makes the guarantee explicit: "the
  resolver owns its balance derivation so a future projection change cannot silently change
  `state.current_balance`" (`:162-165`).
- `monthly_payment` / `current_rate` = the rate-period engine at `as_of` (`_state.py:265-267`),
  independent of the schedule.

So only `schedule / payoff_date / total_interest` move, which is the intent.

### 16.4 Where the standing extra enters (one chokepoint, no drift)

`resolve_loan_seeded` (`loan_payment_service.py`) is the single injection helper the three db-facing
summary loaders route through (`resolve_account_loan`, the loan route's `_resolve`, and the savings
`_compute_loan_account`) -- it exists to centralize the read-switch's confirmed-view injection "so
they cannot drift on HOW the ledger feeds the resolver." Centralize the standing-extra injection at
the SAME chokepoint, for the same reason: `resolve_loan_seeded` loads
`loan_standing_extra(account_id, user_id)` (the existing cycle-free leaf helper,
`recurring_transfer_query.py:55`, already used by the detail page) once and passes it into
`resolve_loan`. This makes it structurally impossible for a summary surface to resolve a loan's
trajectory WITHOUT its plan: a new caller cannot silently regress to contractual, because the
chokepoint owns the load. The pure `resolve_loan` keeps its defaulted
`extra_principal=Decimal("0.00")` for the rare direct callers (the `date.max` "ever paid off" probe,
where the boolean is unaffected).

**Stability (no feedback loop).** `project_forward` applies the standing extra to every forward
month until the balance reaches zero, whether or not a projected payment row exists that month (the
5d behavior, `_payoff.py:464-468`), so the committed payoff is a function of (balance, P&I, escrow,
extra), NOT of how far the recurrence extends. Setting the recurrence `end_date` to that payoff
(16.6, the writer) is therefore a stable fixed point.

### 16.5 Refinance stays contractual-vs-contractual

`_build_refinance_comparison` (`calculators.py:365`) currently measures the current side from
`state.schedule` forward rows (`:410,:422`). After 16.3 that slice is committed, which would compare
an accelerated current loan against a minimum-payment refi -- an unfair mix. So this commit repoints
the current side to the contractual `original_forward`:

- `current_total_interest` = `sum(row.interest for row in scenarios.original_forward)`,
- `current_remaining_months` = `len(scenarios.original_forward)`,
- `current_payoff` = `original_forward[-1].payment_date`,
- `current_monthly` = `state.monthly_payment` and `current_principal` = `state.current_balance` stay
  as-is (both unaffected by committed-vs-contractual).

The `scenarios` come from the one shared producer (reuse `build_baseline_scenarios`;
`original_forward` is contractual regardless of the extra passed), so refinance reads the
contractual baseline from the same seam every other surface reads -- an explicit "this surface wants
the minimum" opt-in, not an accident of which producer it happened to call. Correct the misleading
`schedule.py:7-9` and `_build_refinance_comparison` docstrings that claim "committed... with actual
payments."

### 16.6 Surfaces this one change corrects

| Surface | Reads | Effect of Step 8 |
|---------|-------|------------------|
| Net worth / year-end debt-progress | `state.schedule` via `generate_debt_schedules` | plan-aware -> net worth correct (16.1) |
| Year-end mortgage-interest (tax) | `sum(debt.schedule interest)` (`_income_tax.py:236,255`) | the interest actually paid (less; correct for the deduction) |
| `/savings` debt tile payoff | `state.payoff_date` (`_projections.py:172`) | matches the detail page |
| Standalone schedule page | `ctx.state.schedule` (`schedule.py:43`) | matches the card's band chart (its docstring's promise finally holds) |
| Recurrence `end_date` **writer** | `projected_payoff_end_date(state.schedule)` (`loan_recurrence_sync.py:106`) | generates only to the REAL payoff -> kills phantom post-payoff payments |
| Refinance current side | repointed to `original_forward` (16.5) | stays contractual, like-for-like |
| Detail page | already committed via `build_baseline_scenarios` | unchanged; `ctx.state` now agrees with its own band chart |

The recurrence-writer row is the sharpest instance the H1 finding omitted: today the writer sets the
recurring payment's `end_date` to the CONTRACTUAL payoff, so a loan retiring early under its plan
keeps generating years of mortgage payments (about $120k of phantom checking debits over a five-year
gap) after it is really paid off. Step 8 fixes it for free.

### 16.7 Commits (as built)

Shipped as ONE implementation commit (`a6ff83b6`), not the three planned below, because the
extraction (8b) coupled them and each part is green only together. The 8a/8b/8c decomposition stayed
the working order; the plan doc is `21974f0b`.

1. **8a -- red test first. SHIPPED.** `test_standing_extra_payoff_consistent_across_surfaces`
   (`test_loan_unified_figures.py`) asserts the summary seam (`resolve_account_loan`) and the
   year-end debt aggregation share the committed detail trajectory's payoff AND life-of-loan
   interest for a loan with a standing extra -- red on the old contractual code (summary Jan-2056 vs
   committed Sep-2043). The direct per-month net-worth arithmetic (16.1) is covered by the
   schedule-equality PROXY (the cash leg already carries the extra, so proving the liability
   schedule is committed IS net-worth consistency); a literal per-month assertion was NOT added. A
   sibling pure-resolver test (`test_projected_overpayment_routes_into_the_forward_schedule`) locks
   the "stop stripping to confirmed-only" half.
2. **8b -- the seam + extraction. SHIPPED.** 16.3 (full payments + `extra_principal` in
   `resolve_loan`) and 16.4 (central injection via `loan_standing_extra_for_account`). To keep
   `loan_payment_service` under its 1000-line ceiling WITHOUT trimming (operator decision
   2026-07-07, "re-apply with extraction"), the two resolver-seeding wrappers (`resolve_loan_seeded`
   / `resolve_account_loan`) moved to a NEW `app/services/loan_resolution.py`; six importers
   repointed, the ledger-read fence in `test_posting_ledger_loan_reconciliation.py` updated (only
   `confirmed_loan_view` remains a `loan_payment_service` read-switch reader). This extraction was
   not in the original plan; it is the cleaner resolution of the ceiling.
3. **8c -- refinance. SHIPPED.** 16.5 (current side reads `original_forward`; the `schedule.py` and
   `_build_refinance_comparison` docstrings corrected). The refinance unit test was rewritten for
   the new `scenarios` signature (adversarial-review finding C1).
4. **8d (optional) -- lock it. DEFERRED.** The pylint checker fencing a "your projection" surface
   from a raw contractual forward was not built -- a follow-up if the split ever regresses.

### 16.8 Verification

- **Done (automated):** the cross-surface invariant test (8a) locks that the summary seam and
  year-end share the committed detail trajectory for a standing-extra loan; the part-a test locks
  the projected-payment routing. Full suite **7293 passed**; `pylint app/` **10.00**; no migration
  (a read-path fix, no schema change). Adversarial review CONFIRMED the core logic (balance
  unchanged, no double-count) and surfaced C1 (fixed) + coverage/docstring notes (addressed).
- **Remaining (manual acceptance):** a dev prod-clone spot-check on account 3 with a standing extra
  -- confirm net worth, `/savings`, schedule page, detail band chart, and year-end agree to the cent
  in the running app, and that the recurring payment's `end_date` equals the committed payoff
  (payments stop there).

### 16.9 Carve-outs and out-of-scope

- **L1 (display-only, pre-existing).** A CONFIRMED payment dated after `as_of` routes to the forward
  override carrying its FROZEN actual (base + standing extra frozen at settlement), so that one
  month's forward chart double-applies the extra. The ledger balance is authoritative, and it needs
  the rare data-hygiene case of a settled future-period payment. Documented as a carve-out in
  `compute_payoff_scenarios`; Step 8 widens its surface (all summary surfaces, not just detail) but
  does not change its display-only nature.
- **L2 (done in 5d).** The "override suppresses the searched extra" docstrings in
  `amortization_engine/_payoff.py` (`_search_extra_for_payoff` / `required_extra_for_projection`)
  were corrected to the step-5 behavior (the searched extra applies to every forward month).
- **Debt-strategy baseline (decision D).** Reads `monthly_payment`, not the schedule, so it is
  unaffected and out of scope. Whether its "pay minimums" baseline should fold in a persisted
  standing extra is a separate, lower-stakes product decision.
- **Level 2 (materialized loan postings).** Orthogonal: even with sum-of-postings balances,
  projected postings must be generated from *some* trajectory, so the committed-vs-contractual
  choice still has to be made. Step 8 is the right next move regardless and is compatible with a
  later Level 2.

## 17. Steps 6 & 7: merge tool and inflation fix (as built)

**Status: both SHIPPED to branch `feat/escrow-config-redesign` 2026-07-07.** These were the last two
steps; the redesign is complete pending the PR to `dev`. Each was implemented, targeted-tested, held
to `pylint app/` 10.00, and adversarially reviewed in a fresh subagent before commit. On the
operator's real data neither fixes a live break -- account 3 carries no inflation rate (step 7 is a
latent-bug fix) and its split history reconciles correctly across the two lines (step 6 is a
history-reunification convenience) -- but both complete the model correctly and were the operator's
explicit choice to build.

### 17.1 Step 7 -- domain-dated escrow inflation projection (commit `83be140c`)

The loan card's "Escrow may increase to ~$X/month next year" note
(`dashboard._project_next_year_escrow`) compounded inflation off each version's `created_at`, a
technical insert timestamp: the expand backfill wrote `now()`, and a same-day version edit resets
it, so the projection base was arbitrary (and an old line could show a spurious multi-year jump).
Fixed per Sec. 8, with one operator decision that DEVIATES from Sec. 8's literal wording:

- **Projection semantic (operator decision 2026-07-07): ONE full annual step from today** --
  `amount * (1 + rate)` -- NOT the spec's literal "compound to next civil Jan 1." Rationale: the
  Jan-1 target makes the projected figure depend on the month you view it in (it shrinks toward zero
  change as the year passes); one annual step is stable regardless of viewing date and matches the
  per-year meaning of `inflation_rate`. Worked: $7,403.88/yr @ 3% -> $7,626.00/yr -> ~$635.50/mo,
  the same whether viewed in January or November.
- **DRY separation.** `escrow_calculator.calculate_monthly_escrow` lost its `as_of_date` param and
  inflation branch -- it is now a pure sum, so the loan-payment split can never leak inflation
  *by construction*. A new `project_monthly_escrow(components, years)` owns the forward compounding
  (whole annual steps, sum-then-round E-26 preserved). `calculate_total_payment` lost its
  never-passed `as_of_date`; `ResolvedEscrowLine` lost `created_at` (its only consumer was the old
  branch).
- **Blast radius:** `escrow_calculator.py` + `dashboard.py` only; read-path, no migration. The note
  is dormant on real data (no inflation rate set), so this corrects the math for any line that ever
  sets one. Adversarial review CONFIRMED clean (fixed one stale docstring it caught).

### 17.2 Step 6 -- merge tool (commit `b7bfe00b`)

Reunifies a line whose history split across two DB lines -- a legacy rename-split backfill (one line
per historical name) or an operator Remove+Add. The mortgage carries exactly this shape on the dev
prod-clone (an origination line tombstoned 2026-07-06 plus a new-name line from that date), so the
capability the earlier proposal lacked is real, not hypothetical.

- **Correctness by construction, not by reconcile (deviation from Sec. 7, and the reason it is
  safe).** `escrow_calculator.plan_escrow_line_merge(source, target)` builds the unified version set
  (every target version kept; each source version the target lacks on that date is moved; a
  same-date collision keeps the target's and drops the source's) and then VERIFIES via
  `_escrow_unchanged_by_merge` that `escrow_monthly_as_of` is byte-identical before (the two lines
  summed) and after (the single merged line) at every date in the union of both lines' effective
  dates. Escrow is a right-continuous step function whose only breakpoints are effective dates, and
  both sides share that breakpoint set, so agreement there is agreement on EVERY calendar date. When
  a date would change, the lines genuinely overlap (two concurrent charges, not one renamed line)
  and the merge is REJECTED -- so it can neither move a settled payment's split nor silently drop a
  real charge. The forward-only guard is subsumed (preserving escrow on every date preserves it on
  settled dates too).
- **No posting reconcile (the Sec. 7 "merge reconcile" is retired).** Because escrow-per-date is
  preserved AND the split stores the escrow *amount* -- never a line id (the only FK to
  `escrow_lines` is `escrow_component_versions.line_id`; verified) -- existing postings stay
  byte-identical and a later reconcile re-derives the same. The spec's "repoint then re-derive" was
  actually LESS safe: it would move a settled split if the merge changed escrow. A test settles a
  payment, merges, and asserts the posted principal/interest/escrow are byte-identical with no
  reconcile, then identical again after one.
- **Mechanics.** The route repoints surviving versions (`version.line = target`), flushes to persist
  the new `line_id`, then deletes the source so its `all, delete-orphan` cascade removes exactly the
  collided drops (no explicit per-version delete -> no double-delete). Ownership is checked for BOTH
  the URL target and the posted `source_line_id` (404 for foreign/missing). The drawer offers a
  "Merge in" control listing the account's other lines -- including hidden fully-removed
  predecessors, labelled by effective span + removed state -- as sources, so a hidden split line is
  reachable without un-hiding removed lines from the card.
- **Adversarial review** found no critical/high/medium, independently verified the boundary-date
  invariant is complete (and noted it additionally forces version-identity, so
  `project_monthly_escrow`'s raw-amount/inflation reads are preserved too), and confirmed the
  no-reconcile and IDOR posture. Its 3 Low notes (a docstring IFF overclaim, a cosmetic blank line,
  a rejection-message wording) were addressed.

## 15. Related

- `docs/audits/balance_architecture/recurring_loan_balance_root_cause.md` -- the north star
  (materialize, don't recompute; one answer per balance question).
- `docs/audits/balance_architecture/implementation_plan_temporal_escrow.md` -- the range-model
  temporal escrow this supersedes; its cash==split analysis (Sec. 2) and no-future-dating assumption
  (Sec. 2 as-built) are what Sec. 5 here deliberately changes.
- `docs/design/loan_audit.md` -- the loan-detail overhaul that surfaced the account-3 double-count.
- `migrations/versions/f2a7c1e9b4d3_...py` -- the data-only fix for that double-count (the collapsed
  row this backfill drops).
