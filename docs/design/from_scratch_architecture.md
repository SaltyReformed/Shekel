# Shekel from scratch: what it should be, what it would cost, and in what order

**STATUS: REVISED 2026-09-01 after three adversarial reviews, which broke a great deal of it.
Section 9 records what they refuted.** Section 3.2 is RULED.
**Section 6's order was ruled and is now WITHDRAWN by its own author** -- the plan gate refuses it
and a cheaper order buys the same correctness (Section 6). Nothing here has been built. This
document answers the developer's question of 2026-09-01,
*"how would you design this from scratch?"*, and it exists because `balance:X-f3c-4`'s specification
was measured unbuildable-as-written and the measurement that refuted it turned out to refute more
than the one step.

Its companion is `cash_difference_acceptance_audit.md`, which holds the option space for the
acceptance act alone. **This document supersedes that one's framing**: the audit asked what
`X-f3c-4`'s gate should be; this asks why the app needs the act at all, and finds the answer one
layer down.

**Two rulings have been taken, both 2026-09-01 (developer).**

1. The two competing balance-level records become ONE evidence-ranked relation (Section 3.2).
2. **Section 6's ORDER is approved and goes AHEAD OF EVERYTHING ELSE in `docs/plans/steps.md`** --
   *"I approve adding this work ahead of everything else in steps.md"*. That inverts today's `#5` /
   `#6` and puts the restructure ahead of six ranked steps plus the `recurrence:R5` family.

Everything else below is a proposal, and Section 8 lists what is still open.

---

## 1. What was measured, on which database, on what day

**Every figure here was re-derived in this session. Each decays. Re-derive before relying on one.**

Two databases, both throwaways on the dev postmaster:

- `shekel_xf3c4` -- the scheduled production dump of **2026-09-01 02:06:55**, migrated to
  `31bb08f73e50`.
- `shekel_x4head` / `shekel_x4now` -- a
  **live `pg_dump` of `shekel-prod-db` taken 2026-09-01 22:20**, after the developer's own true-up
  that evening. Production's stamp is `a4c6f1d92b73`; migrating it to this branch's head applies
  **28 Alembic revisions** -- an endpoint that measures nothing anyone will ship; see Section 5.

Bank evidence: five SECU exports in `~/Downloads/checking/` covering **2026-01-02..2026-07-17**,
plus `~/Downloads/Transactions-2026-09-01-ce49076a-….csv` covering **2026-07-01..2026-08-31** and
stating a balance as of 2026-09-01 of `$3,847.00`.

### 1.1 The two exports chain, and the chain is self-verifying

The new file carries no running-balance column, so its daily closings were derived BACKWARD from its
stated as-of balance. It overlaps the older export on **11 days (2026-07-01..07-17)** and the two
agree on **all 11**. Independently, the daily-closing-only export agrees with the line export on all
**112** shared days.

**State the control at its real strength.** Of the 155 days in the measured span the bank *states* a
closing for only **71**; 42 are derived backward from the 2026-09-01 export's own lines and 42 are
carried forward over quiet days. The 11-day overlap is ONE level constraint -- that
`3847.00 - SUM(new-file deltas 07-18..08-31) = 2229.73` -- plus ten confirmations that the two files
agree on lines they share. **So 2026-07-18..2026-08-31 has its AGGREGATE pinned and its per-day
distribution corroborated by nothing.** For `e_a` the exposure is small and an adversarial review
could not exploit it (the only lines between 08-28 and the as-of are five 08-31 postings totalling
`-$269.42`), but "known for every day" was an overstatement.

### 1.2 The identity, closed against the shipped producer

An account's outstanding difference decomposes exactly into the three things that can be wrong:

> `difference = e_a - e_o - e_m`

where `e_a` is the owner's typed balance being wrong, `e_o` the stored opening being wrong, and
`e_m` the app's movement records being wrong. The derivation is
`cash_difference_acceptance_audit.md` Section 3; its two code preconditions were re-verified here
(`cash_ledger.dated_deltas` at `_walk.py:350` and `balance_at._cash_fold`'s `recorded` at
`_cash_fold.py:595-597` are the same expression over the same facts, and no movement may be dated at
or before `opened_on` since `X-f3c-2b-1`).

On the 2026-09-01 02:06 restore, over 2026-03-26..2026-08-28:

| term | what it means | measured |
|---|---|---|
| `e_a` | the owner's typed balance vs the bank's closing that day | **`$0.00`** |
| `e_o` | stored opening equity vs the bank's closing for `opened_on` | **`-$2,493.47`** |
| `e_m` | app movements vs bank movements over the span | **`+$4,270.78`** |

`0 - (-2493.47) - 4270.78 = -1777.31`, which is `balance_at.cash_outstanding_difference`'s own
answer for that database. **The identity closes to the cent against the shipped producer**, and a
hand replay of `opening + SUM(dated_deltas)` reproduced the producer's `$5,893.73` before any of it
was trusted.

