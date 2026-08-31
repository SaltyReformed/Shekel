# The cash balance architecture: the plan of record

**This is the ONLY live document for the balance arc, and it carries the work that REMAINS.**
Amendments are edits HERE, a shipped step gets its checkbox ticked with its commit hash HERE, and no
new planning documents get written for this arc. **The rules are `../../plans/conventions.md`** and
`tools/plan_gate/` grades this file against them.

**What is already done is in `archive/`, indexed by `archive/README.md`. None of it governs
anything, and no live sentence here depends on one** (rule 5) -- an obligation a live step inherits
is stated at that step.

## Where the arc stands

**A signpost, not a log**, and `../../plans/conventions.md` rule 6 is what it is held to: replaced
each session, capped, and carrying no volatile value -- branch state, what production runs and the
migration head are MEASUREMENTS, named by their command rather than copied.

| | | detail |
|---|---|---|
| **just landed** | **X-f3c-2b-1 -- a movement cannot predate the books it is in** (`2cf2ac0a`). An opening equity is the CLOSING balance for its own day (**R-HG**), the same rule an assertion's `observed_on` states, so nothing may be dated on or before `opened_on`. Refused at `settle_day.record_settle_day` (the ONE ORM writer) and at the bulk `reconcile_service.record_settled_days`, and made UNSTORABLE in both directions by deferrable constraint triggers over `budget.transactions`, `budget.transaction_entries` and `budget.account_openings`. **12 rows over five accounts, not the 8 over four N-378 counted**: Checking's four 2026-03-27 rows net the `$2,057.42` between its `$689.16` opening and its `$2,746.58` first assertion. It MOVES TWO BALANCE SHEETS -- reproducing the deploy's own `backfill_all_account_anchor_postings` on a production clone, `verify_statement_baseline` gains `Fidelity Money Market Savings -- Opening` at `$4,879.26` on 2026-04-08 and 2026-04-22, tie-out closing both sides. **The first draft called it money-neutral on two harnesses that CANNOT SEE the axis**: the fold seeds at the equity as a scalar, so no `opened_on` change can move either, and the positive control varied the equity instead of the day (adversarial review, 2026-08-28) | Section 5, X-f3c-2b-1 |
| **in flight** | Nothing. Read branch state from `git branch -vv` and the deployed revision from `docker inspect shekel-prod-app`. What to pick up next is `../../plans/steps.md`'s first row | Section 5 |
| **what changed the plan** | **A DATA REPAIR IS PERFORMED THROUGH THE APP'S OWN DOORS, NEVER BY A MIGRATION WRITING MONEY ROWS** (developer, 2026-08-28, **R-HJ**), so the account-10 repair left `X-f3c-2b-1` and rides `X-f3c-2b-2`'s door. Two of the three questions a migration would have forced are already answered by the doors' own code, which is the evidence: a question that exists only because you are writing raw SQL says you are writing it in the wrong place. With it, **R-HK** merges accounts 2 and 10 -- one real Fidelity account, two app rows, `$5,363.56` of it double-counted on the balance sheet (**N-384**) -- which opens the books 2026-03-26 at `$5,350.21` -- the bank's own close for the calendar's first day, replayed to ZERO mismatches over the export's 17 stated days -- rather than R-HH's 2026-04-08; **R-HL** / **R-HM** settle where the dividends' income lands and how the accrual window clears the last one, and **R-HN** rules that a downgrade of an append-only append deletes nothing. **X-f3c's ORDER was reversed and it decomposed into five leaves** (2026-08-27, **balance:R-GW**): the residual is RECORDED before the reset is DELETED, measured by `tests/manual/measure_cutover_against_bank.py` and reported SPLIT by whether a day carries an assertion, because pooled a same-day assertion cancels the gap to the cent (**N-337**) | Section 4, balance:R-GW / R-HJ |
| **blocked on you** | **One OPERATOR act gates the money-moving leaves: import the account's own statement history.** Production holds 0 statement imports, 0 bank lines and 0 matches, while the SECU exports the shipped adapter reads sit on disk covering 2026-01-02 to 2026-07-19 -- and X-f3c's correctness is measurable only against them (**N-368**). Everything else this arc owes is a `developer-decision` / `operator` row in `ledger.md`; what to do next is `../../plans/steps.md`'s first row, never this section | ledger.md, N-368 |
| **complementary arcs** | TWO, neither part of this arc and neither pausing it: the recurrence redesign (block 9) and the pay calendar (block 10). **The pay calendar's `C2` IS this arc's `X-l`**, and also recurrence `R-F12` -- one commit under three names, so whoever builds it must satisfy all three specifications | `implementation_plan_recurrence_redesign.md`, `implementation_plan_pay_calendar.md` |
| **the live lesson** | **A comparison against an outside record can be confounded by the very mechanism under test, and the pooled number reads as the strongest evidence.** X-f3c's order was measured against the developer's own bank file, and the headline -- today's code equals the bank on 17 of 75 days where the cutover-as-specified equals it on 0 -- is dominated by the 46 days carrying an assertion, which is exactly where the RESET forces the answer. This arc had already ruled that metric misleading one screen over (`bank_agreement` scores the RESIDUE for this reason, **N-337**, 11 of 35 real disagreements reading as exact agreement). Splitting it is what makes it evidence: on the 29 days no assertion touches, `$529.48` against `$1,956.64`. The conclusion survived; the pooled figure was not what established it | Section 4, R-GW |
| **resuming cold** | Branch from `dev`; whether it leads `main` is a MEASUREMENT (`git log --oneline origin/main..dev`). Read the repo's migration head from `alembic_version` rather than from prose, and rebuild the test template only if you add a migration. **Pass `TEST_DB_PREFIX=<name>` when another checkout may be running the suite**, put the venv on `PATH` (`scripts/test.sh` execs bare `pytest`) and keep it ACTIVE for `git commit` (pre-commit hooks are `language: system`). The registries have their own gate: `pytest tools/plan_gate -c /dev/null -q`. Two REFERENCE tags, neither a rebase candidate: `xd-attempt-1-parked-n155` (X-d) and `xx-attempt-1-held-rde` (X-x) | `../../plans/verification.md` |

Section 5 is the work that remains and Section 4 the rulings that govern it; `archive/` is what
already shipped. **Every open defect is a row in `../../plans/ledger.md` whose `arc` reads
`balance`**, and every step is indexed in `../../plans/steps.md` -- both moved out on 2026-08-09
because a finding is not arc-local.

## 1. The problem, in plain words

The app answered "what is this cash account's balance at time T?" in three places, three ways, and
the ways disagreed. Underneath all three was ONE root cause, the cash form of the loan side's:
**the honest balance function was PARTIAL.** The projection started at the latest anchor and summed
only still-Projected rows forward, so it could not answer a past date, could not see a settled row,
and had to be composed with a seed, a flag or a fallback at every call site -- and every
composition was a new producer that could disagree with the others. Plan step X-c2b2 replaced all
three with ONE total fold and they closed together. The modelled-asset form of the same defect
closed at X-g2b. Both records are in the archive.

**What remains:**
1. **The write side and the read side are still two statements of one rule.** The posted account
   ledger is written by its own walk while the projection folds another; they are proven
   byte-identical, which is a test keeping two implementations in step rather than a structure that
   cannot drift. Plan step **X-d**.
2. **A derived value is read as a source of truth.** **Its named instance is GONE and the SHAPE is
   not** -- the number is kept because `app/` cites it (`cash_ledger/_walk.py`,
   `status_seam/` x3) and renumbering would break those. `Account.current_anchor_*` was a
   denormalized copy of the latest `AccountAnchorHistory` row that `resolve_anchor` detected
   diverging and only LOGGED; X-f1c3a re-pointed every reader at the resolver and X-f1c3c dropped
   both columns (2026-08-04, archived). What survives is the general rule the code cites at the
   posting walk -- **the posted ledger is a projection of the facts, never a second opinion about
   them**, which is root cause 1's other face -- plus the residue **X-e** owns.
3. **The pay calendar is a PARTIAL function -- the same shape, on the other axis.**
   `pay_period_service.get_all_periods` returns the materialized rows and stops, so past the last
   one every consumer improvises. Plan step **X-l**.
4. **The assertion RESETS the ledger instead of reconciling with it**, plugging the difference to
   Equity so real activity is invisible on the income statement. **Re-measured 2026-08-13 at
   `$15,413.71` gross, and the GROSS is the instrument's own path length**: the plug telescopes by
   construction, so the net is a function of the LAST assertion alone. Against the BANK the
   untracked spend is `$2,096.37` over the span. The steps are X-f3a..X-f3c (R-EB, amended
   R-FL..R-FN).
5. **The app can record when money moved and the recorded days are still guesses, and (4) is
   downstream of this.** `paid_at` was `db.func.now()` at the click; X-f1b replaced it with
   `transactions.settled_on` but the backfill was that derivation verbatim, so no stored date
   improved. **Measured against the developer's own bank export 2026-08-13: of 110 movements matched
   to bank lines on exact amount, only 33 (30%) carry the day the bank posted them.** Step **X-f6a**
   replaces the guess with the bank's own record.
6. **A surface still picks which producer answers for its account.** `/dashboard`'s hero, its pulse
   and the analytics calendar read the kind-blind cash view while `/grid` and `/savings` read the
   modelled one, so the same account renders two balances for the same period. Plan step **X-j**.
7. **The read pass pins a clock it does not hand to its loaders.** `BalanceContext` fixes the
   pass's `as_of` and `scenario` and memoizes three loan derivations; every other input is loaded
   ad hoc at the wall clock, which is both a redundancy and an impurity. Plan step **X-i**.

Section 5 specifies each step and 5.0 says why its block sits where it does; the EXECUTION ORDER is
`../../plans/steps.md` and who owns each open finding is `../../plans/ledger.md`, with no row
unowned.

## 2. What is already shipped and correct (the foundation this plan builds on)

**This is the FOUNDATION the remaining steps stand on, not the log of how it was built** -- that is
the three as-built records.

**WHAT has shipped and which PR shipped it is `archive/README.md`'s index** -- the table that stood
here was a second copy of it (rule 16), and it carried R-DH (a)'s "an assertion is the closing
balance for its civil day", which ruling **R-FL** has since measured FALSE against the bank. What
belongs here is what no archive holds: the ORACLES a live commit is still graded against.

**The loan baseline is still a LIVE regression gate for CASH commits.** Mortgage (account 3)
**$177,277.97**, Van Loan (account 8) **$15,663.59**. Re-derive both from the seam before and after
every commit in this phase: a cash change that moves a loan figure is wrong.

