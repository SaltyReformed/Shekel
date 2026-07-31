# The anchor/settle partition: when is a settled row already inside an asserted balance?

Status: **Steps S1-a and S1-b COMMITTED as `9c2c3130` on `fix/anchor-settle-partition` (not yet
merged to `dev` or `main`); S1-c DEFERRED; steps 2-4 OPEN.** `pylint app/` 10.00/10 (reproduced).
Full suite **6 failed / 7,669 passed** -- the 6 are the pre-existing month-end bomb, identical at the
merge base `b73e25bc`, unblocked by branch `fix/cross-page-month-end-clock` (Section 8, F3). The
production clone verification is Section 7: **it holds**, with the one exception recorded as F1.
**Step S1-c is DEFERRED to its own session by developer ruling 2026-07-31** -- see Section 5.

**An adversarial review on 2026-07-31 (Section 8) found 12 items, one of which needs a developer
ruling before this branch merges (F1: the opening amendment).** Nothing in Section 8 invalidates the
fix; the current-period figure is -$19.95 under every variant considered.

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

The architectural root is not the granularity. It is that **one question has four implementations**.
Three were found when this document was written; the fourth was found by the 2026-07-31 adversarial
review (Section 8, F4) and is the reason step 3 is not optional polish:

| site | rule | granularity | tie |
|---|---|---|---|
| `cash_ledger/_events.py:391` (read fold) | `occurred_at` vs `asserted_at` | instant | settle wins |
| `account_posting_service/_walk.py:434` (posted ledger) | `sources[i][0] <= fact.asserted_at` | instant | assertion wins |
| `entry_service.py:799` (envelope entries) | `entry_date <= date.today()` | **date** | assertion wins |
| `account_posting_service/_sync.py:304` (the self-heal skip) | `utc_day_start_instant(entry_date) <= max(created_at)` | mixed | assertion wins |

The fourth asks "could this source have moved an anchor's `ledger_before`", which is this question
verbatim, and it asks it in a *third* form again: a civil date pushed back through midnight UTC and
compared against a raw instant. It survived S1-a / S1-b untouched. See F4 for why it is sound only
by accident.

The third does not even compare against the anchor -- it compares against `today` -- and it is
**date-granular and inclusive**, which is the rule this document adopts. Its own docstring states the
correct semantic: *"the owner just looked at their real checking balance and entered it as the new
anchor, so every debit purchase that had already posted is now reflected in that number."*

That rule was right. It was applied to entries and never to transactions, and the two now run inside
one `apply_anchor_true_up` call and disagree.

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

> **CORRECTION (adversarial review, 2026-07-31 -- see F1).** **The R1 row above is the rule this
> document scored, and it is NOT the rule `9c2c3130` ships.** The opening amendment made during the
> build (Section 4, R-DH (a)) changed the rule after these figures were taken and they were never
> re-run. Re-measured on the same account, same 139 rows, same 54 assertions:
>
> | rule | gross plug | net plug | worst single | walk ends at |
> |---|---|---|---|---|
> | R1 un-amended (opening ABSORBS its own day) -- **what the table above scores** | $15,367.94 | **-$940.06** | $1,853.92 | $1,307.66 |
> | R1 **as shipped** (opening OPENS its day, sources ride on top) | $17,282.84 | **-$2,997.48** | $1,986.16 | $1,307.66 |
>
> The re-measurement reproduces the R0, R2 and R3 net plugs to the cent and reproduces R0's gross,
> worst and final exactly, so the method is the same one. The un-amended net (-$940.06) matches this
> table exactly; the gross and worst columns differ from the originals by a methodology detail that
> was not reconciled and is not load-bearing (F1 sub-item). **Net plug is the reliable axis, and on
> it the shipped rule is 3.2x worse than the rule this document and `README.md`'s R-DH entry both
> advertise.**
>
> Both rules land the walk on $1,307.66 and both give the current period -$19.95: the walk resets at
> every later assertion, so the amendment's cost is confined to March history. See F1.

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

