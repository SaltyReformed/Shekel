# The anchor/settle partition: when is a settled row already inside an asserted balance?

Status: **Step 1 COMPLETE and green on `dev` (uncommitted); steps 2-4 OPEN.**
Full suite 7,676 passed / 0 failed, `pylint app/` 10.00/10, and the production
clone verified end to end (Section 7). **Step S1-c is DEFERRED to its own
session by developer ruling 2026-07-31** -- see Section 5. Written 2026-07-31 after a production defect made the grid's
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

### 2.1 The same question, answered three different ways

The architectural root is not the granularity. It is that **one question has three implementations**:

| site | rule | granularity | tie |
|---|---|---|---|
| `cash_ledger/_events.py:391` (read fold) | `occurred_at` vs `asserted_at` | instant | settle wins |
| `account_posting_service/_walk.py:434` (posted ledger) | `sources[i][0] <= fact.asserted_at` | instant | assertion wins |
| `entry_service.py:799` (envelope entries) | `entry_date <= date.today()` | **date** | assertion wins |

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
| R0 instant partition (**shipped**) | $40,554.34 | -$6,998.90 | $4,161.47 | **-$2,693.76** |
| **R1 civil day, assertion closes its day** | **$14,286.82** | **-$940.06** | $2,612.92 | **$1,307.66** |
| R2 civil day, settles win same-day ties | $65,671.21 | -$4,406.95 | $3,624.63 | -$101.81 |
| R3 anchor-only, settled rows ignored (pre-arc) | $41,008.30 | -$1,438.92 | $2,612.14 | $1,307.66 |

R1 wins on every axis and is the only rule under which the walk lands on the balance the bank
actually shows. Its median per-day plug is **$184.55**; today's is **-$160.05** against the shipped
rule's **-$4,161.47**.

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
opening.** (Amended 2026-07-31 during the build, developer ruling: "an account's
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

- **S1-a** `fix(cash-ledger): a settled fact carries the civil day it moved on`
  `CashSourceFact.settled_on` and `CashAnchorFact.observed_on` become real fields, resolved once at
  construction (display-tz civil day of a genuine instant; the NULL-`paid_at` civil-date fallback
  passes through unconverted). `merge_anchor_and_cash_events` partitions on `(civil day, sources
  before assertions)`. `dated_deltas` keys off the same fields rather than re-deriving.
- **S1-b** `fix(posting): the posted ledger partitions on the same day as the fold`
  `account_posting_service/_walk.py:434` and the journal-entry dating move to the same rule, so the
  write side and the read side stay one statement. Migration resyncs anchor and cash postings for
  every account (precedent: `7d63529e4300_backfill_historical_cash_postings.py`).
- **S1-c** `refactor(entries): reconciliation is derived from the entry's date`
  R-DH (d). Deletes the bulk clear, the toggle service/route/UI, and (with the developer's approval
  and a `Review:` line) the `is_cleared` column.

Tests: the 6 that pin the instant partition are re-ruled against R-DH, and the test that was missing
is added -- **the projected balance is invariant to the order of assertion and settle within a
session**, plus R-DH (c)'s two envelope invariants.

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