**Verified for cash, independently of the producers under test:** the fold reproduces the app's own
persisted double-entry ledger to the cent on both accounts carrying postings (Checking `$2,824.26`
over 177 postings, Money Market `$3,659.51` over 10), and R-K's grid identity holds on 360 of 360
real (account, period) pairs. Do not trust a prose figure older than its write date; pin oracles in
tests.

## 3. The solution

An account's balance is a fold over its event stream. The fold is TOTAL: it cannot return `None`,
cannot raise, and answers any date -- asked about a date before every event it answers the seed,
not an error. That single property deletes the partiality and everything built to manage it.

### 3.1 The cash fold, as shipped

```text
CashEvent = (instant, kind, payload)                    -- cash_ledger._events
kind = ASSERTION  balance := anchor_balance             (AccountAnchorHistory, EVERY row)
     | ACTUAL     balance += settled_cash_leg (signed)  (settled transaction rows)

walk_cash_ledger(account, scenario) = replay(events, seeded 0.00)  -- cash_ledger._walk
dated_deltas(walk) -> [(visible_civil_date, delta)]                -- the ONE clock
```

Three grains read that one fold: a scalar at a date, a per-period map, and a daily series. A
still-Projected item whose date has passed is CLAMPED FORWARD, never absorbed by an assertion
(R-G): its effective instant is `max(its attribution date, as_of + 1 day)`, because a plan cannot
have already happened. Before an account's first assertion the fold BACK-PROJECTS over the records
and holds flat before the earliest one (R-I / R-S).

**What "settled transaction rows" MEANS, and it is structural rather than a fence: a row is settled
if and only if it carries a settle day.** `status_seam.apply_status_change` is the single door that
writes `status_id`, and it writes the day in the same call. A `CHECK` cannot express it -- the
predicate lives in `ref.statuses` and a constraint cannot join -- and a reader finding a settled row
with no day FAILS LOUD, because silently dropping it from the fold is silent money loss. Established
at X-f1 and stated HERE rather than in that step's entry, because X-f2 and X-f3 read against it and
rule 5 forbids a live sentence depending on an archived one.

### 3.2 The modeled asset, as shipped

One sequential replay over four event kinds -- ASSERTION, ACTUAL, CONTRIBUTION, ACCRUAL -- serves
all five account kinds. An ASSERTION always wins (R-S). ACCRUAL is DAILY, computed at full
precision and credited in whole cents carrying the sub-cent remainder (R-T / R-X), and runs from
the latest assertion's own day forward, including inside the period that holds it (R-L / R-Y). A
CONTRIBUTION lands on its pay period's `start_date` and exists only when that payday is strictly
after the latest assertion (R-Z).

### 3.3 The anchor half, as DESIGNED (ruling R-EB, amended by R-FL..R-FN 2026-08-13)

`balance(T)` becomes `opening equity + SUM(postings <= T)`. An assertion becomes a RECONCILIATION --
a recorded observation the outstanding set is measured against -- rather than a reset that discards
what the records say, and an unexplained difference becomes a recorded, categorizable transaction.

**Four facts carry it, and the second is the one the app does not have** (R-FL):

| # | the fact | today | the design |
|---|---|---|---|
| 1 | when the money moved | `transactions.settled_on` | unchanged |
| 2 | has this line CLEARED the bank | GUESSED: `settled_on <= the latest assertion's day` (`ReconciledThrough.covers`) | RECORDED: the row names the assertion that saw it |
| 3 | a cleared purchase's cash | leaves the book only when its ENVELOPE closes | posts when the purchase clears (R-FM) |
| 4 | an unexplained difference | an automatic plug to `anchor_equity`, invisible | derived, displayed, and RECORDED on an explicit act (R-FN) |

**Fact 2 is why the reset cannot simply be deleted, and ruling R-FL carries the measurement that
says so.** The steps are section 5; their sequence is `../../plans/steps.md`'s.

## 4. The rulings

**This arc's rulings are in `../../plans/rulings.md`, rows whose `arc` is `balance`.** The key is
`(arc, id)` and no arc document states a ruling; cite one as `balance:R-xx` wherever the bare id
could be another arc's (`../../plans/conventions.md` rules 9 and 10).

**Where a live step needs more than the rule, its Section 5 entry restates it inline** (rule 5).
That is unchanged, and it is why this section is a pointer rather than a deletion: a specification
may depend on a ruling, and the ruling now resolves by key.

## 5. The steps

### 5.0 Why each block sits where it does

**The ORDER is `../../plans/steps.md` and only there.** This section is the arc's RATIONALE: the
fact that forces each block's position, in this arc's own terms. **The block numbers are NAMES, not
ranks** -- four documents cite them -- and this section states no sequence, no member steps and no
readiness. Every copy of those ever kept here drifted: it listed a shipped step as pending, and it
carried an ordering claim three other documents called unsatisfiable. **The phase headings below
group the same work by SUBJECT; this groups it by CAUSE.**

| # | block | the fact that forces its position |
|---|---|---|
| 1 | **the anchor half** | The only remaining work that moves a figure the developer reads, and since 2026-08-13 it ABSORBS block 5: the cutover's residual is meaningless until clearing is a fact and the dates are the bank's, so the importer's first leaf runs inside this block rather than after the card arc |
| 2 | **the pay-calendar door** | Its gate CLEARED 2026-08-05 when the X-f1 cluster reached production (`8d812662`): `accounts.current_anchor_period_id` is GONE rather than going, which is the fact X-ad's trace turned on. The "ONE PR" pairing with X-x ENDED at **R-EY** -- X-x is held behind the writer (**R-DE**) and behind the pay-calendar arc's `C3`, which owns the repair its refusals point at |
| 3 | **the posting restructure** | `X-ai-s` is held until the cutover, which deletes the correction family it would buy attribution for; `X-d` is PARKED on **N-155**, whose fix is X-ai's own placement; `X-ak` carries **N-193**, a reproducible unhandled 500 on a money route |
| 4 | **the credit-card arc** (own document) | `CC1b`'s fold is specified against the reset semantics **R-EB** deletes at the cutover, and `CC3b` derives a settle from `paid_at`, which X-f1b deleted |
| 5 | **the bank import** | ABSORBED INTO BLOCK 1 on 2026-08-13. Its 2026-08-03 position -- after the card arc, so one matching rule covers checking and card rows -- rested on the cutover not needing it; measurement refuted that, and the developer's exports carry both accounts anyway, so the one-rule argument survives inside block 1 |
| 6 | **the read-path residue** | Nothing blocks on it and its footprint is disjoint from the write path: tag `xd-attempt-1-parked-n155`'s 30 `app/` files against tag `xx-attempt-1-held-rde`'s 26, **zero overlap**, measured. UNGATED, which is what lets `X-l` run early -- and it must, because it is block 10's `C2` and recurrence `R-F12` as well |
| 7 | **the gate and vocabulary residue** | Shares files with nothing; interleaves anywhere |
| 8 | **E2 and G** | Runs LAST by ruling. `G2` must not begin before the boundary it rests on is proven |
| 9 | **the recurrence redesign** (own document) -- **NOT part of this arc** | Overlaps every live block of this arc by ONE file (`_recurrence_common.py`, measured against the `xd-attempt-1-parked-n155` tag), so it pauses nothing here. Its own document holds its argument; how its steps interleave with this arc's is `steps.md`'s answer |
| 10 | **the pay calendar** (own document) -- **NOT part of this arc** | Opened 2026-08-08 out of the recurrence arc's F-10: `budget.pay_periods` stores the payday and DERIVES `end_date` / `period_index`, so a gap, an overlap and an index out of date order all stop being expressible. **Its `C2` IS this arc's `X-l`**, and also recurrence `R-F12` -- one commit under three names, and whoever builds it must satisfy all three specifications, including this arc's N-82 / N-128 / N-79-far |
| 11 | **the amount model** (ruling **R-FI**) | It is the ROOT CAUSE of block 1's last open finding and of three mechanisms nothing else will delete, so it precedes every step that reads a projected figure. Its first two leaves touch no file block 1 does, which is why they run beside the anchor half rather than after it |

**Blocks 9 and 10 are SEPARATE ARCS and blocks 4 and 5 are FEATURES consuming this arc's output.**
None is a correctness fix inside this arc, and **none owns a `ledger.md` row whose `arc` reads
`balance`, nor ever will**. Their documents are `implementation_plan_recurrence_redesign.md`,
`implementation_plan_pay_calendar.md`, `implementation_plan_credit_card.md` and
`implementation_plan_bank_import.md`, all under `docs/plans/`. **The card plan's
2026-07-19 sequencing was DISCHARGED and is now archived to
`../../plans/historical/credit_card_sequencing_2026-07-19.md`**: every balance-arc step it named has
shipped (old `X4` survives here as X-e). What gates the card arc is R-EB, and block 4 is the reason.

**Step ids are append-only, and a DECOMPOSED parent ticks with the last of its leaves** --
`../../plans/conventions.md` rules 2 and 10, stated once there and graded there.

**Shipped steps are one line each in `archive/phase_x_as_built_2026-08-04.md`** (Section 1 through
X-f1b, Section 1a for the X-f1 cluster) and are not repeated here. X-ae, X-af and X-aj1 left this
section on 2026-08-11 as copies of rows the archive already carried; the one live fact among them,
X-aj1 leaving `transfer_service.py` at 987 of 1000, is **N-152**'s own row.

### Phase X -- the anchor half (ruling R-EB; runs FIRST)

- [ ] **X-f** `feat(transactions): the app records when money moved` -- the DECOMPOSED parent,
  carrying **N-42**, redesigned 2026-08-03 (R-EB) and re-decomposed 2026-08-13 (R-FL..R-FO). **It
  ticks with X-f4**, not the import: a leaf is derived WITHIN an arc (`_classes.py:78`).

