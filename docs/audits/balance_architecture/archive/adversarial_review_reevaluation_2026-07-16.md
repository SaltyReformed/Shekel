> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is `docs/plans/steps.md`; the code as
> committed is the source of truth for what the app does.

# Adversarial re-evaluation: the balance arc, the from-scratch plan, and the cash audit

**Written 2026-07-16, against `dev` @ `62b9fa0a`.** Commissioned as a full re-evaluation with no
anchoring on past decisions: is the design right, are the documents trustworthy, is the developer
chasing their tail, and what is the correct path from here.

**Every load-bearing claim below was verified against the running code or the dev database, not
read.** Four independent verification passes traced the write path, the seam/fence, the cash side,
and the test suite (each claim rated CONFIRMED / REFUTED / PARTIAL with file:line evidence), and
eight probes ran against the dev DB. One mutation probe was attempted and abandoned when the
tooling declined to run tests over a mutated working tree; that dispute (M5) was settled by
first-hand code reading instead, and is marked as such.

---

## 0. The verdict

1. **The direction locked on 2026-07-14 is right, and its central claim survives independent
   re-verification.** I rebuilt the read-time fold from scratch -- source events only (anchor facts
   + settled shadows + rate periods + escrow lines), never the postings table -- applied the
   reader's visibility rule, and compared it against the seam on **every day of both real loans'
   domains: 212 days, zero mismatches**, baseline reproduced to the cent (Mortgage **$177,277.97**,
   Van Loan **$15,663.59**). The fold model, the "partial function" root cause, and the
   delete-heavy plan structure all hold.

2. **You are no longer chasing your tail -- but the two NEWEST documents contain four defects of
   exactly the class they diagnose**, each found only by running: the cash plan's first commit
   (X0) double-counts on 15 measured real-data shapes; the cash audit's root-cause claim ("the
   anchor has no date") is wrong -- the instant exists and the correct partition rule is already
   implemented in `account_posting_service`; the arc review's M5 tax finding is refuted in
   severity; and its "no paid-loan fixture anywhere" framing is refuted in the flat form. All
   four corrections are in Section 3. **None changes the direction. All four change commits you
   were about to make.**

3. **The from-scratch plan (`implementation_plan_loan_balance_from_scratch.md`) is sound and
   should be built** -- with the amendments in Section 6: one ruling to re-confirm with a worked
   example (D5's cross-account skew), one ordering made an explicit gate (C1 strictly before C2,
   probe-proven), the cash arc's X0 replaced, and two small record corrections.

4. **Stop producing planning documents after this one.** The folder now holds ~13,600 lines of
   docs against ~8,200 lines of subsystem code. Every session that re-reads it finds the older
   layers partly wrong and writes a newer layer. The verified exit exists; the next artifact
   should be commit A1, not another audit.

---

## 1. Method

| pass | what it did |
|---|---|
| write-path trace | Every claim about `loan_posting_service` (`_sync`/`_walk`/`_asof`/`_reader`), loaders, immutability, scenario scoping -- 15 claims, each cited to current line numbers. |
| seam/fence trace | Apparatus measurements, the three recorded breaches, W9905-vs-W9906 contradiction, unfenced tiers, Phase-A absence checks. |
| cash-side trace | `balance_calculator`, `balance_resolver`, anchor schema + write path, transaction settlement model (`paid_at`), posting-ledger dating rules, dead-code checks. |
| test-suite trace | The shape matrix, settled-payment fixtures across the whole suite, `_forward_rows` coverage, the tax hybrid's guards, the cross-page oracle, fail-loud tests. |
| probes (dev DB) | Baseline reproduction; the fold rebuilt and run day-by-day (both loans); the D5 one-clock variant run day-by-day; the grid's mortgage view; scalar-vs-daily-series; anchor cadence; dropped settled transactions; the X0 early-settle census. |

Probes live in this session's scratchpad (`probe_fold.py` is the one to keep -- it is the B1/B2
starting point, again). No production data was touched; all probes were read-only against dev.

---

## 2. What was verified and stands

### 2a. The fold (the whole bet) -- CONFIRMED

