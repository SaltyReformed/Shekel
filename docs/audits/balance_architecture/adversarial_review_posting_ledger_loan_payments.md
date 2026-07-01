# Adversarial review: the Step-4 loan-payment posting plan

**Reviewer pass:** 2026-06-30, independent of the plan's own 2026-06-30 review. Target:
`implementation_plan_posting_ledger_loan_payments.md`. Method: every load-bearing claim re-traced
against live code on `dev` (migration head `7d63529e4300`) and against the prod-clone dev DB; four
independent code-trace passes plus direct reads of `posting_service.py`, `rate_period_engine.py`,
`amortization_engine/_projection.py`, `ledger_account.py`, and the architecture-of-record
(`level1_level2_scope_and_fitness.md`). Nothing here is asserted without a citation.

---

## Verdict

**No -- this is not the most financially sound design, and it is not how you would build it from
scratch.** The plan is careful and well-engineered *as an implementation of a deliberately weaker
goal*: it makes the loan ledger a faithful **mirror of the resolver's contractual schedule** and
proves the mirror exact with an oracle. That choice is safe to validate, but it (a) ships a concrete
money-corruption bug, (b) contradicts the project's **own architecture-of-record**, which says
Step 4 should store each payment's **real** principal/interest split and **retire** the read-time
replay, and (c) bakes the resolver's "ignore the actual cash" limitation into the permanent ledger,
which perpetuates the loan true-up -- the very band-aid the project set out to eliminate.

The plan made exactly the trade your standing guidance says not to make: on a correctness-vs-risk
fork, it chose the lesser-but-safer design instead of the most-correct design sequenced safely
(parallel-run + oracle + staged cutover).

Recommendation in one line: **pause and resolve the split-source fork (Section 4) before building;
adopt the store-the-real-split design; the critical bug and most of the design smells dissolve once
you do.**

---

## 1. What is solid (verified, keep)

- **The trust-but-verify discipline holds up.** The cited code is real and mostly accurate. The
  split arithmetic nets the loan to the resolver's principal by construction; the three transfer
  chokepoints exist where claimed (`transfer_service.py:641`, `:690`, `:951`); the append-only
  tables, immutability guards, and deferred `SUM=0 AND COUNT>=2` trigger are as described
  (`journal_entry.py:370-423`, `posting_infrastructure.py:82-124`).
- **The chokepoint set is complete for changing a confirmed loan payment.** An exhaustive
  enumeration of every transfer mutation path (settle, revert, cancel, amount/actual edit, period
  move, soft/hard delete, restore, recategorize, scenario change, recurrence regenerate, template
  delete, carry-forward, pay-period truncate/reset, account delete, true-up, both rate paths)
  confirms each either does not change a *confirmed* payment or routes through `:641` / `:690` /
  `:951` / the true-up / a rate path. The finalised-edit lock (`transfers/mutations.py:61-98`,
  `state_machine.py:236-288`) blocks a settled-period move, and the system paths
  (recurrence/carry-forward) are projected-only. This is good work and the plan's confidence here is
  earned (with two gaps, Section 6).
- **C1 was a real bug and the fix direction is right.** The original parallel-query + positional-zip
  + equal-length `assert` genuinely would have 500'd on biweekly redistribution / payoff / pay-ahead
  (`loan_payment_service.py:409-436`, `rate_period_engine.py:655-665`). Carrying provenance per row
  instead of re-deriving the set is the correct instinct.
- **The oracle-honesty revision (H2) is good.** Naming invariants #1/#2 as self-consistency checks
  and resting correctness on hand-computed distinct-amount fixtures is the right call.