* [x] **X-f1** `8d812662` a settle carries the day the money moved; absorbs **S2-b**; fourteen leaves in `archive/…2026-08-04.md` 1a. Did NOT close **N-173** (**X-f6a**'s).

* [ ] **X-ax** `fix(carry-forward): a rollover is reconciled against the source it divided` --
  closes **N-249**. **MOVES MONEY.** Carry-forward splits one budget across two periods: it settles
  the source at `sum(entries)` and rolls `estimated - sum(entries)` into the next period's row. The
  split is irreversible and the source is left **Paid, not archived**, so `entry_service` still
  accepts purchases against it and `_resync_settled_envelope` re-derives its actual -- at which point
  the two halves no longer sum to the budget they divided. Live shape: row 2391 settled `$0.00` on
  2026-06-03 with `$157.60` folded into row 2392 (`$257.60 = $100 + $157.60`); one `$30.00` purchase
  against 2391 leaves the projection `$30.00` richer than reality. The other direction covers **8
  rows / `$794.79`** settled at their estimate, any of which a single late `$5` purchase drops to
  `$5`. **The developer ruled the remedy 2026-08-12**: reconcile, do not refuse -- when a settled
  source's spend changes, the target's top-up moves by the same amount. **Its first act is a trace,
  not code**: the top-up is not identified on the target row today (it is folded into
  `estimated_amount` with `is_override = True`), so the step must first rule how a rollover is
  RECORDED before it can be reconciled -- and that recording is a derived quantity, which is
  **R-FI**'s subject, so the design belongs beside the amount model rather than beneath it.
* [ ] **X-f3** `feat(cash): the ledger is sum-of-postings` -- the DECOMPOSED parent of THE CUTOVER,
  split 2026-08-13 (**R-FL**..**R-FO**, Section 3.3) when measurement refuted its one-commit form
  twice over. It ticks with X-f3c; **X-f5 is SUPERSEDED**, its outcome being what the cutover
  produces by construction.
  * [ ] **X-f3a** the DECOMPOSED parent of "clearing is a recorded fact", split 2026-08-14 (**R-FQ**).
    Carries **N-273**.
  * [ ] **X-f3a-2** `feat(reconcile): a walked statement's silence is a fact` -- record that a
    statement was walked LINE BY LINE, so a line it did not show is NOT CLEARED rather than unknown,
    and widen the offer set to every uncleared line. **MOVES MONEY.** Closes **N-273**. **It follows
    X-f3c and R-FQ's theorem is why**: while an assertion RESETS the ledger a line no assertion
    clears reads as `anchor + X` at every later date (**N-285**).
  * [ ] **X-f3c** the DECOMPOSED parent of THE CUTOVER, re-decomposed 2026-08-27 (**R-GW**, with
    **R-GX** and **R-GY**, the two rulings its first draft owed). Carries **N-172**, **N-174**.
    * [x] **X-f3c-1** `2dad8512` -- the assertion RESET left the kind-blind walk (**R-J**) for `balance_at._assertions`, so `walk_cash_ledger` yields FACTS and each fold applies the policy its own kind needs. Byte-identical.
    * [x] **X-f3c-2a** `2aa2296d` -- opening equity is a RECORDED fact (**R-GX**, **R-HE**): an append-only `budget.account_openings` read by BOTH the fold and the posted ledger, seeded at the derived value. `is_opening` decides no figure and R-I's compensator is gone.
    * [ ] **X-f3c-2b** the DECOMPOSED parent of the books boundary (**R-HG**), split 2026-08-28 and
      re-cut the same day (**R-HJ**): the invariant is one commit, and the DOOR -- with the
      account-10 repair it is the only honest route for -- is the other. Carries **N-379**.
      * [x] **X-f3c-2b-1** `2cf2ac0a` -- an opening equity is the CLOSING balance for its own day
        (**R-HG**), so no movement may be dated on or before `opened_on`: refused at
        `settle_day.record_settle_day` and at `reconcile_service.record_settled_days`, and made
        UNSTORABLE both ways by three deferrable constraint triggers. 12 rows over five accounts
        legalised; **account 10 now opens 2026-04-05**, which is what `X-f3c-2b-2` restates FROM.
        Two balance sheets moved. Closes **N-378**.
      * [ ] **X-f3c-2b-2** `feat(accounts): an owner can say when the books opened` -- the DOOR
        **N-275**, **N-379** and **N-382** each name, NOT bounded by `earliest_recordable_day` (that
        floor is a rule about assertions, **R-ER**, and it would make the books unopenable before
        the calendar), plus the withheld bank line **N-383** records. **It then carries the
        account-10 repair, because a repair is performed through the app's own DOORS and never by a
        migration writing money rows** (**R-HJ**): the two questions a migration would have forced
        -- delete or reverse the phantom's journal entry, and whether a leg move re-dates its
        postings -- are already answered by `transfer_service.delete_transfer` and
        `_update._reconcile_postings_after_update`, so they exist only in the raw-SQL spelling.
        Accounts 2 and 10 are ONE real Fidelity account whose history CONSOLIDATES onto 10
        (**R-HK**), which opens the books **2026-03-26 at `$5,350.21`** -- the bank's own close
        for the pay calendar's first day. Under **R-HG** every bank line dated on or before
        `opened_on` is inside the equity, so the six lines to 03-26 are ABSORBED and **no
        transfer endpoint has to move**, which matters because no door moves one:
        `TransferUpdateSchema` carries neither endpoint and the template door RETAINS a
        reattributed row holding a settlement record. Replayed against the export rather than
        argued: **zero mismatches on all 17 days it states a balance**, and a `$0.00` correction
        at all FOUR typed balances plus R-HM's new one. Transfer 1 becomes a plain `$500`
        Checking EXPENSE dated 03-27 -- its arrival leg would double-count money the opening now
        holds, while Checking's own leg must survive (it nets into the `$2,057.42` reconciling
        Checking's opening to its first assertion, whose own repair is **N-275**). That brings
        the `$5,363.56` the archived record still carries on the balance sheet to nothing
        (**N-384**). **THAT DISPOSAL IS TWO DOOR ACTS AND NOT A DELETION**,
        traced rather than assumed: `archive_helpers.account_has_ledger_postings` is
        EXISTENCE-based and account 2 holds four immutable journal entries, so the
        hard-delete guard archives instead and always will -- correctly, since a
        CASCADE would delete only that account's own legs and strand the paired ones
        as unbalanced single-leg entries. What zeroes the record is restating its
        opening to `$0.00` through this step's own door and asserting `$0.00` on
        `observed_on` 2026-04-06, which SUPERSEDES the `$5,363.56` assertion of that
        same day rather than conflicting with it -- `balance_at/_assertions` orders on
        `(observed_on, created_at, id)` and states that the LAST of a day's assertions
        is that day's closing balance. **Restating the opening alone is not enough and
        that is measured**: with the arrival leg gone and the opening at `$0.00`,
        the 2026-04-06 assertion still says `$5,363.56`, so
        `sync_account_anchor_postings` books a `$5,363.56` true-up and the asset
        returns. This step verifies the posted-ledger side follows both acts.
        The five dividends take their own `Income: Interest & Dividends` category
        (**R-HL**), and 07-31's `$14.39` is recorded behind a 2026-07-31 assertion at `$3,673.90`
        that moves the accrual window past it (**R-HM**). Closes **N-383**.
    * [x] **X-f3c-2c** `930f06fc` -- the DECOMPOSED parent of the append-only refusal (**R-HZ**):
      the fixtures stop editing an assertion and the refusal that makes editing one impossible ship
      TOGETHER, so no tree has one without the other. Closed **N-287**; opened **N-392**, **N-393**.
      * [x] **X-f3c-2c-1** `930f06fc` -- a fixture PLACES an assertion and never edits one: three
        re-stamping helpers become `reassert_balance_on`, `append_balance_assertion` states both
        clocks at INSERT, the factories take their day at `create_account`, and the seeded
        origination stays on the bootstrap day. ~40 cases state their own asserted day now.
      * [x] **X-f3c-2c-2** `930f06fc` -- `budget.refuse_append_only_change` on all three tables
        (**R-HY**): every UPDATE refused, a DELETE refused while the owning ACCOUNT stands, so
        `ON DELETE CASCADE` stays the disposal path. `Account.anchor_history` takes
        `passive_deletes="all"`, which N-287's own evidence missed; `append_only_guard_lifted`
        keeps the three controls beneath the trigger graded.
    * [ ] **X-f3c-3** `feat(cash): the app says what it cannot explain` -- the account's
      OUTSTANDING DIFFERENCE (`latest asserted - (opening equity + SUM(postings))`, ONE figure per
      account and not a per-assertion correction) derived off the fold and displayed beside whether
      an imported statement reconciles the span. Money-neutral; the instrument the next leaf needs.
    * [ ] **X-f3c-4** `feat(cash): an unexplained difference is a transaction you accept` --
      **MOVES MONEY.** The explicit act **R-FN** requires, under **R-GY**'s three bounds: dated on
      the latest assertion's own day so that assertion clears it and the balance line does not move
      (R-DH (a)); amount DERIVED from the postings OTHER than itself until the owner categorises
      it; offered only where a `statement_imports` row covers the span at a zero residue. The
      posted ledger, the Book-vs-bank row and the income statement all move. Closes **N-171** --
      Checking's `$1,776.88` leaves `Checking -- Opening` equity -- and flips **PLAIN's arm of
      X-f3d's dispatch** (R-FO).
    * [ ] **X-f3c-5** `feat(cash): an assertion is a check, not a reset` -- **THE FLIP. MOVES MONEY.
      OWN PR, NO BACKLOG.** `balance(T)` becomes `opening equity + SUM(postings <= T)`, PLAIN stops
      booking `account_trueup`, the reconcile reverses the 162 entries that stand, and what a
      clearing link SURVIVES is settled (**N-307**). **Ship-gated on R-DH (c) in both orders
      without a true-up** and on the import **N-368** names. Closes **N-172**, **N-174**, **N-285**,
      **N-290**, **N-307**, **N-308**.
* [ ] **X-ay** `feat(ledger): a modelled account's own inflows are postings` -- closes **N-277**.
  A modelled account's true-up residual is what it EARNED only where the app models no inflow it
  fails to post, and it fails to post all of them: `ref.posting_sources` holds no accrual and no
  contribution kind, so an employer match and a payroll deduction reach the balance through the
  assertion alone. Measured 2026-08-14: account 6 grew `x1.15448` where both IRAs, modelled at the
  same return, grew `x1.11084` -- **`$1,157.16`** X-f3d must call a change in value, not a gain.
* [ ] **X-f4** `refactor(cash): delete what the cutover orphans` -- `ReconciledThrough`'s COVERAGE
  rule and its cash consumers, `account_posting_service/_anchors.py`, the correction machinery, the
  R-I seed compensator. **Its deletion set is NARROWER than it was**: X-f3a-1 re-pointed the cash
  consumers, so what dies here is the DATE ARM of `StatementCoverage` plus whatever of
  `ReconciledThrough` its two surviving callers -- the modelled contribution feed (R-Z) and the
  posted self-heal's date-keyed cost guard -- do not need. Byte-identical by construction, and it must **name the
  residue arm it deletes** rather than letting it go unnoticed -- that branch has fired in production
  (**N-176**). Closes **N-176**, takes **N-161** with its family, and **N-218 closed** (`7a466d31`);
  **N-169 closed already** at `C2-d` (`3e6cd4ec`). **GATES the recurrence redesign's `R5`**, which
  edits `cash_ledger/_events.py` inside this deletion set -- a column rename in a file this step
  deletes from is avoided by ordering rather than by merge.
*THIRTEEN of this span's shipped steps left under rule 5, indexed by `archive/README.md`: X-an, X-f2's four leaves, X-aq, X-as and X-ap (2026-08-13); X-f3a-1 and X-f3d (2026-08-16); X-f1 with five others (2026-08-26); X-f3b and X-i3 (2026-08-27). Sentences above name several of them for how the code came to be, which rule 15 sanctions. The statement importer moved to its own arc 2026-08-13: `../../plans/implementation_plan_bank_import.md`.*

### Phase X -- the amount model (ruling R-FI)

**What this phase is about, in one sentence: the app stores five amounts it also computes, and each
one grew its own private repair mechanism.** Salary income is repaired at READ time by
`income_service.live_projected_net`, which writes nothing back (**N-224**); a loan-payment shadow by
`loan_payment_service.live_loan_transfer_amounts`, which also reads the wall clock (**N-40**); a
transfer shadow by a two-line copy in `transfer_service.update_transfer` beside a drift corrector
that logs `"Correcting shadow %d estimated_amount drift"`; an ordinary template row by
`regenerate_for_template`, which DELETES and recreates every non-override row from a date the form
supplies and never stores. `credit_card:CC4b` plans a fifth. **R-FI deletes the class**: a derived
amount is not stored, one total resolver answers, and the CHECK makes a stale derived figure
unrepresentable rather than merely unlikely.

**Measured on production 2026-08-11**, so the size of each cutover is known rather than estimated:
**997 rows, and the buckets RECONCILE to it** because a set of measurements that does not is a
census nobody can check: 51 projected non-overridden salary rows (`$139,642.27`) + 511 non-override
template rows + 342 transfer shadows + 49 ad-hoc rows + 37 override rows + 7 already-settled salary
rows = 997. **Two of those cells were re-measured at X-au-b and are stated more honestly here.** The
shadow and override cells depend on which bucket claims the 12 rows that are BOTH: counting them as
overrides gives 330 and 49 instead of 342 and 37, and both readings reconcile to 997 -- which is why
the resolver's dispatch ORDER is a rule rather than a convenience. And the 49 ad-hoc rows do NOT all
"own their amount and always will", as the first draft of this sentence said: **21 of them are CC
paybacks**, whose figure is derived from the purchases they repay and stored with no reconciler.
That is the kind carrying NEITHER link, which is why R-FI's discriminator is declared rather than
read off the columns. **That kind is no longer cut over here** -- X-au-i is withdrawn to the card
arc, which deletes the payback outright. `template_id` and `transfer_id` are mutually
exclusive across every row -- STRUCTURALLY since X-au-c1's `ck_transactions_one_pricing_link`,
which widened the convention to the third link and was measured at 0 violations -- but the five rules
are NOT a partition over those two columns (ruling R-FI). Stored-vs-derived drift TODAY is **0 rows /
`$0.00`**, re-measured 2026-08-12 by X-au-b's exhaustive oracle -- which
`../../plans/conventions.md` rule 8 says is not resolved, and which the read-time repair is what
hides.

* [ ] **X-au** `feat(cash): a row's amount is either its own or derived` -- the DECOMPOSED parent
  (**R-FI**), carrying **N-40**, **N-224**, **N-228**, **N-238**. It ticks with the last of its
  leaves. **It SUPERSEDES X-ar**, whose two stated premises tracing also refuted: its deletion set was
  unreachable (`live_amount_overrides` merges the salary and loan halves at
  `cash_ledger/_amounts.py:684`, and the loan half feeds BOTH shadow legs, so the loan half being out
  of scope left nothing deletable), and it claimed **N-40**, which `../../plans/ledger.md` owned to
  `X-i2`. **It also WITHDRAWS `X-au-i`** (developer, 2026-08-21), which leaves the order as `X-ar`
  did: the CC payback is a PHANTOM the card arc's locked 2026-07-19 rulings already condemn -- "the
  phantom payback dies", `CC3b` deletes it and `CC3c` deletes `credit_workflow.py` whole while
  RENAMING `credit_payback_for_id` to `card_charge_for_id` -- so a `credit_source` relation, a ref
  seed and a 23-row migration would buy structure on a column about to be renamed, for a kind R-FI's
  five never included. What was LIVE in it shipped at X-au-j: **N-252**'s hand-edit refusal and
  **N-323**'s narrowed predicate. N-243's payback bullets and N-311 pass to the card arc. The
  `is_envelope` re-pricing measured while tracing it (`$181.58` to `$500.00` through the popover's
  own Save) is NOT the ground: ruling **R-FK** accepted that class for SALARY at `$13,499.89`
  (**N-261**) and put its remedy at the write DOOR.
  **Two obligations RESTATED from leaves archived 2026-08-26**, binding the leaves that REMAIN: a
  derived row's answer must be INVARIANT under a change to its own amount column, agreement being
  blind to the resolver (X-au-b); and the basis is REQUIRED on both `settle_amount` twins, pinned once in `cash_ledger.baseline_amount_basis` (X-au-j).
* [ ] **X-au-c** the amount model's SEAM -- the DECOMPOSED parent of three leaves (developer,
  2026-08-12), because the census this step's own specification demanded as its first act came back
  at ~25 code reads across 15 modules and it is not one session's work. The split puts the schema
  where nothing can move a figure, then the readers where every change is provably byte-identical,
  then the money. It ticks with the last of them.
  **Three of its leaves left for `archive/x_au_c_as_built_2026-08-26.md`** under rule 5 on
  2026-08-26. **What still binds the leaves that REMAIN**, restated because rule 5 forbids a live
  sentence depending on an archived one: `get_payment_history` may never take the resolver and
  nothing reachable from `loan_payment_service` may NAME `cash_ledger`, which is why the
  producer-free arms live in `row_valuation.py`; and the amount rules read no STATUS, the basis
  pinning `date.today()` being `X-i2`'s money rather than theirs.
  * [x] **X-au-c3** `3d1379d1` -- a settle RECORDS what moved rather than refreshing an amount.
    **A LATER leaf must obey**: a figure and a status change are independent facts ONE seam call
    applies. Held here rather than archived because two live blocker cells name it.
