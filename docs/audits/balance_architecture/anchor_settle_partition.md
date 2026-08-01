# The anchor/settle partition: when is a settled row already inside an asserted balance?

Status: **Steps S1-a and S1-b SHIPPED TO PRODUCTION 2026-07-31 (PR #67, merge `fd0ddfab`).
The review's residue is on `fix/n133-review-residue`: F1 RULED and applied, the opening now carries
a stored user-supplied DATE (step 2's opening half), and F2's remainder, F4, F5, F6, F7, F8, F9 and
F12 are all closed. S1-c DEFERRED; steps 3-4 and step 2's transaction half OPEN.**

`pylint app/ scripts/` 10.00/10. Full suite **7,687 passed / 0 failed** (7,677 at the merge base;
10 net new). Production-clone verification is Section 7: **standards 1, 2, 3, 5 and 6 all pass**,
standard 3 after the deploy hooks as designed.

**A SECOND adversarial review (Section 9) ran against the residue before it was committed and found
eight items, four of them High.** All are fixed; the largest was structural and two reviewers found
it independently. Three remain open and are listed there, including **N-134**.

**The developer ruled F1 on 2026-07-31: "Revert + date the opening now."** The EXCEPT clause is
deleted -- an assertion is the closing balance for its civil day, opening and true-up alike -- and
the case the exception protected is answered by the opening's own recorded date instead of by a
placement rule guessing at one. See Section 4.

Written 2026-07-31 after a production defect made the grid's
projected end balance wrong by **-$4,001.42** on the developer's own checking account. Traced,
reproduced against a clone of production at the shipped commit (`69a527cd`, the cash-arc ship), and
the fix prototyped and measured before a line of it was written.

Owned by the balance arc (`README.md`). This is root 4 of that document's "what remains" list --
"nothing in the app records WHEN money moved" -- which the arc had recorded as **noise in the
reconciliation row** and which is in fact a **correctness defect in the projected end balance**. See
ruling **R-DH** and finding **N-130** in `README.md`.

---

## 1. The defect, as it presented

On 2026-07-31 the developer did an ordinary bookkeeping session: read the bank, entered the
checking anchor, then ticked off the bills that had cleared. The grid then reported a projected end
balance of **-$4,021.37** for the current period and a negative balance in every period after it,
against a hand-computed **-$19.95**.

The session, from `budget.account_anchor_history` and `budget.transactions.paid_at`:

| instant (UTC) | event | delta | walked balance |
|---|---|---|---|
| 11:57:10 | Kindle Unlimited settled | -12.80 | 2,864.38 |
| 11:58:11 | Kayla's Spending Money settled | -68.27 | 2,796.11 |
| 11:58:13 | Groceries settled | -265.69 | 2,530.42 |
| 11:58:21 | Gas settled | -93.26 | 2,437.16 |
| 11:58:44 | Data Manager (paycheck) settled | +3,031.97 | 5,469.13 |
| **11:58:53** | **anchor asserted $1,307.66** | **-4,161.47** | **1,307.66** |
| 11:58:59 | CC Payback: Rogue Equipment settled | -1,958.87 | -651.21 |
| 11:59:00 | CC Payback: Mint Mobile settled | -131.60 | -782.81 |
| 11:59:02 | Transfer to Mortgage settled | -1,910.95 | **-2,693.76** |

The three rows recorded in the nine seconds *after* the anchor total **-$4,001.42**. The bank
balance of $1,307.66 already included them; the engine subtracted them a second time.

```
walked balance:                     -2,693.76
remaining projected bills:          -1,327.61
                                    ----------
grid's projected end balance:       -4,021.37     (shipped)
correct:  1307.66 - 1327.61 =          -19.95
```

Reproduced exactly (`-4021.37`) by running `balance_at.grid_balance_view` at the shipped commit
against a clone of production.

**The income, expense and net rows were correct throughout.** They group on the BUDGET clock (the pay
period a row is attributed to), which no part of this defect touches. Only the balance row and the
"Timing & true-ups" remainder were wrong -- and the remainder was wrong *because* it is where the
engine's own double count lands. Of the -$4,588.69 that row displayed, **-$4,161.47 was the plug the
engine booked against itself.**

## 2. Root cause

Three facts, each cited:

1. **`paid_at` is the moment of the click, not the day the money moved.**
   `app/services/status_seam.py:105` stamps `db.func.now()`;
   `app/schemas/validation/transactions.py:62` is `dump_only`, so no API can supply another value.

2. **A cash anchor has no date at all.** `AccountAnchorHistory` (`app/models/account.py:149`)
   carries only `created_at`. There is no "this balance was true as of ___".
   **The loan side already has it**: `anchor_service.apply_loan_anchor_true_up` takes an explicit
   user-supplied `anchor_date`. The cash half is the one that never got it (finding X5).

3. **The fold partitions those two data-entry timestamps at second granularity.**
   `cash_ledger/_events.py:391-398` merges assertions and settles into one stream keyed on the
   instant; `cash_ledger/_walk.py:228-240` applies each assertion as a reset. A settle recorded one
   second after an anchor rides on top of it.

So the question the engine must answer -- *is this settled row already inside the asserted balance?*
-- is answered by comparing two clocks, neither of which measures when money moved. In the
developer's workflow (open the app, true up the anchor, tick off what cleared) the answer is decided
by the order the buttons were pressed.

### 2.1 The same question, answered four different ways

The architectural root is not the granularity. It is that **one question had four
implementations**. Three were found when this document was written; the fourth was found by the
2026-07-31 adversarial review (F4) and is the reason step 3 is not optional polish:

| site | rule as found | rule now |
|---|---|---|
| `cash_ledger/_events.py` (read fold) | `occurred_at` vs `asserted_at`, instant, settle wins ties | `settled_on <= observed_on`, civil day |
| `account_posting_service/_walk.py` (posted ledger) | `sources[i][0] <= fact.asserted_at`, instant | `sources[i][0] <= fact.observed_on`, civil day |
| `account_posting_service/_sync.py` (the self-heal skip) | `utc_day_start_instant(entry_date) <= max(created_at)`, mixed | `min(entry_date) <= max(observed_on)`, civil day |
| `entry_service.py:799` (envelope entries) | `entry_date <= date.today()`, date, **not compared against the anchor at all** | **unchanged -- S1-c, deferred** |

**Three of the four now answer the question the same way, in the same units, against the same two
fields.** The fourth is the survivor and it is the one that never compared against the anchor: it
compares against `today`, which is the SERVER's UTC day, so it contradicts R-DH (b) as well.
Its own docstring states the correct semantic -- *"the owner just looked at their real checking
balance and entered it as the new anchor, so every debit purchase that had already posted is now
reflected in that number"* -- and that rule was right; it was applied to entries and never to
transactions, and the two still run inside one `apply_anchor_true_up` call and disagree.

Step 3 is what makes a FIFTH impossible rather than merely absent. It is now a smaller job than the
review found it: the three converged sites already share `settled_civil_day` and `observed_on`, so
what step 3 adds is the fence, not the convergence.

### 2.2 A second live instance: `is_cleared`

`TransactionEntry.is_cleared` is a **stored flag written as a side effect of the anchor save**
(`entry_service.clear_entries_for_anchor_true_up:795`, a bulk UPDATE). Record the entry then anchor
and it is set; anchor then record the entry and it never is. Live in production on 2026-07-31:

```
 id | txn  | amount | entry_date | is_cleared |     created_at      |   covering anchor
 81 | 2281 | 192.24 | 2026-07-31 |     f      | 2026-07-31 14:41:37 | 2026-07-31 11:58:53
 82 | 2281 | 150.27 | 2026-07-31 |     f      | 2026-07-31 14:42:08 | 2026-07-31 11:58:53
 83 | 2281 |  20.00 | 2026-07-31 |     f      | 2026-07-31 14:42:35 | 2026-07-31 11:58:53
```

Three entries dated today, an anchor exists for today, none reconciled -- because the anchor was
saved at 11:58 and the shopping happened at 14:41. Harmless at that instant (the anchor does not
reflect them either, so the two errors cancel) and self-resolving at the next anchor, but by chance
rather than by design.

The tell that this was known: `entry_service.toggle_cleared:830` is a **manual per-entry override**
whose docstring says it exists "for cases where the auto-clear on anchor true-up is wrong for a
specific purchase". A manual override for a rule that is wrong is a band-aid on the rule.

## 3. Measurements (production clone, 2026-07-31)

Checking account: 139 settled rows, 54 assertions, since 2026-03-26.

**Exposure.** 65 of 139 settled rows (**47%**) were recorded within one hour of an anchor assertion,
carrying **$19,602.13** of gross cash movement -- every one classified by click order. 32 of the 48
anchor-days have rows recorded after that day's last anchor, **$22,357.52** gross.

**Candidate rules, scored by the correction each is forced to plug in at every assertion.** That plug
is the model's own error; a rule that matches reality books small ones.

| rule | gross plug | net plug | worst single | walk ends at |
|---|---|---|---|---|
| R0 instant partition (**the defect**) | $40,554.34 | -$6,998.90 | $4,161.47 | **-$2,693.76** |
| **R1 civil day, assertion closes its day** (un-amended) | **$14,286.82** | **-$940.06** | $2,612.92 | **$1,307.66** |
| R2 civil day, settles win same-day ties | $65,671.21 | -$4,406.95 | $3,624.63 | -$101.81 |
| R3 anchor-only, settled rows ignored (pre-arc) | $41,008.30 | -$1,438.92 | $2,612.14 | $1,307.66 |

R1 wins on every axis and is the only rule under which the walk lands on the balance the bank
actually shows. Its median per-day plug is **$184.55**; today's is **-$160.05** against the shipped
rule's **-$4,161.47**.

> **CORRECTION (adversarial review, 2026-07-31 -- F1, now RULED and applied).** **The R1 row above
> was the rule this document scored, and it was NOT the rule `9c2c3130` shipped.** The opening
> amendment made during the build changed the rule after these figures were taken and they were
> never re-run. Re-measured on the same account, same 139 rows, same 54 assertions -- the plug is
> summed over the 53 TRUE-UPS, excluding the opening's own correction, which is genesis rather than
> a correction of model error:
>
> | rule | gross plug | net plug | worst single | walk ends at |
> |---|---|---|---|---|
> | R1 opening ABSORBS its own day -- **what this table scores, and what now SHIPS** | $15,367.94 | **-$940.06** | $1,853.92 | $1,307.66 |
> | R1 opening OPENS its day (the amendment, reverted) | $17,282.84 | **-$2,997.48** | $1,986.16 | $1,307.66 |
>
> Reproduced independently on 2026-07-31 by running the shipped walk against a pristine clone under
> both variants: the R0, R2 and R3 rows come back to the cent. The un-amended gross is **$15,367.94**,
> not the **$14,286.82** the table above states -- the original figure was never reachable by either
> variant and is the one number in this section that did not reconcile. Section 6's standard 5 is
> restated against the reachable target.
>
> **A structural fact the first measurement missed:** counting the opening's own correction, the net
> plug is IDENTICAL (-$250.90) under both variants. The amendment neither created nor destroyed
> error; it moved **$2,057.42** out of the opening's correction and into the next true-up's. The
> question it decided was not "which rule books less error" but "does the opening state the balance
> the user typed, or a fabricated one" -- and it fabricated $4,804.00 for a day the bank showed
> $2,746.58.

**The historical smoking gun.** On 2026-04-01 the anchor was set to $804.06; a $1,910.95 mortgage
transfer was then marked paid, dropping the walk to -$1,106.89; on 2026-04-02 **the same $804.06** was
entered again and the engine booked a **+$1,910.95** "true-up" to undo its own double count.

**Prototype.** One change to the sort key, run against the production clone at the shipped commit:

| period | shipped | prototype |
|---|---|---|
| 2026-07-30..08-12 (current) | -$4,021.37 | **-$19.95** |
| 2026-08-13..08-26 | -$3,395.19 | $606.23 |
| 2026-08-27..09-09 | -$4,110.62 | -$109.20 |
| 2026-09-10..09-23 | -$3,948.48 | $52.94 |

Every past period end also lands exactly on the balance that was asserted then ($448.77, $1,018.00,
$610.64, $218.58, $1,502.06, $2,037.01, $319.09, $2,877.18). Under the shipped rule the first period
ends at -$397.76, a balance the account never held.

**The remainder becomes readable.** Under the prototype the current period's "Timing & true-ups" is
-$587.27 and decomposes exactly: **-$427.22** of the previous period's bills whose money moved in this
one, plus a **-$160.05** true-up. Under the shipped rule it is -$4,588.69 and decomposes into nothing
a user can act on.

### 3.1 Timezone

Storage does not change: every timestamp stays `timestamptz` in UTC. The question is which calendar
day the engine derives from a stored instant **when it must compare that instant against a plain
`DATE` column**. `pay_periods.start_date`, `pay_periods.end_date` and `transactions.due_date` are
plain `DATE`s that mean the user's civil days; deriving the event's day in UTC and comparing it to
them is comparing two different calendars.

Measured: **22 of 139** settled rows land on a different day; **5 land in a different pay period**
(including a $1,910.95 mortgage payment on both 2026-04-22 and 2026-07-01, and a $178.32 + $8.53 pair
on 2026-06-03); 2 assertions also move period. Two Eastern evenings (**2026-04-22**, **2026-06-03**)
had a single bookkeeping session split across two UTC days -- the exact shape that would defeat this
document's fix.

**The hazard this introduces, and why it dictates the implementation.** Four settled rows carry no
`paid_at` and fall back to `utc_day_start_instant(period.start_date)` -- midnight UTC of a civil
date. Converting *that* to Eastern moves it to the previous day, which is wrong: it was never an
instant. So the fix is **not** "convert instants to Eastern at each read site". It is: **the fact
carries its civil day as a civil day, resolved once at construction, and only genuine instants are
converted.**

That is the same field step 2 fills with a stored, user-supplied column, which is why building it now
is not throwaway work.

## 4. Rulings

Ruled by the developer 2026-07-31 in the session that opened this document. Recorded as **R-DH** in
`README.md`.

**R-DH (a) -- An assertion is the closing balance for its civil day. No exception.**
It absorbs every cash movement dated that day, whatever order the two were recorded in. Multiple
assertions in one day apply in recording order; the last is the day's closing balance.
*Rejected:* the shipped instant partition (measured above: 7x the net plug, and the -$4,021.37 that
opened this document); settles winning same-day ties (worse than either); and the OPENING exception
below.

> **The EXCEPT clause lived for one day and is DELETED** (finding F1 / N-133, developer ruling
> 2026-07-31: *"Revert + date the opening now"*). It said an OPENING sorts BEFORE its own day's
> sources and they ride on top, because an opening states what an account holds as recording BEGINS
> rather than what a day closed at. Three things decided it, all measured:
>
> 1. **The only real data contradicts it.** Checking's opening asserts **$2,746.58** on 2026-03-27
>    and **four settled rows carry that same civil day**, netting **+$2,057.42** (`Data Manager`
>    +2,473.38, `Health Insurance Allowance` +100.00, `Audible` -15.96, `Transfer to Fidelity
>    Savings` -500.00). Every one was clicked **33 seconds to 1.6 hours AFTER** the opening was
>    typed, so the opening was read off a bank that already showed them. Riding them on top makes
>    the walk read **$4,804.00** for a day the bank showed $2,746.58, and makes the next assertion
>    (2026-03-30, $2,653.89) book **-$1,986.16** where absorbing them books **+$71.26**.
> 2. **It cost the rule its one-statement property.** The exception had to be hand-mirrored as a
>    sort position in the read fold and as a date boundary in the posting walk, held in step by
>    convention -- and `dated_deltas`' tie-break never moved with it at all (F5).
> 3. **Where the artifact lands.** Both variants leave exactly ONE wrong region, and they differ in
>    which. Measured on the whole seam, **7 of 15,682 figures move**: with the exception, three days
>    of tracked history (2026-03-27..29) read $4,804.00 and period 0's visible "Timing & true-ups"
>    carries -$1,421.00; without it, the artifact is the pre-tracking back-projection ($689.16 on
>    2026-03-26 and earlier) -- and that figure is R-I doing its job correctly, because $689.16 IS
>    what the account held before that day's four movements. The current period is **-$19.95** and
>    every other period end is identical either way.
>
> **The case the exception protected is now answered by a recorded fact instead of a placement
> rule.** "Open an account at $0 and fund it the same day" was its motivating case, and it has never
> occurred: account 1 is the only account with any settled row on its opening's day. Step 2's
> opening half ships with this revert, so a user who opens an account and funds it later the same
> day dates the opening to the day BEFORE -- which is what actually happened -- rather than relying
> on the engine to guess. That is the ruling's own sequencing: the day partition is the best
> available GUESS while nothing records when money moved, and every place the guess can be replaced
> by a date, it is.

Records on EARLIER days precede every assertion and are what ruling R-I back-projects into the
fold's seed; nothing here touches that arm. The rule is implemented on BOTH walks -- the read fold's
ordering and the posting walk's absorption boundary -- because moving one alone makes the two
disagree (measured during the build: 66 failures against 58 when only the read fold moved).

> **What the revert actually moved, measured on the whole seam.** The two variants were run
> against a pristine `pg_dump` of production through `tests/manual/verify_balance_baseline.py` and
> diffed leaf by leaf: **7 of 15,682 figures move, all on Checking, and nothing else moves at all.**
>
> | figure | exception kept (was shipped) | exception deleted (**ships now**) |
> |---|---|---|
> | current period balance | -$19.95 | **-$19.95** |
> | every other period end | identical | identical |
> | period 0 "Timing & true-ups" | -$1,421.00 | **+$636.42** |
> | daily balance 2026-03-27..29 | $4,804.00 | **$2,746.58** |
> | pre-tracking back-projection (R-I) | $2,746.58 | **$689.16** |
> | R-K's identity | 0 breaks / 60 pairs | 0 breaks / 60 pairs |
>
> Neither variant is artifact-free, and saying so is the honest form of the ruling: each leaves
> exactly one region that is not a balance the account held. The deleted exception put its artifact
> INSIDE tracked history for three days and into a visible grid row; the rule that ships puts it in
> the pre-tracking past, where finding **N-37** already records that the fold's answer before an
> account's first assertion is unruled -- and where $689.16 is in fact correct, being what the
> account held before that day's four movements.
>
> **Cost of the revert, re-measured on the day it was applied** (the review's "35 test re-rulings /
> 41 failures" predated both PR #66 and `dfc36af8`): **30 failures on a 0-failure baseline, and not
> one of them a financial re-ruling.** `tests/test_services/conftest.py` freezes today, and the
> ordinary settle idiom is `paid_at = db.func.now()`, so `seed_user`'s origination landed on the
> very civil day its settles did -- every fixture meaning "an account existed, then money moved"
> silently said "money moved on the opening's own day", and passed only because of the exception.
> That is finding **N-132**'s shape one layer up. The fix is one line per account-creating fixture:
> state the opening's day. Four tests then genuinely re-rule, and they are the controls for the rule
> itself.

*The residual, stated, in BOTH directions:* a movement that genuinely lands **after** the balance was
observed on the same day is absorbed anyway. An OUTFLOW makes the projection read high; an **INFLOW
makes it read LOW by the full amount** -- the shape the deleted opening exception was reaching for,
and the one the $184.55 median does NOT bound, because that median was measured over outflows.
Bounded by the next assertion either way (the developer re-anchors every 2.3 days), self-correcting,
and against the $4,161.47 the instant rule produced on a single day. **It is a guess, and step 2 is what removes it** -- with real
dates on both sides the comparison is between two real-world dates and the same-day case is settled
by the closing-balance semantic rather than by an unknowable ordering.

**R-DH (b) -- The civil day is the user's, not UTC.** `America/New_York` via the existing
`DISPLAY_TIMEZONE`. Storage is unchanged. Shipped in the same commit as (a): both change which day an
event counts on, both require the same posted-ledger resync, and shipping them apart means verifying
the same four months of balances twice.

**R-DH (c) -- The envelope process is unchanged, and made order-independent.** Record an entry per
purchase against the envelope; true up the anchor; either order. The invariant this must satisfy,
worked on the developer's own figures (anchor $1,307.66, Groceries $500 with nothing recorded, other
projected bills $827.61):

```
before:  1307.66 - 500.00 - 827.61  =  -19.95
record a $150.27 purchase and anchor to $1,157.39:
after:   1157.39 - 349.73 - 827.61  =  -19.95
```

**Recording a purchase and truing up the anchor by the same amount must not move the projected end
balance.** It also must not move if only the entry is recorded and no anchor follows. Both become
tests.

> **NOT YET TRUE, and NOT YET TESTED (F2, F11).** Neither invariant exists in `tests/` -- `grep`
> finds these sentences only in this document. And "either order" is still false in the code:
> `entry_service.clear_entries_for_anchor_true_up:787` compares `entry_date <= date.today()`, which
> is (i) the SERVER's UTC day, not the user's civil day, so it contradicts R-DH (b) which *did*
> ship, and (ii) not compared against the anchor at all. Record-then-anchor clears the entry;
> anchor-then-record does not. That is the order-dependence this ruling promises to remove, live in
> production today. It is S1-c's work and S1-c is deferred -- recorded here so the deferral is a
> known gap rather than a silent one.

The two figures the envelope carries are deliberately different and both are already correct:
`build_entry_sums_dict`'s `remaining` (budget minus every entry, ignoring reconciliation) answers
"how much budget is left"; `_entry_aware_amount`'s reservation (budget minus *reconciled* debits)
answers "how much to still hold back from the projection".

**R-DH (d) -- `is_cleared` becomes DERIVED, and the manual toggle is deleted.** An entry is
reconciled iff its date is on or before the latest assertion's observed day -- the same rule as (a),
evaluated at read time. This deletes a stored boolean, a bulk UPDATE, its autoflush coupling inside
`apply_anchor_true_up`, `entry_service.toggle_cleared`, its route, and its UI control. When
reconciliation is wrong the user corrects the entry's **date**, which is the fact that was actually
wrong. *Rejected:* keeping the flag with a better auto-rule (leaves a denormalized copy of a derivable
fact, which is the `Account.current_anchor_*` disease this arc is already removing at step X-e).

> **Measure the entry-side residual BEFORE building this (F11).** Applied to the three entries in
> Section 2.2 -- dated 2026-07-31, against an anchor whose `observed_on` is 2026-07-31 -- this rule
> **reconciles all three**, drops the envelope reservation by **$362.51**, and moves the projected
> end balance from **-$19.95 to +$342.56**, against a bank balance read at 11:58 that does not
> contain the 14:41 shopping. That is R-DH (a)'s accepted residual applied to entries, which is
> consistent -- but it was never measured there. **$362.51 in a single day is roughly twice the
> $184.55 daily median measured for transactions**, because an envelope entry is by definition
> "what I spent today, after I read my balance this morning" -- the residual's worst shape, not its
> average one. Section 2.2 currently calls today's uncleared state "harmless by chance"; under this
> ruling it becomes harmful by design.
>
> R-DH (e) is what closes it, and only if the entry form's default stops being today. **Sequence the
> two together, or ship (d) after step 2.** Do not ship (d) alone.

**R-DH (e) -- A date means the day the money hit the account**, not the day the purchase happened.
They differ by a day or two for a debit card. Defaults to today, user-correctable; the error when the
user does not bother is one day's spend and self-corrects at the next assertion.

**R-DH (f) -- "Timing & true-ups" splits into two named figures**, and the anchor form shows the
difference before it is saved ("your recorded balance is $1,157.39, you entered $1,157.39, difference
$0.00"). *Period timing* should read $0.00 whenever every bill's money moves inside the period it was
budgeted to, so a persistently non-zero value is a diagnostic: either a bill is budgeted to the wrong
period, or dates are being recorded late. *Book vs bank* is the untracked spend and should be small.
Today's are -$427.22 and -$160.05 respectively, summed into one unreadable -$4,588.69.

## 5. The build

### Step 1 -- the civil-day seam (fixes production)

- **S1-a DONE** (`9c2c3130`) `fix(cash-ledger): a settled fact carries the civil day it moved on`
  `CashSourceFact.settled_on` and `CashAnchorFact.observed_on` become real fields, resolved once at
  construction (display-tz civil day of a genuine instant; the NULL-`paid_at` civil-date fallback
  passes through unconverted). `merge_anchor_and_cash_events` partitions on `(civil day, sources
  before assertions)`. `dated_deltas` keys off the same fields rather than re-deriving.
- **S1-b DONE** (`9c2c3130`) `fix(posting): the posted ledger partitions on the same day as the fold`
  `account_posting_service/_walk.py:434` and the journal-entry dating move to the same rule, so the
  write side and the read side stay one statement. Shipped as a deploy hook
  (`posting_service.resync_all_cash_postings` + `init_database.resync_all_cash_postings_after_migration`)
  rather than a migration, so the go-forward sync is the only statement of the rule. **The hook is
  load-bearing, not hygiene** -- see Section 7, standard 3.
- **S1-c DEFERRED** `refactor(entries): reconciliation is derived from the entry's date`
  R-DH (d). Deletes the bulk clear, the toggle service/route/UI, and (with the developer's approval
  and a `Review:` line) the `is_cleared` column. **Read F11 and the R-DH (d) note before starting.**

Tests: the 6 that pin the instant partition are re-ruled against R-DH, and the test that was missing
is added -- **the projected balance is invariant to the order of assertion and settle within a
session**, plus R-DH (c)'s two envelope invariants.

> **STATUS: the re-rulings happened at the step; the additions did NOT, and are now DONE** (F2).
> `9c2c3130` added **zero** net tests -- `def test_` counts identical in all 8 changed files, 7,675
> collected at both `b73e25bc` and that commit. The residue branch closes it:
> `tests/test_services/test_anchor_settle_partition.py` is new and holds all five owed properties at
> the grain the ruling states them --
>
> - R-DH (c)'s two envelope invariants, at PROJECTED-BALANCE grain;
> - order-independence at PROJECTED-BALANCE grain (the existing control is walk-grain only);
> - `resync_all_cash_postings`: idempotent, and it re-dates a stale entry and COUNTS it.
>
> **Every one is negative-controlled**, which is the lesson F2 exists to record: three separate
> mutants (assertions applying before their day's sources; every debit treated as cleared; the
> resync walking without re-posting) were introduced one at a time, and each was caught by exactly
> the test that should catch it.  The three tests written for the deleted opening exception were
> re-ruled and re-controlled the same way: reintroducing the exception on both walks fails five
> tests in `test_account_posting_service.py`.

### Step 2 -- the app records when money moved

`transactions.settled_on` and `account_anchor_history.observed_on` as stored, user-editable `DATE`
columns, backfilled from step 1's derivation so **no figure moves on the day it ships**. The entry
date is un-hidden on its creation form. The walk reads the columns instead of deriving them; nothing
else in the engine changes. This is the arc's existing plan step **X-f** plus finding **X5**,
promoted from "after X-d" to "now" by R-DH.

- **S2-a DONE** (migration `c4a19e7b2d80`) `feat(anchor): an assertion carries the civil day it was
  true`. The ANCHOR half, pulled forward by the F1 ruling because it is what answers the case the
  deleted opening exception was reaching for. `account_anchor_history.observed_on` is a stored
  `DATE`, backfilled from `(created_at AT TIME ZONE 'America/New_York')::date` -- the derivation it
  replaces, verbatim, so **0 of 15,682 seam figures moved**. `AccountSpec` / `create_account` take
  it and the account-create form offers a "Balance as of" field defaulting to today; a future date
  is refused. The anchor PERIOD is now resolved FROM that day rather than from `today`, so the
  row's two statements of "when" cannot disagree. `cash_anchor_facts` reads the column.
  **F6 shipped with it and had to**: the posting walk's monotonic source pointer assumed
  `observed_on` was non-decreasing in `created_at` order, which a user-supplied column breaks.
  **F12 shipped with it too**: the double-submit index re-keys onto `observed_on`.
- **S2-b OPEN** -- the TRANSACTION half (`transactions.settled_on`) and the true-up form's own date
  field. Until it lands, a settle's day is still derived from `paid_at` (the click) and a true-up's
  `observed_on` still defaults to today, which is exactly what the derivation gave: figure-neutral,
  and the residual below is still a guess on the settle side.

### Step 3 -- one predicate, fenced

A single `is_inside_assertion` shared by the read fold, the posting walk and the entry reconcile,
backed by a custom pylint checker on the `shekel-refname-compare` pattern, so a fourth answer to
section 2.1's question cannot be written.

> **This step is now smaller than the review found it, and what remains is the FENCE.** F4's fourth
> answer is converged (the self-heal skip compares days), F5's `dated_deltas` tie-break is correct
> by construction, and the two hand-mirrored statements of the OPENING placement are gone with the
> exception itself. What is left for step 3:
>
> - the ENTRY reconcile (`entry_service.py:799`), the one site that still compares against `today`
>   rather than against an anchor -- that is S1-c's work, and step 3 is what stops it being
>   writable a fifth way;
> - the checker that makes a fifth answer un-writable, which is the part no convergence buys.
> - `ledger_report_service/_attribution.py` restates the day derivation twice (`:445`, `:548`)
>   behind a justification that R-DH (b) has now falsified (F7).

### Step 4 -- the remainder is named

R-DH (f): the row splits, and the anchor form previews its own difference.

## 6. Verification standard

Every step is verified against the production clone, not only the suite:

1. The current period reads **-$19.95**, and every past period end equals the balance asserted then.
2. `balance[p] - balance[p-1] == net[p] + reconciliation[p] + contribution[p] + accrual[p]` holds on
   every (account, period) pair, as it does today.
3. The read fold and the posted ledger agree per account per date, as they do today.
4. Order-independence: for the 2026-07-31 session, permuting the anchor against the three settles
   produces one answer.
5. The gross plug over Checking's 53 true-ups drops from $40,554.34 to **$15,367.94** (net
   -$6,998.90 to **-$940.06**). **Restated after F1**: the $14,286.82 the first measurement quoted
   was never reachable by any variant; $15,367.94 is the reachable target and the shipped rule hits
   it exactly.
6. **Added by the review:** nothing outside Checking moves except what a ruling says moves. The
   whole-seam baseline (`tests/manual/verify_balance_baseline.py`) is captured before and after
   against a pristine production clone, and every moved cent is explained.

## 7. Verification results (production clone)

Two runs, both against read-only restores of a production `pg_dump` on `shekel-dev-db` (never
written back). **7.0 is the residue branch's run and supersedes 7.1's numbers where they differ**;
the earlier run is kept because it is what found F1.

### 7.0 Residue branch (`fix/n133-review-residue`, 2026-07-31)

Code with the F1 revert, the stored `observed_on`, F4, F5, F6, F7, F8, F9 and F12 applied.

| # | standard | result |
|---|---|---|
| 1 | current period -$19.95; past ends equal a balance asserted then | **PASS** -- -$19.95, and all 9 past ends land on an asserted balance |
| 2 | `balance[p] - balance[p-1] == net + reconciliation (+ modelled)` | **PASS** -- 0 breaks over 420 period pairs, all 8 non-loan accounts |
| 3 | read fold == posted ledger, per account per date | **PASS after the deploy hooks** -- 0 breaks over 84 dates; **37 of 56 breaks on Checking BEFORE them**, which is F8's measurement reproduced |
| 4 | order-independence for a bookkeeping session | **PASS**, and now at PROJECTED-BALANCE grain in `tests/test_services/test_anchor_settle_partition.py` |
| 5 | the plug drops | **PASS** -- gross $40,554.34 -> **$15,367.94**, net -$6,998.90 -> **-$940.06**, worst $4,161.47 -> **$1,853.92** over 53 true-ups |
| 6 | nothing moves except what a ruling says moves | **PASS** -- against the un-amended reference captured before any code changed, **0 of 15,682 leaves moved**: the revert lands exactly on the measured variant, and the stored column plus F4/F6/F7/F8/F9 move nothing at all |

Also verified in this run:

- **The deploy resync is idempotent and its count is now meaningful.** First pass on a pristine
  clone: `RE-POSTED 16 transaction(s) and 1 transfer(s)`. Second pass: `already at target (0
  changed)`. Under the old "counts walked" line both passes would have printed 999 (F8).
- **The trial balance closes**: `SUM(account_postings.amount) = 0.00` after every hook, and a second
  full pass of all three hooks writes nothing (639 postings / 316 entries before and after).
- **The migration runs both directions** on the dev database and on two production clones, with the
  backfill exact: 0 rows where `observed_on <> (created_at AT TIME ZONE 'America/New_York')::date`
  over 67 rows.
- **`pylint app/ scripts/` 10.00/10; full suite 7,682 passed / 0 failed** (7,677 at the merge base).

### 7.1 Adversarial review run (2026-07-31)

Run against a **fresh** `pg_dump` of production (read-only; restored into `shekel_audit` /
`shekel_audit_pre`, never written back). Code at `9c2c3130`.

| # | standard | result |
|---|---|---|
| 1 | current period -$19.95; past ends equal the balance asserted then | **PASS** -- -$19.95, and all NINE past ends land exactly on an asserted balance: $448.77 / $1,018.00 / $610.64 / $1,100.99 / $31.73 / $1,502.06 / $126.06 / $319.09 / $2,877.18 |
| 2 | `balance[p] - balance[p-1] == net + reconciliation (+ modelled)` | **PASS** -- 0 breaks over 60 period pairs |
| 3 | read fold == posted ledger, per account per date | **PASS after the deploy hooks** -- 0 breaks over 7 non-loan accounts; **36 of 56 breaks on Checking BEFORE them** (final $1,307.66 vs -$2,693.76). See F8 |
| 4 | order-independence for the 2026-07-31 session | **PASS at walk grain**; no test at projected-balance grain (F2) |
| 5 | gross plug drops | **PARTIAL** -- $40,554.34 -> $17,282.84 as shipped, $15,367.94 un-amended (F1) |
| 6 | nothing else moves | **PASS** -- 924 of 15,682 captured figures moved, all on Checking, plus **one** loan figure: Mortgage period 7's map, $177,554.69 -> $177,277.97 (-$276.72), which is the single documented payment whose visible day moves from 2026-07-02 UTC to 2026-07-01 Eastern |

Also verified, not previously stated as standards:

- **The deploy resync is idempotent.** A second full pass of all three hooks writes nothing
  (315 journal entries before, 315 after).
- **The trial balance closes throughout.** `SUM(account_postings.amount) = 0.00` before, between and
  after every hook.
- **The resync changes no rendered figure.** Baseline captured against the resynced clone is
  byte-identical to the baseline captured against the un-resynced one (0 of 15,682 leaves moved), so
  the hook is ledger hygiene for the ledger-authoritative surfaces, not a projection change.
- **`pylint app/` 10.00/10** reproduced.
- **The suite is 6 failed / 7,669 passed**, identical at `b73e25bc` (F3).

### 7.1 The two test-instrument findings the build produced (N-131, N-132)

Both are recorded in `README.md`'s ledger; the detail is here.

**N-131 -- the cross-page locks are a month-end TIME BOMB.**
`tests/test_integration/test_cross_page_balance_equality.py` fails 6 tests on the last days of a
month, at the unmodified shipping commit, independent of this work. Confirmed by the review: the
same 6 fail with the same 7,669 passing at the merge base `b73e25bc`, so it is neither caused nor
cured by S1-a / S1-b. It fires roughly 12 days a year and would block any hotfix PR's merge gate.
**CLOSED by branch `fix/cross-page-month-end-clock` (`92879e86`, off `origin/main`)**, which carries
no application change and therefore warrants its own PR ahead of this one. Until that merges, any
claim of "0 failed" on this branch is false (F3).

**N-132 -- fixtures separated their events by HOURS against a partition that now reads civil days.**
Two shapes, both fixed in `9c2c3130`:

- fixtures that placed a settle an *hour* before or after an assertion to mean "before" / "after"
  collapse onto one civil day and stop discriminating the case they name. Converted to day offsets,
  with the reason recorded at each site.
- four fixtures built **midnight-UTC** instants to MEAN a civil day
  (`_test_helpers.override_anchor`, `conftest._pin_opening_to`). Midnight UTC is the previous
  EVENING in Eastern, so under R-DH (b) they filed a true-up in the PREVIOUS pay period and emptied
  the anchor column's remainder of the very assertion the fixture had just made. Both now build the
  instant in `DISPLAY_TIMEZONE` and convert to UTC for storage.

**The review found the conversion incomplete** in exactly the place it mattered most: the two tests
written for the OPENING amendment still use a *day* offset where their docstrings claim an *hour*,
which makes them unable to fail. That is F2, and it is the mirror image of N-132 -- the same
fixture-grain mistake, pointed the other way.

## 8. Adversarial review findings (2026-07-31)

Twelve items. **Ten are CLOSED on `fix/n133-review-residue`** (F1 by ruling, F2 F4 F5 F6 F7 F8 F9
F10 F12 by the work); **F3 is obsolete** (its branch merged); **F11 remains OPEN** and gates S1-c.
Each finding below carries its own closure note.
Nothing here says the fix is wrong -- the current-period figure is -$19.95 under every variant
considered, and standards 1, 2, 3 and 6 all pass. Method: a fresh read-only `pg_dump` of production
restored into two throwaway databases on `shekel-dev-db`, the seam walked directly, the whole-seam
baseline captured at `b73e25bc` and at `9c2c3130` and diffed, and the suite plus `pylint` re-run at
both commits. Production itself was never written to.

### Blocking a ruling

- **F1 (High) -- CLOSED by developer ruling 2026-07-31: "Revert + date the opening now".** The
  EXCEPT clause is deleted from R-DH (a) and from both walks, and step 2's opening half ships with
  it so the case the exception reached for is answered by a recorded date. Verified: 0 of 15,682
  seam leaves differ from the un-amended variant measured before any code changed. Detail at
  R-DH (a). The original finding, for the record:
  Full detail inline at R-DH (a) and in the Section 3 correction. Net plug -$2,997.48 shipped vs
  -$940.06 un-amended; the -$940.06 that this document and `README.md`'s R-DH entry both advertise
  is the un-amended rule. Concretely: Checking's opening day carries +$2,057.42 of settles clicked
  33 seconds to 1.6 hours after the opening was typed, and stacking them on top makes the engine
  read $4,804.00 for a day the bank showed $2,746.58. Bounded to March history and period 0's
  remainder. **Decide: revert (measured-best, 35 test re-rulings) or keep and correct both
  documents.**

### Test integrity

- **F2 (High) -- CLOSED.** The blind tests were repaired first (note below), and the three owed
  tests now exist in `tests/test_services/test_anchor_settle_partition.py`, each negative-controlled
  by a distinct mutant. The original finding:
  `test_account_posting_service.py:228` `test_a_settle_on_the_openings_own_day_rides_on_top` says
  "an hour after ... the same civil day" but passes `origin + timedelta(days=1)`, a DIFFERENT day,
  which rides on top under both rules. Proven by reverting the amendment in both walks: that test
  and its sibling `test_a_settle_on_an_earlier_day_is_inside_the_opening` both still passed.
  The amendment's only real pin is `test_same_instant_settle_is_absorbed` (`:361`) -- a test named
  and documented entirely for the DELETED instant rule, so fixing its stale name could silently
  delete the coverage. Also missing: R-DH (c)'s two envelope invariants; any test for
  `resync_all_cash_postings`. See the Section 5 status note.

  > **THE BLIND TESTS ARE FIXED (2026-07-31).** The root cause of the day offset was not
  > carelessness: `_origin_instant` returns the row's `created_at`, which is the ambient WALL CLOCK,
  > so `origin +/- an hour` really does cross midnight on a suite run near 23:30 or 00:30 Eastern --
  > a smaller offset would have been flaky rather than blind. The fix is therefore a **pinned
  > opening** (`_pin_opening` over the shared `restamp_opening_assertion`, the idiom
  > `test_cash_walk.py` already uses) at 12:00 EDT, so plus-or-minus an hour is PROVABLY the same
  > civil day -- and each test now asserts that precondition rather than assuming it.
  >
  > - `test_a_settle_on_the_openings_own_day_rides_on_top` is parametrized **both directions**
  >   (`recorded_after` / `recorded_before`), which is the order-independence pair the single
  >   direction was missing, at the assertion most exposed to R-DH's residual.
  > - `test_same_instant_settle_is_absorbed` is renamed
  >   `test_a_trueup_absorbs_a_settle_the_opening_rode_on_top_of` and re-documented: one civil day,
  >   both assertion kinds, opposite answers. It is the discriminating control that BOTH halves of
  >   the rule can break.
  > - The instant-partition vocabulary that made the blind test read as correct is gone from the
  >   class docstring, the section header and `_settle_expense`.
  >
  > **Negative-controlled, which is the whole point:** with the amendment reverted on both walks all
  > three now FAIL (`-200.00` and `-75.00` against the expected `0.00`); before the repair the
  > opening test PASSED under the reverted rule. Full suite 7,677 passed / 0 failed, `pylint app/`
  > 10.00/10, `app/` byte-unchanged.
  >
  > **Still owed from this finding:** R-DH (c)'s two envelope invariants, a test for
  > `resync_all_cash_postings`, and the order-independence test at PROJECTED-BALANCE grain.
- **F3 (High) -- OBSOLETE.** `fix/cross-page-month-end-clock` merged at PR #66, and the baseline is
  now 7,677 passed / 0 failed. The original finding: Actual: 6 failed /
  7,669 passed (7,675 collected, not 7,676). The 6 are `test_cross_page_balance_equality`, the known
  month-end bomb; **identical 6 failures and identical 7,669 passed at the merge base `b73e25bc`**,
  so nothing regressed. Fixed on branch `fix/cross-page-month-end-clock`. The honest claim is
  "6 failed, all pre-existing and unrelated", never "0 failed" (CLAUDE.md rule 4).

### DRY and latent correctness

- **F4 (Medium) -- CLOSED.** `self_heal_anchor_corrections` compares `min(entry_date)` against
  `max(observed_on)`: civil days on both sides, the rule both walks already apply. The zone-sign
  dependency is gone rather than documented, and `utc_day_start_instant` -- whose only remaining
  purpose was to manufacture the instant this comparison needed -- was DELETED, having no callers
  left. The original finding:
  `account_posting_service/_sync.py:304-311` compares `min(utc_day_start_instant(entry.entry_date))`
  against `max(created_at)`. It is sound ONLY because `America/New_York` is west of UTC (midnight
  UTC of a display day always precedes that day's start in UTC), so it can only over-fire, which is
  an idempotent no-op walk. **For a display zone east of UTC it silently UNDER-fires** and leaves a
  stale anchor correction posted with no error. Nothing states or gates that. Root fix, one line and
  it pre-pays step 3: compare days -- `min(e.entry_date) <= to_display_date(latest)`, the rule both
  walks already use.
- **F5 (Medium) -- CLOSED by construction.** With the opening exception gone the walk has ONE
  placement for both assertion kinds, so `dated_deltas`' source-before-assertion tag matches it for
  every anchor and the Returns docstring's chronology claim is true again. The comment now records
  why it was ever false. The original finding: `cash_ledger/_walk.py:327-336` tags source 0 / assertion 1 with the
  comment "the same tie-break the walk applies", but `_events.py:111-113` puts `_OPENING_ORDER = 0`
  BEFORE `_SOURCE_ORDER = 1`. For an OPENING the two orders are opposite. Arithmetically inert today
  (its one consumer `_cash_fold._actual_steps` day-sums and `sample_cumulative` reads day
  boundaries), but the Returns docstring's "reading the list shows the same chronology the walk
  applied" is false in exactly the place a reader debugging an opening-day discrepancy would look,
  and any future sequential consumer replays the opening wrongly.
- **F6 (Medium) -- CLOSED, and it had to be: step 2's opening half shipped in the same pass, which
  is precisely what would have armed it.** `walk_account_ledger` now sorts its facts by
  `(observed_on, asserted_at)` -- the read fold's order, stated the same way -- before advancing the
  monotonic source pointer. The original finding: `walk_account_ledger` iterates
  `cash_anchor_facts` in `(created_at, id)` order with a MONOTONIC `source_index` pointer, while the
  read fold explicitly re-sorts by `(observed_on, asserted_at)`. The two agree today only because
  `observed_on = to_display_date(created_at)` is monotone in `created_at`. **The moment step 2 makes
  `observed_on` a user-supplied column**, a user correcting an anchor's date backwards inverts the
  order, the pointer skips sources, and the two walks disagree -- the exact drift Phase X exists to
  prevent. Nothing asserts the invariant. Cheapest fix now: sort `facts` by
  `(observed_on, asserted_at)` in the posting walk too, so both sides state the order once and the
  same way.

### Documentation that still teaches the deleted rule

- **F7 (Medium) -- CLOSED, and wider than the finding scoped it.** All eight named sites are
  corrected, plus six the review did not list (`dates.py`'s `to_display_civil_date`,
  `_asset_fold` x3, `_asset_contributions` x2) and the test-side citations. `utc_instant`'s
  argument FOR the instant partition is replaced by the measurement that refuted it;
  `_attribution.py`'s "deliberately different" paragraph now states the narrower boundary stance
  that actually survives. The original finding:
  The commit's stated reason for deleting `utc_civil_date` / `to_utc_civil_date` was "leaving a
  UTC-civil-date helper in the tree is how the old rule gets reintroduced". The survivor carries the
  old rule's ARGUMENT:
  - `app/utils/dates.py:137,154-155` -- `utc_instant` cites both deleted functions AND argues FOR
    the instant partition using the exact 2026-07-25 / $108.15 case R-DH rejected. **Fix this one
    first.**
  - `app/utils/dates.py:176,182` -- cites deleted `to_utc_civil_date` and
    `cash_ledger.attribution_instant`.
  - `app/services/_posting_write.py:5-6` -- module docstring names `_utc_civil_date`, deleted in
    this same commit.
  - `posting_service.py:302,308,320` and `:364,379` -- still "The UTC civil date of ..." and
    "Mirrors the Commit-3 backfill's `COALESCE((paid_at AT TIME ZONE 'UTC')::date, ...)`". Both now
    return display-tz via `_civil_settle_date`.
  - `account_posting_service/_walk.py:1` -- "the **moment-granular** correction producer",
    contradicted by its own line 13.
  - `balance_at/_cash_fold.py:856` -- "its attribution instant".
  - `calendar_service.py:790` -- "`paid_at`'s UTC civil day".
  - **`ledger_report_service/_attribution.py:38-43` is worse than stale.** It says "*Instant vs
    civil date.* The write-side walk partitions by UTC INSTANT ... The two are **deliberately
    different**." Both sides are now the same display-tz civil day. That paragraph is the stated
    justification for keeping an independent restatement of the attribution rule in that package
    (`to_display_civil_date` copied at `:445` and `:548`); the justification has evaporated and the
    commit renamed the cross-references while leaving it. It is now a plain DRY violation with a
    false defence attached. Fold into step 3.
  - Test-side: `test_account_posting_service.py:165,175,180` ("the walk attributes by instant",
    "the moment partition", "partitions sources by instant"); `test_cash_walk.py:228,252` (cite
    `sources[i][0] <= fact.asserted_at`).

### Operations

- **F8 (Medium) -- CLOSED.** `resync_all_cash_postings` returns sources CHANGED (both sync
  functions already returned their emitted entries, so no extra query), the deploy line prints
  `already at target (0 changed)` in steady state and names the re-date when it happens, and the
  one-way risk is stated in both the service docstring and the deploy log. Measured on a pristine
  clone: `RE-POSTED 16 transaction(s) and 1 transfer(s)`, then 0. The original finding: Measured at HEAD
  against a PRISTINE production clone: the fold and the posted ledger disagree on **36 of 56 dates**
  on Checking (final $1,307.66 vs -$2,693.76); after the three hooks, 0 of 56. So
  `resync_all_cash_postings_after_migration` is mandatory. `entrypoint.sh` runs
  `set -eEuo pipefail` and calls `python scripts/init_database.py` at `:259`, so a failure aborts
  the container and the auto-rollback fires -- that part is right. Two gaps:
  - the hook returns counts **walked**, not counts **changed**, so the deploy log cannot tell an
    operator whether the one-time re-date actually happened, which is the one fact worth logging for
    this hook;
  - the re-date is one-way and undocumented as such. If the healthcheck fails AFTER the resync
    commits, the rolled-back image reads a display-dated ledger with UTC rules. Bounded on this data
    (1 payment, 1 day) but it should be a stated risk, not a discovered one.
- **F9 (Low) -- CLOSED.** `joinedload(Transaction.pay_period)` beside the existing eager `entries`,
  and the same on the transfer pass. The original finding: `posting_service.py:816-826` eager-loads
  `Transaction.entries` but not `pay_period`, while `_transaction_entry_date` and `_settled_target`
  (`txn.pay_period.user_id`) both dereference it, and `Transaction.pay_period` is a plain lazy
  relationship (`app/models/transaction.py:205`). 122 extra SELECTs on this dataset, plus one anchor
  walk per emitting source via `_self_heal_account_anchor_corrections`. Same function whose
  docstring makes a point of the eager `entries` load.

### Document accuracy (fixed in this pass)

- **F10 (Low) -- three inaccuracies in this document, all corrected in this pass.**
  - the status block: "COMPLETE and green on `dev` (uncommitted)" -- it is committed as `9c2c3130`
    on `fix/anchor-settle-partition`, not on `dev`. "verified end to end (Section 7)" -- there was
    no Section 7; the document ended at Section 6. "7,676 passed / 0 failed" -- see F3. Section 7
    now exists and holds the results the citation promised.
  - Section 3.1's **"22 of 139"** did not reconcile. Re-measured: **4** settled rows with NULL
    `paid_at`, **18** whose UTC day differs from their Eastern day, **5** that move pay period. The
    period count matches exactly; the day count does not, and the two methods were not reconciled.
    Not load-bearing (nothing in the fix turns on the count), but settle it before quoting it again.
  - Section 3's list of past period ends quotes **$218.58** and **$2,037.01**, which are the FIRST
    anchor of a two-anchor day. Both those days close on a later assertion (**$31.73** on
    2026-06-03, **$126.06** on 2026-07-01), so the list contradicts R-DH (a)'s own "the last is the
    day's closing balance". The engine is right and the list was wrong; Section 7 carries the
    measured nine.

### Forward design

- **F11 (Medium) -- R-DH (d) as ruled would make today's production figure worse.** Full detail
  inline at R-DH (d). Measure the entry-side residual before building S1-c.
- **F12 (Low) -- CLOSED with step 2's opening half, as the finding predicted.** The index now keys
  `(account_id, pay_period_id, anchor_balance, observed_on)`. Approved as a destructive
  drop-and-recreate by the developer 2026-07-31; migration `c4a19e7b2d80` carries the `Review:`
  line and reverses cleanly. `AnchorPoint.as_of_date` -- a UTC-day field whose own docstring cited
  this index as its justification, and which no production code read -- was deleted in the same
  pass. The original finding:
  `app/models/account.py:190-195` keys on `((created_at AT TIME ZONE 'UTC')::date)`. Two assertions
  of the same balance in the same period on two different Eastern days that share a UTC day (23:00
  EDT one evening, 01:00 EDT the next) are now rejected as a same-day duplicate, although R-DH calls
  them two different days' closing balances. The guard's day and the semantic day no longer mean the
  same thing. Narrow, and it fails as a rejected save (`DUPLICATE_SAME_DAY`) rather than a wrong
  number, so it is a step-2 cleanup item: when `observed_on` becomes a stored column the index
  should key on it.

## 9. Second adversarial review (2026-08-01) -- the residue's own review

Three neutral reviewers ran against the residue branch before it was committed:
the project's `code-reviewer` (standards, financial correctness, IDOR,
migration), a test-integrity auditor (mutation-testing every new and re-ruled
test), and a design reviewer (attacking the ruling and the schema). **Two of the
three independently found the same root defect, and one proved it live.**

### R1 (High, FIXED) -- `is_opening` was derived in RECORDING order while every consumer reads BUSINESS-DATE order

`cash_anchor_facts` loaded `ORDER BY (created_at, id)` and set
`is_opening=(index == 0)`, while the partition, the posting walk, ruling R-I's
seed (`anchor_corrections[0]`) and the period view's assertion component (`[1:]`)
all read business-date order. The two agreed for free while `observed_on` was
DERIVED from `created_at`; **step 2's stored column broke that**, and the review
caught it firing in this branch's own new test: an origination observed
2026-03-19 with a microsecond-later `created_at` than a true-up pinned to exactly
noon inverted the two, and a `$1,307.66` TRUE-UP posted to the ledger tagged
`account_opening`.

Fixed at the loader: `cash_anchor_facts` now orders `(observed_on, created_at,
id)` and sets the flag on THAT list, `resolve_anchor` takes the same key
descending so "latest" means the row the walk replays last, and the two
downstream re-sorts (the posting walk's F6 sort, the read fold's merge sort) are
DELETED as restatements. One ordering, stated where the rows are read.

### R2 (High, FIXED) -- the period and the assertion's day came off two different clocks

`resolve_anchor_period_id`'s docstring claims the two "cannot disagree", and that
was true only on the create path. Both true-up writers picked the period with
`get_current_period(user_id)` (`date.today()`, the PROCESS day) while
`stage_anchor_true_up` dated the row `display_today()`. In any process not pinned
to the display zone -- CI, a script, the migration host -- a 21:00 ET true-up on a
period's last day files the row in the NEXT period while dating it in this one,
and the grid (which buckets by `observed_on`) then disagrees with the ledger
(which stamps `pay_period_id`) by the whole correction. Both route sites now pass
`as_of=display_today()`; registration's bootstrap period and its origination
assertion now come off one clock too.

**A related documentation defect, three sites:** comments asserting that
`date.today()` is "the SERVER's UTC day" are FALSE for the deployed container,
which pins `TZ: America/New_York` precisely to fix that class of bug (parity
finding M01). Using `display_today()` is still right -- it is zone-explicit and
does not depend on an env var -- but the stated reason was wrong and is corrected.

### R3 (High, FIXED) -- `observed_on` had no lower bound

The field validated only "not in the future". It opens the modelled-return
accrual window (`_asset_fold._AccrualWindow.days()` materialises EVERY calendar
day from it to the horizon) and the contribution model's first period, and
`fields.Date()` accepts `0001-01-01`. Measured by the reviewer: 740,560 days
enumerated, 234 ms in `sorted()` alone before any accrual math, on every
dashboard render. The correctness half is worse -- back-dating an investment
account fabricates a payroll contribution into every past period.

Bounded by `account_service.earliest_observable_day`: `min(earliest pay period
start, today)`. Taking the EARLIER of the two is load-bearing -- a user whose
schedule is entirely in the future must still be able to assert what they hold
now. The form's `min`/`max` read the same helper, so the browser refuses what the
service refuses.

### R4 (High, FIXED) -- the case the ruling relies on this field to answer was not reachable through the UI

R-DH (a)'s justification for deleting the opening exception is that the opening's
own DATE answers the "$0 account funded the same day" case. The reviewer walked
the journey: the field defaults to today and the help text said *"Leave it as
today unless you are entering an account you already had"* -- so a user opening a
brand-new account is told to leave the default, and the funding is then absorbed.
Traced: the account reads **$0.00 while holding $500.00**.

The rule is right and the copy was wrong. The help text now states the
closing-balance semantic and names the funding case explicitly. **The residual's
direction is also corrected here**: this document said an absorbed same-day
movement makes the projection read HIGH, which is the outflow case only; an
INFLOW makes it read LOW by the full amount, and the `$184.55` median was
measured on outflows.

### R5 (Medium, FIXED) -- two "as of" surfaces dated the anchor from the keystroke

`AnchorPoint` carried no business date after `as_of_date` was deleted, so the
account-detail caption and the investment hero both rendered
`to_display_date(created_at)`. On a back-dated opening they say "anchored Jul 31"
while the engine treats the balance as Jan 1's closing balance -- and on an
investment account the "growth since" caption then contradicts the figure beside
it, whose accrual window opens on `observed_on`. `AnchorPoint.observed_on` added;
both surfaces repointed.

### R6 (Medium, FIXED) -- a seventh statement of "which civil day", missed by F7's sweep

`dashboard_pulse_service._utc_day` truncated the anchor instant to a **UTC** day
for the staleness count while the caption beside it used `to_display_date`. One
instant, two derivations, compared against a display-tz "today": the staleness
count was off by one for four hours every evening. Now one derivation, in the
user's zone. Recorded but not done: staleness should measure from `observed_on`,
not from the recording instant at all -- that needs
`dashboard_service._get_last_anchor_date`'s contract to change, which has callers
beyond this module.

### R7 (Medium, FIXED) -- the migration's two refusals

The index re-key had no duplicate pre-flight, although the migration that CREATED
that index (`e8b14f3a7c22`) does exactly one. And `downgrade` both destroyed
user-supplied data silently and could fail on data the new rule legitimately
admits. Both fixed and both verified by injecting the offending row: `upgrade`
refuses with the colliding tuples named, `downgrade` refuses with the hand-dated
rows named and the operator's recovery path spelled out.

### R8 (High, FIXED) -- two existing tests silently stopped testing what they name

The test auditor proved that `create_account_of_type`'s new "open the day before
today" default, combined with `_origin_instant` reading `created_at`, put the two
exactly one day apart -- so `origin - timedelta(days=1)` landed on the opening's
OWN day and `test_a_settle_on_an_earlier_day_is_inside_the_opening` became a
duplicate of the own-day test. `test_transfer_attribution_uses_income_shadow_day`
had the same defect. **This is N-133 / F2's shape recreated by the fix for it,
from a different cause.**

Root fix: `_origin_day` returns the opening's `observed_on` -- the day the
partition actually reads -- and every offset is built from it through
`settle_instant_on`. Verified with a mutant that absorbs only the assertion's own
day: both tests now FAIL under it, where before only two tests that do not name
the boundary covered it.

### R9 -- everything the reviewers confirmed clean

Worth recording, because a review that only lists defects reads as if nothing was
checked: no `float` anywhere in the diff; `Decimal` discipline intact; ownership
and IDOR unchanged and correct on both changed routes; the `update_account`
refactor behaviour-preserving field by field; the backfill exactly equal to the
derivation it replaces; a single Alembic head; the table already in
`AUDITED_TABLES`; transfer invariants untouched; `resync_all_cash_postings`'
changed-count accurate and its two eager loads many-to-one; F4's rewrite sound
with the zone dependency genuinely removed; F5 closed by construction; and **all
five new tests plus the four re-ruled ones VERIFIED-CAN-FAIL** by mutation, each
caught by exactly the test that should catch it.

### Still open from this review

- **N-134 -- `update_account` moves the anchor balance with NO history row when
  no period contains today.** Behaviour preserved verbatim through the
  `stage_anchor_true_up` refactor and flagged in the code, but it breaks E-19's
  "a matching `AccountAnchorHistory` row from the moment it exists": the cash
  walk then replays a history that disagrees with `current_anchor_balance`, which
  is exactly the divergence `cash_ledger._facts` logs. Not changed under an
  unrelated ruling.
- **`observed_on` is write-once.** It is offered only on create; no route writes
  it afterwards, and `stage_anchor_true_up` hard-codes `display_today()`. A user
  who gets it wrong has no UI to fix it until the next true-up resets the balance
  anyway. S2-b (the true-up form's date field) is what closes this.
- **The loan/cash index asymmetry expires at S2-b** and is noted in the model.
- **`pay_period_id` on the history row.** The reviewers split on whether it is now
  redundant. Against: the schedule is mutable, `journal_entries.pay_period_id` is
  NOT NULL, and a day in a period gap still needs a home. For: it is a
  denormalized copy of a derivable fact. The honest statement is that the row
  carries three "when"s, the third is derived from the second on the create path
  only, and nothing detects a disagreement.

### Suggested order

**Done, in this order, on `fix/n133-review-residue`:** F1's ruling -> S2-a (the stored `observed_on`,
because `create_account` had to be able to date the opening BEFORE it posts the correction, or every
fixture re-stamp leaves a reverse-and-repost pair in the seeded ledger) -> the F1 revert and its
fixture work -> F4 + F6 -> F7 -> F8 + F9 -> F2's three owed tests.

**What remains:** **F11 before S1-c** (measure the entry-side residual first -- it is the one
finding this pass did not touch, and R-DH (d) as ruled would make today's figure WORSE by $362.51);
then step 3's fence; then S2-b, the transaction half of step 2.

The review's original order, for the record:

1. **F1** -- the ruling, because everything below is cheaper once the rule is settled.
2. ~~**F2** -- fix the two blind tests, rename `test_same_instant_settle_is_absorbed` for the rule
   it actually pins.~~ **DONE 2026-07-31** (see the F2 note above; the fix was a PINNED opening, not
   a smaller offset). Still owed: the three missing tests -- R-DH (c)'s two envelope invariants, a
   test for `resync_all_cash_postings`, and order-independence at projected-balance grain.
3. **F4** and **F6** -- two small changes that remove a hidden zone dependency and a step-2 landmine,
   and pre-pay step 3.
4. **F7** -- clear the stale docstrings, `utc_instant` and `_attribution.py` first, since those two
   actively argue for the deleted rule.
5. **F8**, **F9** -- deploy log and the N+1.
6. **F11** before S1-c; **F12** with step 2.
