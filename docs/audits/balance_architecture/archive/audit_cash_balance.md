# The cash side: the same disease, never diagnosed

**Written 2026-07-14. Every number below was RUN against the dev clone, not read.** Companion to
`implementation_plan_loan_balance_from_scratch.md` -- the cash account is the *other half* of the
balance architecture, and it has never had an audit.

**Headline: $1,910.95 has left your checking account since you last asserted your balance, and no
figure the app shows you reflects it.** You have compensated by re-asserting your Checking anchor
**43 times in three months -- 3.2 times a week.** You are doing the app's arithmetic by hand.

---

## 0. Why the cash side looks fine and is not

The loan arc consumed six months because loans are *visibly* complex: amortization, escrow, ARM,
true-ups. Cash looks trivial: a balance, plus transactions. So nobody looked.

But cash uses **the same broken pattern as the loan, one layer worse**:

| | the loan | cash |
|---|---|---|
| what the app treats as truth | the POSTING LEDGER -- a derived cache | the ANCHOR -- a user assertion |
| what the truth actually is | the event stream (origination + payments + true-ups) | the event stream (assertions + transactions) |
| how the balance is computed | a fold over the *derived cache*, spliced with a schedule replay | an anchor **plus a filtered subset of transactions -- and the filter drops exactly the transactions that actually happened** |

**The loan's balance function is a fold over the wrong thing. The cash balance function is not a fold
at all.**

---

## 1. Four defects, all live, all measured

### D1 -- Settled transactions after the anchor are DROPPED. Not deferred. Dropped.

`balance_calculator.sum_projected` (`:480`) sums **only Projected** items. `calculate_balances`
(`:106-112`) calls it for the anchor period *and every post-anchor period*. The docstring states the
premise:

> *"The same Projected-only sum applies to every period the balance walk visits, anchor and
> post-anchor alike... in post-anchor periods **nothing is settled yet**."* -- `:494-498`

**That premise is false**, and the module has a detector for it being false:

```python
# balance_calculator.py:120-124
stale_anchor_warning = _detect_stale_anchor(periods, anchor_period_id, txn_by_period)
# "Purely informational -- it does not change the calculated balances."
```

**Measured, on your real data:**

| | |
|---|---|
| current Checking anchor | **$2,640.16**, asserted **2026-06-29** (`account_anchor_history` row 65) |
| Transfer to Mortgage | **$1,910.95**, status Paid, `paid_at` **2026-07-07** -- *eight days AFTER the anchor* |
| Kindle Unlimited | $12.80, Paid |
| **total settled, post-anchor, ignored** | **$1,923.75** |

Both sit in pay period 2026-07-02, which is entirely after the anchor period. They are **not** in the
anchor (it predates them) and they are **dropped** from the projection (they are settled). So they
exist in **no balance the app computes**. The app says your Checking is **$1,979.39**. You have paid
your mortgage since it last knew anything, and it has not noticed.

This is not a semantic difference between two views. It is money leaving an account and no producer
seeing it.

### D2 -- The "date-precise" scalar is period-flat, and contradicts its own sibling

Two producers, **the same seam**, the same account, the same day:

```
              balance_at()      cash_daily_balance_series()
              (dashboard hero,  (analytics calendar day cells)
               grid)
 2026-07-02      1979.39            2357.92     <-- -378.53
 2026-07-06      1979.39            2279.39     <-- -300.00
 2026-07-13      1979.39            2279.39     <-- -300.00
 2026-07-14      1979.39            1979.39     <-- agree, by coincidence
```

**`balance_at()` returns the same number for every day of the pay period.** It is not "your balance
today" -- it is *the projected balance at the END of the current pay period*, and the dashboard
labels it as today's. Cause: `_sum_period_as_of` (`balance_resolver.py:745-753`) deliberately does not
filter by due date. They agree on the last two days only because every flow has already passed.

### D3 -- Before the anchor, one producer fabricates and the other omits