* [ ] **X-au-d** `refactor(salary): a projected paycheck is not stored` -- the SALARY cutover. The
  recurrence engine stops pricing salary rows, the 51 live rows go NULL, and
  `income_service.live_projected_net`, `transaction_service._freshest_amount` and
  `_reconcile_cached_amount` are deleted. Closes **N-224** and **N-228** (the batch producer called
  with a one-element list, which disappears with the mechanism rather than by threading a map).
* [ ] **X-au-e** `refactor(recurrence): a template row reads its template's series` -- the TEMPLATE
  cutover, onto X-au-a's series. Generation stops pricing, 511 rows go NULL, and regeneration's amount
  arm plus the conflict chooser's keep-vs-use decision are deleted: a hand-edited month owns its
  figure, so the collision the chooser mediates cannot occur (developer, 2026-08-11). **This is where
  the measured `$502.45` defect class dies** -- `_get_transaction_amount` priced a third paycheck from
  a truncated period list, and a generator that prices nothing cannot mis-price.
* [ ] **X-au-g** `refactor(loans): a loan payment resolves on its own due date` -- the LOAN-PAYMENT
  cutover, closing **N-40**. **It must RULE first**, and the ruling is forced rather than optional: a
  resolver may not read the wall clock, and `live_loan_transfer_amounts` resolves its basis through
  `_resolve_loan_basis(..., date.today())`. The rule ruling D5 already applied to escrow -- a shadow's
  figure resolves on the shadow's own DUE date -- is what makes the loan half resolvable. Dormant on
  production (`budget.loan_payment_settings` is EMPTY, both payments predating migration
  `c2a2c508e103`, which deliberately did not backfill), so it moves `$0.00` there and is graded on a
  seeded loan.
* [ ] **X-au-f** `refactor(transfers): a shadow's amount is its parent's` -- the TRANSFER cutover.
  `transfers.amount` resolves from the template series for a generated transfer, a shadow resolves
  from its parent, and the copy at `transfer_service.py:534` with the drift corrector at `:814` both
  go. **Transfer Invariant 3 becomes STRUCTURAL rather than maintained.** `uq_transfers_adhoc_dedupe`
  is unaffected: its predicate is `transfer_template_id IS NULL`, and an ad-hoc transfer owns its
  amount. **It runs AFTER the loan leaf and the first draft had them the other way round**, which an
  adversarial review reproduced: a loan-payment shadow IS a transfer shadow, so this step NULLs it,
  while `loan_payment_service._manual_shadow_amount:660` reads `shadow.estimated_amount` under a
  docstring asserting that column is "NOT NULL, always the generated base" -- manual-mode loan
  payments would be broken for the whole interval between the two leaves.

