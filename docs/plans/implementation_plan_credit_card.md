# Credit card arc: the plan of record

**Status: APPROVED 2026-07-19. Not started.**

**The 2026-07-19 "Ratified sequencing" was DISCHARGED and is ARCHIVED** to
`historical/credit_card_sequencing_2026-07-19.md`. It stayed here, live-looking, until 2026-08-11,
warned about only by a paragraph in ANOTHER document -- which is the same failure rule 15 fixed for
archived files: the warning must be on the artifact. Two of the bare ids it ordered now name LIVE
steps in other arcs (`pay_calendar:C8`, `recurrence:D1`), so it could send a reader at the wrong
work by grep alone.

**What gates this arc is ruling R-EB**, newer than that 2026-07-19 ruling: `CC1b`'s fold is
specified against the reset semantics R-EB deletes at the cutover, and `CC3b` derives a settle from
`paid_at`, which `X-f1b` deleted. **When this arc may start is `steps.md`'s answer**, and the reason
is `../audits/balance_architecture/README.md` Section 5.0 block 4.

**This arc's findings live in `ledger.md`, its steps are indexed in `steps.md`, its rules are
`conventions.md` and what "done" means is `verification.md`** -- the shared registries for every
arc. What stays HERE is the argument: the context, the locked rulings, the architecture and each
step's specification.

## Context

Shekel's "Credit" status is a spreadsheet-era placeholder: marking an expense Credit flips it to a
balance-excluded status (contributes $0 to its period forever, never settles, never posts to the
ledger) and auto-creates a phantom "CC Payback" Projected expense in `period_index + 1`
(`app/services/credit_workflow.py`). The developer pays those bills with a real credit card that has
interest, due dates, minimum payments, and flat-rate cash back.

The current model is financially wrong in five ways: the debt exists nowhere (net worth overstated
by the outstanding card balance); spending attribution is destroyed (the source never posts; the
payback posts under a generic category in the wrong period); cash back is manual; the payback lands
in `period_index + 1` while real cash leaves on the statement due date (up to ~6 weeks later); and
interest/grace is unrepresentable.