```
 2026-03-26   balance_at() = 2640.16     per-period map: ABSENT
 2026-04-09   balance_at() = 2640.16     per-period map: ABSENT
 2026-05-07   balance_at() = 2640.16     per-period map: ABSENT
```

The scalar hands back **today's** anchor as March's balance. The map omits those periods entirely.
And the app's own anchor history knows the real answer: **$2,746.58**, asserted 2026-03-27.

The seam's own package docstring forbids exactly this -- *"flat-carrying them backward would fabricate
balances the account never had"* (`balance_at/__init__.py:12-15`). The map obeys it. The scalar does
not.

### D4 -- The anchor has NO DATE. This is the root cause of D1.

```sql
budget.account_anchor_history
    id | account_id | pay_period_id | anchor_balance | notes | created_at
```

**There is no effective date.** Your bank balance is true at an *instant*; the app records it against
a **14-day bucket**.

That is why `calculate_balances` cannot do the correct thing. In the anchor's own period it has no way
to know which transactions preceded the assertion, so it drops them **all** -- and then reuses that
same "drop settled" rule for every *post-anchor* period, where the premise is not merely unprovable
but flatly false. **One missing column produced D1.**

---

## 2. The tell: you have re-anchored Checking 43 times in three months

| account | anchor assertions | per week |
|---|---|---|
| **Checking** | **43** | **3.2** |
| Fidelity Roth IRA | 6 | 0.4 |
| Fidelity Traditional IRA | 5 | 0.4 |
| Empower 401(k) | 3 | 0.3 |

You are not using the anchor as an anchor. You are using it as a **band-aid for a fold that discards
your paid transactions.** Every few days the projection drifts away from your bank, and you re-assert
the balance to force it back.

**This is the strongest evidence in either audit that the design is wrong -- stronger than any single
number.** The app is making you do its reconciliation by hand, three times a week, and the "stale
anchor" banner is the app telling you it knows.

---

## 3. What is genuinely different about cash (and why a naive read switch is WRONG)

The loan is the **complete-data** case. Origination + every payment + every true-up *is* the whole
truth, so a fold over those events is exact.

Cash is the **incomplete-data** case. You do not enter every coffee. **The anchor exists precisely
because the record is incomplete**, and that is legitimate and must survive.

So: **do not "switch cash reads to the posting ledger."** The ledger knows only what you entered; it
would confidently report a balance missing every un-entered transaction. I am not recommending it, and
the earlier note in the adversarial review that flagged the $1,331.26 ledger-vs-dashboard gap should be
read with this caveat -- those two numbers answer different questions.

**But that caveat justifies exactly one thing: the anchor's existence as a periodic reset. It
justifies none of D1-D4.**

---

## 4. What should be done: the same fold

The anchor is an **ASSERTION event** -- identical in kind to a loan true-up. The model is the one
already locked for loans, with no new machinery:

```
events(cash account) =
    ASSERTION    balance := asserted_balance    at the anchor's effective date
    TRANSACTION  balance += signed amount       at its effective date
                     status = ACTUAL (settled) | PLANNED (projected)

balance_at(T) = fold(events where effective_date <= T)
```

**The fold being built for loans handles this for free.** `_replay_events` (`_walk.py:374-415`)
already treats an anchor as a RESET that subsumes everything before it. Point it at cash and:

* a settled transaction **before** the anchor is **subsumed by the reset** -- correct, that is exactly
  what the anchor asserts;
* a settled transaction **after** the anchor is **folded** -- **D1 does not get fixed, it becomes
  inexpressible**;
* a projected transaction is a PLANNED event, clamped to the future by the same "a plan cannot have
  already happened" rule (ruling D1 of the loan plan);
* the scalar and the daily series become **the same function sampled at different dates**, so D2
  cannot recur;
* a pre-anchor date reads **the anchor that was in effect then**, which kills D3.

### The finding that makes D3 embarrassing rather than hard