* [ ] **X-au-h** `refactor(transactions): is_override says one thing` -- closes **N-238**. The flag
  carries FOUR facts, not the two the first draft named, and an adversarial review found the other
  two. (1) the user RE-PRICED the row and (2) the user MOVED it to another period -- both written at
  `routes/transactions/mutations.py:295`; (3) this row SURVIVES the regeneration sweep
  (`_recurrence_common.py:375`, and `pay_period_admin.py:821` escalates it to "deleting this period
  loses data"); and (4) this row is EXEMPT from the partial unique index, whose predicate is literally
  `is_override = FALSE` (`models/transaction.py:199-203`, `models/transfer.py:64`) --
  `carry_forward_service/_execute.py:352` sets the flag purely to stay index-safe. **So the naive
  absorption is unsafe and measured so**: a moved-but-not-repriced row would carry a NULL amount, fall
  back INSIDE `idx_transactions_template_period_scenario`, and collide with the target period's
  canonical generated row. Meanings 3 and 4 need representation before the flag can go.
* [ ] **X-av** `refactor(salary): the pay rate is a dated per-paycheck gross` -- closes **N-237**
  and **N-391**. **Re-scoped 2026-08-29 by ruling R-HW(b)**, which changed its UNIT as well as its
  dating: the stored fact becomes what ONE paycheck pays, effective-dated, and `annual_salary`
  becomes `gross x periods_per_year` -- derived and shown beside the entry, never a second stored
  figure. Rule 1 of R-FI still applies to the dating half: `apply_raises(base_salary, raises,
  as_of)` resolves RAISES as-of while the base is the base for all time, so the app cannot tell "I
  got a raise" from "I typed the wrong salary". **The unit half is where the bank evidence points**
  -- the owner's stub states BOTH `$91,675.00` a year and `$3,526.00` a paycheck, and `26 x
  $3,526.00 = $91,676.00`, so the source contradicts itself and the app resolves it silently. A
  stored per-paycheck figure can hold the number that actually arrives and SHOW the annualisation,
  which makes the contradiction visible instead. **It does not presume the gross is the culprit**:
  N-391 records that a `$0.04` error in any of the twelve hand-entered deductions reproduces the
  same net, so the step opens with an operator re-reading one stub.
* [x] **X-aw** `078077db` -- a paycheck's gross is a RATE (**R-HW**), so **N-239** died by construction. Closed its horizon half; opened **N-390** and **N-391**. Record in `archive/four_shipped_steps_2026-08-30.md`.
* [x] **X-bh-1** `b955d0c8` -- the engine reads the owner's CALENDAR: `PayrollBasis` carries it,
  `all_periods` is deleted, and the four judgements become two producers (`pay_calendar._rhythm`),
  so **D25**'s narrow context is unrepresentable rather than forbidden in prose. Both trees driven
  against the dev database agree on all 63 paychecks, `$170,974.29` of net and every analytics
  month. Opened **N-394**, **N-395**, **N-396**.
* [ ] **X-bh-2** `refactor(salary): the rhythm runs backward too` -- closes **N-390**. The producers
  count SAVED paydays plus the forward projection `pay_calendar:R-PC9` rules; below the opening
  payday they count nothing, so a month or year the record opens INSIDE is counted from the first
  RECORDED paycheck. **Ruled at `balance:R-IA`**: a stored
  `budget.pay_schedule.history_opens_on`, asked at registration, bounds the backward rhythm --
  the app knows the CADENCE and cannot derive when the job began. N-390 carries the figures, the
  `$502.45` reproduction and the holiday shift no projection models.

### Phase X -- the posting restructure

* [ ] **X-ai** `refactor(posting): the posted ledger gets one verb and one trigger` -- the END
  STATE R-DM named and **R-DU** ruled, on BOTH ledgers. **The row-level posting writer stops being
  the interface**: you do not tell the ledger "this row settled", you tell it "account A in
  scenario S changed; re-derive it from its facts". **R-DV** then fixed what OWNS an entry: an
  entry is the projection of exactly ONE source event and the event owns it; the account is the
  scope of the loop, never an owner. That answer makes N-161 and N-162 die by construction and
  removes the write OSCILLATION account-owned entries would have created. Its correction half already books in the period CONTAINING the day it asserts (**R-EA**, shipped
  at X-ai-r). Carries **N-144**,
  **N-153**, **N-155**, **N-157**, **N-158**, **N-160**, **N-161**, **N-162**, **N-163**,
  **N-164**, **N-165**, **N-166**, **N-167**. Requirements its leaves must satisfy, each from a
  review rather than from the design: the verb's source set is the UNION of the walk's facts and
  the ledger's already-posted source links (**N-162**); the verb returns the `(account, scenario)`
  pairs its emitted legs touched and the caller re-enqueues them with a stated termination argument
  (**N-165**); a per-`(account, scenario)` advisory lock is taken inside the verb (**N-166**); and
  the registry-scoped commit-boundary grader is BLIND to bulk `UPDATE` / `DELETE` across 20 call
  sites, which **X-ai-g** classifies rather than forbids (**N-163**).
  * [ ] **X-ai-a** the cash verb, built from R-DV's sentence with N-162's, N-165's and N-166's
    requirements in it from the start.
  * [ ] **X-ai-b** the trigger: the commit-boundary grader, drained from the registry the writers
    populate.
  * [ ] **X-ai-c** the loan side onto the same verb.
  * [ ] **X-ai-g** the bulk-statement census: each of the 20 sites is proven unable to touch a
    posted row, routed through a writer, or named in the one docstring that states what the grader
    cannot see. **The remedy is NOT a checker forbidding bulk statements** -- several are
    legitimate and performance-critical.
  * [ ] **X-ai-s** the `journal_entries` migration -- source identity as an EXCLUSIVE ARC of typed
    FKs with an AT-MOST-ONE check (**R-DY**), plus the reversal linkage **N-167** asks for. **HELD
    pending X-f3**: it buys per-assertion attribution for the correction family X-f3 deletes, and
    running it first would ship a migration and a backfill for something about to be removed.
* [ ] **X-d** `fix(cash): the posted account ledger is a checked projection` -- E1a's shape for
  cash. The posting writer consumes X-a's walk instead of its own, and the per-visible-date assert
  (`sum(postings) == fold(ACTUAL events)`) makes a stale posting a detectable, repairable cache
  inconsistency. **Its ship gate is ALREADY MEASURED AND CLEAN -- do not re-run it as if it were
  open.** Swept on the 2026-08-01 production clone, in both directions and positive-controlled:
  **0** entries with both concrete FKs NULL and a non-correction source kind, and **0** whose
  `transaction_id` resolves to a missing, soft-deleted or non-contributing row -- against 170
  transaction-linked, 19 transfer-linked and 128 both-NULL entries (every one of the 128 a
  correction) proving the joins bite. **So no F1-class human decision waits on today's data**; that
  is a fact about the data, not the mechanism, and the step must still decide whether the residue
  arm's defence moves to the checked-projection assert or is ceded. **It also inherits
  `ledger_report_service/_attribution.py`'s two duplicate date loaders**, whose `duplicate-code`
  disable named the shipped step 3 as its resolver: X-d deletes their twin rather than extracting a
  third shared home, which is why extracting one now would be scaffolding for a caller X-d removes.
  Both carried from `anchor_settle_partition.md` at its archive move. **PARKED at tag
  `xd-attempt-1-parked-n155`** (six commits, one deliberately RED, off PR #80; the branch
  `feat/xd-checked-projection` was deleted 2026-08-04 once the tag was pushed, because X-f4 deletes
  `account_posting_service/_anchors.py` and the correction machinery that branch conflicted in, so
  the code is a REFERENCE for the re-land and never a rebase candidate) **and nothing there is for
  merge.** The park is
  **N-155**, the step's own assert: it compares an account's WHOLE ledger against its WHOLE
  source-row walk but rides on the PER-ROW write path, so any operation settling N rows one at a
  time grades a half-finished state and REFUSES the write -- three confirmed production defects,
  one reproduced twice independently. The placement is X-ai's, not a patch at each loop. Carries
  **N-135** (wrap both bare fact fields in their own types). Re-lands after X-ai.
* [ ] **X-aj** `refactor(status): one status seam, and the fence is structural` -- rulings
  **R-DN**, **R-DO**, **R-DP**. Its merge half shipped as X-aj1 above, which ANSWERED
  **N-145** (`transfer_service.py` at 999 of the 1000-line ceiling, blocking X-d): R-DN
  ruled it and X-aj1 took the module to 987. That row is archived here rather than carried
  (2026-08-13) -- what survives is **N-152**'s claim that 987 is an answer and not a
  solution, and the structural remedy is the PACKAGE X-f2-c3 later built.
  * [ ] **X-aj2** the structural write door and the DELETION of W9907 (**R-DP**), carrying
    **N-149**, **N-151**, **N-185** and **N-188**. Its trace decides the three candidates R-DP
    names and must rule what a row may be BORN as: today `create_transfer` runs no legality check,
    so "every row is born Projected and every other status is a verified transition" is available
    and makes the constructor question disappear -- but it would refuse creations the tests
    currently make, one of which (Received) is not in the transfer map at all, so the refusal would
    be correct and the test wrong. That is a behaviour change on a creation path and gets its own
    worked ruling, not a build decision.
    **It carries N-321 too, enumerated here for that same reason**:
    `grid/_transaction_entries.html` withdraws the purchase-date and description inputs,
    the add form and the delete button on `status.is_settled`, where `entry_service`
    refuses only `amount` / `is_credit`, a delete and an UNDATED add -- so **R-FW**'s
    purchase-date correction is reachable from statement review and not from the popover.
    **It also carries N-334, and the five surfaces are enumerated here because a
    ledger row is an index entry** (conventions rule 4): since plan step X-az a row records
    WHICH KIND of settle day it holds -- a bank observation, a balance assertion's UPPER BOUND,
    or the owner's own -- and no screen says so. `grid/_transaction_entries.html` is the one that
    says the opposite (*"Posted <date> -- this money has left your account"*, for a day the bank
    may never have confirmed); `accounts/_statement_review_body.html`,
    `grid/_transaction_full_edit.html`, `transfers/_transfer_full_edit.html` and
    `statement_match._accepted_view` are silent. The telemetry half is smaller and belongs with
    them: `entry_service.create_entry` logs `settled_day_basis` and `update_entry`'s event does
    not, so one door's receipt can tell a bound from an observation and the other's cannot.
* [ ] **X-ak** `refactor(transfers): a shadow inherits its parent's fields by ONE rule` -- closes
  **N-148**, **N-150**, **N-152**, **N-156**, **N-159**, **N-170**. **Root: the transfer -> shadow
  mirror is written THREE times and the three already disagree** -- at construction, per-field on
  edit, and again as drift repair, with `scenario_id` mirrored at construction and absent from the
  drift-repair list although that function's docstring claims it re-syncs every mirrored field.
  Transfer Invariants 3, 4 and 5 are `CLAUDE.md` CRITICAL invariants and they currently rest on
  three lists staying in step by memory. Not a live money defect on today's data, which is exactly
  why rule 7 applies. **Its scope is REVERSED by N-150**: rule the stored COPY first (remove it,
  make it structural at the database, or keep it with the cost stated), because unifying the
  copiers while the copy stands makes the denormalization cheaper to maintain. Sequenced after
  X-aj. Also owns **N-152**'s and **N-156**'s answer: an over-ceiling module becomes a PACKAGE
  rather than being shaved again.

### Phase X -- the read-path residue

* [ ] **X-x** `refactor(balance): one pay-calendar precondition, one answer` -- closes **N-116**,
  **N-125**, **N-126**, **N-129**. Rulings **R-CX**..**R-DA**. X-v's sibling one axis over, and its
  own trace inverted the row's premise. **The census replaces the row's count**: an AST pass that
  taints period-valued expressions and reports only ABSENCE tests measures **96 branches in 49
  `app/` files** plus 8 in Jinja, resolving to about 50 distinct answers. They are FIVE questions,
  not one: any periods at all (Q1), which period contains today (Q2), which contains date T (Q3), is
  there a next one (Q4, a normal terminal state), is the requested window non-empty (Q5,
  navigation). **Q1 is unreachable for an owner and Q2 corrupts money**: a 5-day calendar hole moves
  `/savings` net worth by **+$3,228.55**, while `/grid` renders the repair card at the same instant.
  (That measurement ALSO put `Account.current_anchor_balance` on screen as a current balance; **that
  half is now impossible** -- ruling R-EH dropped the column at X-f1c3c, in production since
  `8d812662`. The net-worth figure stands; the substitution does not.)
  **HELD behind X-ad** (ruling R-DE), because N-127 measures the repair its refusals point at as
  non-functional.
  **Its 2026-07-31 build is a REFERENCE, not a rebase candidate.** Tag
  `xx-attempt-1-held-rde` (branch `wip/x-x-held` deleted 2026-08-05 once the tag was pushed, the
  `xd-attempt-1-parked-n155` precedent). Measured at tag time: 39 behind `main`, 3 ahead, 38 files,
  and a test merge conflicts in **7 files across 10 hunks**. Three findings force the re-derive
  rather than a rebase: **one third of it already shipped** (X-x2's anchor-cache arm, below); **its
  reasoning is falsified in at least four places** that assert `accounts.current_anchor_period_id is
  NOT NULL` as a load-bearing guarantee, and that column is gone too; and **one conflict is SEMANTIC**
  -- in `savings_dashboard_service/_projections.py` the branch DELETES the no-current-period fallback
  because `ctx.current_period` is no longer nullable, while `main` (ruling **R-EM**, shipped at
  X-f1c3a) KEEPS the branch and asks the seam for the balance at `ctx.balance_ctx.as_of`. Both cannot
  stand, and choosing is a design decision rather than a merge resolution.
  * [ ] **X-x1 THE ONE ANSWER** (R-CY) -- `PayCalendarGapError`,
    `pay_period_service.require_current_period` / `covers`, the application-level handler and its
    repair page, on the `require_baseline_scenario` / `BaselineMissingError` pattern name for name.
    **It takes the GRID's two pre-checks as its first callers** rather than shipping a door nobody
    walks through: an unreachable handler has no negative control.
  * [ ] **X-x2 THE FABRICATIONS** (R-CY) -- the branches that publish a figure the app did not
    compute take the raising accessor. **Its first arm is DONE and must not be rebuilt**: the
    anchor-cache substitutions died with the column at X-f1c3c (ruling R-EH), and an AST pass over
    `main` finds no live read left -- only prose in 5 `app/` files. What REMAINS, re-measured against
    `8d812662`: the fabricated `$0.00` in four producers, and `build_trend_periods`'
    `current_index = 0` into an empty list, still live at `routes/accounts/detail.py:228` and
    `analytics_view.py:485`.
  * [ ] **X-x3 THE ONE PREDICATE** (R-DA) -- `onboarding.has_periods` asks Q2 rather than Q1, so the
    checklist and the page it renders on cannot disagree.
  * [ ] **X-x4 THE STATES SPLIT** (R-CZ) -- an empty requested window stops answering with the
    absence card, and the card's copy stops naming two states.
  * [ ] **X-x5 THE HARNESS** -- delete `verify_savings_producers.py`'s dict-or-attribute `_get`
    reader and the two-spelling readers its own docstring says X-x deletes. Verified dead by RUNNING
    it against the tree.
* [ ] **X-ad** `feat(periods): the pay calendar a new user can actually enter` -- the DECOMPOSED
  parent, split 2026-08-10 (**R-EZ**). Two write doors, two findings, two commits: the one that
  CREATES a calendar and the one that GROWS it share no function and no test. **It RUNS BEFORE
  X-x** -- R-DD sequenced it after, ruling **R-DE** reversed that, and the later ruling governs --
  but **the pair no longer ships as ONE PR**: **R-EY** moved **N-127** to pay-calendar `C3`, so the
  repair X-x's refusals point at is that step's and X-x waits on both.
  *Its opening line claimed this step "re-anchors accounts". That was expired when written: ruling
  R-EH deleted `accounts.current_anchor_period_id`, so there is nothing left to re-anchor.*
  * [x] **X-ad-a** `2a4eb477` -- registration ASKS for the payday, the cadence and the horizon and
    the bootstrap payday is DELETED; closed **N-123** (= pay-calendar `P3`), satisfies `C4`'s `P8`.
  * [ ] **X-ad-b** the rolling top-up stops writing HISTORY (**N-124**, ruling **R-EZ**). With
    rolling enabled, a lapsed schedule's page load appends periods from `last.end_date + 1`
    wherever that is and populates them -- **61 -> 113 -> 132 periods and +991 transactions** over
    two `/grid` loads on a clone whose schedule ended 1,000 days ago, 19 of the new periods
    entirely historical. It self-heals, which is why nothing ever reported it, and what it heals
    with is fabricated history. **An automatic writer may not create a period that has already
    ended**: the top-up creates nothing on a lapsed schedule and the lapse is surfaced instead,
    because the owner's pay may well have changed while they were away and the app must not guess
    that it did not.
* [ ] **X-y** `refactor(balance): the baseline decision that is not the balance seam's` -- closes
  **N-117**. The fifteen surfaces that resolve the baseline DIRECTLY (`get_baseline_scenario`)
  rather than through a `BalanceContext`, and so answer this state without the seam being asked:
  two grid fragments and the carry-forward preview (400), template and transfer generation (a silent
  commit that generates NOTHING), `salary/profiles` (a flash telling the user to register a new
  account -- a competing repair story, wrong since `/grid/create-baseline` shipped),
  `period_population` (`return 0`), `spending_report_service` (`return None`), both posting syncs (a
  silently narrowed scenario set), and `escrow_rates` / `loan/params` (which hand a NULLABLE id to a
  query). **X-v deliberately did not reach these** (R-CC took only the two that fabricate a
  financial statement); the rest are a different question one tier down -- what may a WRITE do
  without a scenario -- and deserve their own answer. Sequenced after X-x.
* [ ] **X-i** `refactor(balance): one read pass, one derivation, one clock` -- closes **FU-3**,
  **N-14**, **N-56**, **N-72**'s second half, **N-89**, **N-91**, **N-92**, **N-93**,
  **N-115**. **N-40 LEFT this set on 2026-08-11** (ruling R-FI): the clock read is a property of a
  DERIVED amount being stored, so it closes at **X-au-g** and no gate reads a prose closes-set. **Nine rows, one root cause:** `BalanceContext` pins the pass's `as_of` and `scenario`
  and memoizes three LOAN derivations, and every OTHER input is loaded ad hoc at the wall clock. The
  arc solved this for loans at D-ctx and stopped there. Two symptom families the ledger filed apart
  are one defect. RECOMPUTED: the calendar loaded three times per modelled grid render, the
  contribution feed at ~9.4 ms per investment account with no cache, the modelled base built 14
  times for 4 accounts on one `/savings` render, `contractual_schedule_from_origination` twice on the
  property page. UNPINNED: the employer-match gross resolving at an implicit `date.today()`,
  `live_amount_overrides` calling `date.today()` inside the pinned fold, and the standing overpayment
  read off the CURRENT template row whatever date the pass is pinned at.
  * [ ] **X-i1 THE MEMO** -- additive, byte-identical on both databases. The context gains the input
    tier the loan derivations already have, through the SAME `_memoize_once` mechanism rather than a
    second one: the per-account contribution feed, the override map, the standing extra, the
    contractual schedule. **The CALENDAR left this list 2026-08-13**, taken early by `C2-c`
    (`b8a72f6c`) as a method beside `loan_walk`, which owns its derivation, rather than through
    `_memoize_once`, which stores what the seam fills from above. Every loader keeps its clock, so no
    figure can move and the harness is the proof. **Its tier is WIDENED by N-115** (ruling **R-BU**, which SEQUENCED the residual double load here
    rather than deferring it): the dashboard tracks
    section pays twice per render for three more loaders, and the expensive one is **two full
    `calculate_paycheck` runs (7.2 ms / 7 SQL for the second alone)**.
  * [ ] **X-i2 THE CLOCK** -- the cutover. Each memoized loader takes `ctx.as_of` and `ctx.scenario`,
    and this **MOVES MONEY**: the gross measured at `$3,631.74` today against `$3,722.53` at a 2027
    read and `$0` before the first pay period, and FU-3 changes a loan's whole forward trajectory on
    a historical read. Own trace, own oracle, own every-figure sign-off. **Does NOT close N-56**:
    the grid's two self-refresh endpoints are two HTTP requests, so a per-pass memo cannot reach
    across them -- its fix is the `hx-swap-oob` topology, riding here as its own commit. **Sequenced
    BEFORE X-j** on a measured ground: X-j moves three more surfaces onto the modelled view whose
    contribution load costs `2.7 -> 14.8 ms` per render entry, so shipping X-j first ships that
    regression and then removes it. Forks for its trace: which salary profile a historical read
    picks when several are active, and whether deductions resolve at the read date.
  * [x] **X-i3-a THE BOUNDARY** `765daebd`, corrected at `4dddfe73` -- a request is a QUERY or a COMMAND and its transaction says which. Closed **N-353**; opened **N-358**, **N-359**.
  * [x] **X-i3-b THE ACCOMMODATIONS** `1feb0930` -- each accommodation for "the two reads can differ" narrowed to the COMMAND it still describes. Opened **N-364**, **N-365**.
  * [x] **X-i4 THE BINDING** `79a1730c` -- `_memoize_once` takes the `Account` and refuses a foreign one. Ruling **R-GV**. Closed **N-354**; opened **N-360**-**N-363**.
  * [ ] **X-i5 THE COMMAND'S RENDER** -- **N-358**. A mutation route's re-render rides the
    transaction its writes are in, so it reads at READ COMMITTED and is the one render `X-i3-a` does
    not cover; it also keeps `X-i3-b` a NARROWING rather than a deletion. The shape is the one
    `/grid` already has, and the trace owed is WHICH routes render rather than redirect. **X-i4 made
    it load-bearing**: such a render now reads the pre-write FOLD, not merely an older snapshot.
  * [ ] **X-i6 THE READS A PASS DOES NOT MEMOIZE** -- **N-362**, whose row carries the census. A
    pass binds what it MEMOIZES: seven entries early-out before the funnel, the contribution feed
    scopes its payroll loaders off the ACCOUNT's owner, and `bank_agreement` walks the account
    directly. It owes an ARM as well as a fix -- nothing mechanizes "one door" today.

* [ ] **X-bc** `fix(accounts): the collateral link's owner is a foreign KEY, not a validator` --
  closes **N-360**, whose row carries the measurement. `collateral_account_id` is a SINGLE-column FK,
  so only `_validate_collateral_link` keeps a secured loan and its asset in one owner; a link written
  by anything else makes `home_equity_service` value another owner's loans through an unscoped
  backref. **The schema already carries the fix** (`uq_accounts_id_user`, which two other tables
  target this way), and production holds one link, same owner, so nothing is backfilled.

* [ ] **X-j** `feat(balance): one account, one answer -- or a row that explains the difference` --
  closes **N-87**, **N-90**, and **N-83**'s DISPLAY half. **This step is the OWN RULING R-AK
  deferred, and its title does not presuppose the outcome.** R-AK ruled the dashboard, the pulse and
  the analytics calendar STAY on the kind-blind cash view inside X-g3 and said the surviving argument
  deserves its own ruling with its own measurement. **Root: the seam offers two families that answer
  "what is this account worth" for the SAME account, and the CALLER picks.** X-g3b proved the
  resolution on the grid -- give the surface the rows that explain the modelled tiers, then let it
  render the modelled balance, and R-K's identity holds for all five kinds. The two candidate end
  states are "one producer, with the difference rendered" and "two producers that legitimately answer
  different questions, with the NAVIGATION that equates them fixed instead"; the trace decides.
  Live on the developer's own default screens.
* [ ] **X-k** `fix(recurring): the recurrence bound is reconciled, not stored and forgotten` --
  closes **N-18**, **N-19**, **N-23**, **N-24**. **Ruling R-AP, taken AGAINST the recommendation**:
  they stay in this ledger and get a step. The ground the recommendation rested on SURVIVES as the
  step's scoping rule -- X-k touches the recurrence engine and the transfer write door and NOT the
  seam, so it must not grow into one. **The "shares no file with any other remaining step" half is
  FALSE, corrected 2026-08-08**: the recurrence redesign's `R5` edits `recurrence_engine.py` and
  `transfer_service.py`, both of them X-k's. Block 9's one-file overlap was measured against the two
  parked TAGS and never against this step. **Sequence X-k AFTER `R5`**, which deletes
  `compute_due_date` and re-keys the generation index; rebasing X-k over that is cheaper than the
  reverse, and X-k's own root is untouched by it. **Root:
  `RecurrenceRule.end_date` is a stored derived value never reconciled against what was actually
  GENERATED**, and the write door's refusals have no consistent batch contract: one refused loan
  payment rolls back an entire carry-forward batch, and three generation call sites have no
  `ValidationError` handler so a refused write 500s on extend and unarchive. The money consequence
  is on a balance screen: a shadow generated past a bound that later moves EARLIER keeps its
  checking-side expense leg.
* [x] **X-l -- the pay calendar answers any date.** `4f134bf4`. ONE step under three names, so rule 11 keeps its row while its record left for `archive/five_shipped_steps_2026-08-26.md`.
* [ ] **X-m** `refactor(growth): the projection engine takes its axis, not its boundaries` -- closes
  **N-86**. **Root: `growth_engine.project_balance` takes a derived boundary as an ARGUMENT its
  caller must compute to match the window the caller also passes**, and nothing checks that they
  agree. That is Section 8's rule verbatim -- an argument a caller can get wrong is a defect, not a
  contract. **Cost when a caller gets it wrong: `$1,000.00` of annual-limit room per period of
  divergence, compounded over the horizon.** Both live callers are correct today and pinned in both
  directions, but the app carries TWO correct YTD boundaries whose difference is invisible in the
  rendered figures, and a THIRD projection surface would have to KNOW the rule. The fix is a
  signature change: trace every caller before touching it.
* [ ] **X-n** `fix(loan): a redistributed payment carries its REAL installment` -- closes **N-36**.
  **Root: `_redistribute_to_distinct_months` OVERWRITES the fact instead of carrying it.** Its own
  docstring is the evidence: it shifts a colliding payment's DUE date to the next month so the
  monthly engine does not sum two into one, and says only the due date shifts because `payment_date`
  is a FACT carried through untouched. **Both are facts** -- the real installment a payment
  satisfies is as much a fact as the period its cash moved in, and the redistribution destroys it.
  The codebase therefore holds one question with two keys ON PURPOSE (archived ruling D5 keys the
  split inputs on the DUE date; `rate_period_engine._replay_from_anchor` stayed on
  `payment.period_start`), stated at the site so it cannot be rediscovered as an accident. Contained
  today because the replay's rows are DISCARDED whenever a `confirmed_view` is supplied -- and
  containment is not a fix.
* [ ] **X-e** `refactor(accounts): current_anchor_balance is a reconciled cache or it is nothing` --
  carries **N-96**, **N-85**, **N-180** and **X5**. **WIDENED 2026-07-27 (R-AO): the one-liner is
  not the step the code describes.** Most of the census is being taken by X-f1c3a..c, which delete
  the columns outright; what remains here is what those leaves do not reach -- the two callerless
  public seam entries N-96 / N-85 name, the falsified de-duplication rationale in
  `balance_at/_loan_interest.py` whose surviving question is UNVERIFIED (**N-180**), and X5's
  backdated-assertion option, now largely answered by `observed_on`. **Its census is HISTORICAL and must be re-taken before this step is scoped**: it was
  measured when the columns still existed, and `balance_at/_investment.py` was deleted outright at
  `17c57cde` (its modelled-asset work went to `_asset_fold.py`).
* [ ] **X-p** `fix(analytics): the calendar's chips and its balance line are on one clock` -- closes
  **N-58**. (**N-97** closed 2026-08-14, `7a466d31`; the "same day" guarantee beside its dead
  citation had been false since X-c2b2 -- which is N-58.)
  **Sequenced AFTER X-f by that finding's own ruling**, and the reason is not
  caution: X-f shrinks the date noise at its SOURCE, so ruling before it would decide the question
  against numbers X-f then changes. It is scheduled, not deferred. **Root: one day cell renders two
  facts on two clocks with no row explaining the gap** -- the chips are placed on the BUDGET
  attribution date, the balance line steps on the day the money MOVED. Measured: `paid_at - due_date`
  is median 2 days, p75 6, max 25 across 130 settled Checking rows. The option space: place the chip
  on the cash clock (which changes which MONTH a row appears in), give the calendar R-O's treatment
  (a reconciling figure), or rule the divergence acceptable and label it.
* [ ] **X-ab** `refactor(ledger): one asset-vs-liability rule, read and write` -- closes **N-122**
  (R-CU). `ledger_account_service.ledger_class_id_for_category` asks X-z's question a second time on
  the POSTING path, and the two agree by reading. **It is not a residue fix**: it decides which
  ledger class a real account's paired posting account carries, so it is the write path, and Section
  8's "a DRY refactor of a PREDICATE can move money" is the case rather than the caveat. Its trace
  must answer what a re-class does to accounts that already carry postings.
* [ ] **X-ac** `refactor(savings): one liquid-savings reduction` -- closes **N-121**. The cockpit
  reduces `_sum_liquid_balances` over one `account_data` TWICE per render and publishes the answer
  under two context keys -- ruling **R-AZ**'s "one fact under two keys" beside a redundant
  computation. Collapsing it changes what the page publishes, so it wants its own commit
  and its own render diff.

### Phase X -- the gate and vocabulary residue

* [ ] **X-at** `feat(salary): a substituted tax year says so, and a new year can be entered` --
  closes **N-235**, **N-236**. X-as makes an unconfigured year resolve to the latest configured
  year's rules, which is the only available answer and a correct one; what it does not do is SAY so.
  `/analytics/taxes?year=YYYY` accepts any year in `[2000, 2100]`, so a 2019 request renders its
  refund hero, W-2 preview and Schedule A against another year's law with nothing on the page saying
  which, and the only record is a DEBUG line. Carry the resolved year out of the resolver and render
  it. Its second half is the door that is missing entirely: nothing in `app/` creates a
  `TaxBracketSet` outside the signup seed, so the settings screen can write a year's state and FICA
  rows and never its brackets -- it cannot finish the year it starts, and a user following next
  year's IRS release has nowhere to put it.
* [ ] **X-ag** `feat(pylint): lax digit acceptance is refused, not remembered` -- closes **N-139**.
  X-ae converted every submitted-id surface it found and the AST now finds exactly one digit-
  predicate call site in `app/` and `scripts/` (the implementation of the replacement). Nothing stops
  the next one. **What this step must NOT do is what its first two drafts specified**, both refuted
  before it was written: matching `isdigit` / `isdecimal` / `isnumeric` by METHOD NAME reports CLEAN
  over the defect it exists for, because a bare `try: int(raw) / except ValueError` passes it -- and
  that is precisely the form the first ruling specified and measurement rejected. The instrument is
  an open question the step's trace decides, and it must be shown FIRING on a planted defect.
* [ ] **X-ah** `fix(routes): a query-string id is parsed like every other id` -- closes **N-142**.
  The one submitted-id surface X-ae did not convert: 34 `request.args.get(..., type=int)` call sites
  where Werkzeug catches the `ValueError` (so no crash) but the coercion is `int()` (so `'١٠٦'` is
  106, `' 2026 '` is 2026, `'1_0'` is 10). **It needs a per-site ruling, which is why it is a step**:
  the path parameters were all row ids and the schema fields all row ids, so each took one blanket
  rule, but these are MIXED -- `account_id` and `period_id` are row ids while `year`, `month`,
  `offset`, `periods` and `show_all` are not, and `offset=0` / `show_all=0` are meaningful, so a
  blanket `parse_row_id` would silently refuse them. Owes a second small rule in `digit_strings`:
  ASCII-strict, canonical, but admitting zero.
  **The count is DERIVED and here is how, because it moves and three other sentences state it.**
  It is an AST census over `app/` -- a `Call` whose func is a `.get` attribute on a receiver whose
  name ends in `args`, carrying a `type=int` keyword -- and a line grep is not the same thing: a
  grep counts docstring mentions and continuation lines and misses `grid/page.py`'s local
  `request_args`, which is how a review of `pay_calendar:C2-f3e` re-measured it at 40 and was
  wrong in both directions at once. It read **42** until that step and reads **34** after it, which
  collapsed twelve sites in `routes/transactions/forms.py` into four; `url_converters.py`,
  `routes/transfers/_helpers.py` (which counts its own `request.form` site, so it says 35) and
  `steps.md` state the same number and were moved with it.
* [ ] **X-bg** `feat(transfers): an occurrence that did not happen is not an archive` --
  closes **N-386**, whose row carries the measurement. **The door derives its own
  destructiveness from a link rather than from what the owner said**:
  `routes/transfers/mutations.py:378` is `soft = bool(xfer.transfer_template_id)`, so a
  template-linked instance can only ever be soft-deleted, and no second door reaches a
  SETTLED one -- the template hard-delete takes its archive fallback whenever any sibling
  is Paid and its bulk delete is narrowed to unsettled rows. The money does leave (the
  fold excludes `is_deleted`; `transfer_service._delete:68` reverses the posted effect
  before the rows go), so this is not a balance defect; the exposure is that a
  soft-deleted row still CLAIMS its occurrence and
  `transfer_recurrence.resolve_conflicts(action="update")` restores exactly that shape.
  **The remedy is NOT a wider delete**, which would only move the ambiguity: *this
  occurrence did not happen* and *archive this row* are two owner statements one flag is
  answering for both, and the recurrence engine already reads that flag as an owner HOLD
  (`_recurrence_common.owner_hold_on` -> `BLOCK_DELETED`). What the step owes is the
  second statement, and the arm that keeps a restore from resurrecting the first. Met
  live by `balance:X-f3c-2b-2`'s account-10 repair, which accepts the soft delete under
  **R-HJ** rather than waiting for this.
* [ ] **X-bf** `test(harness): a template says which revisions it was built from` -- closes
  **N-385**, whose row carries the measurement. **`alembic_version` records which revision is
  HEAD, never which revisions RAN, and the `down_revision` graph is mutable** -- so a template
  built before a re-parent, a rebase or any chain edit carries the identical stamp and a
  different schema, and neither `scripts/build_test_template.py` nor `tests/conftest.py`
  compares the two. Measured on this arc's own branch: `d3b6f1c8a274` was re-parented onto
  `recurrence:R17`'s `c8e5a2f31b47` after the template was built, so the built chain FORKED past
  R17 and the template kept the unique index R17 replaces -- **16 failures across five modules
  in three arcs**, every one a `UniqueViolation` on the deleted index, reading exactly like a
  cross-arc regression while the stamp said head. **The remedy is a STATE comparison and the
  stamp is what failed, so asserting it harder cannot work**: record at build time the ORDERED
  revision list the builder actually applied and refuse a bootstrap whose repo chain no longer
  produces it, which a fork changes and a rebase changes. Two remedies are refused: stamp
  equality, measured GREEN on the broken template, and a `pg_indexes` spot check, which names
  one object where the next instance will be a different one -- the same shape as an allowlist.
* [x] **X-bd** `39935763` -- every route in the sweep is its OWN pytest item. Closed **N-364**, whose diagnosis it measured FALSE; opened **N-387**. Record in `archive/four_shipped_steps_2026-08-30.md`.
* [x] **X-be-2** `167aab8d` -- a test SAYS what world it starts in. Closed **N-387**, whose read-only premise it measured FALSE; opened **N-388**. Record in `archive/four_shipped_steps_2026-08-30.md`.
* [x] **X-be-3** `0aa2cc80` -- the sweep grades EVERY GET route and carries no list of the ones it
  does not. Closed **N-388**: `_UNREACHED_RULES` and the skip branch that fed it are DELETED, the
  world holds a row of every kind a GET rule takes an id for, and coverage is an equality against
  `url_map` no list can satisfy. **All 17 unrequested rules answer and NONE 5xx.** The fill map is
  keyed `(blueprint, converter)` with NO bare-name fallback. `X-bd`'s and `X-be-2`'s two standing
  constraints moved into `_SWEEP_CASES`, the one home an archive cannot govern away.
* [ ] **X-be** `refactor(services): three modules are at the line ceiling, not near it` -- closes
  **N-365**, whose row carries the census and both instances. **Root: in a corpus whose docstrings
  ARE the design record, a per-module LINE cap binds what gets WRITTEN DOWN.** The fork, per module:
  SPLIT, or a ceiling with a stated reason for its number -- raising one to fit is what
  `conventions.md` rule 4 refuses about plan caps, and that argument does not change for code. A
  split is a design decision, so the deliverable is three proposals rather than an edit.
* [ ] **X-al** `fix(pylint): a duplicate-code disable that suppresses nothing is a finding` --
  closes **N-154**. `useless-suppression` is enabled precisely so a stale disable is reported, and
  it is BLIND to a `duplicate-code` one -- measured both directions: removing a disable left
  `pylint app/` at 10.00/10, and planting one back left it at 10.00/10 with no `I0021`. **The
  blindness is upstream, not a misconfiguration** (R0801 is a close-time checker over a similarity
  graph, so suppression accounting has no line to credit). The likely shape is a pre-commit arm that
  strips each disable in turn and fails if the tree stays clean without it; the instrument is not
  ruled here and must be shown FIRING on a planted stale disable. **FIFTEEN live `duplicate-code`
  disables remain in `app/` and not one has been re-measured** (4 in `models/`, 5 in `routes/`, 6 in
  `services/`). The step's first deliverable is the census.
* [x] **X-am** `7b0ddae8` -- the `Settled` ARCHIVE is DELETED (**R-HA**, which carries what `CC3b` owes). Closed **N-177**; as-built in `archive/x_am_as_built_2026-08-27.md`, entry in `archive/four_shipped_steps_2026-08-30.md`.
* [ ] **E2-0** `the membership trace` -- NO code. Answer from the code: which modules are members,
  what the public re-export surface is, whether `account_projection` is in or out, and whether
  `ledger_report_service` is (**N-35**). Re-run the fence scan and check the arrow risk: whether any
  member imports a NON-member that would then have to move too. **A FIFTH question, N-33's 13
  private-NAME crossings**: routes import 13 private names from `app.utils.account_validation`, and
  the names LIE about their visibility -- routes are their consumers, so they are cross-package API.
  The honest fix is a rename to public, after which extending W9910 to private NAMES is a
  zero-exception tightening. Expect this step to DECOMPOSE from what it finds.
* [ ] **E2-n** the move itself, and the registry deletion. Decide the decomposition from E2-0, not
  here. **The deletion of `_FENCED_MODULE_RULINGS` is the LAST commit, never the first**: prove the
  boundary holds before removing the gate that compensates for its absence (the C3b3
  prove-the-successor-first precedent, applied eight times in this arc).
* [ ] **G1** `refactor(gates): the ledger-model and balance-seam fences stop carrying name lists` --
  closes **N-147**. **Its first action is a trace and no code is written before it**, because the two
  remaining allowlists are not the same problem wearing two hats. `_LEDGER_MODEL_ALLOWLIST` names the
  modules permitted to import the three ledger models; the balance seam checker carries roughly a
  dozen module sets plus a per-module EXPORT map, which encodes what each producer may publish rather
  than merely who may import it. **What the trace must establish, per allowlist**: whether the entry
  exists because a boundary is genuinely absent (the fix is the boundary, as W9910 was), because a
  legitimate member is spelled as an outsider (the fix is the spelling), or because the rule is a
  value-level invariant a TYPE could carry (`ReconciledThrough`'s shape). **Ruling all three the same
  way is the error this step exists to avoid.** Each checker's deletion is the LAST commit of its own
  arm.
* [ ] **G2** `refactor(money): the value types that retire three checkers` -- retires **W9901**,
  **W9904** and **W9902**, the three with no structural replacement scheduled anywhere. **Its first
  action is a trace**, because the two halves are different sizes and only one is obviously worth it.
  **The money half:** `Money`, a value type over `Decimal` that cannot be constructed from a `float`
  and whose rounding is a method carrying the app's rule, retiring W9901 and W9904 together.
  **Re-measured 2026-08-25 (was "44 and 37"): 47 `Numeric(12, 2)` columns, 36 `.quantize(` sites,
  17 bare.** Its trace must decide whether it lands at the ORM boundary (a `TypeDecorator`, so the
  blast radius is the type rather than the call sites) or as a hand conversion -- the
  `TypeDecorator` route is the one that makes the checkers redundant BY CONSTRUCTION.
  **The SCHEMA layer is the third surface and the only live money today** -- the corrected census
  **N-212** cites, by AST parse of `app/schemas/` on 2026-08-25: **103 of 103 `fields.Decimal` carry
  `places=` and NOT ONE passes `rounding=`**, so every one quantizes against `ROUND_HALF_EVEN` and
  disagrees with `round_money` at every half-cent boundary (marshmallow 4.3.0: `0.005 -> 0.00`,
  `4.345 -> 4.34`). It was **104 of 104** at `afbf3b3e`, the tree N-212 was written against, so its
  five were a line-based grep rather than a stale count. **The same type answers N-256**: 47 carry
  no upper bound (salary 14, loans 11, savings 6, transfers 4, transactions 3, 9 elsewhere) and
  `accounts.py:62` / `:82`, both halves of a balance assertion, carry no validator at all, so
  `10 ** 10` passes the schema and 500s at flush. One `Money` field type stating the mode and the
  `numeric(12,2)` domain ONCE closes both, where 150 hand-added arguments would be the same rule
  maintained by remembering.
  **The label half is small and should not wait for it:** `DisplayLabel`, a type returned by a ref
  table's `.name` whose `__eq__` against a `str` RAISES, retiring W9902 and its two module sets. The
  Jinja half needs its own answer.

* [ ] **X-ba** `refactor(ref): one lookup, not twenty-six copies of it` -- collapse
  `app/ref_cache/_accessors.py`'s twenty-six near-identical functions into one
  `enum_id(member)` reading `type(member)`, and migrate the call sites. Finding **N-341**.
  Each is `require_init(); return cache().enum_ids[Enum][member]` under a docstring running 1 to 35
  lines, and the repetition is what took the module past pylint's 1,000-line ceiling with four
  lines of headroom -- `bank_import:X-f6e-1` SPLIT it into a package to buy room without touching a
  public name, which is a floor rather than the fix.
  **The census, measured 2026-08-23 and not to be re-taken**: 26 canonical accessors plus five that
  are not (`transaction_type_is_income`, `acct_category_member`, `acct_type_icon`,
  `acct_type_max_term`, `ledger_class_is_debit_normal`); **`acct_type_icon` and
  `acct_type_max_term` have 0 references in `app/`, `tests/`, `scripts/` or `tools/`** and are
  candidates for DELETION rather than folding -- with them the `_cache.acct_type_meta` map
  `init()` populates to feed them. 98 modules `from app import ref_cache`; none reaches a private
  name through it, and none uses the `from app.ref_cache import <name>` form.
  **What the migration owes**: the per-accessor rationale several docstrings carry is not noise --
  `raise_type_id` names the producer it serves and the ruling behind it -- so the census decides
  per function whether its prose moves to the ENUM, where the meaning belongs. It rewrites a
  surface 98 modules import, so it takes its own PR.

*X-az left 2026-08-26 (`archive/five_shipped_steps_2026-08-26.md`); its obligation is RESTATED because
a later leaf must obey it -- the settle-day pairing is a BICONDITIONAL, a day and its basis share ONE
lifetime, and a RESUBMITTED day is an ECHO that may not restate its basis (`$4,173.07`, 59 of 66).*

## 6. The findings ledger

**Moved to `../../plans/ledger.md`**, the one findings table for every arc. This arc's rows are the
ones whose `arc` column reads `balance`; a row's owner names a step in `../../plans/steps.md`, whose
specification is section 5 of this document, and the rules it is graded against are
`../../plans/conventions.md`.

## 7. Verification standard

**Moved to `../../plans/verification.md`**, one copy for every arc. This arc WROTE it and is why
several of its clauses exist -- three of the five defects behind the suite's two clock gates were
fixture-clock bugs created here, and this arc writes more anchors, assertion instants and due dates
than anything else in the codebase.

## 8. Process lessons

**Moved to `../../plans/lessons.md`**, one copy for every arc.

## 9. Rules for this document

**Moved to `../../plans/conventions.md`**, one copy for every arc.

`tools/plan_gate/` grades this document against them through a pre-commit hook
scoped to it and the CI step that runs the custom pylint checkers -- so EDITING
THIS FILE is what runs the gate. This document's own caps live in the gate's
constants beside the other arcs'.