**R-DH (a) -- An assertion is the closing balance for its civil day, EXCEPT an
opening.** **The EXCEPT clause is REOPENED and needs a developer ruling before this
branch merges (F1).** It was added mid-build on a hypothetical, never scored against
the production clone, and when scored it is the second-worst plug in four months of
real data. The un-amended half of the ruling is not in question and is what fixed
production. (Amended 2026-07-31 during the build, developer ruling: "an account's
opening should be where tracking starts".)  A TRUE-UP sorts after its own day's
sources and absorbs them; an OPENING sorts BEFORE them and they ride on top,
because an opening states what an account holds as recording BEGINS rather than
what a day closed at.  Without the amendment a brand-new account silently
discards the balance the user just typed: assert an opening of `$100`, record a
`$100` transfer the same day, and the reset swallows it for an account holding
`$200`.  Records on EARLIER days still precede the opening and are what ruling
R-I back-projects into the fold's seed; the amendment does not touch that arm.
It is implemented on BOTH walks -- the read fold's ordering and the posting
walk's absorption boundary -- because moving one alone makes the two disagree
(measured: 66 failures against 58 when only the read fold moved).  The original
rule, which the amendment narrows: It absorbs every cash movement
dated that day, whatever order the two were recorded in. Multiple assertions in one day apply in
order; the last is the day's closing balance. *Rejected:* the shipped instant partition (measured
above: 7x the net plug, and the -$4,021.37 that opened this document); settles winning same-day ties
(worse than either).