**Your anchor history IS the cash account's past, and the app throws it away.**
`balance_resolver.resolve_anchor` reads only the **latest** row. You have **43 assertions of your real
Checking balance** across three months -- a genuine, user-verified historical record -- and the app
keeps one of them and fabricates the rest by flat-carrying today's figure backward.

Under the fold, every one of those 43 rows is an ASSERTION event, and your checking history becomes
*more* accurate than your loan history, for free.

---

## 5. The prerequisite migration

**`account_anchor_history` needs an `effective_date`.** Nothing else in this document can be done
properly without it.

* add nullable -> backfill from `created_at` (the assertion instant, which is the honest civil date of
  the assertion) -> `NOT NULL` after verifying zero NULLs;
* `CHECK (effective_date <= CURRENT_DATE)` -- you cannot assert tomorrow's bank balance;
* the working downgrade drops the column.

This is the one schema change the cash fix rests on. It also lets the anchor's own period be handled
correctly (transactions before the assertion are subsumed; those after are folded), which today is
guessed.

---

## 6. Sequence

**The loan comes first**, and not for sentimental reasons: the loan is the *complete-data* case, so it
is where the fold machinery can be proven exactly, against an oracle, with no judgment calls. Prove it
there, then point it at cash, where the data is incomplete and the failures are subtler.

But **one step should not wait**, because it is correct on its own terms and needs no migration:

| # | commit | why |
|---|---|---|
| **X0** | `fix(balance): a settled transaction after the anchor is not a projection` -- count settled transactions in **post-anchor** periods. | **Correct with no migration**: a post-anchor period is *entirely* after the assertion, so every settled transaction in it is unambiguously not in the anchor. Recovers the **$1,923.75** you have already spent. It does NOT touch the anchor's own period -- that genuinely needs the date (X1). This is a root fix for a false premise, not a band-aid. |
| **X1** | `feat(db): an anchor is asserted at a DATE, not in a fortnight` -- the migration above. | Unblocks everything else. |
| **X2** | `refactor(balance): a cash account is an event stream` -- the fold. | Deletes `calculate_balances`, `sum_projected`'s balance role, `_detect_stale_anchor` (nothing to detect), and the scalar/daily-series duplication. |
| **X3** | `fix(balance): the past is the anchor history, not today's anchor carried backward` (D3). | 43 real assertions, currently discarded. |
| **X4** | `refactor(accounts): current_anchor_balance is a derived cache or it is nothing` | Today `resolve_anchor` **detects** the column diverging from its history table and only **logs** it (*"The cache is NOT mutated here"*, `balance_resolver.py:214-238`), while the grid header reads the **column** (`routes/grid.py:290-292`) and the body reads the **history row**. Verified: they agree today. It is a latent split, not a live one -- but it is a stored derived value with no reconciliation, which is the same normalization defect as the loan's. |

---

## 7. What I have NOT proven

* **The $1,331.26 "dashboard vs balance sheet" gap I reported yesterday is NOT a clean defect.** The
  posting ledger and the seam answer different questions (what happened vs what is projected), and the
  ledger is missing un-entered transactions by design. The real, defensible finding is D1: **$1,923.75
  of settled transactions that appear in NO producer.** I am correcting my own framing.
* **The cache-vs-history split (X4) is latent, not live.** Column and history agree on both cash
  accounts today.
* **I have not audited INVESTMENT or PROPERTY balances** to this depth. Both are anchor-plus-model and
  share the anchor's missing date, so at minimum D3 and X4 apply to them too.
* **I have not measured what the fold would say your Checking balance is.** It requires X1's date to be
  exact within the anchor's own period. The floor is clear -- today's $1,979.39 is overstated by up to
  $1,923.75 of already-spent money -- but the precise figure needs the migration.

---

## 8. The one sentence

> **Your loan balance was wrong because the app folded a derived cache instead of the facts. Your cash
> balance is wrong because the app does not fold at all -- it takes your assertion, throws away the
> transactions you actually paid, and then asks you to re-assert it three times a week.**