**`e_a = $0.00`, and it is ONE DAY'S draw rather than a property.** The governing assertion,
2026-08-28 at `$4,116.42`, IS the bank's own closing for that day, exactly -- but
**5 of the last 13 assertions are non-zero, up to `$645.18`**, and 12 of those 13 sit in the window
whose closings are DERIVED rather than stated. What carries the claim is the aggregate, not the
draw: across all 58 assertions in the bank-covered range
**22 are exact, the mean miss is `$158.05` and the median `$39.20`**. On that basis the owner's
true-up is the most accurate instrument in the system -- and on the newer restore the decomposition
cannot be recomputed at all, because the governing assertion has moved to 2026-09-01, a day no bank
record covers.

**The identity's closure is NOT corroboration of any bank figure.** The bank terms cancel by
construction, so the closure holds against a fabricated bank series just as well -- adversarial
review demonstrated exactly that. What it verifies is the two preconditions and the fold's seed.

### 1.3 Which side the movement error is on

Over 2026-03-27..2026-08-28, comparing the fold's own source facts against the bank's lines:

| | app | bank | app - bank | rows |
|---|---|---|---|---|
| money IN | `$37,232.92` | `$34,918.29` | **`+$2,314.63`** | 28 vs 25 |
| money OUT | `-$32,028.35` | `-$33,984.50` | **`+$1,956.15`** | 178 vs **233** |
| net | `$5,204.57` | `$933.79` | `$4,270.78` | 238 vs 258 |

**Both sides are wrong and they partly cancel.** The app records `$2,314.63` more income than the
bank shows and `$1,956.15` less spending, against **233 bank debit lines for 178 app outflows**. The
`-$1,777.31` outstanding difference is the residue of two large errors, not an economic quantity.

The shape of the app's own facts, decomposed from `walk.source_facts` (net `$5,204.57`, reconciling
with the producer by construction):

| type | kind | facts | net |
|---|---|---|---|
| Expense | purchase inside an envelope | 77 | `-$5,752.40` |
| Expense | envelope's own leg | 40 | `-$794.79` |
| Expense | one-off row | 31 | `-$7,184.32` |
| Expense | recurring template row | 46 | `-$3,332.39` |
| Expense | transfer shadow | 16 | `-$14,964.45` |
| Income | one-off row | 3 | `+$2,304.27` |
| Income | recurring template row | 23 | `+$31,428.65` |
| Income | transfer shadow | 2 | `+$3,500.00` |

*A first version of this table classified on the raw `transactions.is_envelope` COLUMN and
misassigned 37 facts worth `$586.64`, including every Groceries row. The operative predicate is
`Transaction.tracks_purchases`, which defers to the TEMPLATE's flag -- the same error the migration
rule made in Section 4.3. The figures above are the corrected ones.*

**Only 77 of 238 facts are movements with their own date and amount.** Of the other 161,
**116 carry a figure DERIVED from the plan**; 13 are owner-corrected and 32 are computed from
entries. The day half is unqualified:
**every one of the 161 carries `settled_day_basis = entered`** --
*"the app's own record with no bank document behind it"* -- and
**no fact in the span carries an `observed` day.**

### 1.4 What THE FLIP would do

`balance_at._outstanding._books_balance_at` IS `X-f3c-5`'s balance function, shipped early at
`X-f3c-3` precisely so the flip becomes a re-pointing. Evaluated over every day the bank states a
closing for, after the books open (155 days, 2026-03-27..2026-08-28):

| series | mean abs error | median | max | days exactly right |
|---|---|---|---|---|
| today (assertions reset the fold) | `$341.04` | `$79.79` | `$2,572.35` | 37 / 155 |
| **after THE FLIP** (`opening + SUM(postings)`) | **`$2,411.59`** | **`$2,133.22`** | **`$7,564.25`** | **0 / 155** |

**This head-to-head is NOT the load-bearing claim and must not be quoted as one.** It is partly
confounded by the mechanism under test: an assertion resets the fold, so today's series is pinned to
what the owner typed on 58 of those days. Partitioned on that mechanism, every one of the 155 days
falls in one of two classes -- 58 assertion days and 97 days carried forward from one -- because
**all 51 days with recorded movement are also assertion days**. The owner settles rows and types the
balance in the same sitting, every time.

Two claims survive any partition and they are the ones that carry the argument:

1. **`e_m = +$4,270.78` over 2026-03-27..2026-08-28; of the 101 days on which either record shows
   activity, 98 disagree.** *(An earlier draft welded two spans: the "71 of 74" belonged to a
   113-day sub-window ending 2026-07-17, over which `e_m` is `$6,943.99`, not `$4,270.78`.)* This is
   a direct measurement of the movement record against the bank. THE FLIP makes the balance a pure
   function of that record, so the flip cannot be safe until the postings are the bank's. No
   comparison is involved.
2. **Post-flip is right on 0 of 155 days.** It applies no assertion policy at all, so its error is
   not a comparison against the reset and no partition can rescue it. *(An earlier draft added
   "including the days the reset does not touch"; the same paragraph proves that set is EMPTY.)*

And one sentence about the step this began with: **`X-f3c-4` as specified would book
`-$1,777.31` on 2026-08-28, the single day `e_a = $0.00` proves the app already correct.**

### 1.5 The import is money-neutral, rehearsed on production

`statement_import.record_statement` writes exactly three tables --
`budget.account_external_identities`, `budget.bank_statement_lines`, `budget.statement_imports` --
and no assertion and no transaction. Grepped across `app/services/statement_import/` and
`app/services/statement_match/`.