A seeded "Credit Card" account type already exists (`app/ref_seeds.py:59`,
`AcctTypeEnum.CREDIT_CARD`) but classifies `PLAIN` (all behavioral booleans false). The seam
reserves the hook: `app/services/balance_at/_liability.py:104` ("When revolving debt one day gets a
real forward model, this is the ONE place that changes").

## Locked developer rulings (2026-07-19; do not reopen)

1. **Re-account model.** Marking a purchase "Credit" MOVES the transaction to the card account and
   settles it there: real category, real date/period, real liability. The phantom payback dies.
   Paying the card is a transfer checking -> card. Undo moves it back, reverts to Projected.
2. **Full statement cycle.** Card params carry statement-close day + payment-due day + min-payment
   rule. A statement is a DERIVED snapshot, never stored. Grace: prior statement paid in full by its
   due date -> zero interest; otherwise a finance charge from the event stream.
3. **Derived statement payment.** ONE auto-maintained projected transfer checking -> card per
   statement on the due date; amount live-derived = statement balance minus reward redemptions
   posted since close (floor 0). Mirrors `derive_from_loan`. Underpayment fires a C7-style warning
   plus a projected finance charge.
4. **Cash back: flat rate; redemptions are events.** `cashback_rate` on card params; rewards ACCRUE
   derived; REDEMPTIONS are real recorded credit events. Manual at any amount, or auto-redeem at
   `auto_redeem_threshold` (nullable, e.g. $25).
5. **Migration: freeze history, migrate live rows.** Settled historical Credit pairs untouched. LIVE
   pairs converted inside the Alembic migration to settled card purchases (real category, original
   period) + payback deleted. Credit status becomes historical-only (terminal `credit: {credit}`);
   old workflow code deleted. Entry-level `is_credit` follows the same rule (split tender).
6. **Zero-card owners at migration time:** the migration creates a $0-anchor "Credit Card" account
   inline (account row + origination anchor history + linked/anchor-equity ledger rows, the
   account-factory invariant satisfied by hand; params-less = dormant plain liability until
   configured). Deploy never bricks.
7. **Renames ship in-arc at CC3c** (approval granted; add `Review:` lines):
   `credit_payback_for_id -> card_charge_for_id`,
   `TransactionEntry.credit_payback_id -> card_charge_id`, `is_credit -> is_card_tender` (+
   index/constraint renames), in the same commit that changes their semantics.
8. **Finance-charge math: APR/365 daily periodic rate x average daily balance;** when grace is lost,
   new purchases enter the ADB from their settle date. All Decimal; `round_money` at the boundary
   only.

### Grid / companion hard requirements (developer, 2026-07-19)

- **A charged expense stays visible in place** on the source account's grid for its period: a
  display-only ghost row derived from `charged_from_account_id` provenance (CC badge, $0 balance
  effect, uncharge affordance) -- never a second stored row.
- **The derived statement payment renders on the CHECKING grid as a payable** and deducts from the
  projected balance in its due-date period (structural: it is a real projected transfer with an
  expense shadow on checking).
- **The companion flow survives intact:** a companion user recording a card-tender entry on an
  envelope row (`app/routes/companion.py` + the entries blueprint's accessible-transaction path)
  feeds the card-side transaction end to end while the envelope row + entries stay on the owner's
  checking grid. Pinned by a companion-access test at CC3c.
- Timing shift to expect (accepted): the payment deducts in the DUE-DATE period, which can be later
  than the old next-period payback; the current period's end balance excluded card activity in BOTH
  models. A conservative deduct-at-close placement option is a possible later addition; correct
  timing ships first.

## Architecture (decided during planning)

- **Every card event is a real row on the card account**: purchases (retargeted or direct), payments
  (transfer shadows), interest charges and reward redemptions (system-generated PROJECTED rows the
  user confirms, projected->done philosophy). The card's past balance is a pure transaction sum;
  Step-2/Step-3 posting writers (`posting_service.py`, sign-based `_signed_cash_leg`, already
  liability-aware) need NO ledger-schema change. No loan-style split writer. The `PostingSourceEnum`
  planned `credit_payback` source dies (docstring).
- **New kind `REVOLVING`**: boolean `has_revolving_credit` on `ref.account_types`; classifier
  precedence AMORTIZING -> REVOLVING -> INTEREST -> APPRECIATING -> INVESTMENT -> PLAIN
  (`app/services/account_projection.py`). Seeded Credit Card row gains the flag via migration;
  `has_parameters` stays False.
- **Satellite `budget.credit_card_params`** (1:1 account_id CASCADE, loan_params pattern):
  `statement_close_day`/`payment_due_day` (SmallInt, CHECK 1-31, month-end clamp semantics shared
  with the recurrence engine's rule), `min_payment_percent` Numeric(5,4) + `min_payment_floor`
  Numeric(12,2), `cashback_rate` Numeric(5,4) default 0, `auto_redeem_threshold` Numeric(12,2) NULL
  (NULL = manual-only), `credit_limit` Numeric(12,2) NULL (display/utilization only). All CHECKs
  named; row added to `AUDITED_TABLES`. NO auto-create branch (no honest sentinel for the day
  columns) -- a params-less card is a dormant plain liability; every card feature gates on the row.
- **APR rides `budget.rate_history`** (verified: account-scoped, `monthly_pi` nullable, rate CHECKed
  to [0,1], unique (account_id, effective_date); every reader is loan-gated via LoanParams
  resolution, so card rows are invisible to loan code). Card gets its own thin loader + write route;
  `loan_features.py` docstrings updated to say "loan or card APR".
- **The card must NOT ride `calculate_balances`** (Projected-only premise + cash D1 would drop
  settled post-anchor purchases -- a card owner does not re-anchor daily). REVOLVING gets its own
  TOTAL fold producer in `balance_at`, sharing the instant-partition absorb/reset core from
  `account_posting_service/_walk.py` (R-B: shared, never copied; per the ratified sequencing, X1
  lands it first and CC1a consumes it).
- **Context memos use the C8d injection shape**: `BalanceContext` memos inject their deriver (the
  `loan_payoff(account, derive)` precedent) instead of lazy-importing the seam -- the lazy-import
  shape closed real import cycles. Applies to CC1b's walk memo and CC4b's derived payment.
- **Sign convention**: the card balance stays a negative cash-style number; `_liability.py`
  magnitude and net-worth abs rules unchanged.
- **Scope exclusions**: no cash advances (transfers OUT of the card rejected, sibling of the loan
  guard), no balance transfers, no category reward rates, no promo APRs.
- **System-row marker**: nullable `transactions.system_origin_id` FK to new seeded
  `ref.transaction_origins` (`finance_charge`, `reward_redemption`) -- IDs-for-logic; category
  matching rejected (user-editable names).
- **Multi-card policy**: zero cards -> action hidden/ValidationError; one -> it; multiple ->
  explicit picker; keyboard `c`/palette default = lowest `sort_order` then `id`.

## The steps (each commit independently green + revertable; additive-first)

### Phase 0 -- Foundation

- [ ] **CC0a** `feat(ref): account types carry a revolving-credit kind` -- migration adds
      `has_revolving_credit` (server_default false) + updates the Credit Card row (name-match legal
      in migration); model + all 19 `ACCT_TYPE_SEEDS` tuples + `_seed_account_types` upsert;
      `AccountProjectionKind.REVOLVING` + classifier branch; behavior-preserving shims (scalar
      `balance_at` REVOLVING joins PLAIN's date-precise cash branch); audit all ~12
      `classify_account` call sites for no-op. Oracle: byte-identical pre/post parity for a Credit
      Card account's scalar/map/grid; classifier precedence tests incl. the both-flags pathological
      type; migration up+down + template rebuild.
- [ ] **CC0b** `feat(cards): budget.credit_card_params satellite` -- model + migration as specced in
      Architecture; constraint negative controls; inert by design.
- [ ] **CC0c** `feat(cards): card params setup flow` -- create/update routes + Marshmallow schema
  (percent->fraction `@pre_load`, E-28); `_setup_redirect_url` REVOLVING branch -> cash_detail
  until Phase 6; params-absent dormancy pinned by test.

### Phase 1 -- The revolving balance producer

- [ ] **CC1a** `refactor(balance): the card consumes the shared instant-partition fold core` -- per
      the ratified sequencing X1 lands the shared absorb/reset helper; this step consumes it (if the
      sequencing changes and X1 has not landed, this step extracts it from
      `account_posting_service/_walk.py` byte-identically instead, with the walk's own tests as the
      no-move oracle). Classify any new fenced public fn (W9909 fails closed).
- [ ] **CC1b** `feat(balance): a card is an event stream -- the revolving fold (additive)` --
      `balance_at/_revolving.py`: events = anchor facts at assertion instants + settled rows at
      settle instants (shared civil-date rule) + projected rows forward;
      `revolving_positions(account, ctx, dates)` + `revolving_period_map` via the shared
      `window_sample_date`; unwired (the C3a additive discipline); `BalanceContext` clock, no
      `date.today()`; memo via the injection shape. Oracle: hand-computed matrix -- pre-anchor
      absorb, settled post-anchor purchase counted (negative control: the cash producer drops the
      same row, the cash D1 shape, shown to fire), re-anchor reset, projected forward, direct
      income, payment shadow, evening-Eastern `paid_at` boundary, multi-scenario, reset_pay_periods
      same-value re-anchor (N-4 shape).
- [ ] **CC1c** `feat(balance): the seam dispatches REVOLVING to the fold (cutover)` -- four
      surfaces: `_account_balance_map` REVOLVING arm; scalar reads `revolving_positions` (deletes
      the CC0a shim); `grid_balance_view` REVOLVING arm = fold map + EMPTY increments (interest is
      real rows; reconciliation invariant pinned; probe `period_subtotal` semantics first);
      `liability_owed_at_dates` forward values replace the flat hold. `stale_anchor_warning` returns
      False for REVOLVING (nothing is dropped anymore). Moved numbers individually explained (the
      balance README Section 7.1 standard); dev-clone live-render (savings cockpit, grid,
      obligations, net-worth trend); fence classification tests updated.

### Phase 2 -- Statement math (pure, no Flask/db -- the rate_period_engine discipline)

- [ ] **CC2a** `feat(cards): the statement cycle is a pure derivation` --
  `app/services/card_statement.py`: `cycle_window`/`statement_sequence` (month-end clamp
  shared/pinned vs the recurrence engine), `due_date_for`, `statement_balance` (fold through
  close), `grace_kept`, `minimum_payment` (= max(floor, round_money(pct x bal)) clamped to
  balance). Oracles: Feb-31st clamp, leap year, due-before/after-close, paid-in-full vs
  one-cent-short (control fires), floor crossover, zero.
- [ ] **CC2b** `feat(cards): the finance charge folds the daily balance` --
      `finance_charge(events, cycle, rate_records)`: day-by-day balance, APR segments
      effective-dated, DPR = APR/365, average daily balance, purchases join the ADB on grace loss
      (ruling 8); grace kept => `0.00`. Oracles: hand-computed ADB with a mid-cycle purchase; APR
      change mid-cycle; boundary days of the closed-open cycle window.
- [ ] **CC2c** `feat(cards): card APR history rides rate_history` -- card-gated write route + schema
      + `load_card_rate_records`; docstring updates; double-submit uniqueness test; pin that the
      loan loaders' account set (LoanParams-driven) never contains the card.

### Phase 3 -- The re-account action, the migration, the deletion

- [ ] **CC3a** `feat(cards): charge-to-card (additive)` -- migration:
      `transactions.charged_from_account_id` (NULL FK SET NULL, partial index) = undo provenance.
      `card_charge_service.py`: `charge_to_card(txn_id, user_id, card_account_id)` -- row lock
      (generalize `lock_source_transaction_for_payback` into a shared home now; rationale carries
      verbatim), guards (expense-only, not shadow, not tracks_purchases, Projected-only, card
      ownership + REVOLVING check), then set provenance, retarget `account_id`, settle via
      `status_seam.apply_status_change(txn, DONE)`, reconcile postings LAST; `uncharge` reverts
      (Paid->Projected legal), moves back, clears provenance, reconciles. Routes beside the
      still-live mark-credit. Oracles: lifecycle, hand-computed posting oracle (card ledger -amount,
      category +amount, checking untouched, trial balance), undo round-trip to zero, C-19-shape
      concurrency, guard controls fire. Hazard checks: `template_id` partial unique unaffected;
      regeneration skips settled.
- [ ] **CC3b**
      `feat(cards)!: mark-credit is charge-to-card -- transaction-level cutover + live-pair migration`
      -- delete mark/unmark routes + PATCH revert path; state machine: Projected loses `credit`,
      `credit: {credit}` terminal; minimal template/JS cutover (`data-can-charge`, `c` key, palette,
      badges' predicates); Alembic migration with in-migration backfill: per LIVE pair -- resolve
      target card (single; multiple -> lowest sort_order; zero -> CREATE the card inline per ruling
      6), set provenance, retarget, status Paid + documented `paid_at` derivation, emit balanced
      settled postings in-migration (loan-backfill precedent), delete payback; frozen settled pairs
      untouched; downgrade restores via provenance. Rework `test_credit_workflow.py` +
      `test_c19_credit_payback_unique.py` (port lock/concurrency shapes). Reports move (settled
      spending now sees real categories at charge time) -- explained in-commit.
- [ ] **CC3c** `feat(cards)!: envelope split tender + renames` -- rewrite `entry_credit_workflow.py`
      -> `entry_card_charge.py`: maintains ONE settled card-side EXPENSE = credit-entry sum (same
      period/scenario/category, provenance set), linked via the RENAMED `card_charge_for_id` +
      partial unique index + `TransactionEntry.card_charge_id`; `is_credit -> is_card_tender`
      (ruling 7; `Review:` lines; index/constraint renames in the same migration). The 2x2 sync
      matrix carries over under the shared lock; sum==0 reverse-and-delete. `_signed_cash_leg` /
      `_credit_entry_sum` semantics unchanged (docstrings updated). Migrate live entry paybacks;
      delete `credit_workflow.py` whole. Oracles: hand-computed $500/$120 split tender (checking leg
      $380, card row $120 at the entries' settle instant), toggle shrink/grow, last-entry deletion
      reversal, migration up+down. HARD REQUIREMENT: the companion flow survives intact (see the
      requirements section) -- companion-access test end to end.
- [ ] **CC3d** `feat(cards): the card refuses what it cannot model` -- reject transfers OUT of the
  card (`_reject_transfer_out_of_revolving` at the `create_transfer` chokepoint);
  `active_accounts_query` gains an orthogonal `revolving` filter (default None); salary
  auto-picker passes `revolving=False`; direct entry on the card stays allowed. Residue sweep
  (docstrings, EVT_* events, enum docstring, comments). Every guard's control fires.

### Phase 4 -- The derived statement payment

- [ ] **CC4a** `feat(cards): card_payment_settings` -- 1:1 `transfer_template_id`
  (LoanPaymentSettings shape; generalization argued and rejected -- zero shared fields),
  `payment_mode_id` FK to new seeded `ref.card_payment_modes` (statement_balance /
  minimum_payment / fixed), dual-seeded; AUDITED_TABLES. Creation flow mirrors the loan payment
  transfer flow: monthly rule `day_of_month = payment_due_day`, one active template per card.
  Straddling close/due periods: due-date-wins placement pinned.
- [ ] **CC4b** `feat(cards): the payment you owe is the payment the card derives` -- a CARD rule
      behind the amount resolver (`balance:X-au-b`, ruling **R-FI**), NOT another entry in
      `live_amount_overrides`, which that arc deletes: projected payment amount = statement balance
      at last close minus `reward_redemption` rows posted since close, floor 0; min mode substitutes
      CC2a's minimum; fixed mode is a template amount the card owns. The card row stores no amount,
      so nothing can hold a figure the derivation contradicts. Oracles: derived amount renders
      identically on grid/card/checking; redemption after close reduces, before close does not (both
      controls); floor-0.
- [ ] **CC4c** `feat(cards): underpayment warns and projects its finance charge` -- C7-style warning
      (payment < minimum due) + one-click "pay statement balance" (flips mode);
      `card_recurrence_sync` maintains ONE projected finance-charge expense
      (`system_origin = finance_charge`) in the next cycle when grace fails; idempotent; recompute
      on write-path triggers only (transfer settle/revert, params/APR change); never touches settled
      rows. Oracles: hand-computed charge appears/disappears; settled charge untouched (control);
      confirming the charge posts + folds at its instant.

### Phase 5 -- Rewards

- [ ] **CC5a** `feat(cards): rewards accrue as a derived figure` -- the
      `transactions.system_origin_id` and `ref.transaction_origins` migration (this or CC4c,
      whichever lands first carries it); pure accrual:
      `round_money(rate x sum(settled purchases)) - sum(redemptions)`, purchases excluding both
      shadows and system rows; rounding on-the-sum, documented. W9909: classify non-producer
      (rewards earned, not balance-at-T). Hand-computed mixed-stream oracle.
- [ ] **CC5b** `feat(cards): redemptions -- manual + auto-redeem threshold` -- manual route (settled
      income row, `system_origin = reward_redemption`, `0 < amt <= accrued`); auto: the sync
      generates ONE projected redemption when accrued >= threshold (NULL = off), one-live-row
      invariant under the C-19 concurrency shape; confirmed via ordinary mark-done. Controls:
      threshold minus one cent does not fire; over-accrued rejected; confirmed redemption moves the
      fold + the next derived payment.

### Phase 6 -- UI (outline; a later shekel-design loop, per-screen audits + dev-clone verify)

Card cockpit page (statement hero, close/due chips, min due, grace state, utilization vs limit, APR
history, rewards chip + redeem, payment setup/track) replacing cash_detail for REVOLVING; grid
affordances (charge picker popover, card badge replacing CC/payback badges, the ghost-row
requirement above); savings cockpit tile states; palette polish. Every screen through the
`shekel-design` loop with its own dev-clone live-verify.

## Consumer inventory (cutover checklist; grep-verified 2026-07-19)

Python: `credit_workflow.py` (delete), `entry_credit_workflow.py` (rewrite),
`routes/transactions/mutations.py` (PATCH revert, delete teardown, mark/unmark),
`routes/transactions/_helpers.py`, carry_forward (route + service `_context.py`),
`state_machine.py`, `status_seam/` (doc), `entry_service.py`, `pay_period_admin.py` (comments;
behavior stays correct), `spending_analysis.py`, `posting_service.py` + `posting_reads.py` (docs),
`db_errors.py`, `log_events.py` (EVT_CREDIT_* -> EVT_CARD_*), `balance_predicates.py` (stays;
historical-only note), `models/transaction.py` (index + FK renamed), `models/transaction_entry.py`
(renamed), `jinja_globals.py`, `enums.py` (doc), `schemas/validation/entries.py`,
`routes/companion.py` + the entries blueprint (companion access path). Templates:
`grid/_transaction_cell.html`, `grid/_grid_row_macros.html`, `grid/_mobile_card_actions.html`,
`grid/_transaction_full_edit.html`, `grid/_transaction_quick_edit.html`,
`grid/_transaction_entries.html`, `_keyboard_help.html`, `analytics/_balance_sheet.html`,
`accounts/cash_detail.html`, `savings/_cockpit.html`. JS: `app.js` (markTxnCredit + `c` key);
`command_palette.js` (Credit command, badge glyph). Tests: `test_credit_workflow.py` (~1,075 lines),
`test_entry_credit_workflow.py` (~1,338), `test_c19_credit_payback_unique.py` (~1,141) -- reworked
per CC3a-c; plus grid-template, state-machine, carry-forward, posting-lifecycle suites asserting
Credit shapes.

## Verification standard

**The standard is `verification.md`**, one copy for every arc; what every commit owes is
`CLAUDE.md`'s Definition of Done. This section states only what is SPECIFIC to this arc.

- **The Phase 1 cutover and the Phase 3 migration are live-render gates**: on the dev clone, before
  landing, render the grid on the card, the savings cockpit, obligations, the net-worth trend and
  the balance sheet. Every moved number is individually explained and signed off.
- **End-to-end acceptance on the dev clone**, one walk: create card + params -> charge a projected
  bill -> statement closes -> derived payment renders -> settle payment -> grace holds (interest
  `$0.00`) -> force an underpayment -> warning + projected finance charge -> rewards accrue ->
  auto-redeem row at threshold -> confirm -> next payment shrinks.
