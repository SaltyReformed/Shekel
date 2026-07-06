# Escrow config redesign: line identity, effective dates, date-aware cash, overpayment

Status: BUILDABLE SPEC (proposed, not started). Written 2026-07-06. **Supersedes** the earlier
"escrow line identity refactor" proposal that this file used to hold (see git history) -- an
adversarial review of that proposal in a fresh session expanded the scope from "add a `line_id`
column" to the full, correct redesign below. **Decisions A-D approved by the operator 2026-07-06
(Section 12); the design is locked and ready to build** per the Section 14 sequence.

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

> **Guard:** a new or edited escrow version's `effective_date` must be strictly after the pay-period
> start of the loan's latest **settled** payment.

This mirrors the existing tracking-start guard exactly
(`loan_loaders.earliest_settled_payment_due_date`, `loan_loaders.py:538-569`) -- reuse that
derivation (`_settled_payment_due_dates`) rather than re-spelling "which payments are settled." The
route rejects a violating effective date with an actionable message naming the boundary date.

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
   and its `derive_from_loan` move land with the overpayment work (step 5), not here.
3. **Effective-date field + forward-only guard** (route + schema + the December-window test).
4. **Date-aware cash** (`live_loan_transfer_amounts` per-shadow; the two other consumers; the Sec. 9
   invariant test).
5. **Overpayment** (`extra_principal` home per decision B; both modes; projection threading).
6. **Rename in place + merge** (immutability test; merge reconcile).
7. **Inflation fix** + docs + `/update-docs`. Full suite is the final gate.

## 15. Related

- `docs/audits/balance_architecture/recurring_loan_balance_root_cause.md` -- the north star
  (materialize, don't recompute; one answer per balance question).
- `docs/audits/balance_architecture/implementation_plan_temporal_escrow.md` -- the range-model
  temporal escrow this supersedes; its cash==split analysis (Sec. 2) and no-future-dating assumption
  (Sec. 2 as-built) are what Sec. 5 here deliberately changes.
- `docs/design/loan_audit.md` -- the loan-detail overhaul that surfaced the account-3 double-count.
- `migrations/versions/f2a7c1e9b4d3_...py` -- the data-only fix for that double-count (the collapsed
  row this backfill drops).