> **What the amendment costs, measured (F1).** Checking's OPENING asserts **$2,746.58** on
> 2026-03-27, and **four settled rows carry that same civil day**, netting **+$2,057.42**
> (`Data Manager` +2,473.38, `Health Insurance Allowance` +100.00, `Audible` -15.96,
> `Transfer to Fidelity Savings` -500.00). Every one of the four was clicked **33 seconds to 1.6
> hours AFTER** the opening was typed, so the opening was read off a bank that already showed them.
>
> | | walk on 2026-03-27 | plug at the next anchor (2026-03-30, $2,653.89) |
> |---|---|---|
> | opening ABSORBS its day | $2,746.58 | **+$71.26** |
> | opening OPENS its day (**shipped**) | **$4,804.00** | **-$1,986.16** |
>
> $4,804.00 is not a balance the account ever held, and -$1,986.16 is the second-largest correction
> in the whole four-month history. Blast radius, stated fairly: the walk resets at the 2026-03-30
> anchor, so the CURRENT period is -$19.95 either way and every period end from index 0 onward is
> unchanged. What moves is period 0's "Timing & true-ups" (**-$1,421.00** shipped vs **+$636.42**
> un-amended, a $2,057.42 swing), the daily balance for 2026-03-27..29, and the pre-tracking seed
> (R-I's back-projection: $2,746.58 vs $689.16).
>
> **The amendment's motivating case has never occurred in production**: account 1 is the only
> account with any settled row on its opening's day, and there the amendment is wrong. Every other
> account measures 0 same-day rows.
>
> **And the case it protects is R-DH (a)'s own accepted residual, pointed the other way.** "The user
> asserted a balance and then money moved the same day" is exactly the residual the paragraph below
> accepts for every true-up, on the grounds that it is bounded and self-corrects at the next
> assertion. An opening's residual is bounded by the same next assertion. The amendment buys one
> unobserved case and costs a measured $2,057.42.
>
> **There is no non-guess rule available before step 2.** "Opening typed then funded the same day"
> and "opening read off a statement that already contains the day's rows" are indistinguishable from
> the stored data. This is a choice between two guesses, and one of them is measurably better on the
> only data that exists. Reverting costs 35 test re-rulings (measured: 41 failures against the
> 6-failure baseline when the amendment is removed from both walks).

*The residual, stated:* a payment that genuinely clears **after** the balance was observed on the same
day is absorbed and the projection reads high until the next assertion. Bounded (the developer
re-anchors every 2.3 days), self-correcting, and measured at a median $184.55 per day against the
$4,161.47 the shipped rule produced. **It is a guess, and step 2 is what removes it** -- with real
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

> **STATUS OF THAT PARAGRAPH: the re-rulings happened, the additions did not (F2).** `9c2c3130`
> added **zero** net tests: `def test_` counts are identical before and after in all 8 changed test
> files (24/24, 24/24, 26/26, 242/242, 7/7, 40/40, 6/6, 18/18) and the collected total is 7,675 at
> both `b73e25bc` and HEAD. Still owed:
>
> - the order-independence test at PROJECTED-BALANCE grain (there is one at walk grain,
>   `test_cash_walk.py::test_both_same_day_settles_go_with_the_assertion_whatever_the_order`);
> - R-DH (c)'s two envelope invariants (absent entirely);
> - any test at all for `resync_all_cash_postings` (absent entirely -- and it rewrites the whole
>   production ledger on every deploy, while both its siblings have integration tests);
> - a test that can FAIL if the opening amendment is removed (the two written for it cannot -- F2).

### Step 2 -- the app records when money moved

`transactions.settled_on` and `account_anchor_history.observed_on` as stored, user-editable `DATE`
columns, backfilled from step 1's derivation so **no figure moves on the day it ships**. The entry
date is un-hidden on its creation form. The walk reads the columns instead of deriving them; nothing
else in the engine changes. This is the arc's existing plan step **X-f** plus finding **X5**,
promoted from "after X-d" to "now" by R-DH.

### Step 3 -- one predicate, fenced

A single `is_inside_assertion` shared by the read fold, the posting walk and the entry reconcile,
backed by a custom pylint checker on the `shekel-refname-compare` pattern, so a fourth answer to
section 2.1's question cannot be written.

> **The fourth answer already exists (F4), so this step is repair, not prevention.** It must take in
> `account_posting_service/_sync.py:304`'s self-heal skip alongside the three named above -- four
> call sites, not three. Section 2.1's table is updated.
>
> **Two more sites belong in the same sweep**, both of which were left holding a hand-mirrored copy
> of a rule rather than calling it:
>
> - the OPENING placement is now stated TWICE and by hand -- as a sort key
>   (`cash_ledger/_events.py:111-113`, `_OPENING_ORDER` / `_SOURCE_ORDER` / `_TRUEUP_ORDER`) and as
>   a date boundary (`account_posting_service/_walk.py:465-468`, `observed_on - _ONE_DAY`). Two
>   forms of one rule, held in step by convention. Whatever F1 rules, the two must move together,
>   and step 3 is where they stop being two.
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
5. The gross plug over the account's history drops from $40,554.34 toward $14,286.82.
   **(Restated after F1: toward $15,367.94 un-amended, $17,282.84 as shipped. The $14,286.82 target
   was never reachable by either -- see the Section 3 correction.)**
6. **Added by the review:** nothing outside Checking moves except what a ruling says moves. The
   whole-seam baseline (`tests/manual/verify_balance_baseline.py`) is captured before and after
   against a pristine production clone, and every moved cent is explained.

## 7. Verification results (production clone, 2026-07-31)

Run by the adversarial review against a **fresh** `pg_dump` of production (read-only; restored into
`shekel_audit` / `shekel_audit_pre` on `shekel-dev-db`, never written back). Code at `9c2c3130`.

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

Twelve items. **F1 is the only one that needs a ruling before merge**; the rest are work items.
Nothing here says the fix is wrong -- the current-period figure is -$19.95 under every variant
considered, and standards 1, 2, 3 and 6 all pass. Method: a fresh read-only `pg_dump` of production
restored into two throwaway databases on `shekel-dev-db`, the seam walked directly, the whole-seam
baseline captured at `b73e25bc` and at `9c2c3130` and diffed, and the suite plus `pylint` re-run at
both commits. Production itself was never written to.

### Blocking a ruling

- **F1 (High) -- the opening amendment is unmeasured and is measurably worse on production.**
  Full detail inline at R-DH (a) and in the Section 3 correction. Net plug -$2,997.48 shipped vs
  -$940.06 un-amended; the -$940.06 that this document and `README.md`'s R-DH entry both advertise
  is the un-amended rule. Concretely: Checking's opening day carries +$2,057.42 of settles clicked
  33 seconds to 1.6 hours after the opening was typed, and stacking them on top makes the engine
  read $4,804.00 for a day the bank showed $2,746.58. Bounded to March history and period 0's
  remainder. **Decide: revert (measured-best, 35 test re-rulings) or keep and correct both
  documents.**

### Test integrity

- **F2 (High) -- zero net new tests, and the amendment's own two tests cannot fail.**
  `test_account_posting_service.py:228` `test_a_settle_on_the_openings_own_day_rides_on_top` says
  "an hour after ... the same civil day" but passes `origin + timedelta(days=1)`, a DIFFERENT day,
  which rides on top under both rules. Proven by reverting the amendment in both walks: that test
  and its sibling `test_a_settle_on_an_earlier_day_is_inside_the_opening` both still passed.
  The amendment's only real pin is `test_same_instant_settle_is_absorbed` (`:361`) -- a test named
  and documented entirely for the DELETED instant rule, so fixing its stale name could silently
  delete the coverage. Also missing: R-DH (c)'s two envelope invariants; any test for
  `resync_all_cash_postings`. See the Section 5 status note.
- **F3 (High) -- "Full suite 7,676 passed / 0 failed" is not reproducible.** Actual: 6 failed /
  7,669 passed (7,675 collected, not 7,676). The 6 are `test_cross_page_balance_equality`, the known
  month-end bomb; **identical 6 failures and identical 7,669 passed at the merge base `b73e25bc`**,
  so nothing regressed. Fixed on branch `fix/cross-page-month-end-clock`. The honest claim is
  "6 failed, all pre-existing and unrelated", never "0 failed" (CLAUDE.md rule 4).

### DRY and latent correctness

- **F4 (Medium) -- a fourth statement of the partition, with a hidden timezone-sign dependency.**
  `account_posting_service/_sync.py:304-311` compares `min(utc_day_start_instant(entry.entry_date))`
  against `max(created_at)`. It is sound ONLY because `America/New_York` is west of UTC (midnight
  UTC of a display day always precedes that day's start in UTC), so it can only over-fire, which is
  an idempotent no-op walk. **For a display zone east of UTC it silently UNDER-fires** and leaves a
  stale anchor correction posted with no error. Nothing states or gates that. Root fix, one line and
  it pre-pays step 3: compare days -- `min(e.entry_date) <= to_display_date(latest)`, the rule both
  walks already use.
- **F5 (Medium) -- `dated_deltas`' tie-break was not updated for the amendment, and its docstring
  now asserts something false.** `cash_ledger/_walk.py:327-336` tags source 0 / assertion 1 with the
  comment "the same tie-break the walk applies", but `_events.py:111-113` puts `_OPENING_ORDER = 0`
  BEFORE `_SOURCE_ORDER = 1`. For an OPENING the two orders are opposite. Arithmetically inert today
  (its one consumer `_cash_fold._actual_steps` day-sums and `sample_cumulative` reads day
  boundaries), but the Returns docstring's "reading the list shows the same chronology the walk
  applied" is false in exactly the place a reader debugging an opening-day discrepancy would look,
  and any future sequential consumer replays the opening wrongly.
- **F6 (Medium) -- a step-2 landmine already in the tree.** `walk_account_ledger` iterates
  `cash_anchor_facts` in `(created_at, id)` order with a MONOTONIC `source_index` pointer, while the
  read fold explicitly re-sorts by `(observed_on, asserted_at)`. The two agree today only because
  `observed_on = to_display_date(created_at)` is monotone in `created_at`. **The moment step 2 makes
  `observed_on` a user-supplied column**, a user correcting an anchor's date backwards inverts the
  order, the pointer skips sources, and the two walks disagree -- the exact drift Phase X exists to
  prevent. Nothing asserts the invariant. Cheapest fix now: sort `facts` by
  `(observed_on, asserted_at)` in the posting walk too, so both sides state the order once and the
  same way.

### Documentation that still teaches the deleted rule

- **F7 (Medium) -- eight live docstrings, including the one helper the commit deliberately kept.**
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

- **F8 (Medium) -- the deploy hook is load-bearing and its absence is silent.** Measured at HEAD
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
- **F9 (Low) -- N+1 in the new deploy resync.** `posting_service.py:816-826` eager-loads
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
- **F12 (Low) -- the anchor dedupe index still buckets by UTC day.**
  `app/models/account.py:190-195` keys on `((created_at AT TIME ZONE 'UTC')::date)`. Two assertions
  of the same balance in the same period on two different Eastern days that share a UTC day (23:00
  EDT one evening, 01:00 EDT the next) are now rejected as a same-day duplicate, although R-DH calls
  them two different days' closing balances. The guard's day and the semantic day no longer mean the
  same thing. Narrow, and it fails as a rejected save (`DUPLICATE_SAME_DAY`) rather than a wrong
  number, so it is a step-2 cleanup item: when `observed_on` becomes a stored column the index
  should key on it.

### Suggested order

1. **F1** -- the ruling, because everything below is cheaper once the rule is settled.
2. **F2** -- fix the two blind tests (`+ timedelta(days=1)` -> `+ timedelta(hours=1)`), rename
   `test_same_instant_settle_is_absorbed` for the rule it actually pins, and add the three missing
   tests. Do this before merging: right now the amendment's only gate is a mis-titled test.
3. **F4** and **F6** -- two small changes that remove a hidden zone dependency and a step-2 landmine,
   and pre-pay step 3.
4. **F7** -- clear the stale docstrings, `utc_instant` and `_attribution.py` first, since those two
   actively argue for the deleted rule.
5. **F8**, **F9** -- deploy log and the N+1.
6. **F11** before S1-c; **F12** with step 2.