Rebuilt independently from `_replay_events`'s own primitives, bounded by `_asof.effective_date()`'s
rule (payment: `pay_period.start`; anchor: `LEAST(anchor_date, containing period.start)`):

```text
Mortgage (acct 3): 6 events,  fold(today)=177277.97 == seam,  113 days checked, 0 mismatches
Van Loan (acct 8): 8 events,  fold(today)=15663.59  == seam,   99 days checked, 0 mismatches
```

Three things fall directly out of the event listing itself:

* **B-11's root is visible in the stream**: the Mortgage's 2018 origination row exists in
  `budget.loan_anchor_events` and is absent from the events (`load_loan_anchor_facts` filters to
  `user_trueup` + `tracking_start`, `loan_loaders.py:187-196`). Plan step C1 is correctly aimed.
* **FU-1 is visible in the stream**: the Van Loan's duplicate same-day anchors appear as the
  delta sequence `-108.16, +898.44, -897.16, -3.94`. Data artifact, confirmed; the plan is right
  not to touch it silently.
* **The postings table has not drifted from the source events on real data** -- which is exactly
  the invariant Phase E promotes to a write-time check.

### 2b. The D5 receipt -- CONFIRMED, with one load-bearing consequence

Re-running the fold with ONE clock (payments visible on their due date, anchors on `anchor_date`):

```text
Mortgage: fold(today)=177277.97 == seam.  History differs on 26 days in 4 windows (max 11d).
Van Loan: fold(today)=15663.59  == seam.  History differs on 13 days in 5 windows (max 5d).
```

Today's balance is unchanged, history repositions in bounded windows -- the plan's receipt is
accurate. **But the first Mortgage window (2026-03-26..03-31) is not a repositioning; it is a
false zero worth $178,375.43**: under one clock the tracking-start anchor becomes visible on
03-31 instead of 03-26, and with no origination event in the stream the fold reads $0 for six
days. The plan's own C1 (origination always an event) fills that window with the estimated tier.
**Therefore C1-before-C2 is not a preference; it is a correctness gate.** Section 6 makes it one.

### 2c. The live defects -- CONFIRMED live, one now timestamped

* **B-3 (grid renders the Mortgage rising)** -- re-measured today: grid `178,103.41` flat then
  RISING to `181,925.31` by 2026-08-27 while the seam falls `177,277.97 -> 176,719.77`. The
  stored `accounts.current_anchor_balance` for the loan is still `178103.41`, and both loans
  carry cash-anchor history rows (Mortgage 2026-05-01, Van Loan 2026-05-22) -- B-15's write path
  has already fired on real data. A1 is correctly scoped (route gate + picker gate).
* **The cash treadmill, caught in the act**: Checking now has **44** anchor assertions. Today at
  13:35 the user marked ~14 transactions Paid and at **13:36:44** asserted anchor #72
  ($2,826.58). Between the 2026-06-29 anchor and that moment, **$9,431.72 of settled activity
  (16 transactions, both directions) sat in post-anchor periods that no balance producer
  counted** (`sum_projected` is Projected-only, `balance_calculator.py:480,523-525`). D1-cash
  confirmed, bigger than the audit's $1,923.75 snapshot.