- **Real-data claims check out.** On the prod-clone dev DB: the ledger holds exactly 103 journal
  entries / 206 postings, all two-leg; the Mortgage's latest anchor is `user_trueup 2026-05-22 =
  177,829.83`; the one post-anchor Mortgage payment is the pay-period-2026-05-21 row whose **monthly
  due date 2026-06-01 > anchor 2026-05-22** (the due-date-vs-pay-period-start subtlety is handled
  correctly by `monthly_due_date`); the Van has zero post-anchor; and **no loan payment has a
  divergent `actual_amount`** today. So on current data the plan would post exactly one correction,
  and its teeth are entirely synthetic -- as the plan admits.

---

## 2. CRITICAL -- the plan ships a cash-ledger corruption bug

This is the finding that alone blocks the plan as written. It is independently confirmed by direct
read of `posting_service.py`.

**The mechanism.** Step 2's cash path reconciles a transfer by reading the *net* posted on the
to-account ledger:

```
posting_service.py:630   current = _posted_net(xfer.id, to_ledger.id)
posting_service.py:227-237   # _posted_net filters JournalEntry.transfer_id == transfer_id
                             # AND Posting.ledger_account_id == ledger_account_id -- NO source_kind
```

The plan posts the Step-4 correction's **loan leg onto that same loan ledger, under the same reused
`transfer_id`** (Section 5.1 step 3-4; the loan ledger is `_ledger_account_for(loan_account_id)`,
the very `to_ledger` of the payment transfer). So once a correction exists, `_posted_net` returns
`cash + correction_loan_leg`, not `cash`.

**The corruption (real Mortgage figures).** After settle, the loan ledger holds `+1910.95` (cash);
after the correction, `+1910.95 - 1635.81 = +275.14`. Now revert/edit/delete the payment ->
`sync_transfer_postings(xfer, settled=False)` (`transfer_service.py:690`): `target=0`,
`current=_posted_net=275.14`, `delta=-275.14`. It posts a **cash reversal of +275.14 on Checking /
-275.14 on Loan** instead of the correct `+1910.95 / -1910.95`. Checking is left wrong by
`1,635.81`, and **nothing ever repairs it** -- the loan sync touches only loan/interest/escrow,
never Checking (the plan states this as a safety property; it is exactly what makes the corruption
permanent). The same misread corrupts edit-while-settled, delete, and restore of any loan payment.

**The plan guarded the wrong direction.** Its L2 mitigation (Section 2.7) adds a *new*
`source_kind`-filtered reader so Step 4 will not read Step 2's cash leg. But the dangerous direction
is the reverse: **Step 2's unchanged `_posted_net` reads Step 4's correction leg.** The plan does not
touch `_posted_net`.

**This is the symptom of a deeper smell -- `transfer_id` overloading.** `transfer_id` means "which
transfer produced this entry," and the one-FK-per-entry / which-`source_kind`-applies rule is
enforced only by builder convention, not by any constraint (`journal_entry.py:86-90`). Putting two
semantically different entries (a cash movement and a principal correction) on one `transfer_id`
makes `source_kind` a mandatory, silently-failing filter on every `transfer_id` reader -- and the
codebase already ships one that omits it. See Section 5 for the clean fix (route the correction
through the income shadow's `transaction_id`, which existing readers structurally cannot see), which
removes this whole bug class rather than patching `_posted_net`.

---

## 3. The foundational problem -- a contractual mirror, not the real split

This is the heart of the review. It is not a bug; it is the design being aimed at the wrong target.

### 3.1 The resolver ignores the actual cash, by design

`replay_schedule` cannot even see the cash -- its signature takes `confirmed_payment_dates:
list[date]` (`rate_period_engine.py:581`); the `PaymentRecord.amount` is discarded one level up
(`loan_resolver/_periods.py:346-350`). Each step reduces principal purely from the schedule:
`principal = period_pi - interest` (`rate_period_engine.py:553`), and the module says so explicitly:

> "The cash amount and escrow are NOT inputs -- only the COUNT and dates of the confirmed payments
> matter." (`rate_period_engine.py:597-599`)

Consequences, all verified: an **extra-principal payment does not pay the loan down faster** (the
extra is invisible); a **short payment still subtracts the full scheduled principal** (the balance
looks more paid-off than reality); the only real-cash signal in a resolved balance is the **anchor
(a user true-up)**.

### 3.2 The plan freezes that fiction into the permanent ledger

The plan's split is, by construction, the resolver's contractual split:

```
loan net = cash + loan_leg = effective - interest - escrow
         = effective - interest - (effective - row.payment)
         = row.payment - interest = row.principal   (CONTRACTUAL; cash cancels out)