Rehearsed on the live production restore at branch head, importing both exports in the order `N-368`
names:

- file 1: **306 lines recorded**, balance `$2,229.73` effective 2026-07-17, evidence `FILE_CHAIN`;
- file 2: 119 lines, **91 recorded** (the 28-line overlap correctly deduplicated), balance
  `$3,847.00` effective 2026-08-31, evidence **`CORROBORATED`** -- the app graded the new file
  against the existing chain and confirmed it, reproducing by itself the hand check of Section 1.1;
- `budget.transactions` unchanged at 1,028; `budget.account_anchor_history` unchanged at 86;
- **balances moved on 0 of 1,431 account-days** (9 accounts x 159 days, `cash_daily_balance_series`
  captured before and after and diffed).

**THE BALANCE SNAPSHOT WAS NEARLY BLIND, AND IT WAS PRESENTED AS THE STRONGEST EVIDENCE.**
`cash_daily_balance_series` applies the assertion RESET, so a change to a movement dated on an
assertion day is cancelled on that same day -- and on account 1,
**54 of 56 movement days are assertion days**. A positive control run against a production restore
injected `+$500.00` into settled row 1629 on 2026-08-14 and the harness reported **`0 of 1431`**:

```text
row 1629 settled_on=2026-08-14  amount 2572.36 -> 3072.36  (THE MUTATION LANDED)
assertions on that same day: 1
ACCOUNT-DAYS WHOSE BALANCE MOVED: 0 of 1431
```

**The reason it cannot fail is the assertion reset -- the exact mechanism Section 1.4 argues to
delete.** What the snapshot rules out is a NEW row or a NEW assertion, never an ALTERED one.

**What actually carries the money-neutrality claim** is the other two controls: `record_statement`'s
only three `session.add` targets are the bank tables, and the row counts were unchanged. Both hold.

