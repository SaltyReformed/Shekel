# Implementation plan: the loan read switch (genesis posting ledger)

Status: IN PROGRESS -- reconciled as-built through Commit 3 (2026-07-01). Written 2026-07-01 after an
adversarial review of the anchor-based draft
(`~/.claude/plans/i-have-finished-docs-audits-balance-arch-squishy-snowglobe.md`, superseded) and a
firsthand code trace. The developer chose the genesis (opening-equity) design over the anchor-based
intermediate, chose to fix the mortgage-interest-for-taxes surface in this arc, and (2026-07-01) chose
**FULL** -- retire `LoanAnchorEvent` -- over MINIMAL for the read switch (Section 2).

This is the deferred second half of Build-Order Step 4 of Option D
(`level1_level2_scope_and_fitness.md`, build-order item 4: "Post confirmed loan payments with their
real principal / interest split; **retire the read-time replay of confirmed history**"). Step 4
shipped WRITE-ONLY (PR #51); this plan makes the ledger the authoritative source for a loan's
confirmed balance and retires the resolver's confirmed replay.

## As-built through Commit 3 (2026-07-01, on `dev`, not yet PR'd)

Commits 1-3 are done and committed. Two design changes were made during Commit 3; **this section governs
where it disagrees with Sections 3.1-3.3 below** (those are kept for rationale but describe the
pre-Commit-3 design -- the code is the unified design described here):

1. **The walk RESETS at every anchor -- Section 3.2 as-written was WRONG.** Section 3.2's "the
   running-balance walk is unchanged; only the starting point and the eligibility lower bound change" is
   INCOMPLETE and would fail this plan's own penny-exact oracle for every trued-up loan (every fixture +
   the real Mortgage): a from-origination walk that does NOT reset at a user-trueup accrues post-trueup
   interest on the wrong balance. The walk seeds at zero and, in ONE chronological merge of anchors +
   payments, RESETS the running balance to each anchor's verified value as it is reached (a payment due
   exactly on an anchor date sorts BEFORE the reset, so it is subsumed -- the strict
   `anchor_date < monthly_due_date` boundary, pinned by a dedicated test). Proven (unit + code-reviewer's
   independent re-derivation): `-(sum of linked postings)` reproduces the resolver penny-for-penny on a
   trued-up loan (the pre-anchor payment corrections cancel against the anchor correction; the pre-anchor
   payments' interest still lands in the interest ledger).

2. **Opening and true-up are ONE mechanism ("anchor correction").** Sections 3.1 and 3.3 describe them
   separately; they are the SAME operation: each anchor posts `owed_before - anchor_balance` to the
   loan-linked ledger and its negative to the per-loan `equity_opening` account, tagged OPENING for the
   origination anchor (where `owed_before` is 0, giving `-original_principal`) and TRUEUP for a
   user-trueup. `sync_loan_anchor_corrections` reconciles them, keyed by `(source_kind_id, entry_date)`,
   self-healing when a pre-trueup payment moves an earlier anchor's `owed_before`.

**Structure:** the walk change pushed `loan_posting_service.py` (863 lines) over the 1000-line gate, so it
is now a package (mirroring `loan_resolver`):
`loan_posting_service/{_walk,_common,_payments,_anchors,_sync,__init__}.py`. Public API and every caller
unchanged.

**Commits (on `dev`, not yet PR'd):** C1 ref migration `f8a93f1` (docs `2550590`); C2 chart resolver
`3ce8f9b`; C3 opening+true-up posting service `0a0370c` + test-constant dedupe `a7c4a76`. C3's
`sync_loan_anchor_corrections` is built + unit-tested but UNWIRED (that is Commit 4); the payment-walk
change is wired but inert on reads. Full suite 6781; `pylint app/` 10.00; adversarial code-reviewer no
Critical/High.

---

## 1. What changed from the anchor-based draft, and why

The prior draft used an **anchor-based reader**: `owed = latest_anchor - Sigma(post-anchor eligible
principal)`. It read the loan anchor (`LoanAnchorEvent`) live at read time and filtered the ledger to
post-anchor payments. That is a sound increment, but it is not the from-scratch design: it keeps the
external anchor load-bearing and keeps a read-time boundary filter, which is exactly the
"recompute-with-a-boundary-rule" pattern the whole arc exists to retire
(`recurring_loan_balance_root_cause.md:126-153`: "never recompute a balance with a special boundary
rule; sum stored changes up to a date").

This plan uses the **genesis design** that Option D actually names
(`level1_level2_scope_and_fitness.md:518` "Confirmed balances become 'sum of postings up to T'";
`:793-794` "post each loan's opening balance as an equity_opening entry"):

> A loan's opening balance is posted once as an `equity_opening` journal entry. Every confirmed
> payment posts its real principal reduction (Step 4 already does this). Every balance true-up posts a
> dated correction entry, never an anchor edit. The confirmed balance is then
> `owed(T) = -(sum of the loan's linked-ledger postings whose pay period has begun by T)`. No external
> anchor is read; no post-anchor eligibility filter is applied.

### Why genesis is the correct end state

- **It removes the boundary rule entirely.** The anchor-based reader had to exclude pre-anchor
  payments (the Step-2 cash legs for pre-anchor payments would otherwise double-count, the "pre-anchor
  pollution"). Genesis posts a correction for **every** confirmed payment from origination, so there is
  no post-anchor cut and no pollution to exclude. The reader is a plain sum.
- **It makes the ledger authoritative.** The opening balance is a posting, not an external read, so a
  loan's confirmed balance is fully reconstructable from the ledger. This is the foundation Step 5's
  actuals reporting (income statement / balance sheet / trial balance) needs.
- **Corrections become postings, not edits.** A true-up is a dated correcting entry (the bitemporal
  "never rewrite the past, post a correction" discipline, `level1_level2_scope_and_fitness.md:522`),
  matching how `LoanAnchorEvent` was the right instinct applied only to loans.

### Two honest limits of doing this at the loan layer only (developer must accept)

1. **The app-wide trial balance does not close yet.** Cash accounts have no opening-equity posting:
   `account_posting_total(checking)` is the sum of settled **changes**, not the absolute balance
   (verified: `posting_service.py:821-857` + the cash oracle
   `test_posting_ledger_cash_reconciliation.py:559-632`, whose expected value excludes the anchor).
   After this plan, the loan sub-ledger sums to an absolute balance while cash sums to changes. This
   asymmetry is deliberate and resolves when cash gets its opening-equity postings in Step 5. The
   correctness win here is the **loan balance**, not a closed balance sheet.
2. **`equity_opening` was never actually reserved.** The fitness doc (`:749`) and the agent memory
   both claim Step 4 "reserved `equity_opening`." It did not: `LedgerAccountKindEnum` has exactly seven
   members (`enums.py:290-296`), none an opening kind, and `PostingKindEnum` has no opening/correction
   kind (`enums.py:235-241`). So this plan carries a small ref migration (below), unlike the
   anchor-based draft which needed none.

---

## 2. The one remaining decision: FULL vs MINIMAL

Both variants post the opening-equity entry, post corrections for all confirmed payments, and read the
balance from the ledger. They differ in whether the resolver's read-time replay of confirmed history is
actually retired.

**FULL (recommended).** Stop writing `LoanAnchorEvent` rows: origination writes only the opening-equity
posting, a true-up writes only a correction posting. The resolver becomes forward-only, its forward
projection seeded from the ledger's confirmed balance; its confirmed-history schedule rows are retired
and any consumer that needed them reads the ledger instead. This is the literal "retire the read-time
replay of confirmed history," the single source of truth for confirmed loan history becomes the ledger,
and there is no dual write.

**MINIMAL (fallback).** Keep writing `LoanAnchorEvent` and keep the resolver generating history rows
(for the amortization-table display and any other history consumer), but read the **balance** from the
ledger. A true-up then writes BOTH an anchor row (for the resolver) AND a correction posting (for the
ledger) -- a dual write whose two sides must agree, guarded by the parallel-run oracle. Lower risk,
smaller blast radius, but it leaves the "two representations of the confirmed past" smell the arc is
trying to kill.

The scope difference is one consumer: the loan-detail **amortization table**
(`app/templates/loan/_schedule.html`, iterates `amortization_schedule` rows). FULL must feed that table
from a ledger-derived history adapter (which is strictly more correct -- it would show **actual** paid
interest/principal per month, where today's schedule-replay table is wrong off-schedule). MINIMAL keeps
the resolver generating that table.

Recommendation: **FULL** -- **DECIDED by the developer 2026-07-01.** Reached by staging the
`LoanAnchorEvent` retirement as the final, independently reversible commit -- so the whole arc is
reversible right up to the last step, and the retirement lands only once every consumer is proven to read
the ledger. The commit list below is written for FULL; the "MINIMAL stops here" marker is now moot (kept
only to show what FULL adds beyond it).

---

## 3. The core mechanism

### 3.1 The opening-equity posting (new)

At loan-params creation and in the historical backfill, post one balanced journal entry per scenario the
loan lives in:

```
loan-linked ledger (Liability)   -(original_principal)   [kind OPENING]
equity_opening ledger (Equity)   +(original_principal)   [kind OPENING]
                                  ----------------------
                                  0
```

- `original_principal` and `origination_date` come from `LoanParams` (both immutable by design,
  `loan_params.py:22-26`); this is the same source the origination `LoanAnchorEvent` uses today
  (`params.py:112`). Do NOT use `current_principal` (demoted, non-authoritative, `loan_params.py:79-88`).
- The equity leg lands on a **per-loan** `equity_opening` ledger account (class EQUITY), resolved by
  extending `ledger_account_service.get_or_create_loan_ledger_account`'s `_LOAN_LEDGER_KINDS` map
  (`ledger_account_service.py:400-404`). Per-loan (not a single per-owner "Opening Balance Equity")
  because the per-loan shape reuses the existing `uq_ledger_accounts_loan` unique on
  `(user, loan_account_id, kind_id)` (`ledger_account.py:269-274`) with no new constraint; a per-owner
  singleton could not be uniquely constrained without hardcoding a ref id (which the project forbids).
- **Dating under a NOT NULL `pay_period_id`** (`journal_entry.py:165`): `entry_date = origination_date`;
  `pay_period_id` = the pay period containing `origination_date`, clamped to the user's **earliest**
  pay period when origination predates all periods (an imported mid-life loan). The reader bounds by
  pay-period start, so attributing the opening to the earliest visible period makes it counted in every
  period the loan is displayed. The entry links to neither a transfer nor a transaction (both nullable,
  disambiguated by `source_kind_id = LOAN_OPENING`, `journal_entry.py:191-214`); it is reconciled via
  its postings' per-loan ledger account, not an entry back-link.

### 3.2 Corrections for every confirmed payment from origination (change to Step 4's walk)

> **As-built correction (see the top banner):** this section's "the running-balance walk is unchanged"
> is WRONG. The walk must ALSO reset the running balance to each anchor's verified value as it is
> reached; without that reset a trued-up loan's post-anchor interest is computed on the wrong balance and
> the reader diverges from the resolver. The paragraph below is kept for the from-origination rationale
> only.

Today `compute_loan_payment_splits` walks from `select_latest_anchor` and only splits post-latest-anchor
payments (`loan_posting_service.py:357,369`). Change it to walk from **origination**, splitting every
confirmed payment, so the loan-linked ledger nets to the real principal for the loan's whole life. The
running-balance walk (`_split_one_payment`, `loan_posting_service.py:166-237`) is unchanged; only the
starting point (origination balance instead of latest-anchor balance) and the eligibility lower bound
(dropped -- keep only the `period_start <= as_of` upper bound) change. The shared upper-bound predicate
stays `is_confirmed_payment_eligible` minus its `anchor_date` lower test (or a new
`is_confirmed_payment_as_of` that keeps only `period_start <= as_of`).

### 3.3 True-up corrections (new; replaces the anchor rebase)

A balance true-up at date D verifying balance V posts:

```
loan-linked ledger   (ledger_balance_at_D - V)   [kind TRUEUP]
equity_opening       (V - ledger_balance_at_D)   [kind TRUEUP]
```

where `ledger_balance_at_D` is the reader's value just before the correction. This drives the running
total to `-V` from D forward while **keeping** all prior payment corrections (they are real facts). It
exactly reproduces today's anchor behavior (the balance jumps to the verified value at D, then
amortizes forward) but as an append-only posting. The current stale-reversal that reverses "payments
pushed behind a new anchor" (`_stale_loan_payment_shadows`, `loan_posting_service.py:594-651`) is
retired for that case (nothing is pushed behind under genesis); it stays only for reverted / unsettled /
hard-deleted payments.

### 3.4 The reader (new, in `loan_posting_service.py`)

```
confirmed_loan_balance_at(loan_account_id, scenario_id, as_of: date) -> Decimal | None
confirmed_loan_balance_map(loan_account_id, scenario_id, periods) -> OrderedDict[int, Decimal] | None
```

`owed(T) = round_money(-(sum of Posting.amount on the loan's linked ledger, scenario-scoped, whose
journal entry's pay period has start_date <= T))`. This includes the opening (kind OPENING), the payment
principal (the Step-2 cash leg + the Step-4 correction), and true-ups (kind TRUEUP) -- every posting on
the linked ledger, no kind filter, no eligibility lower bound. Returns `None` when the loan has no
OPENING posting in the scenario (an unconfigured loan), which routes to the existing needs-setup path,
never to `$0`. The map is one scenario-scoped posting load plus a Python running sum keyed by period
start.

Domain guard (fixes a footgun in the prior draft): the reader answers only `as_of <= today`. A future
`as_of` must route to the forward projection, not the ledger sum. Enforce this in the reader (clamp or
raise), not by caller convention.

### 3.5 Seed the forward projection from the ledger (not the anchor)

`resolve_loan` is pure (no db). The db-facing loader reads `confirmed_loan_balance_at(loan, scenario,
today)` and passes it into `resolve_loan` as `forward_seed_balance`. Inside, it overrides BOTH the
current-balance derivation (`_state.py:244-246`, currently `_replay_from_anchor(...).balance_as_of`) AND
the forward projection seed (`ProjectionInputs.starting_balance`, `_payoff.py:292-298`, at BOTH call
sites `:383` and `:503`). Use ONE override value threaded once, not two mechanisms -- a lesson from the
prior draft, which used `dataclasses.replace(state, current_balance=...)` for one and a param for the
other and could desync the headline balance from the schedule off-schedule.

### 3.6 The per-period map and the tax-interest hybrid

- **Per-period balance map:** for periods whose start <= today, read `confirmed_loan_balance_map`
  (ledger); for periods after today, read the re-seeded forward projection. The AMORTIZING branch of
  `net_worth_kernel.build_account_balance_map` (`:454-466` -> `compute_loan_period_balance_map`,
  `account_projection.py:205`) reads the ledger map for the confirmed region.
- **Mortgage interest for taxes (developer-approved fix):** the correct computation is a HYBRID, not a
  straight switch. Today `_compute_mortgage_interest` (`_income_tax.py:151-180`) sums schedule
  `row.interest` for the year, which includes **projected** future rows. The ledger has **actual**
  interest only for settled payments. So:
  `mortgage_interest(year) = Sigma(ledger INTEREST postings with entry_date in year) + Sigma(schedule
  row.interest where NOT row.is_confirmed AND payment_date.year == year)`.
  Actual where we have it, projected where we do not -- the same confirmed/projected split as the
  balance. Ledger recipe: sum signed `account_postings.amount` where
  `ledger_accounts.loan_account_id = loan AND ledger_accounts.kind_id = LOAN_INTEREST AND
  journal_entries.scenario_id = scenario AND journal_entries.entry_date BETWEEN Jan1 AND Dec31` (sum
  signed so reversal legs net out; `entry_date` is the civil paid date, the tax-correct basis). For a
  past year this is entirely ledger (more correct than today); for the current year it is ledger to
  date + projection for the remainder.

---

## 4. Atomic-commit decomposition

Each commit independently green (targeted tests + `pylint app/ scripts/` 10.00 on touched files), an
adversarial `code-reviewer` pass on the staged diff, full suite as the final gate. Reversible until
commit 8 (the first read switch); the `LoanAnchorEvent` retirement (commit 11) is last and independently
reversible.

1. **Ref migration (inert). DONE (`f8a93f1`).** Add `LedgerAccountKindEnum.EQUITY_OPENING`,
   `PostingKindEnum.OPENING` and `.TRUEUP`, `PostingSourceEnum.LOAN_OPENING` and `.LOAN_TRUEUP`; seed
   `ref.ledger_account_kinds`, `ref.posting_kinds`, `ref.posting_sources`; working downgrade that deletes
   the seeded rows. No new tables or columns; no `AUDITED_TABLES` change.
2. **Equity-opening chart resolver (inert). DONE (`3ce8f9b`).** Extend
   `get_or_create_loan_ledger_account`'s `_LOAN_LEDGER_KINDS` with `EQUITY_OPENING -> (EQUITY, "Opening")`;
   the amortizing-loan + kind guards already cover it. Unit tests.
3. **Opening + true-up posting service (inert, write-only). DONE (`0a0370c`; test-const dedupe
   `a7c4a76`).** As-built (see top banner) this UNIFIED the opening + true-up into one "anchor correction"
   (`sync_loan_anchor_corrections`, keyed by `(source_kind_id, entry_date)`, self-healing) and made the
   split walk RESET at every anchor via one chronological anchors+payments merge (Section 3.2's "walk
   unchanged" was wrong). Retired the "pushed behind anchor" stale case. Split `loan_posting_service` into
   a package. Unit tests with hand-computed literals (mid-life reset, opening, true-up, payoff, self-heal,
   payment-due-on-anchor tie-break).
4. **Wire opening + true-up at the chokepoints (inert on reads). NEXT.** `create_params` posts the opening
   (note: a payment-less new loan has NO scenario in `_scenarios_with_loan_payments`, so the opening needs
   its own baseline-scenario wiring, not just the existing all-scenarios payment sync);
   `apply_loan_anchor_true_up` posts the correction. **FULL (decided): stop writing the `LoanAnchorEvent`
   row here** -- the anchor correction posting replaces it. (The resolver keeps reading `LoanAnchorEvent`
   until commit 11, so a coexistence window is unavoidable; stage the event-write retirement so commits
   4-10 stay reversible.) Idempotent reconcile-to-target, touching only the loan's ledgers. **Two
   landmines surfaced by the C3 code-reviewer, both handled HERE:**
   - **Oracle identity (b) breaks.** `test_posting_ledger_loan_reconciliation.py`'s
     `_assert_loan_reconciles` identity (b) (`account_posting_total(loan) == settled_transfer_effect -
     per_loan_correction_net`) holds in C3 only because no `_assert_loan_reconciles` fixture posts anchor
     corrections. `_per_loan_correction_net` keys on `ledger_accounts.loan_account_id`, which ALSO matches
     the per-loan `equity_opening` account -- so once C4 (and the C7 backfill) post opening/true-up, the
     equity legs land in `per_loan_correction_net` and the opening/true-up linked legs in
     `account_posting_total`, but neither is in `settled_transfer_effect`, breaking (b). Update (b) to net
     out the anchor corrections (exclude `equity_opening` + the OPENING/TRUEUP-kind linked legs) when they
     go live.
   - **Double walk.** Once both syncs are wired, each chokepoint calls `walk_loan_ledger` TWICE (via
     `sync_loan_payment_postings` and `sync_loan_anchor_corrections`), each doing ~5 DB loads +
     O(payments) replay. Fold both reconciles behind ONE `walk_loan_ledger` call per (loan, scenario).
5. **The reader (inert).** `confirmed_loan_balance_at` / `confirmed_loan_balance_map`, `None` sentinel,
   future-`as_of` domain guard. Unit tests with hand-computed literals.
6. **Oracle gate (before any flip).** Extend `test_posting_ledger_loan_reconciliation.py`: reader ==
   resolver on-schedule; diverges by exactly the extra/short principal off-schedule; the **pre-anchor
   payment is now correctly summed** (genesis has no pollution to exclude -- this replaces the prior
   draft's `$1,000`-pollution guard with a "pre-origination payment is included" assertion); a true-up
   correction case; a Dec-31 boundary; two scenarios; a no-opening -> `None` case. Re-run the `+$10`
   interest-bug non-vacuity injection against the reader.
7. **Historical backfill (deploy hook + boundary migration).** Post opening + all payment corrections +
   true-up corrections for every existing loan across scenarios, reusing the go-forward sync so
   backfill == go-forward by construction (the Step-4 pattern, `loan_posting_service.py:832-863` +
   `scripts/init_database.py`). Verify up/down on the prod-clone dev DB; the oracle detects the
   unposted-opening gap before and zero mismatches after.
8. **Read switch: current balance (the flip).** Consolidate the three db-facing loaders
   (`resolve_account_loan`, `routes/loan/_helpers._resolve`,
   `savings_dashboard_service/_projections._compute_loan_account`) into one injection helper (fixes the
   DRY smell and the W9906/fence tension the prior draft left unresolved), read the ledger, thread ONE
   `forward_seed_balance` into `resolve_loan`. Gated by the cross-page equality oracle PLUS a new
   off-schedule cross-page case (the prior draft's cross-page citation was wrong: only loan-detail reads
   the resolver directly; the savings tile reaches `resolve_loan` via the service -- both still move
   here). **MINIMAL stops after this commit** (plus 9 and 10).
9. **Read switch: per-period map + past scalar.** AMORTIZING map/scalar reads
   `confirmed_loan_balance_map` for the confirmed region, the re-seeded projection for the future.
10. **Tax-interest hybrid.** Replace `_compute_mortgage_interest` with the ledger-actual +
    schedule-projected hybrid (3.6). Tests: a past year (all ledger, off-schedule-correct), the current
    year (ledger + projected remainder), a year-boundary paid-in-January case.
11. **FULL only -- retire `LoanAnchorEvent` + the read-time replay.** Stop writing anchor rows; make the
    resolver forward-only seeded from the ledger; retire history-row generation and feed the
    amortization table from a ledger-derived history adapter; retire `select_latest_anchor` /
    `_replay_from_anchor` from reads; add the reader to the W9906 balance-producer fence with its call
    sites inside the allowlisted seam cluster. This is the least-reversible commit; land it only once
    commits 8-10 are green on the prod clone.

---

## 5. Design decisions

| # | Decision | Why |
|---|---|---|
| G1 | Genesis (opening-equity + sum-of-postings), not anchor-based | Developer-chosen. Removes the read-time boundary rule entirely; makes the ledger authoritative; foundation for Step-5 reporting. |
| G2 | Per-loan `equity_opening` ledger account | Reuses the existing `uq_ledger_accounts_loan` unique with no new constraint; a per-owner singleton cannot be uniquely constrained without hardcoding a ref id. |
| G3 | Opening dated at `origination_date`, `pay_period` clamped to earliest | `pay_period_id` is NOT NULL; an imported loan's origination predates all periods, so clamp to the earliest visible period while keeping the true `entry_date`. |
| G4 | Opening posted per scenario in `_scenarios_with_loan_payments` + at params-create for the baseline | Postings are scenario-scoped via `journal_entries.scenario_id`; reuse the existing all-scenarios loop. Only the baseline exists today, so one entry in practice; forward-compatible with clone (Phase 3). |
| G5 | True-up = correction posting, keep prior corrections | Corrections-as-postings (append-only), never edits; reproduces the anchor jump while retaining full history. |
| G6 | Reader bounds by `pay_period.start_date <= as_of`, future `as_of` routes to projection | The "period has begun" upper bound is the only boundary genesis keeps; a future `as_of` is a projection, out of the reader's domain. |
| G7 | Tax interest = ledger-actual (settled) + schedule-projected (unsettled) | Straight ledger switch would understate the current year (ledger lacks the projected remainder); the hybrid is actual-where-known, projected-elsewhere. |
| G8 | FULL retires `LoanAnchorEvent`; retirement is the last, reversible commit | Ends the dual write and the two-representations smell; staged last so the arc stays reversible until proven. |
| G9 | Cash reads stay on `balance_resolver` | Out of scope; cash genesis is Step 5. Loans-vs-cash asymmetry is accepted until then. |

---

## 6. Risks

- **App-wide trial balance does not close until Step 5** (loans absolute, cash changes-only). Accepted;
  state it in the Step-5 plan so no one expects `assets == liabilities + equity` from the ledger yet.
- **`round_money` parity.** The ledger stores each leg cent-quantized; the resolver rounds once. Both
  accrue interest via the shared `accrue_monthly_interest` (`money.py:172-186`, rounds once per month)
  and both sum cent-quantized principals, so on-schedule they are penny-exact by construction. The real
  assumption to assert in the oracle is the cash decomposition `cash == scheduled_P&I + escrow` exactly;
  keep the penny-exact on-schedule assertion.
- **Backfill must reconcile origination + all payments + all historical true-ups** (including the
  synthetic W4 true-ups the Step-4 migration created for display continuity,
  `d3d25212504b:_backfill_trueup_events`). If a loan's replay-from-origination does not reach the user's
  verified balance, the true-up corrections are what close the gap -- the backfill must post them or the
  reader will disagree with today's displayed balance. The oracle's parallel run catches this.
- **The amortization-table adapter (FULL)** must reproduce the schedule display from ledger postings for
  the confirmed region; get its interest/principal per row from the actual legs, not a re-derivation.
- **Multi-scenario is latent, not active.** Only the baseline exists today; the per-scenario opening
  loop is forward-compatible but untested against a real second scenario until clone (Phase 3) ships.
  Note it; do not build clone here.
- **Ordering.** Backfill (7) before any read switch (8); tax-interest (10) after the interest postings
  are backfilled; anchor retirement (11) last.

---

## 7. Verification

1. **Unit tests** (`test_loan_posting_service.py`): hand-computed opening, correction, true-up, and
   reader values -- mid-life import, on-schedule, extra/short, payoff overpayment, true-up, no-opening
   (`None`), future-`as_of` guard, two scenarios. Arithmetic shown per the testing standard.
2. **Loan reconciliation oracle** (`test_posting_ledger_loan_reconciliation.py`): parallel run, reader
   (scalar + map) vs the resolver; penny-exact on-schedule; exact divergence off-schedule; pre-origination
   payment correctly included; true-up correction; Dec-31 boundary; scenario + owner isolation;
   backfill == go-forward; the `+$10` non-vacuity injection.
3. **Cross-page equality oracle** (`test_cross_page_balance_equality.py`): add an off-schedule loan case
   proving all four loan surfaces return the identical ledger-derived balance.
4. **Tax-interest tests** (`test_year_end_summary_service` / `_income_tax`): past year all-ledger and
   off-schedule-correct; current year ledger + projected remainder; year-boundary paid-in-January.
5. **Gates:** `pylint app/ scripts/` 10.00 with every `--fail-on` checker; full suite (count shown), run
   alone (`project_test_db_concurrency_flakes`); migrations tested up/down; rebuild the test template
   after the ref migration.
6. **Manual prod-clone** (dev == prod clone, 2FA off): mark the Mortgage's next payment Paid with a
   +$500 actual -> the balance drops $500 MORE than scheduled on the loan card, the savings tile, and net
   worth, with NO true-up prompt; a real true-up posts a correction and the card reconciles; the ledger
   sums to the displayed balance on real data; a hard delete strands nothing. Re-clone to leave dev
   pristine, then `dev -> main` PR so CI runs.

---

## 8. Corrections to prior artifacts (fix before implementing)

The anchor-based draft and the surrounding docs carry several inaccurate citations found during the
review; correct these so the implementation does not trust them:

- `equity_opening` was NOT reserved by Step 4 (fitness `:749`, memory) -- add it (commit 1).
- `resolve_account_loan` returns `(LoanParams, LoanState)`, not a bare `LoanState`; it feeds
  `net_worth_kernel` / `home_equity_service` / `debt_strategy`, NOT loan-detail (loan-detail uses
  `_helpers._resolve -> resolve_loan`).
- The oracle's pre-anchor fixture pins `$1,000`, not `$3,821.90` (the latter is a prod-clone Mortgage
  figure, not a test assertion).
- The cross-page test's "two surfaces" are two AGGREGATE-only surfaces; only loan-detail reads the
  resolver directly.
- `dataclasses.replace` at `_state.py:229` is on `LoanInputs`, not `LoanState`.
- `balance_at.py:517-519` is a degrade-to-transaction-sum; `:537-541` is the non-loan anchor fallback;
  the real loan flat-carry is `:513-516 -> account_projection.py:196`.
- The two-mechanism override (replace current_balance + forward_seed_balance) and the omitted
  `resolve_loan` hop / second `_build_forward_inputs` call site (`_payoff.py:503`) -- unify to one
  seed threaded once (3.5).