* **D2-cash (period-flat scalar)** -- live divergence today: `balance_at` = **897.10**,
  `cash_daily_balance_series` = **1,896.58**, same account, same date, same seam. And for the six
  days before today's anchor both producers now answer **2,826.58** -- today's anchor
  flat-carried backward. Every re-anchor rewrites the account's entire displayed past; with 44
  anchors the history has been rewritten 44 times while `account_anchor_history` holds the real
  record (X3's point, confirmed).
* **Dead code confirmed**: `/analytics/year-end` 302s (`analytics.py:349-363`);
  `compute_year_end_summary` has zero non-test callers. The Taxes tab IS live and reaches
  `_income_tax` via `tax_report_service.py:648`.

### 2d. The write-path story -- CONFIRMED, with two additions the documents miss

All 15 write-path claims verified (the clock at `_sync.py:139`, the anchor drop at
`_walk.py:356-358`, the discarded running balance, the "RECORDED, never SHOWN" rule at
`_walk.py:243-252`, the clock-free cash walk, the tie-breaks, immutability, no `scenario_id` on
fact tables). Two additions:

* **Escrow changes never trigger a posting sync at all** -- they are forward-boundary-guarded
  instead (`escrow_rates.py:225-257`). The plan's risk list already flags this (Phase E work);
  recorded here because the audit's sync-caller list wrongly included escrow.
* **There is a SECOND write-path clock read the plan does not list**: the settle-time freeze
  resolves the loan's P&I as of `date.today()` (`loan_payment_service.py:762`) when capturing
  `actual_amount`. A payment settled late across a rate boundary freezes today's cash, not the
  due date's. A3 deletes the sync's clock, not this one. Under D3's drift warning it becomes
  visible, but it should be on the plan's risk register.

### 2e. The fence measurements -- CONFIRMED

681 lines / 52 of logic / 138 of allowlist data; 95-108 allowlist entries; ~6 hand-synced module
lists; the three never-referenced structures are real (their only consumer is a completeness
test); the three recorded breaches are quoted in the checker's own comments; W9905's message
still names the resolver as authoritative while W9906 calls it the hole; `net_worth_kernel.py`
sits at exactly 1,000 lines. Two sharpenings:

* **W9906's user-facing message is stale**: it still says `resolve_account_loan` is "NOT a
  producer and not flagged" while the code fences it (`balance_seam.py:551-556` vs `:184`).
* **The `loan_resolver` package itself is entirely unfenced** -- not in any producer set, not in
  `_FENCED_MODULE_RULINGS`, so W9909's fail-closed check does not cover it either. Consumer ->
  `loan_loaders` -> `resolve_loan` -> `.current_balance` is an open route no gate can see. This
  sharpens B-12 and is one more reason Phase D (structure replaces policy) beats growing the
  fence.

---

## 3. Corrections to the record (found by running, all in the two NEWEST documents)

### 3.1 CRITICAL for the cash arc: X0 as specified double-counts, and the correct rule already exists

`audit_cash_balance.md` X0 rules: *"a post-anchor period is entirely after the assertion, so every
settled transaction in it is unambiguously not in the anchor -- count them."* **The premise is
false, and it is false on your real data.** A pay period is a budget dimension; settlement is an
instant. Nothing prevents settling a transaction budgeted to a future period (verified: no date
guard anywhere in the status machine; `status_seam.py` writes `paid_at` freely), and you do it
routinely:

```text
15 (anchor, transaction) pairs on Checking where the transaction SETTLED BEFORE the
anchor was asserted but is BUDGETED to a period AFTER the anchor's period.
Example: Transfer to Fidelity MM $500, paid_at 2026-04-11, period 2026-04-23 --
         with anchors asserted 04-11, 04-13, 04-15 (x3), 04-17, 04-22 in between.
```

The anchor's own contract says it "already reflects all settled activity known at the moment it
was asserted" (`anchor_service.py:246-250`). The bank balance asserted 04-13 already excludes
that $500. X0 would subtract it again in the 04-23 period: **a $500 double-count, times 15
measured shapes.** X0 is the same boundary-predicate disease (a period standing in for an
instant) that this arc keeps diagnosing -- committed in the document that diagnoses it.

**The fix requires NO migration, because the correct rule is already written in the codebase.**
The cash POSTING walk partitions transaction facts by settle instant against each anchor's
assertion instant -- `_anchor_facts` takes `asserted_at = row.created_at` and attributes each
net to `COALESCE(paid_at, period_start_at_midnight_UTC)`
(`account_posting_service/_walk.py:139-155, 187, 228-299`), and its module docstring names the
period-granular version of X0's mistake as a known critical
(`_walk.py:20-22`). The projection engine (`sum_projected`) is the one holdout that ignores
instants -- exactly parallel to the loan side, where the anchor-drop clock was the one holdout.

**Consequences for `audit_cash_balance.md`:**

* **D4's root-cause claim ("the anchor has NO DATE ... one missing column produced D1") is
  wrong.** The assertion instant exists (`created_at`, timestamptz, and the user cannot backdate
  an anchor -- the period is always derived from today, `anchor.py:200`,
  `account_service.py:82-97`). D1's root cause is that the projection engine ignores the instants
  it already has.
* **X1 (an explicit `effective_date`) is demoted from "the prerequisite migration" to a
  feature decision** -- it is needed only if you want backdated assertions ("my statement says
  $X as of the 1st"). Worth having; not blocking.
* **X0 is replaced**: count a settled transaction in the balance walk iff its attribution
  instant (`COALESCE(paid_at, period-start)`) is after the latest anchor's `created_at` -- the
  posting walk's exact rule, ported or (better) shared. This also fixes the anchor's OWN period
  correctly, which X0 explicitly could not.

### 3.2 The arc review's M5 (tax double-count, "$7,181.97 / no guard at all") -- severity REFUTED

The claim: delete the `not row.is_confirmed` exclusion in `_loan_year_interest` and 5,741 tests
stay green while the Schedule-A deduction double-counts confirmed interest.

What the code actually holds (`_income_tax.py:250-263`): the projected term excludes a row when
EITHER it is confirmed OR its due slot appears in `load_settled_payment_due_months` -- two
deliberately overlapping guards (`loan_loaders.py:582-620` explains why `is_confirmed` alone
cannot make the cut for early-settled payments). On production-shaped data every confirmed row's
due month IS a settled due month, so the second clause blocks the double-count even with the
first deleted. The green mutation run is therefore credible -- **and the "$7,181.97 overstated,
$1,580.03 phantom tax savings" interpretation is wrong for production-shaped data.** The
`settled_due_months` clause is itself test-pinned with a computed non-vacuity delta
(`test_year_end_summary_service.py:950-1029`), and the hybrid identity is pinned at `:792-864`
(ledger-confirmed $992.50, both halves nonzero).

What remains true and worth one line in the plan: the `is_confirmed` arm is
redundant-by-overlap on every shape any test builds, i.e. it is currently documentation, not a
guard. Keep it, and give the pair one negative-control test on the shape where they differ, when
the oracle work (B2) is being written anyway. **Do not spend an A2-class commit on it.**

### 3.3 "No balance fixture contains a loan that was ever PAID" -- flat form REFUTED, narrow form CONFIRMED

The suite DOES have settled-payment loan fixtures asserting balance values:
`cross_page_loan_off_schedule_ctx` (settled $5,000 off-schedule payment; consumed by the
net-worth splice test, the cross-page oracle's off-schedule case, and two savings-dashboard tests
including a lump-sum payoff asserting `is_paid_off` with **no true-up**). The narrow claims
stand, verified:

* `TestScalarAndMapAgree::test_every_loan_shape` builds six shapes and none has a settled
  payment, while its class docstring claims "every loan shape the app can produce"
  (`test_balance_at.py:2909-2910, 2936-3014`).
* `_forward_rows`' `is_confirmed` filter has zero discriminating coverage: every test pinning
  exact forward values feeds all-unconfirmed schedules; the one test with confirmed rows computes
  its expectation with the same function (symmetric-mutation-blind). B-4/M1 ($4,449.72) stands.

**A2 is therefore re-scoped, not dropped**: the missing shape is *settled payments + a later
true-up, asserted on the FORWARD tail* (and added to the scalar/map matrix). The tax half of
A2's old justification collapses per 3.2; the forward half is the real payload.

### 3.4 Two citation defects in the from-scratch plan

Small, but this project's standard is "if you cannot cite it, you cannot claim it":

* The plan's scenario-scoping argument cites `resolution_context.py:123-127` for "no consumer
  ever reads a loan balance in a non-baseline scenario." No such text exists there (`:123-127`
  is the constructor's return). The underlying facts were re-verified independently (the fact
  tables carry no `scenario_id`; dev has exactly one scenario, the baseline) -- but the sentence
  is an assertion, not a citation, and B1 should pin it with a test instead.
* "`transactions/mutations.py:610-616`" is `app/routes/transactions/mutations.py` (the freeze is
  real, at `:599-616`; the path is wrong).

---

## 4. The questions, answered independently

**Is this DRY?** The shipped code: no. Ten-plus producers for one question, four stored/derived
copies of a loan balance, two hand-synchronised boundary rules (the splice), and a fence
policing what an abstraction should make impossible. The 07-14 diagnosis ("one missing
abstraction, not ten duplicate functions") is correct and my probes support it: six events
reproduce every day of the Mortgage's history. The plan's fold + typed balances is the DRY fix;
the fence never was. One prior DRY claim is corrected in 3.2 (the tax guard pair is redundancy
*by design*, with a documented reason -- the correct response is a negative control, not a merge;
the C3 lesson applied).

**Is this SOLID?** The seam's dependency direction is right and stays. SRP: `net_worth_kernel`
at its 1,000-line ceiling holding three dispatches -- extraction is overdue and Phase C/D does
it by deletion. OCP: inverted today (adding a module means editing up to 6 allowlists); Phase D
restores it structurally. DIP/LSP: consumers depend on concrete bundles that leak balances
(`LoanState.current_balance`, `AmortizationRow.remaining_balance`); C4/C6 delete both. Verdict:
not SOLID today; the plan's end state is.

**Is this fully normalized?** No -- four denormalized balance stores exist and two have already
misfired on real data (`accounts.current_anchor_balance` written onto loans -- both real loans
carry rows; the postings-as-truth outage class B-1). The plan addresses all four: A1 gates the
column's loan path, E1 turns postings into a reconciled projection, C4 deletes the DTO copy, C8
derives the payoff date. Cash keeps its column as a cache with X4's reconciliation. This is the
right shape: **denormalization is acceptable only with a reconciliation invariant**, and E1 is
that invariant.

**Is this robust?** Fail-loud-on-read converts data corruption into five-page outages, and the
corruption it guards against is manufactured by the app's own write-time clock. That is not
robustness; it is a tripwire in front of a self-inflicted hazard. A3 (delete the clock) plus
Phase E (write-time reconciliation) is the robust design: the raise moves to the write path
where it is actionable, and the read path becomes total. Ratified. One residual: the
settle-time freeze clock (2d) needs a line in the risk register.

**Is this maintainable for a solo developer?** The measured record says the CURRENT design is
not: 42 balance-core commits in three weeks, a defect found in every one's review, 40:1
apparatus-to-logic in the fence, ~13,600 doc lines for ~8,200 code lines, and gates (10.00
pylint + 7,387 green) that a $197k defect passed. The plan is maintainable *because it deletes*:
Section 10's concept/code kill-list is the single strongest argument for it. The discipline that
must survive the rebuild: probe-first, exhaustive oracles (not samples), negative-control every
guard, and **plans measured in commits, not pages**.

**Is it future-proof and extensible?** The event model is the extensible one: a new account kind
is a new event vocabulary over the same fold; a new surface calls `positions()`; predictions are
typed as predictions. The fence model is anti-extensible (every addition edits allowlists). The
cash side proves the generality claim: assertion events + transaction events fold identically,
and the posting walk already implements the instant partition the fold needs (3.1).

**Is it financially correct?** The seam's loan answers: yes, verified to the cent on every day
of both loans' recorded history, twice over (my fold, and the 07-14 review's independent one).
The surfaces around it: no -- B-3 live on the grid today (rising mortgage), the cash projection
drops settled facts ($9,431.72 uncounted over the last 17 days until today's manual re-anchor),
two cash producers disagree by $999.48 today about the same account, and the pre-anchor past is
fabricated by flat-carry. The plan + amended cash arc close every named gap. One modeling
question affects financial correctness and is put to you as R-A below (D5's cross-account skew).

**Would I build it this way from scratch?** The target, yes -- it is the canonical design:
source events -> one total fold for "what do I owe/have" -> the double-entry ledger as a
*derived, reconciled* projection for reporting -> predictions typed as predictions. That is how
accounting systems are built (source documents first, ledgers derived), and the app had it
inverted. For cash specifically, the from-scratch framing is **bank reconciliation**: the anchor
is a statement-balance assertion, settled transactions are cleared items, and the classical
primitive the app lacks is the cleared-vs-booked distinction -- `paid_at` approximates it and is
sufficient now; an optional user-editable settled-date would perfect it later. I would not build
the fence, the splice, the seed flags, or fail-loud-on-read from scratch; the plan deletes all
of them.

---

## 5. Rulings: ratified, amended, and one new one needed

* **D1 (records only; a plan cannot have already happened)** -- RATIFIED. The clamp formulation
  (`max(due_date, as_of + 1d)`) makes the cliff inexpressible without a today-fork. Note the
  accepted simplification: a delinquent loan's balance holds flat (no penalty-interest accrual);
  true today as well; document it as intended.
* **D2 (contractual back-projection as an explicit ESTIMATED tier)** -- RATIFIED, including the
  honest step at tracking-start. The fold probe makes the alternative concrete: without C1 the
  pre-tracking window is a false $0 (2b), and with C1-but-smoothing you would discard the
  origination fact.
* **D3 (planned cash wins + drift warning, `extra_principal` exempt)** -- RATIFIED. Add to its
  commit: the settle-time freeze clock (2d) is the drift the warning will surface; name it in
  the plan.
* **D4 (the grid refuses amortizing accounts; gate the true-up route)** -- RATIFIED. Re-measured
  live today; both gates one commit; nothing touches the seam.
* **D5 (one clock)** -- RATIFIED IN DIRECTION, with one consequence to re-confirm (R-A) and one
  hard gate (C1 strictly before C2, per 2b).

### R-A (new, needs your ruling before C2): which date is an ACTUAL payment's balance event?

D5 as locked books an actual payment on its **due date**. Your real July payment: due 07-01,
settled 07-07, cash $1,910.95, principal $276.72. Net worth on 2026-07-03 under each rule:

| rule | loan side on 07-03 | checking side on 07-03 | net-worth effect |
|---|---|---|---|
| **(a) due date (D5 as locked)** | already reduced (-$276.72 owed) on 07-01 | cash still present (leaves 07-07) | **overstated by ~$1,910.95 x 6 days, every month** (debt down AND cash not yet gone) |
| **(b) settled date (`paid_at` civil date), sequenced by due date** | reduces 07-07 | cash leaves 07-07 (the ledger already dates cash by `paid_at`, `posting_service.py:103-125`) | consistent; both sides move together |

(b) is also the more literal reading of this arc's own rule -- the due date is the *schedule's*
date; `paid_at` is the *record's*. Sequencing (the order payments hit the split math) stays
due-date, so out-of-order settlement cannot reorder installments; only visibility moves.
`paid_at` can be NULL on old rows -- fall back to the due date. **Recommendation: (b).** If you
rule (a), the skew is bounded (days between due and settle, monthly) and should be documented as
accepted; nothing else in the plan changes either way.

### R-B (new, replaces the cash plan's X0/X1 sequencing): the instant partition

Per 3.1: the projection counts a settled transaction iff `COALESCE(paid_at, period start)` is
after the latest anchor's `created_at` -- the posting walk's existing rule, shared not
duplicated. X1 (explicit anchor `effective_date`) becomes an optional feature (backdated
statement assertions), not a prerequisite. X2-X4 stand as scoped, sequenced after the loan fold
proves the machinery (the loan-first sequencing argument in `audit_cash_balance.md` Section 6 is
correct and is ratified).

---

## 6. The amended build order

Deltas against `implementation_plan_loan_balance_from_scratch.md` Section 8 -- everything not
named here is ratified as written, including the regression baseline discipline (with one note:
live-number tables in these documents go stale within days -- today's Checking figures already
differ from the 07-14 audit's -- so pin oracles in tests, never in prose).

| step | change | why |
|---|---|---|
| A1 | none | Both gates re-verified live today. |
| **A2** | **re-scope**: settled-payments + later-true-up shape added to the matrix **with forward-tail assertions** (that is what kills B-4/M1's $4,449.72). Drop the tax justification (3.2); instead add one negative-control for the `is_confirmed`/`settled_due_months` pair while in the file. | The flat "no paid fixture" claim was wrong; the narrow gap is the forward tail. |
| A3 | none; add to its commit message the note that the settle-time freeze clock (`loan_payment_service.py:762`) remains and is D3's problem, per 2d. | Second clock read was unlisted. |
| B1/B2 | none in scope; seed B1 from this session's `probe_fold.py` (it already implements event assembly, both date structures, and the day-by-day comparison). B2 stays the hard gate: every day, every shape, generated shapes included. | Rebuilt twice now by two independent reviews; both matched. Commit it as code. |
| **C1 -> C2** | **make the ordering an explicit gate**: C2 may not land without C1, and C2's sign-off must include the tracking-boundary window (2b's probe: 6 days x $178k on the Mortgage if reordered). | Probe-proven false-zero window. |
| C2 | blocked on **R-A**. | Cross-account skew needs an eyes-open ruling. |
| C3-C8, D, E | none. For E, consider (do not commit to) a DB-level invariant alongside the write-time assert -- the deferred-trigger pattern already exists for SUM=0. | |
| **Cash X0** | **replace** with R-B's instant partition (no migration). X1 demoted to optional feature. X2-X4 unchanged, after the loan cutover. | X0 as written double-counts on 15 measured real shapes (3.1). |
| Fence | freeze it: no new entries, no new lists. Fix nothing but the stale W9906 message text if it misleads during the build (2e). Phase D retires it to a smoke alarm as planned. | Growing it further is negative-value; `loan_resolver` being wholly unfenced (2e) proves containment already failed. |

**What to stop doing** -- the 07-14 list is ratified verbatim (stop managing the partiality, stop
growing the fence, stop 1,500-line plans, stop treating green gates as evidence), plus one
addition earned by this review: **stop asserting facts in documents without a citation that was
re-checked at writing time.** Three of the five documents under review contained at least one
load-bearing claim that was false or uncited (X0's premise, D4-cash's root cause, M5's severity,
the scenario citation), and each survived until someone ran the code. The standard the audits
demand of the app -- no guessing, prove it -- applies to the audits.

---

## 7. Am I chasing my tail? (the direct answer)

You were. The mechanism was named correctly on 07-14 -- a partial balance function forcing every
caller to compose it with flags, each composition a new producer, each producer a new defect --
and I verify the mechanism, not just the story: every defect class in the register maps onto
either the partiality (B-1/B-8/B-11/B-13), the unfenced copies of the answer (B-2/B-3/B-12/B-15),
or a boundary predicate standing in for an instant or a record (B-9/FU-7, D5's period-start
artifact, X0's period-for-instant). The tail-chasing FEELING since then is not evidence of a bad
direction; it is what convergence on a correct root cause looks like from inside: each layer of
document found the previous layer's residual errors *because the claims finally became
checkable*. The checkable claims held (Section 2). The four that did not are corrected (Section
3), and none of them was directional.

The remaining risk is not analytical, it is procedural: the live defects (grid, cash projection)
keep bleeding while documents accumulate, and every additional planning pass has itself been
producing one new boundary-predicate error. Build A1 and A2 next, in that order, and let B2's
oracle -- not another review -- be the thing that judges the cutover.

---

## 8. What this review did NOT verify

* The 17 exotic-shape matrix (asserted by the 07-14 audit's clone-driven pass; consistent with
  everything probed here, but not re-driven).
* The 6.6x performance measurement (direction is plausible -- the fold is 6-8 events vs a
  273-360-row walk plus ledger reads -- but I did not re-time it).
* The 46-producer census count (the structural claim it supports -- unfenceable tiers exist --
  was verified independently via `loan_resolver` and the ORM column; the exact count was not).
* Production parity: all probes ran against dev's clone; prod was not touched.
* M5's mutation run (the tooling declined a mutated-tree test run, correctly; settled by
  first-hand code reading of both guards and their pinning tests instead -- see 3.2).