**A RESET-FREE instrument catches what this one misses**, verified on the same mutation --
`cash_ledger.dated_deltas` reported `account 1 2026-08-14: 2611.90 -> 3111.90`. Any A/B rehearsal of
the RELEASE must use the reset-free view (`dated_deltas`, or `_books_balance_at`, which is THE
FLIP's own function and applies no reset), never the balance series.

And immediately after the import, with no further act, the app said what it could not say before:

```text
OPENING CORROBORATION: stored=689.16  bank=3182.63  agrees=False
SPAN AGREEMENT: days=159 compared=158 unchecked=1 unimported=1 disagreeing=99 reconciles=False
```

*(The outstanding difference on the live restore reads `-$1,850.98` rather than `-$1,777.31`,
because the developer true-upped on 2026-09-01 and the governing assertion moved. The figure is a
function of the LAST assertion alone -- N-171's row says so, and this is that row being right.)*

---

## 2. The root cause, in one sentence

> **Shekel stores a PLAN and a RECORD OF WHAT HAPPENED in one table, and uses a status column to
> pretend the first turns into the second.**

Every open finding in this arc is a symptom. The assertion RESET exists to hide the gap between the
two. The `anchor_equity` plug is the reset's residue, which is `N-171`. `X-f3c-4` exists to book the
plug. `N-314` exists because two records both claim to say what the balance is. `X-f3c-5` is
dangerous because it deletes the reset without replacing what the reset was hiding.

Section 1.3 is the mechanism, stated concretely: a `$400` grocery envelope is consumed by real
purchase rows carrying their own dates and amounts, and that works -- but a `$150` electric bill is
"settled" by flipping its own status, and **no movement is recorded at all**. The row's *planned*
figure enters the fold as though it were an *observed* movement. 161 of 238 facts in the span are
that.

---

## 3. The design

### 3.1 One movement concept, not two

**Every plan item is satisfied by zero or more MOVEMENTS.** A bill is the one-movement case; an
envelope is the many-movement case. `budget.transaction_entries` becomes the general movement table,
and `is_envelope` stops being a concept.

`budget.transaction_entries` is already most of a movement row: `transaction_id` (the plan item it
satisfies), `account_id` (held to the parent's by `fk_transaction_entries_parent_account`),
`amount`, `description`, `purchased_on` (when it happened), `settled_on` (when the money moved),
`user_id`, an optimistic lock and timestamps. What it lacks against `budget.transactions` is a
category, a type, `reconciled_by_id`, and the settle-day basis pair.

What this buys, structurally rather than aesthetically:

- **the balance folds movements only, never a plan row.** `opening + SUM(movements)` becomes true by
  construction, which is exactly what THE FLIP needs and cannot currently have;
- **a `$400` envelope with `$437.22` of real spending shows a `$37.22` variance** instead of losing
  it. That mechanism, generalised, is what produced the `$1,956.15` of under-recorded spend;
- **"Paid" stops being a state anyone sets.** It becomes a consequence of coverage, so it cannot be
  set wrongly;
- the two settle paths that already sit side by side in one door (`transaction_service/_settle.py`:
  `settle_from_entries` versus the MANUAL branch) collapse to one;
- 80 branch sites go (Section 4).

### 3.2 One evidence-ranked LEVEL relation -- **RULED 2026-09-01**

The owner's true-ups and the bank's statement closings are observations of the same quantity -- an
account's balance on a day -- stored in unrelated places with no rule about which governs. That is
`N-314`.

**They become one append-only relation:** `(account, day, amount, source, evidence)`,
evidence-ranked. `app.enums.StatementBalanceEvidenceEnum` and `statement_import.weaker_of` already
ship and already rank exactly this.

**A level observation never moves a balance.** It produces `discrepancy = observed - computed`. Zero
is the healthy state; non-zero is a reconciliation exception with named remedies (Section 3.3).

This dissolves `N-314` rather than ruling it: there is no "which authority wins" branch to write,
because there is one relation with a declared rank. It also removes the need for `bank_import:X-gh`
to be *a rule* -- what remains there is the *write* (a confirmed import's anchor becomes a level
row), which is mechanical.

The OPENING stays a separate, constitutive fact (`budget.account_openings`, shipped at `X-f3c-2a`
under `R-GX`/`R-HE`). It is the seed the fold starts from, not an observation of it, and merging the
two would undo a shipped ruling for no gain.

### 3.3 A discrepancy is resolved by naming its cause -- never by a plug

Four causes, four doors, and **all four already exist**:

| cause | measured on production | remedy | state |
|---|---|---|---|
| the opening is wrong | `e_o = -$2,493.47` | restate the opening | shipped, `59b485df` (`X-f3c-2b-2a`) |
| movements missing or wrong | `e_m = +$4,270.78` | match, or mint from the bank line | shipped (`statement_match`) |
| the observation is wrong | `e_a = $0.00` | supersede it | shipped, append-only (`X-f3c-2c`) |
| genuinely unidentifiable | what remains | an explicit accepted movement | `mint_uncategorized`, shipped |

The automatic `account_trueup` plug goes away.
**`N-171` is then deleted by construction rather than booked**, which is what `X-f3c-4` was trying
to do the hard way.

**`X-f3c-4` becomes small and honest.** It is the fourth row above, offered where the bank has NOT
read -- because that is the only place unidentified movement can hide -- and refused over reconciled
days, naming the true-up or restatement door instead. That is the audit's Option 1, and it is the
only reading under which the act and its evidence point the same way.

**The acceptance act must NOT get its own writer.** `statement_match/_uncategorized.py`'s
`mint_uncategorized` is already the single door for "the row a bank observation requires and the
books do not hold", with two callers (`_variance.mint` under `R-GD(i)`, and
`_income.record_income_from_line`). Its own docstring names the reason:
*"this package's own root cause is a money rule spelled twice."* `X-f3c-4` is its third caller.
Whether it moves out of the `statement_match` package is a placement question, not a design one.

### 3.4 The bank is the default source of movements

The owner already does not type most purchases; they roll into the true-up. Importing does it for
him and yields **more** information for **less** typing: real posting days, real amounts, merchant
strings, and the bank's own categories. Over the measured span those categories are Shopping (120
lines), Food & Drink (35), Transportation (13), Utilities (11), Services (6), Home (7),
Entertainment (12), Financial Services (29), Income (20).

The true-up then becomes what it should be -- a check that reads *agrees*.

### 3.5 What does NOT change

The grid, the pay-period clock, envelopes as budgets, the two-year projection, the recurrence
engine, the double-entry ledger underneath, and the `Routes -> Services -> Models` boundary. The
planning surface is what Shekel gets right. What changes is that
**ticking a row stops being how reality enters the app**.

---

## 4. What the movement unification would cost

Measured on this branch, 2026-09-01.

### 4.1 Application code

**`is_envelope` is the WRONG PROXY for this refactor's size, and 80 is not an estimate of it.** The
raw counts are exact -- 62 Python sites across 28 modules, 18 in templates -- but
**only 19 of the 62 are executable**; 43 are prose. Actual branches on the flag: **four**. All five
sites in `transaction_service/_settle.py`, called "the heart of the change" below, are COMMENTS; its
real predicate is `if not txn.tracks_purchases:`.

**On the right token** (`tracks_purchases`) there are ~14 executable Python sites in modules the
table below never lists.
**And the layer the design actually rewrites is invisible to both censuses**: `balance_at` and
`cash_ledger` are **17,125 lines with ZERO occurrences of either token**. The union blast radius --
`is_envelope`, `tracks_purchases`, `TransactionEntry`, `settled_amount`, `settled_on`, `status_id`,
`credit_payback_id` -- is **187 files in `app/`, 152 of which never mention `is_envelope`.**

The table below is kept because its counts are exact and its shape is informative,
**not because it sizes the work.**

| area | sites | what they do |
|---|---|---|
| `app/schemas/validation/` (3 files) | 17 | form validation branching on whether a row takes entries |
| `app/models/` (4 files) | 8 | the column, `tracks_purchases`, relationships |
| `app/services/transaction_service/_settle.py` | 5 | the two settle paths -- **the heart of the change** |
| `app/routes/templates/crud.py` | 4 | template create/edit |
| `app/services/carry_forward_service/` (3 files) | 5 | envelope carry-forward |
| `app/services/statement_match/` (4 files) | 5 | candidate/offer shaping |
| `app/services/reconcile_service/` (3 files) | 3 | reconcile panel rows |
| `app/services/recurrence_engine/` (2 files) | 3 | generation |
| `app/services/entry_service/` (2 files) | 3 | the entry doors |
| `app/routes/transactions/`, `app/routes/_form_errors.py` | 5 | gates and mutations |
| `app/services/dashboard_service/_bills.py` | 2 | bill listing |
| `app/ref_cache/_accessors.py` | 1 | |
| templates | 18 | grid row macros, full-edit form, mobile |

### 4.2 Tests

**This is the dominant cost, and it is a feature rather than a problem.**

- **422 `is_envelope` sites across 63 test files**;
- **62 test files reference `TransactionEntry` / `transaction_entries`**;
- the suite is 399 test files, so roughly **one test file in six** touches the concept directly.

Those tests are the reason the change is safe to attempt at all. They are also why it cannot be done
in one commit.

### 4.3 The data migration -- NOT small, and forbidden as a migration

**Two errors, and the document contained the evidence against itself.**

**(a) The predicate was a dead column.** The rule was keyed on `transactions.is_envelope`. The app's
own single source of truth is `Transaction.tracks_purchases`, which
**defers to the TEMPLATE's flag** whenever `template_id` is set -- so every recurring envelope
(Groceries, Gas, Kayla's Spending Money) reads `is_envelope = FALSE` on the row and is an envelope
anyway. Measured on a production restore migrated to `dev`'s head:

| | rows |
|---|---|
| `transactions.is_envelope` (the wrong predicate) | 4 |
| `tracks_purchases` (the right one) | **238** |
| settled | 196 |
| the old rule's migration set | 193 |
| not purchase-tracked, needs one movement | 156 |
| purchase-tracked but holding no entries | 9 |
| **rows genuinely needing a movement** | **165** |
| rows that already have movements | 31 |

The old rule would have **over-minted 30 rows worth `$7,680.86`**, double-counting against entries
that already exist, and under-minted **`$208.15`** -- which is exactly Section 1.3's
*"envelope's own leg"*. The document measured the money its own migration rule drops and never
reconciled the two sections.

**(b) A migration may not do this at all.** Ruling **`balance:R-HJ`** (developer, 2026-08-28): *"**A
DATA REPAIR IS PERFORMED THROUGH THE APP'S OWN DOORS, NEVER BY A MIGRATION WRITING MONEY ROWS.** ...
A question that only exists because you are writing raw SQL is evidence you are writing it in the
wrong place."* Section 4.3 proposed a migration writing 165 money rows the cash fold reads. The
ruling was never cited.

**So the cost is not a migration at all**, and the rows that make that expensive are named: of the
165, **14 carry no `settled_on`** and **38 are transfer shadows** bound by the four Transfer
Invariants. Those are 52 design questions, not a `WHERE` clause -- which is the same conclusion
`R-HJ` reaches from the other direction. Section 4.5's re-cut routes them through the settle door,
where a human answers them.

**And the DOWNGRADE was never addressed**, which `CLAUDE.md`'s Definition of Done item 7 requires.
After the change a plan row may be covered by *N* movements and a movement may have no plan row at
all; the downgrade has one `settled_amount` and one `settled_on` per transaction, so it must sum the
movements (losing every date) and has nowhere to put a parentless one. It is value-lossy the moment
the feature is used. Two revisions already in the release REFUSE their downgrade; this one would
have to do the same, explicitly, rather than silently lose money records.

### 4.4 Honest risks

1. **Transfer shadows are 248 of 926 rows** and carry four CRITICAL invariants (`CLAUDE.md`). A
   shadow's movement is the parent transfer's, and getting that wrong breaks invariants 3 and 4.
   `transaction_service/_settle.py` already records that an adversarial review settled one leg of a
   pair through exactly this seam.
2. **The envelope's own leg.** Three facts in the measured span are an envelope's own delta rather
   than a purchase's (`-$208.15`), and `balance_at._cash_periods._budget_legs` reads a partially
   spent envelope at its whole cost -- the spent part as movements, the rest as a reservation. The
   refactor must preserve that reading exactly or the grid's subtotals move.
3. **`credit_payback_id` / `is_credit`** on entries, and the `Credit` status, are a second axis the
   unification has to carry rather than flatten.
4. **`pay_period_id`.** A purchase takes its parent's, deliberately. A movement with no plan item (a
   bank line nothing was planned for) has no parent to take it from, and
   `ReviewScope.period_holding` is the resolver that must answer instead.

### 4.5 Decomposition -- RE-CUT, by provenance rather than by row kind

**The first cut was wrong in two ways.** Leaf 1 was not additive: the real column diff between a
movement and a transaction is **21 columns**, not four, and it includes `scenario_id`, which the
fold REFUSES a mismatch on. Leaf 2 -- *"the fold reads movements for one row kind at a time"* -- has
no row-kind axis to switch: the fold's predicate is kind-blind and consumed at 66 sites across 17
modules, so that leaf would have to ERECT a discriminator in the one layer the concept has never
reached, run two balance semantics live on one account, and have leaf 5 delete it again. That is the
defect this arc exists to remove, shipped on purpose.

**The re-cut, taking Section 4.3's measurement seriously:**

1. **`tracks_purchases` DERIVES through one accessor from the template's flag** -- ruled
   `balance:R-JQ` on 2026-09-03, correcting this list's *stored column, template-derived backfill*,
   which was the cache `R-IY` deletes. Green, no fold change -- and it makes Section 4.3's 30-row
   error unconstructible.
2. **Entries gain the full movement column set, `scenario_id` included**, backfilled from their
   parent. Additive; downgrade is a column drop.
3. **`settle_from_entries` becomes the ONLY settle path.** The MANUAL branch writes a single
   covering movement **through the service door**, which is what satisfies `R-HJ`, for the 205
   purchase-tracked rows holding no entries and the untracked ones. The fold still reads
   transactions.
   **This is where the 14 dateless and 38 invariant-bound rows get answered by a human at a door**,
   not by a `WHERE` clause.
4. **The fold re-points to movements in ONE commit, for every kind at once** -- because after leaf 3
   every settled row has exactly one covering movement, so `opening + SUM(movements)` is an identity
   provable against the pre-state. No semantics straddle.
5. **Delete `is_envelope`, `tracks_purchases`'s branch sites and the template sites.**

The costly, irreversible act is leaf 3, and it is a service-door write with a rehearsal. The fold
flip is a re-pointing graded by a before/after equality.

---

## 5. Production, the release, and the import

**The developer's question: when does production need the release and the import?**

**Three different numbers measure three different questions, and a reader must not collapse them.**

| endpoint | revisions | what it answers |
|---|---|---|
| production -> `release/2026-09-01` (cut at `5108de8c`) | **30** | what the release actually ships |
| production -> `origin/dev` today (`f0018d86`) | **31** | how far behind production has drifted |
| production -> this branch's head | 28 | nothing shippable -- a stale sibling of the fork |

**The release number is 30 and merging into `dev` cannot move it**, which is the whole point of
cutting a release BRANCH rather than opening a `dev -> main` pull request. `C4-c`'s `b7a41e2c9d63`
is what took `dev` to 31 after that cut.

Production's live stamp is `a4c6f1d92b73`, read from the running `shekel-prod-db` container on
2026-09-01 rather than quoted; that revision is `origin/main`'s single migration head, so
**production sits exactly at `main`**. `main` carries 140 migration files and `dev` 170, and
restoring the live dump and migrating it applied **30** revisions, landing on `c9a4e7b21d58`, and
re-taken after `C4-c` merged it applies **31**, landing on `b7a41e2c9d63`. Three independent
derivations agree at each point. The earlier "28" was production to THIS BRANCH's head -- and that
branch is **37 commits behind `dev`**, which is also why every rank read from its `steps.md` is one
high.

*(The alembic chain FORKS at `e2d7a94f61c3`: this branch's `31bb08f73e50` and dev's `f1c8b3d5e920`
are siblings. Adding this branch to a release produces two heads and a REFUSED `flask db upgrade`
until the second lander re-parents. A release cut from `dev` alone is unaffected.)*

### 5.1 What is and is not money-neutral

| act | moves money? | evidence |
|---|---|---|
| `statement_import.record_statement` | **no** | its only three `session.add` targets are the bank tables |
| the import ROUTE (`import_statement`) | **yes, once rules exist** | it commits `record_statement` AND `file_new_swipes` in one unit of work |
| the RELEASE | **yes** | at least `X-f3c-2b-1` legalises rows by moving five openings |

**`file_new_swipes` is not merely a money-moving act bundled into an import.** Its module docstring:
*"**It MOVES MONEY, and it is the only door in the app that moves money without a press.**"* It
turns each new swipe line whose merchant carries a standing rule into a purchase, dated by the bank.

**Today it would file nothing, because `budget.merchant_rules` is 0 rows** (66 merchants exist, zero
rules). **Neutrality is a property of an EMPTY RULE SET, not of the import**, and the first rule the
owner states ends it. So the honest split is: *recording the lines never moves money; the import
route bundles the only unpressed money door in the app; the release moves money regardless.*

### 5.2 What the release is still the gate for

Production has no `budget.account_openings` table at all, so
**the opening cannot be restated there** and `X-f3c-2b-2c` stays blocked. That is unchanged and it
is the real reason to release.

**Testing against dev data cannot answer what a release does to production.** Every rehearsal here
ran against a restore of production instead, and that should be the standing procedure.

**The A/B instrument must be RESET-FREE.** Section 1.5's positive control shows
`cash_daily_balance_series` reporting `0 of 1431` for a `$500` move that landed. A migration ALTERS
EXISTING ROWS -- exactly the class that instrument is blind to -- so a release rehearsal built on it
would grade a money-moving release as neutral. Use `cash_ledger.dated_deltas` or
`balance_at._outstanding._books_balance_at`; neither applies an assertion policy.

**Two revisions in the release invalidated Section 4's original pricing**, and neither was in any
database this document first rehearsed on: `c9a4e7b21d58` makes a transfer shadow's amount DERIVED
(0 of 248 live shadows now store a figure), and `b8e4c1f7a903` changes the entry amount constraint
to `<> 0` so a refund is a negative purchase.

**Section 4 has now been re-priced twice, and the second re-take changed nothing.** Against a
restore migrated to `b7a41e2c9d63` (2026-09-02) every figure is identical to the `c9a4e7b21d58`
reading -- 926 live rows, 238 purchase-tracked, 165 needing a movement, 14 dateless, 38 shadows.
**The reason is worth stating rather than the result**: `C4-c` drops `period_index` and `end_date`
from `budget.pay_periods`, and this census is over `budget.transactions`. A census is only stale
when a revision touches ITS OWN subject, which is a thing to check rather than to assume in either
direction.

*(`C4-c` is nonetheless the same pattern one table over: a pay period's `end_date` stops being
stored and becomes derived. That is the amount-ownership argument of Section 6.1 applied to the
calendar, and it is corroboration that the direction is the project's own.)*

## 6. The order -- the ruled one is WITHDRAWN, and a cheaper one replaces it

**Section 6's original order was ruled by the developer on 2026-09-01 and is withdrawn by its own
author the same day.** Two findings make that unavoidable rather than advisable:

1. **The plan gate REFUSES it.** Inverting `#5`/`#6` was demonstrated against a mirror of
   `tools/plan_gate` and fails **three arms** -- `X-f3c-5` would be ranked ahead of the unshipped
   step it is `blocked by`, `X-f3c-2b-3`'s `starts` would precede its blocker, and the order table
   would no longer be sorted into execution order.
   **An approved order the gate will not accept is not a plan.**
2. **It front-runs a family nobody has started while a cheaper family is already in flight**, and it
   moves the ENTIRE credit-card arc from "behind one step" to "behind a schema refactor". `X-f3c`
   transitively blocks **29 open ranked steps -- 18 of them the whole credit_card arc** -- against
   the standing developer priority of 2026-08-19 (*bank_import to production, then credit_card*).

### 6.1 The replacement

**It is largely a RE-RANK of rows that already exist rather than an insertion**, which is a real
argument beside the technical one:

| # | what | why here | new rows? |
|---|---|---|---|
| 1 | release, import, restate the opening to `$3,182.63` | removes **`$2,493.47`** of `e_o` now; owned by a separate session | none -- operator |
| 2 | the amount-ownership cutover: `X-au-k` `#10`, `X-au-d` `#11`, `X-au-e` `#12`, `X-au-f` `#17`, `X-au-h` `#18` | **already ranked, four of five `starts=NOW`** | **none -- a re-rank** |
| 3 | the level relation, reshaped per 6.2 | ruled; independent of the movement work | 1-2 |
| 4 | the movement unification, re-cut per 4.5 | priced against the POST-cutover column set | 5 |
| 5 | the one-time manual reconcile | done once, into the right shape | 1 |
| 6 | THE FLIP | by then it moves nothing | re-point |
| 7 | `X-f3c-4`, re-specified per 3.3 | almost nothing left to do | re-point |

**Why the cutover goes SECOND, and this is the strongest single argument in the document:**

- `X-au-e` NULLs `estimated_amount` on **511 template rows** and `X-au-d` on 51 salary rows. A
  movement table built before them copies figures three ranked steps are about to delete.
- **`X-au-f` makes Transfer Invariant 3 STRUCTURAL rather than maintained.** Transfer shadows are
  the single largest named risk in Section 4.4 -- 38 of the 165 rows needing a movement. Running
  `X-au-f` first REMOVES that risk instead of managing it.
- `c9a4e7b21d58`, already on `dev`, has begun the same cutover: 0 of 248 live shadows still store a
  figure. The family is in motion.
- `X-au-g` ticks with `#16`, which **another balance session is building right now**. Sequencing the
  cutover forward finishes work already in progress rather than opening a second front.

**What it costs, stated plainly.** The credit-card arc still waits, and so does THE FLIP. This order
is not faster to the flip; it is cheaper to sequence, it lands the `$2,493.47` immediately, and it
builds the movement table against derivations rather than against stored figures that are being
deleted underneath it.

**It also creates a work-in-progress hazard the coordinator must manage**: both balance lanes would
be inside `X-au-*` at once, and `X-au-d`/`X-au-e`/`X-au-f` are explicitly DELETION steps. A module
list accompanies the recommendation.

### 6.2 The level relation still needs re-ruling

- **`R-GW`** (developer, 2026-08-27, on measurement):
  *"the residual is RECORDED before the reset is DELETED."* Rows 6 and 7 above delete the reset
  before the residual act exists. The proposal's own answer is that after a full reconcile
  **there is no residual left**, so R-GW's premise dissolves -- but that argument was never made,
  and the ruling is the developer's to dissolve or keep.
- **Section 3.2's "one append-only relation" is not implementable as written.**
  `budget.account_anchor_history` is append-only at the DATABASE tier -- a `BEFORE UPDATE` trigger
  that raises, plus `REVOKE UPDATE, DELETE`. The bank's anchor is **RELEASED BY AN UPDATE**
  (`release_anchors_from` nulls `balance_effective_on` and `balance_evidence_id`), and that release
  exists because two adversarial reviews reproduced a `$150.00` hole reported as covered.
  **The fix is more correct than the original**: a release becomes an APPENDED superseding row
  rather than an in-place update, which also preserves the audit trail the UPDATE destroys. But it
  makes 3.2 a larger change than "move two tables into one", and 3.2 is the part that was ruled.

## 7. What this deletes

The developer's standing instruction is to eliminate fences, checkers and allowlists in favour of
structural code. What this design removes, as opposed to guards:

- **the `account_trueup` equity plug** -- no longer written, so `N-171` needs no booking act;
- **the assertion RESET** -- `R-FO`'s five-way dispatch loses its PLAIN arm honestly rather than by
  the flip's fiat;
- **`is_envelope`** -- 80 branch sites, and with it the two settle paths in one door;
- **`N-314`'s ruling** -- replaced by an evidence rank in a schema, so no code branches on which
  record wins;
- **`R-GY`'s offer gate in its current direction** -- the gate stops being a fence on an act and
  becomes the flip's own per-account readiness measure, which is what `SpanAgreement.reconciles` is
  actually good at.

What it does NOT delete, and should not: the books-boundary triggers (`X-f3c-2b-1`, `2b-2b`, `2d`),
the append-only refusals, and the transfer invariants. Those make states unstorable, which is
structure, not a fence.

---

## 8. Open questions

1. ~~Is the movement unification approved?~~ **Approved 2026-09-01** as part of Section 6's order.
   What is now open is Section 4.5's RE-CUT, which is a different decomposition from the one
   approved.
2. ~~Does the reconcile happen after the restructure?~~ **Answered: after.**
3. **Is the replacement order in Section 6.1 accepted?** The ruled order is withdrawn; the gate
   refuses it. This is a fresh decision, not a revision.
4. **`R-GW`** ruled the residual is recorded before the reset is deleted. Does a full reconcile
   dissolve its premise, or does it still bind (Section 6.2)?
5. **`R-IQ`**: rationale refuted, mechanism sound and load-bearing for the `-$2,493.47`. Amend into
   a standing repair prompt, withdraw, or keep as a gate?
6. **`R-IP` ruled the acceptance act is a TWO-armed door** -- mint OR reprice. Section 3.3 specifies
   only the mint, and Section 3.1 would delete the columns (`settled_amount`, `settled_basis_id`,
   `honoured_correction`) that R-IP's freeze predicate reads.
   **Section 3.1 silently repeals half a ruling taken the same day.** How is the reprice arm
   written?
7. **The INVERTED gate is unsafe as specified.** `statement_imports.period_start`/`period_end` are
   min/max of the file's LINE days, so coverage is systematically UNDER-claimed (**N-434**). Under
   the current gate that error is safe -- it refuses spans the bank covered. Inverted, it means days
   the bank DID read are reported unread, so the act would book an expense there. Does the inversion
   wait on N-434, or does the gate need a different shape?
8. **`R-GL`** sequences `bank_import:X-gh` after the cutover. Under Section 3.2 that reason may no
   longer bind.
9. **`R-FN`'s "categorisable later"** rests on a settled row being re-categorisable, and
   `category_id` is locked. Nobody has specified the revert-categorise-re-settle path.
10. Is a single 30-revision release acceptable after a RESET-FREE A/B rehearsal, or should it split?

---

## 9. What three adversarial reviews refuted, 2026-09-01

Three neutral reviewers ran independently against the measurements, the design, and the cost and
order. **The direction survived; a great deal of the evidence did not.** Recorded here rather than
silently corrected, because the pattern matters more than any single figure.

**Refuted, and corrected above:**

| claim | what was wrong |
|---|---|
| "0 of 1,431 account-days moved" as the money-neutrality proof | the harness is pinned by the assertion RESET; a `$500` injected move read `0 of 1431` |
| "the import is money-neutral" | true of `record_statement`; the ROUTE bundles the only unpressed money door in the app |
| the migration set of 192 rows | keyed on a dead column; the real predicate is `tracks_purchases`, the set is 165, and `R-HJ` forbids a migration doing it |
| "80 branch sites" as the refactor's size | 19 of 62 are executable, 4 are branches, and the 17,125-line fold layer contains neither token |
| "one test file in six" | one in two on the real surface -- and the suite cannot grade `e_m` at all |
| the five-leaf decomposition | leaf 1 not additive (21 columns, not 4), leaf 2 has no axis to switch and would run two semantics live |
| "71 of 74 compared days disagreeing" | welded from a different, shorter span; over this span it is 98 of 101 |
| the `-$1,850.98` explanation | BOTH terms moved, not just the assertion |
| the shape table's envelope rows | classified on the inert column; 37 facts misassigned |
| "the bank's closing is known for every day" | stated for 71 of 155; the rest derived or carried |
| "the identity closes to the cent" as corroboration | the bank terms cancel; it holds against a fabricated bank series |
| the ruled ORDER | refused by the plan gate on three arms |

**Attacked and NOT broken:** the algebra of `difference = e_a - e_o - e_m`; both books-boundary
triggers, with firing positive controls; `opening + SUM(dated_deltas)` IS `_books_balance_at`, 155
days, 0 mismatches; no plan-tier contamination of the compared window; the bank export dedup,
reproducing `$933.79` to the cent; no transfer-shadow double count; the `transaction_entries` hole
closed; `record_statement` writing exactly three tables; every Section 1.5 figure; and
**`e_m = +$4,270.78` with THE FLIP right on 0 of 155 days** -- the two claims the argument rests on.

**The lesson worth keeping.** The blind harness is the one that matters: a gate shipped with no
positive control, presented as the strongest evidence in the document, and unable to fail for
exactly the reason the document was arguing about. It was found only because a reviewer was told to
construct a control and try to make it fire.