```

The actual cash only ever lands in the "escrow" residual leg; it never touches principal. The plan
states the reason plainly: "reads stay on the resolver in Step 4, so the ledger MUST equal it." The
oracle then *proves* `ledger == resolver`. That validation strategy **structurally forbids the
ledger from ever being more correct than the resolver.**

### 3.3 This contradicts the architecture-of-record and your own goals

The chosen architecture (`level1_level2_scope_and_fitness.md`) says, of this exact step:

> Build-Order Step 4: "Post confirmed loan payments with their **real principal / interest split**;
> **retire the read-time replay** of confirmed history." (`:580`)
> "The confirmed loan ledger finally **stores each payment's real principal / interest split instead
> of re-deriving it**." (`:523`)
> D beats A because "it leaves the past recomputed: the loan resolver re-derives every confirmed
> payment's effect on every read, which is fragile because **a fact is being recomputed**. D **stores
> the fact once**." (`:496-497`)

And your standing notes: loans are "the complete-data case for sum-of-postings"
(`project_loan_trueup_semantics`); loan balance must "self-calculate (no monthly true-up); fix model
defects, not band-aids" (`project_loan_balance_self_calculation`).

The plan does the opposite of all of this: it **re-derives** the split from the resolver on every
sync (reconcile-to-resolver), stores the **contractual** number rather than the real one, and keeps
the resolver as the authority. When Step 5 later "retires the replay" and reads `sum(postings)`, it
will be reading numbers that *were copied from the replay it is retiring* -- the authority never
actually transfers; it is frozen. Worse, because the stored principal is contractual, **the true-up
survives as the only way to reflect an extra or short payment** -- the band-aid persists.

### 3.4 Worked examples (the fork in one table)

Real Mortgage: balance 177,829.83, 6.875%/12 -> interest 1,018.82; scheduled P&I 1,293.96; configured
escrow 616.99 (verified in the DB).

| Scenario | Cash paid | Plan (contractual mirror) | Store-the-real-split |
|---|---|---|---|
| **On-schedule** | 1,910.95 | principal **275.14**, escrow 616.99, interest 1,018.82 | **identical** (275.14 / 616.99 / 1,018.82) |
| **+$500 extra principal** | 2,410.95 | principal **275.14**; the $500 booked as **"escrow" expense**; loan does **not** drop the extra; **needs a true-up** | principal **775.14** (= 2,410.95 - 1,018.82 - 616.99); loan drops the extra; **no true-up** |
| **-$200 short** | 1,710.95 | principal **275.14** (full paydown as if on-schedule); escrow understated to 416.99 | principal **75.14**; balance reflects the shortfall honestly |
| **Van early payoff overpay** | > payoff | overpayment booked as **"escrow"** on a **no-escrow** loan (even mints an Escrow account for it) | overpayment is a refund/suspense leg; loan closes at 0 |

On-schedule, the two designs are byte-identical (and identical to today). They diverge **only** when
the user pays off-schedule -- which your data does not show today but which the plan must support
("extensible to any data a user could have"). In every divergence, the plan is the wrong number and
needs a true-up; the store-the-real-split design is right and self-maintaining.

---

## 4. Escrow modeling -- two distinct problems

### 4.1 The residual is mislabeled "escrow," and the real escrow is already on hand

`escrow = effective - row.payment` is **not escrow** -- it is *every dollar of cash that differs
from the contractual P&I*, whatever the cause. It is correct only when escrow is the sole cause.
Mislabel cases (all verified reachable): extra principal, late fees, short payment (produces a
**negative escrow expense** -- economically meaningless), payoff-row overpayment, and cent rounding.

Crucially, **the app already stores the real escrow** as named components with annual amounts
(`loan_features.py:116-156`; monthly via `calculate_monthly_escrow`,
`escrow_calculator.py:137-177`), and on the real Mortgage the configured monthly escrow is **exactly
616.99 -- identical to the residual** (DB-verified). So config-based escrow is *equally correct on
the normal payment and strictly more correct off-schedule*. The plan discards data it already has in
favor of an inference that is right only in the easy case. The store-the-real-split design uses
`escrow = calculate_monthly_escrow(components)` and routes any genuine leftover
(`cash - interest - config_escrow`) to **principal** (extra) or a **suspense/refund** leg (true
excess), so "escrow" always means escrow.

### 4.2 Escrow-as-expense vs escrow-as-asset (ratify before Step 5)

Escrow paid is the borrower's money impounded by the servicer, disbursed later for tax/insurance --
arguably a **prepaid asset**, not an expense at payment time. The app tracks **no** impound balance
(verified: no escrow asset anywhere), so net worth already drops by the full payment and treats
escrow as vanished. The plan's escrow-as-expense therefore *matches today's behavior* and causes no
Step-4 regression -- but the Step-5 read switch will **cement** it. This is a conscious modeling
decision (net worth understated by the live impound, a sawtooth up to ~1 year of escrow, several
thousand dollars at peak); ratify it deliberately before it becomes permanent ledger history rather
than letting the implementation default decide it.

---

## 5. Design-quality issues that bake in debt

1. **`transfer_id` overloading (the root of the Section-2 bug).** The clean fix is to link the
   correction to the **income shadow's `transaction_id`**, not the transfer's `transfer_id`. The
   shadow is a `Transaction` on the loan account that the cash transaction path already **skips**
   (`posting_service.py:733`, the `transfer_id is not None` guard), and `_posted_net` filters
   `transfer_id` so it would **structurally never see** a `transaction_id`-linked correction. That
   removes the corruption class entirely -- no retrofit of `_posted_net`, no easy-to-forget
   `source_kind` filter on every future `transfer_id` reader. (If you keep `transfer_id`, you must
   both retrofit `_posted_net` with `source_kind='transfer'` AND add a checker that every
   `transfer_id` reader filters `source_kind`, because the safety is otherwise unenforced and fails
   silently as wrong money.)

2. **The `ledger_accounts` row-kind discriminator is straining.** Today four kinds are inferred from
   the NULL-pattern of `account_id` / `category_id` / `is_fallback` (`ledger_account.py:12-67`). The
   plan adds a fifth and sixth kind (loan interest/escrow) that **share the orphan shape**
   `(account_id NULL, category_id NULL, is_fallback false)` and are told apart only by a new
   `loan_account_id`; Step 5's Equity/opening-balance rows will be a seventh. Encoding kind in the
   NULL-pattern of five columns is fragile and a normalization smell. Prefer an explicit
   `ledger_account_kind_id` (ref enum) discriminator -- it makes every kind a positive value, makes
   orphan-enumeration trivial and safe, and scales to Step 5 without another shape collision.
   - Sub-defect in the plan's own L3 mitigation: it makes `loan_account_id` `SET NULL` on loan
     delete but keys orphan predicates on `loan_account_id IS NULL`. A deleted-loan row would then
     have `(NULL, NULL, false, loan_account_id NULL, loan_posting_kind_id NOT NULL)` and be
     **misclassified as an orphan**. The robust discriminator is `loan_posting_kind_id IS NULL`
     (an orphan never carries a posting kind). Latent only because loan hard-delete is RESTRICT-
     blocked -- but it shows the NULL-pattern approach is error-prone.
   - Also: the L3 work guards an orphan-enumeration predicate that **does not exist yet** (no live
     query enumerates the bare orphan shape; the Step-3 oracle reconciles by `transaction_id`
     linkage). So the Commit-2 test "loan-expense row not returned by the orphan predicate" tests a
     future predicate. Fine as forward-defense, but state it as such.

3. **The provenance passthrough is not "computation-neutral plumbing."** `source_transaction_id`
   would put a **database row id into the pure engine** whose entire contract is "no Flask, no db,
   plain values" (`rate_period_engine.py:4-8`). And the chain is **broken today**: `replay_schedule`
   takes bare dates, so threading the id means **widening the replay interface** across
   `_replay_from_anchor` / `replay_schedule` / `_replay_payment_row` and copying the field at both
   record-rebuild sites -- a real change, not an additive tag. It also rides on the shared
   `AmortizationRow`, so **every projected row carries a meaningless `None`** (an Interface-
   Segregation smell). Prefer returning provenance *beside* the rows (e.g. `replay_schedule` returns
   `list[tuple[AmortizationRow, source_id]]` or a dedicated `ConfirmedStep`), keeping the pure
   engine and the shared row clean.

4. **The reconcile/reverse asymmetry (H1) is a symptom, not a fact of life.** The cash path handles
   *every* lifecycle event -- including delete -- with one idempotent `sync` reconciling to a
   per-transfer target (delete = reconcile-to-zero, `posting_service.py:787-818`). The plan needs a
   *separate* explicit `reverse_loan_payment_postings_for_transfer` only because it reconciles the
   **whole loan to the resolver** rather than each payment to its own target. A **per-payment
   target** design (each confirmed payment's correction reconciled to its own legs, exactly like
   cash) unifies the lifecycle -- delete becomes reconcile-to-zero again, and the special reverse
   disappears. (You still run the replay once to get each row's interest; you just reconcile
   per-payment instead of per-loan.)

5. **An existing oracle invariant silently breaks for loans.** The Step-3 cash oracle asserts
   `account_posting_total(acct) == settled_transfer_effect + settled_transaction_effect`
   (`posting_service.py:821-857, 860-909`; test at
   `test_posting_ledger_cash_reconciliation.py:599-622`). Once loan corrections exist,
   `account_posting_total(loan)` becomes principal while `settled_transfer_effect(loan)` stays the
   full cash, so the equality breaks for any resolver-backed loan. It is masked today only because
   the test's mortgage has no `LoanParams`. The Step-4 oracle must **supersede** that per-account
   assertion for loan accounts, not sit beside it.

6. **A completeness gap the oracle's scoping can hide.** Replayed-row count is **not** confirmed-
   shadow count: pre-anchor payments, **future-confirmed** payments (settled but dated ahead -- routed
   to the forward override, *summed by month*, `_payoff.py:184`), and **post-payoff** payments
   (`balance <= 0: break`) all produce confirmed shadows with **no replay row**. Step 2 already
   posted a cash entry for every one of those settled transfers. Step 4 posts a correction only for
   rows that exist, so a post-payoff or future-confirmed loan payment leaves an **uncorrected cash
   entry on the loan ledger** -> the loan's `sum(postings)` is overstated for the eventual read
   switch. Because the Step-4 oracle is "scoped to the post-anchor replayed subset" (plan 2.5), it
   can pass while these extras sit wrong. Define the loan ledger's intended value over the **full**
   confirmed-shadow set, and test the post-payoff and future-confirmed cases explicitly.

---

## 6. Smaller but real corrections to the plan

- **Over-wiring (Rule 13 / latent danger).** The plan wires the loan reverse "at `:690` AND the
  payback-delete paths." **A loan payment is never a credit payback** -- paybacks are plain
  `Transaction`s with no `transfer_id` (`credit_workflow.py:305-306, 529-540`), and `mark_as_credit`
  refuses transfers outright. The payback wiring is dead at best; if implemented as
  `WHERE transfer_id IS NULL AND source_kind=loan_payment` it would match **other loans' orphaned
  corrections** and reverse them. Wire the loan reverse at `:690` only.
- **An un-wired transition (Concern A).** `create_params` makes a loan resolvable and seeds its
  origination anchor (`routes/loan/params.py:97, :112`) but is not a chokepoint. Because
  `classify_account` keys AMORTIZING off the account *type*, not the params
  (`account_projection.py:67-85`), a user can settle loan payments **before** configuring
  `LoanParams`; those post as Step-2 cash, the loan sync no-ops (`resolve_account_loan is None`,
  L1), and when params are later created **no sync fires** -- the ledger stays out of sync until the
  next chokepoint self-heals. Add `create_params` to the wiring (or document the self-heal window).
- **Reset re-anchor (Concern B).** `reset_pay_periods` re-anchors via
  `anchor_service.stage_anchor_true_up`, **not** the wired `apply_loan_anchor_true_up`
  (`pay_period_admin.py:743`). Safe only because reset is zero-settled-gated; note that the gate, not
  the wiring, is the safety, so loan postings cannot desync there.
- **Settled-period-move re-stamp (Concern D).** All period-move safety rests on the route-layer
  finalised-edit lock; the code already warns that a future settled-period-move UI must *re-stamp*
  the entry, since an append-only `entry_date`/period denorm cannot be reconciled
  (`transfer_service.py:74-77`). Relevant given the pay-period-CRUD roadmap's period-move UI.
- **Idempotency is index-based, not `ON CONFLICT`.** `get_or_create_loan_expense_ledger_account`
  mirrors `get_or_create_category_ledger_account`, which is SELECT-then-INSERT relying on the partial
  unique to reject a racing duplicate (it is not caught/retried) -- fine for a solo user; state it.
- **Dead/misleading comment (pre-existing, out of scope, worth a ticket).** `_payoff.py:263-282`
  claims to "surface historical overpayments" with a `$2080 vs $1580 -> $500` example, but
  `extra_payment = max(period_pi_of_row - period_pi_current, 0)` is identically 0 for a fixed-rate
  loan and reflects only a *payment decrease* for an ARM -- never an overpayment. The one place that
  purports to show overpayments does not.

---

## 7. Stepping back -- is this step even the right shape?

- **Step 4 delivers zero bug-class reduction on its own.** The recurring "wrong loan balance" bug was
  the read-time boundary rule, and it was already fixed structurally by **Level 1** (the `balance_at`
  seam + the no-bypass checker, shipped). Step 4 changes no reads
  (`balance_at.py:491-516` -> resolver, verified), so it neither fixes nor regresses any displayed
  balance. Its entire payoff is deferred to the Step-5 read switch. That is a legitimate
  validate-then-switch posture -- but it means the value of Step 4 is *entirely* in becoming the
  authoritative record, which Section 3 shows it does not actually become.
- **The plan re-scoped the architecture's Step 4.** The architecture's Step 4 bundles the read switch
  ("retire the read-time replay"); the plan splits it into a write-only Step 4 + a new Step-5 switch,
  pushing actuals-reporting to Step 6. Splitting write from switch is reasonable sequencing; just be
  explicit that you have done it, and that the hard, decision-bearing work (does the ledger become
  authoritative? does it capture reality? how does the confirmed/projected seam read?) all lives in
  the deferred switch -- so Step 4 should avoid over-investing in machinery the switch will rework.
- **The honest stopping-point question.** The architecture's *own* Part J recommended Level 1 as the
  firm fix and rated the posting ledger as "the best app, not necessary for the bug." Steps 2-3 are
  shipped and reconcile in prod, so the arc is committed; continuing to Step 4 is consistent with the
  chosen Option D. But if the goal is "robust, error-proof finances with no band-aids," the highest-
  leverage version of Step 4 is the one that **eliminates the loan true-up** by storing the real
  split -- not the one that freezes the contractual split and keeps the true-up alive.

---

## 8. Recommendation

**Do not build the plan as written.** Resolve the split-source fork first, then the rest follows.

1. **Adopt store-the-real-split (the architecture's actual Step 4).** At settle, compute the split
   **once** and store it as the authoritative posting:
   - `interest = running_ledger_balance * rate / periods` (rate from the rate history; balance from
     the ledger, so interest accrues on the *real* balance, not the contractual one);
   - `escrow = calculate_monthly_escrow(components)` (the config you already store);
   - `principal = actual_cash - interest - escrow` (so extra/short payments land in **principal**,
     where they belong);
   - any genuine excess (payoff overpayment, unexplained surplus) -> a **suspense/refund** leg, never
     "escrow."
   This makes the ledger authoritative, handles every off-schedule case correctly, uses only data the
   app already has, and finally lets the loan balance be `sum(actual principal reductions)` with the
   true-up reserved for genuine reconciliation -- the stated goal. Optionally allow a per-payment
   override for users who have the lender's exact statement split (the gold standard; not required).

2. **Sequence the risk instead of dodging it.** Keep the resolver, run the ledger in parallel, and
   build a **hand-computed amortization oracle** (not "ledger == resolver"). On on-schedule data the
   two agree to the penny (proven on your real data); they diverge only on off-schedule payments,
   where the ledger is the correct one. Stage the read cutover per the existing seam.

3. **Fix the linkage and schema regardless of (1).** Route the correction through the income shadow's
   `transaction_id` (kills the Section-2 corruption structurally); give `ledger_accounts` an explicit
   kind discriminator; return replay provenance beside the rows, not inside the pure engine and the
   shared `AmortizationRow`; reconcile per-payment so the lifecycle is one idempotent op; supersede
   the per-account oracle invariant for loans; define and test the loan ledger over the **full**
   confirmed-shadow set (post-payoff / future-confirmed included).

4. **Trim and patch the wiring.** Drop the payback-path wiring; add `create_params`; note the
   reset-re-anchor gate and the settled-period-move re-stamp requirement.

5. **Ratify escrow-as-expense** (vs a tracked impound asset) as a deliberate, durable modeling
   decision before the read switch cements it.

If, after seeing the worked examples, you decide the **contractual** model is genuinely what you want
for loan balances (it is rock-solid for on-schedule borrowers and immune to escrow-config noise),
that is a defensible position -- but then the true-up is a *permanent* part of the model, "escrow"
must be renamed to "non-P&I residual," and the architecture doc's Step-4 wording ("real split,"
"retire the replay") should be corrected to match. What you should not do is ship a contractual
mirror while believing you have built the real-split authoritative ledger; they are different
designs, and only one matches the goal you wrote down.

---

## Appendix -- citation index

Resolver ignores cash: `rate_period_engine.py:553,581,597-599`; `loan_resolver/_periods.py:346-350`.
Cash-ledger corruption: `posting_service.py:208-237,630,641-644,733`. Architecture goal:
`level1_level2_scope_and_fitness.md:496-497,523,580`. Escrow stored: `loan_features.py:116-156`;
`escrow_calculator.py:137-177,180-196`; DB monthly escrow = 616.99. `transfer_id` by-convention:
`journal_entry.py:86-90,191-199`. Ledger kinds: `ledger_account.py:12-67`. Pure-engine contract:
`rate_period_engine.py:4-8`. AmortizationRow: `amortization_engine/_projection.py:165-190`. H1:
`transfer_service.py:690,714`; `journal_entry.py:191-199`. Payback never a transfer:
`credit_workflow.py:305-306,529-540`. create_params un-wired: `routes/loan/params.py:97,112`.
Read path on resolver: `balance_at.py:491-516`; `net_worth_kernel.py:182`. Chokepoint lock:
`transfers/mutations.py:61-98`; `state_machine.py:236-288`. Real data: prod-clone dev DB,
2026-06-30 (103/206 ledger; anchors; 1 post-anchor Mortgage / 0 Van; zero divergent actuals).
