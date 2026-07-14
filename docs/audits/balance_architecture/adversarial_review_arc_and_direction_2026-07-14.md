# Adversarial review: the balance arc, its design, and where to go from here

**Written 2026-07-14, against `dev` @ `3e7bdc0a`.** Commissioned to answer one question:
*"Am I chasing my tail?"*

**Answer: Yes -- and the mechanism has a name, and the exit is nearer and far cheaper than the audit
you just wrote believes.** This document does not re-find the bugs in
`audit_loan_balance_producers.md`; that register is largely sound and I confirm its worst findings
independently. It reviews the DESIGN and the DECISIONS: whether this is DRY, SOLID, normalized,
robust, maintainable for a solo operator, extensible, financially correct, and whether it is what you
would build from scratch.

**Everything below was RUN, not read.** Where I claim a number, the probe that produced it is cited.
No production data was touched; the dev clone is unchanged (verified by row counts before and after,
and every write probe ran inside a rolled-back transaction).

**The probes live in this session's scratchpad** (`probe_fold.py`, `probe_fold3.py` -- the corrected
fold, `probe_perf.py`, `probe_dates.py`, `probe_b1.py`, `probe_b3.py`, `probe_b15.py`,
`probe_cash.py`). They are throwaway by the project's own convention and are not committed; copy any
you want to keep before the scratchpad is cleared. **`probe_fold3.py` is the one that matters** -- it
is the recommendation, in 60 lines, and it is the starting point for F3.

---

## 0. The short version

Three statements, each proven below.

1. **The "one fold" the audit says must be BUILT already exists in your codebase.** It is
   `loan_posting_service/_walk.py::_replay_events` (`:374-415`). It seeds a balance at zero, walks a
   merged chronological event stream, applies each anchor as a reset and each payment as a split,
   with ONE running balance. It runs only at WRITE time, and it **throws its running balance away.**
   I re-ran it at READ time in ~60 lines. It reproduces your developer-confirmed baseline to the cent
   and matches the seam on **every single day of both real loans' history -- 111 days, zero
   mismatches** -- with no ledger read, no `is_originated`, no `owed_from`, no `projection_seed`, no
   splice, and no fail-loud. It is also **6.6x faster** than the machinery built to avoid it.
   **Section 2.**

   **Read Section 2 even if you read nothing else.** My *first* version of that fold passed a 14-day
   sample with a perfect score and was **wrong by $178,103.41 on 22% of days.** I reproduced this
   arc's exact failure mode, in my own work, within an hour -- and the only thing that caught it was
   going to look for a discrepancy no test would have found. That is the clearest possible statement
   of what you are actually up against, and it is not a discipline problem.

2. **The tail-chasing has a single mechanical cause: the balance function is PARTIAL by accident.**
   `confirmed_loan_balance_at` returns `None` when the ledger has no opening. Every piece of
   machinery this arc has built -- `LoanLedgerNotOpenedError`, `is_originated`, `owed_from`,
   `projection_seed`, `loan_ledger_domain`, "the two kinds of zero", the splice, `is_retired` vs
   `is_paid_off`, and the 100-entry pylint fence -- exists to manage that partiality. **A loan's
   balance at T is always knowable from data you already have.** The partiality is not a property of
   the domain; it is an artifact of asking a derived cache a question the source could answer. Every
   new rule added to manage it creates a new predicate, and every new predicate is a new place for
   the next commit to go wrong. That is the tail. **Section 3.**

3. **One line of code generates most of the register.** `_walk.py:357` drops loan anchors dated after
   the sync's wall clock, so **what gets persisted depends on when the sync ran.** The same file, 130
   lines away, states the correct rule for payments and follows it -- *"posting early changes when
   the fact is RECORDED, never when it is SHOWN"* (`_walk.py:237-252`) -- and says it was learned
   from a real $1,636 defect. The cash ledger (`account_posting_service`) already does it right: its
   walk takes no `as_of` at all. **The loan walk is the only place in the app where a user-supplied
   future date meets a clock-bounded filter.** **Section 4.**

**What this is NOT.** It is not "throw away the arc." The `balance_at` seam is correct, the
ledger-authoritative read switch was the right instinct, and the double-entry ledger stays. The
correction is that the read switch switched to the wrong **layer**: it made a *derived cache* the
authority instead of the *source events*. The fold is the same principle -- the past belongs to
recorded facts, not to the schedule -- applied one level deeper, where it is total instead of
partial.

### And one thing neither of your documents contains

**Your CHECKING account is wrong by $1,331.26 today, and two pages of your app already disagree about
it.** The dashboard and grid say **$1,979.39**; the /analytics balance sheet, reading the posting
ledger, says **$648.13**. Nothing in the codebase asserts they must agree. The cause is the *same
disease* the loan arc has spent six months on -- a hand-asserted anchor, a projection that ignores
the confirmed event stream, and a ledger holding the truth that no reader is wired to -- and **the
cash side has never been audited.** **Section 6.** Two months of work went into the loans while the
larger, simpler account quietly drifted.

---

## 1. Method

The audit's own thesis is that reading is not proof, so I held it to that standard too.

| lens | what it did |
|---|---|
| trace | Read the seam, the kernel loan cluster, `account_projection`, the walk, the reader, the resolver, `resolution_context`, and the checker, in full. |
| prove the time bomb | Drove a future-origination loan through the **production write path** on the dev DB, advanced the clock, and read every surface. Rolled back; row counts verified identical. |
| census | Enumerated every balance-at-T producer for **every** account kind, mapped each against what the fence can actually see. |
| mutation | Applied the audit's claimed-vacuous mutations to real source in an isolated worktree and ran the suites. |
| **prototype** | **Built the recommended fold and ran it against real data.** This is the load-bearing evidence, and it is the one thing the audit did not do. |

---

## 2. The finding that reframes the whole arc: the fold already exists

The audit's Section 5 recommends *"a loan's balance is a fold over its event stream, there is exactly
ONE fold, and nobody else may answer"*, and its S1 proposes to BUILD a "dumb, obviously-correct
reference fold" as an oracle, costed as a significant new arc.

**It is already written.** `loan_posting_service/_walk.py`:

```python
def _replay_events(events, periods, escrow_lines):     # :374
    balance = _ZERO_MONEY
    for is_anchor, item in events:
        if is_anchor:
            anchor_corrections.append(LoanAnchorCorrection(anchor=item, owed_before=balance))
            balance = item.anchor_balance              # a true-up RESETS
            continue
        payment_escrow = escrow_calculator.escrow_monthly_as_of(...)
        split, balance = _split_one_payment(item, balance, periods, payment_escrow)
        payment_splits.append(split)
    return payment_splits, anchor_corrections           # <-- the running balance is DISCARDED
```

That is the fold, exactly as specified: origination/true-up reset, payment reduces, one running
balance, chronological. It is used only to generate postings at write time, and the balance it
computes is thrown away. The app then **reads that balance back out of the postings it generated** --
a fold over the derived artifact instead of the source.

### The prototype -- and the mistake I made building it, which is the most important thing here

I rebuilt the fold as a read-time function using only primitives that already exist
(`loan_loaders`, `resolve_periods`, `_split_one_payment`, `escrow_monthly_as_of`). Two changes from
`_replay_events`: **no `as_of` filter on anchors**, and it **keeps** the running balance as a step
function.

**Version 1 sampled every 14 days and reported this:**

```
=== Mortgage (account 3) ===          === Van Loan (account 8) ===
  events in confirmed fold : 6          events in confirmed fold : 8
  FOLD   balance today     : 177277.97  FOLD   balance today     : 15663.59
  SEAM   balance today     : 177277.97  SEAM   balance today     : 15663.59
  BASELINE (developer)     : 177277.97  BASELINE (developer)     : 15663.59
  worst past divergence    : 0.00       worst past divergence    : 0.00
```

Baseline reproduced to the cent. Zero divergence from the seam on every probe date, both loans. **I
was about to recommend it on that evidence, and it was wrong.**

A loan payment has **two dates** -- its installment `due_date` and its pay-period (cash-basis) date --
and my fold used the due date for *both* sequencing and visibility, while the ledger reader bounds
visibility by the **pay-period start** (`_asof.effective_date()`). On your real data those differ by
**up to 11 days on every single payment**:

```
Mortgage (payment_day=1)      due_date     period.start   delta
                              2026-06-01   2026-05-21      11d
                              2026-07-01   2026-07-02      -1d
```

My 14-day sample simply never landed inside one of those windows. **Probing every day instead:**

```
                                   days   result
  v1 (due-date visibility)          117   26 MISMATCHES, worst $178,103.41
  v2 (naive period-start)           117   64 MISMATCHES
  v3 (the reader's own rule)        111   PERFECT -- 0 mismatches, both loans
```

**v3 is the answer**, and it is only correct because it replicates the reader's *exact* bounding rule:
anchor visible on `LEAST(anchor_date, containing period.start)`, payment visible on
`pay_period.start`. Order by the **event** date; bound by the **visibility** date. That two-date
structure is already in your codebase (`_merge_anchor_and_payment_events` sequences by due date;
`_asof.effective_date()` bounds by period), and any fold **must carry both** or it is simply a second
bounding policy -- the very class of divergence this arc exists to end.

**Read that sequence again, because it is the whole review in miniature.** I built the right
abstraction, sampled it, got a perfect score against the incumbent, and was wrong by $178,103.41 on
22% of days. Nothing caught it but going and looking for a date semantics difference I had noticed in
the code. **That is precisely how C3 shipped $197,049.32 and how B-4's $47,120 mutation left 1,369
tests green.** It is not carelessness -- it is what happens when a system's correctness rests on
hand-matched boundary rules and the oracle is a sample rather than an exhaustive check.

The lessons, which apply to your F3 oracle and not just to me:

* **Sampled agreement is not agreement.** Probe **every day** and **every shape**, or you are testing
  your luck.
* **The fold is not free.** It is `_replay_events` plus the reader's effective-date rule. Three more
  lines, and they are load-bearing.

With that correction: **six events. Eight events.** That is the entire financial history of your
mortgage as the app models it. The seam, the postings, the splice, the two kinds of zero, the
fail-loud and the 100-entry fence all exist to answer a question that a six-element fold answers
exactly, on every day, in one pass, with no failure mode.

### It is TOTAL, and that is the whole point

The fold cannot return `None` and cannot raise. Asked about a date before any event, it returns
`0.00` -- not as a sentinel, but as the correct fold of an empty prefix. Consequently:

* `LoanLedgerNotOpenedError` has nothing to fire on -- a loan with no postings still has events.
* `is_originated` is not a flag to thread; it is `T >= origination_date`, derived on demand.
* `owed_from` / `projection_seed` / `DebtSchedule` disappear as threaded state.
* The splice disappears: past and future are one stream, not two producers hand-synchronised at a
  boundary.
* "The two kinds of zero" collapses to one kind, which is true.

Every one of those is a predicate this arc invented, tested, fenced, documented -- and shipped a
defect in.

### The forward arm also works, and I measured where it does not

Extending the fold over **projected payment records** (which your app already materializes -- the
Mortgage carries 23 Projected transfers out to 2028-06, the Van Loan 24):

```
--- future: fold(projected records) vs seam(schedule) ---
Van Loan   2026-08-13  fold=15205.63  seam=15205.63   delta=0.00
           2027-07-14  fold=10023.08  seam=10023.08   delta=0.00
Mortgage   2026-08-13  fold=177000.01 seam=176999.67  delta=0.34
           2027-07-14  fold=173835.25 seam=173831.03  delta=4.22
```

The Van Loan matches **exactly at every horizon**. The Mortgage drifts, and the cause is precise and
known: the fold splits the *planned cash* ($1,910.95) using `escrow_monthly_as_of`, which
**deliberately applies no inflation** (`escrow_calculator.py:550`: *"inflation is a forward-projection
display concern only"*), while the seam replays the *contractual* P&I ($1,293.96). The Van Loan has no
escrow, so the two coincide.

**This is not a defect in the thesis; it is the one knob the forward fold must be wired to.**
`project_monthly_escrow` already exists for exactly this. I flag it because it is the sort of detail
that sinks a migration if it is discovered late, and because it is a genuine modelling question you
must rule on (below).

### And the prototype found something the audit missed

The fold reproduced the **false pre-tracking zero** (B-11) too -- and running it showed why, which
reading did not:

```
Mortgage: params.origination_date=2018-12-01  original_principal=202000.00

  ROWS IN THE DATABASE (budget.loan_anchor_events):
    2018-12-01     202000.00  source=origination      <-- EXISTS
    2026-03-31     178375.43  source=tracking_start
    2026-05-22     177829.83  source=user_trueup

  EVENT STREAM the app actually builds (load_loan_anchor_facts):
    2026-03-31     178375.43  is_opening=True  tracking_start=True
    2026-05-22     177829.83  is_opening=False
```

**The origination event is in your database and is excluded from the event stream.**
`loan_loaders._opening_anchor_fact` (`:108-154`) returns *tracking-start, else origination* -- so for
a mid-life import the origination row is simply never an event. That is why every producer that walks
this stream reports your mortgage as **debt-free for the whole of 2018-2026.**

The audit frames B-11 as *"the map emits a false zero and C1b built `loan_ledger_domain` and did not
wire it in"* -- a consumer forgot a clamp. **The truth is one level down: the event stream is
missing an event that is sitting in the table.** `loan_ledger_domain`, `tracked_from`, and the
whole "two kinds of zero" doctrine are machinery built to manage a hole the app dug itself.

---

## 3. The root cause, one level below the audit's

The audit's root cause:

> *"What does this loan owe at time T?" has TEN implementations, and the app's answer has been to pick
> one as authoritative and build a pylint fence to stop the others being called.*

That is right, and it undercounts. An independent census found **46 balance-at-T producers across all
account kinds, 17 of them outside any fence**; for loans alone there are **22 reachable ways** to
obtain a balance, not ten -- but only **six distinct algorithms**. The audit's list mixes four
different *tiers* -- an algorithm, a function, a DTO field (`AmortizationRow.remaining_balance`,
`LoanState.current_balance`), and a DB column (`accounts.current_anchor_balance`).

**That mixing is not sloppiness. It is the finding.** "Producer" has no structural definition in this
codebase: a balance is sometimes a function you *call*, sometimes a field you *read*, sometimes a
column you *load*, sometimes a Jinja variable you *render*. The number is unknowable **because the
invariant is not typed.**

So the sharper root cause is:

> **The app has no type for "a balance". It has a `Decimal` that anyone can compute, copy into a DTO,
> store in a column, or render in a template -- and a linter that can only see the first of those four.
> Every producer is a place the answer can differ, and the app's response has been to add a rule, a
> flag, or an allowlist entry each time one is found.**

And the reason there are so many producers in the first place:

> **The one honest producer is PARTIAL.** `confirmed_loan_balance_at` answers `None` for an unopened
> ledger and RAISES for a future date. A partial function cannot be the single source, so every
> caller that needs a total answer must compose it with something else -- a projection, a seed, a
> fallback, a flag -- and each composition is a new producer.

Make the function total and the producers have no reason to exist. That is the exit.

---

## 4. The write-time clock: one line, most of the register

`loan_posting_service/_sync.py:139`:

```python
as_of = date.today()            # at WRITE time
```

consumed at `_walk.py:356-359`:

```python
anchors_in_window = sorted(
    (anchor for anchor in anchor_facts if anchor.anchor_date <= as_of),   # <-- THE LINE
    ...
)
```

**Therefore the persisted content of your loan ledger is a function of (your data, the wall clock at
the moment the sync happened to run).** That is not a cache; that is a corruption generator.

### It is worse than the audit's B-1 says

B-1 reports a missing OPENING and a 500. Driven on the **real Mortgage** (read-only), the wall clock
at sync also silently rewrites **every payment split**, because anchors and payments share one running
balance and `_split_one_payment:191` routes all cash to `excess` when `balance <= 0`:

```
Mortgage (acct 3) -- sync as_of = TODAY (what actually ran)
    SUM interest=4078.38  principal=1097.46  escrow=2467.96  excess(refund)=0.00

Mortgage (acct 3) -- sync as_of = BEFORE origination (a sync that ran too early)
    SUM interest=0.00     principal=0.00     escrow=0.00     excess(refund)=7643.80
```

Identical user data. **$7,643.80 of real mortgage cash booked as a Refund Receivable asset, and the
entire Schedule-A deductible interest figure erased.** This is also, exactly, the mechanism behind
B-5 (the balance sheet rendering a **negative** liability of -$7,643.80 with HTTP 200) -- which the
audit files as an unrelated latent finding. It is not unrelated. It is the same line.

### The codebase already knows the right rule and already applies it twice

* **Payments, same walk, 130 lines away** (`_walk.py:237-252`): *"No period-begun UPPER bound.
  Settlement is the confirming event... Both entries carry the payment's `pay_period_id`, so the
  READERS' period bound still keeps an early-settled payment out of every displayed balance until its
  period begins -- **posting early changes when the fact is RECORDED, never when it is SHOWN.**"*
  The docstring records that this was learned from a real ~$1,636 understatement on your Mortgage.
* **The cash ledger** (`account_posting_service/_walk.py:474`): `walk_account_ledger(account_id,
  scenario_id)` -- **takes no `as_of` at all.** Zero clock reads in the entire module. It posts every
  anchor and lets the reader bound.

The readers are already correctly as-of bounded (`_asof.effective_date()`, `bisect_right` on period
starts). **So the rule is written down, proven, and implemented in two of the three places it
belongs. The loan anchor path is the one holdout, and it is the one that produced the outage.**

**This is not a design decision that needs a ruling.** Removing `as_of` from the loan walk makes the
persisted ledger a pure function of your data, matching the cash side. It is the cheapest, highest-value
commit available, and it is a down payment on the fold rather than a band-aid.

*(One correction to B-1, in the audit's favour and against its severity: the container entrypoint
runs `backfill_all_loan_postings()` on every start (`entrypoint.sh:259` -> `init_database.py:285`),
which heals it. So the outage window is `[origination_date, next container restart)`, not permanent.
A stable prod container runs for weeks. It is still an outage fired by the clock with no user action,
and the split corruption above persists silently until that restart.)*

---

## 5. The fence is the wrong category of mechanism

This is the part of the design I would most strongly urge you to stop investing in.

### The cost, measured

| | |
|---|---|
| `balance_seam.py` | 681 lines |
| ...of which **actual matching logic** | **53 lines** |
| ...allowlist **data** | 138 lines |
| `loan_balance.py` (W9905) | 131 lines |
| checker tests | 1,077 lines, 60 tests |
| **total apparatus** | **~2,136 lines, 100 allowlist entries** |
| **ratio of apparatus to logic** | **40 : 1** |
| module lists a dev must hand-sync when adding a module | **6** |
| planning/audit docs for this subsystem | **11,502 lines** (vs 8,152 lines of the code they describe) |

Three of the allowlist structures (`_ENGINE_CLUSTER_MODULES`, `_LOAN_LEDGER_DEFINING_MODULES`,
`_LOAN_RESOLVER_DEFINING_MODULES`) **are never referenced by the checker at all.** Their only consumer
is a test that asserts the allowlists are complete. That is apparatus guarding apparatus.

### It cannot converge, and the checker's own comments prove it three times

The identical breach has now shipped **three times**, and each fix was "add a name to a list":

1. `balance_seam.py:30-40` -- the loan resolvers were excluded as "rich detail, not a balance."
   *"and that was the hole. `LoanState` bundles the rich detail WITH `current_balance`... and a
   name-keyed fence cannot see an attribute read."*
2. `balance_seam.py:84-96` -- `generate_debt_schedules` was a non-producer until someone noticed
   *"one line (`schedules[a.id].current_balance` in a template context) would have put a balance-at-T
   on a screen with every gate silent."*
3. `balance_seam.py:210-225` -- `BalanceContext.loan` handed out a whole `ResolvedLoan`;
   *"the fence binds on names, and the method was called `loan`, which is far too generic to guard."*

**The leak is a return TYPE. The fence is a call POLICY. No allowlist entry can express "this
dataclass field is a balance."** As the census put it: *a linter can only stop the others being
**called**. Most of them are not called -- they are **read**.*

### Three things that should end the debate

* **The seam exports a balance while its own docstring denies it.** `SecuredLoanSeries`
  (`balance_at/_secured_debt.py:61-68`) says *"It carries no balance"* and carries
  `list[AmortizationRow]`, whose `remaining_balance` (`amortization_engine/_projection.py:220`) **is a
  balance-at-T**. `property_equity_chart` reads exactly that field to draw the debt line -- **wrong by
  $299,701.35** (the audit's B-2). The fence's own flagship remediation leaks.
* **W9905 and W9906 give opposite instructions about the same function.** W9905's message
  (`loan_balance.py:105`) tells you the authoritative balance *"is the resolver
  (`loan_resolver.resolve_loan` -> `LoanState.current_balance`)"*. W9906's header calls that exact
  producer **"the hole."** And a **green test locks it open**:
  `test_allows_resolve_loan_from_consumer` (`test_shekel_checkers.py:1037`) asserts that
  `resolve_loan` called from `app.routes.loan._helpers` is *not* flagged.
* **`accounts.current_anchor_balance` is unfenceable by construction.** It is an ORM column read --
  never a `Call` node -- read as a balance in **15 modules** and rendered raw into templates
  (`grid/_anchor_edit.html:92`, `accounts/form.html:49`, `loan/setup.html:38`). No call-graph checker
  will ever see it. It is also the number the grid renders for your Mortgage (the audit's B-3).

### Naming is being driven by the linter

`BalanceContext.loan` had to be renamed `resolved_loan` **because the AST matcher cannot distinguish
`ctx.loan` from any unrelated `.loan`** (`resolution_context.py:151-160`, a 40-line docstring
explaining this). When the domain model is being reshaped to satisfy a pattern-matcher, the
pattern-matcher has become the architecture. That is a bad trade for a solo developer.

### What to do instead

In order of leverage, and none of these is a linter:

1. **Make the balance a TYPE, not a convention.** The two legitimate notions -- the *cash-flow*
   balance (a transaction running sum: the grid, obligations) and the *kind-correct* balance (net
   worth) -- are both bare `Decimal` today, which is precisely why B-3 can render a loan's cash-flow
   sum as its balance. Distinct types make that a category error instead of a $1,910/month bug.
2. **Make the engine cluster private inside the seam package.** Move `net_worth_kernel`,
   `balance_resolver`, `balance_calculator`, `account_projection`, `net_worth_investment`,
   `daily_balance_series` under `balance_at/` and underscore them. Python's import boundary then
   enforces what 100 allowlist entries currently police, and **~60 of those entries delete
   themselves.** No new toolchain.
3. **Delete or rename `AmortizationRow.remaining_balance`.** It is the single largest leak, it is what
   makes the seam's own export a liar, and **not one** of its four read sites is reachable by any
   allowlist (two are Jinja templates). A schedule table legitimately wants a per-row balance -- so
   the honest API is a seam entry that returns rows already carrying the *seam's* answer.
4. **The fold** removes the *reason* the other producers exist. Under it, a schedule row is a
   *prediction*, and can finally be typed as one.

Keep a thin W9906 as a backstop afterwards. It is a decent smoke alarm. It is not a fire door.

---

## 6. The cash side is the loan problem, unaudited -- and it is live

Both of your documents are about loans. The producer census forced the question: **is the cash side
equally rotten and simply less looked at?** It is. Every number below was driven against the dev clone.

### Your checking account, right now

| producer | says | reads |
|---|---|---|
| dashboard hero / grid (`balance_at`) | **$1,979.39** | the stored anchor + *Projected* transaction sums |
| /analytics balance sheet (`ledger_report_service`) | **$648.13** | the confirmed posting ledger |
| | **delta $1,331.26** | **and nothing asserts these must agree** |

Compare the loans, post-read-switch:

```
acct         balance_at (seam)    ledger postings     delta
Checking               1979.39             648.13   1331.26   <-- nobody reconciles
Mortgage             177277.97         -177277.97      0.00   <-- read switch shipped; ties out
Van Loan              15663.59          -15663.59      0.00
```

**Checking is exactly where the loans were before the read switch:** a hand-maintained anchor, a
projection that cannot see the confirmed event stream, a ledger holding the truth, and no reader
wiring the two together.

### Four independent cash defects, all live

1. **The projection DROPS settled activity after the anchor.** `balance_calculator.py:100-119` seeds
   at the anchor and adds only `sum_projected`, on the comment's theory that *"settled items are
   already in the anchor balance"* (`:105`). That is true only for items dated **before** the anchor.
   On real data, Checking's current period carries **2 Paid expenses totalling $1,923.75** that the
   projection simply ignores. The code *knows* -- it raises `stale_anchor_warning` -- and does nothing
   but show a banner.
2. **The "date-precise" scalar is period-flat, and contradicts its own sibling.**
   `_sum_period_as_of` (`balance_resolver.py:745-753`) deliberately does not filter by `due_date`. So
   `cash_balance_at` (the dashboard hero) and `cash_daily_balance_series` (the analytics calendar) --
   **both in the same seam** -- give different answers for **12 of the 14 days** in the current period
   ($378.53 apart on 2026-07-02). "Balance today" on your dashboard actually means "projected balance
   at the end of this pay period."
3. **Pre-anchor: one producer fabricates, its sibling omits** (the audit's B-18, confirmed exactly).
   `balance_at(Checking, 2026-03-26)` returns **$2,640.16** -- today's balance presented as March's --
   while the per-period map omits those periods entirely. The anchor history proves March's real
   figure was $2,746.58.
4. **The anchor column and the anchor history can disagree, and the same page renders both.**
   `accounts.current_anchor_balance` is a cache of `account_anchor_history`; `resolve_anchor`
   *detects* divergence and only **logs** it (*"The cache is NOT mutated here"*,
   `balance_resolver.py:214-238`). The grid header reads the **column** (`routes/grid.py:290-292`)
   while the body reads the **history row**.

### The honest caveat, which does not excuse any of it

Cash is genuinely **not** the complete-data case loans are: the anchor exists precisely *because* not
every bank transaction gets entered. So a naive "switch cash reads to the ledger" is **wrong**, and I
am not recommending it. But all four defects above are **independent of that completeness question**,
and each is wrong on your real money today.

**This changes the priority order.** A $1,331.26 live error on the account you actually spend from,
plus a dashboard that disagrees with your own balance sheet, outranks several of the latent loan
findings the audit's arc would have you fix first.

---

## 7. What your test suite can and cannot see (mutation-tested, and the audit is WRONG here)

I mutation-tested the suite in an isolated worktree: inject a real defect, run the real tests, report
what goes red. **This partially REFUTES your own audit, in your favour, and it finds a new
five-figure hole the audit missed.**

| mutation | expected | actual | verdict |
|---|---|---|---|
| M1 -- delete the `is_confirmed` filter in `_forward_rows` (audit B-4) | red | **3,891 passed, 0 failed** | **VACUOUS -- confirmed** |
| M3 -- hardcode `is_originated=True` (audit B-17) | red | **236 passed, 0 failed** | **VACUOUS -- confirmed** |
| M4 -- the five named guards, one faithful defect each | red | **all five go RED** | **load-bearing** |
| **M5 -- double-count mortgage interest in the tax deduction (NEW)** | red | **5,741 passed, 0 failed** | **VACUOUS -- not in the audit** |

### The audit oversells the vacuity, and you should know that

`TestScalarAndMapAgree`, `TestBrokenLoanFailsLoud`, `TestLoanNotYetOriginated`,
`TestMultiLoanIsolation` and the cross-page oracle **all bite** under minimal faithful defects. The
oracle pins **value**, not merely agreement -- shifting every surface by a uniform $0.01 still fails
it. Seven further money paths I probed (transfer invariant 3, the net-worth sign convention, the grid
interest-reconciliation identity, the splice boundary, the resolver's confirmed-payment gate,
half-speed amortization, a seed that forgets payments) were **all caught**. Your suite is much
stronger than "the gates prove nothing" implies. Do not tear it up.

### But there is exactly ONE structural blind spot, and it explains every hole

> **No balance-seam fixture contains a loan that was ever actually PAID.**

`TestScalarAndMapAgree::test_every_loan_shape` builds six shapes -- never-paid, trued-up,
mid-life-import, paid-off (via a **$0 true-up**, not payments), not-yet-originated, closing-now. **Not
one has a settled payment.** Its docstring claims "every loan shape the app can produce" and omits the
single most common real one: *a loan somebody is paying.*

That one gap is the whole explanation. With zero confirmed rows in any fixture, **every branch keyed
on `is_confirmed` is a no-op in every test**, so deleting it is invisible. `_forward_rows` sits at
**100% line coverage and is completely untested.** Coverage cannot see this; only a fixture with a
paid loan can.

Two proven consequences:

* **M1** -- on a mortgage with two paid rows and a later true-up, `forward_balance_at_date` returns
  **$199,449.72** against a ledger truth of **$195,000.00** -- a **$4,449.72** divergence. The filter
  exists to fix a real production bug (its docstring cites a live $3.94 divergence) and has **no
  regression test.**
* **M5, which nobody has found before** -- `_income_tax.py:241-245` partitions a loan's yearly
  interest into `confirmed + projected`, and the projected term excludes `is_confirmed` rows *so they
  are not counted twice*. Delete that filter and **5,741 tests pass**. For a $240k/6%/30yr mortgage
  with six settled payments in the year:

  ```
  confirmed interest for the year : 7181.97
  deduction overstated by         : 7181.97   (counted twice)
  phantom tax savings @ 22%       : 1580.03
  ```

  **This is a number a user puts on a tax return**, in the same subsystem as the audit's B-6, with no
  guard at all.

### Two smaller structural findings

* **M3's real mechanism** (which the audit did not identify): the guard
  `test_debt_track_does_not_count_an_unborrowed_loan_as_repaid` has a local `_ad()` helper that
  **hand-rebuilds the production dict** and feeds *that* to the consumer. It tests the seam and it
  tests the reducer, and never the **wiring line** in `_projections.py` -- exactly what M3 breaks.
  `is_paid_off`, the sibling field in the same dict, has 23 guards; `is_originated` has **zero**.
* **A guard living in the wrong file produces a false green.** The year-end unoriginated-loan skip
  (`_net_worth.py:242`) is pinned by exactly one test, and it lives in **`test_balance_at.py`**.
  Delete the guard, run `test_year_end_summary_service.py` + `test_analytics.py`: **154 passed.** A
  developer changing year-end code and running year-end tests ships it.

### The fix is small and it is the highest-leverage test work available

**Add a settled-payment shape to `test_every_loan_shape`.** It kills M1 and M5 at once, and it closes
the class. Then make the `is_originated` guards call `_project_one_account` instead of reconstructing
its output.

**Restated as a rule for the F3 oracle:** *the fixture matrix must contain the shape the feature
exists for.* Every `is_confirmed` branch in this codebase was written to handle a paid loan, and no
test has ever shown it one.

---

## 8. Your questions, answered directly

**Is this DRY?** No -- but not in the way the audit says. The duplication is not ten copies of a
function; it is **one missing abstraction**. Six algorithms answer one question because no type says
"this is *the* balance." Deduplicating the call sites (what the seam did) without deduplicating the
*computation* gives you one door to six answers. The seam is a facade, not a model.

**Is this SOLID?** The seam has a clean dependency direction (consumers -> seam -> engine), and that
is real and worth keeping. But:
* **SRP** -- `net_worth_kernel.py` sits at *exactly* its 1000-line ceiling and holds the loan cluster,
  the investment dispatch, and the cash dispatch. The plan itself notes the next change must extract
  it.
* **OCP is inverted.** Adding a module requires editing up to **6 global allowlists** in a linter.
  The system is *closed* for extension and *open* for modification -- exactly backwards.
* **Dependency inversion is violated by the DTOs.** Consumers depend on concrete bundles
  (`AmortizationRow`, `LoanState`) that carry balances, so the abstraction leaks the very thing it
  abstracts.

**Is this fully normalized?** **No, and this is the most under-acknowledged problem.** Four
stored/derived balances can drift from the facts, and three of them are drifting today:
* `accounts.current_anchor_balance` -- written onto **loan** accounts by an unguarded route. I drove
  the real `PATCH /accounts/3/true-up` with `anchor_balance=1.00`: **HTTP 200**, the Mortgage's stored
  anchor became **$1.00**, the grid then rendered **$1.00**, the loan ledger still said
  **$177,277.97**, and **no `LoanAnchorEvent` was written** (the posting sync inside that path is a
  documented no-op for amortizing accounts). A second, stored, never-reconciled loan balance, one
  click away.
* The **same column vs its own history table.** It is a cache of `account_anchor_history`;
  `resolve_anchor` *detects* divergence and only **logs** it. The grid header reads the column, the
  body reads the history row -- so one page can render two anchors.
* The **posting ledger** -- a derived projection of (params, anchor events, settled cash) treated as
  the source of truth, with **no runtime reconciliation** and **no DB-level invariant** requiring an
  originated loan to have an opening. Denormalization without a reconciliation guard is where drift
  lives, and B-1/B-5 are that drift.
* `LoanState.current_balance` -- copied into DTOs and read by 7 route sites.

**Is this robust?** The fail-loud design converts a *data-integrity* problem into a **500 on five
pages** (`/savings`, year-end, `/debt-strategy`, property, grid). That is a defensible trade *only
because* the ledger can be stale. Remove the staleness (the fold) and the raise has nothing to fire
on. **Today you have a design whose safety mechanism is a page outage, protecting against a
corruption its own write path creates.**

**Is this maintainable for a solo developer?** **No, and this is the finding that should drive your
decision.** The evidence is not opinion:
* **11,502 lines of docs for 8,152 lines of code** in one subsystem.
* **42 commits** to the balance core in three weeks.
* **Every commit's adversarial review found defects that commit created.** C2 created three. C3
  created one worth **$197,049.32** -- and passed `pylint app/` 10.00 with zero findings and a
  7,387-test green suite.
* The gates cannot see the defects: they are predicate errors, and predicates are not what linters
  and agreement-invariants check.

A system where a careful, disciplined change has a high probability of introducing a five-figure
financial defect that all gates pass is, by definition, not maintainable -- however good the
discipline.

**Is it future-proof / extensible?** No. A sixth account kind means a new boundary rule in the
dispatch, new allowlist entries, and a new set of "which zero is real" questions. A new surface means
choosing among 46 producers correctly.

**Is it financially correct?** The **loan seam** is (17 shapes agree, and my independent fold confirms
it to $0.00 on every past date for both real loans). **Everything around it is not.** Right now, on
your real data:

| surface | says | truth |
|---|---|---|
| grid, Mortgage, 2028-06 | $222,055.26 (**rising** $1,910.95/mo) | $170,456.89 -- off by **$51,598.37** |
| dashboard, Checking | $1,979.39 | balance sheet says $648.13 -- off by **$1,331.26** |
| year-end 2026 net worth | "grew $255,300.26" | fabricated from `jan1 = 0` |
| Van Loan, period 0 | $0.00 owed | $17,134.85 |

And there is **no independent oracle**. Your only real oracle today is four hand-maintained numbers on
a dev clone. Mutation testing (Section 7) confirms the consequence: deleting the `is_confirmed` filter
from the forward projection leaves **3,891 tests green** while moving $4,449.72, and double-counting
the **mortgage-interest tax deduction** leaves **5,741 tests green** while inflating a Schedule A
deduction by $7,181.97. Both are invisible for one reason: **no fixture in the balance suite contains
a loan that was ever actually paid.**

**Would I build it this way from scratch?** **No.** From scratch:

```
event   = (sequence_date, visibility_date, effect)         # BOTH dates. See Section 2.
            origination | true-up (reset) | payment (split)  -- facts you already store

balance_at(loan, T) = fold(events where visibility_date <= T,
                           ordered by sequence_date)       # ONE function, TOTAL, no None, no raise

predicted(loan, T)  = payment RECORDS where they exist,
                      the contractual schedule only beyond the record horizon

postings            = the persisted PROJECTION of the fold  # for the GL / balance sheet, reconciled
```

No seam. No fence. No splice. No flags. The schedule is a *prediction*, not a balance source. And the
double-entry ledger stays exactly where it is -- as the general ledger it was built to be, not as the
answer to "what do I owe."

---

## 9. Adversarial review of the audit and the plan themselves

### Where they are right, and I confirm independently

* The `balance_at` seam **is** correct and single-sourced. My fold agrees with it to $0.00 on every
  past date on both real loans. That vindication is real; do not discard it.
* B-1 (the clock-fired outage), B-3 (grid, live), B-5, B-11 (live), B-12 (the unfenced tier) all
  confirmed. B-1 is *worse* than reported (Section 4).
* *"Two wrong implementations agreeing is not a proof"* (B-4) is the single most important sentence
  in the audit and is correct. B-4 and B-17 are both **confirmed vacuous** by mutation (Section 7).
* *"A safety that is a predicate is not a safety."* Correct, and it is the diagnosis of the whole arc.
* The one-fold recommendation is **the right direction.**

### Where the audit is too HARD on itself, and it matters

*"The gates prove nothing"* is **overstated, and believing it would be expensive.** Mutation-tested,
**all five of the arc's named guards bite**, the cross-page oracle pins value rather than mere
agreement, and seven further money paths I probed were all caught (Section 7). The suite is a real
asset. It has **one** structural blind spot -- no fixture has a loan that was ever *paid* -- and that
single gap explains every vacuous guard found, in both the audit's register and mine. **Fix the
fixture, not your faith in testing.**

### Where they are wrong or misleading

1. **The audit costs the fold as a large new build (S1, S8) and gates it behind a ruling (R4). It is
   already written.** This is the most consequential error in the document: it makes the correct
   answer look expensive and the incremental patches look cheap, which is precisely backwards and is
   how you get another six months of S2-S7.
2. **Section 5's "the postings ledger becomes a DERIVED CACHE, not the source of truth" reads as a
   reversal of the shipped read switch, and is not framed as such.** It is not a reversal -- the read
   switch's *principle* (the past belongs to facts, not to the schedule) is exactly right, and the
   fold is that principle applied to the source rather than to the cache. But the audit does not say
   this, and a future reader will conclude that two months of shipped, prod-verified work was wrong.
   It was not. It aimed one layer too shallow.
3. **The arc (S0-S9) is ten more steps of the same activity that produced the register.** S2-S7 patch
   individual surfaces against the *current* model. Every one of them adds or adjusts a predicate.
   On this arc's own measured track record, expect each to introduce ~1 new defect. **The arc is the
   tail.**
4. **B-11's root cause is misdiagnosed** as a consumer forgetting a clamp. The origination event is in
   your database and is excluded from the event stream (`_opening_anchor_fact`). Proven in Section 2.
   Fixing the consumer leaves the hole.
5. **B-5 is filed as unrelated and latent. It is the same line as B-1**, and its -$7,643.80 is the
   exact figure the write-time clock fabricates on your real Mortgage.
6. **"Ten producers" is a ~2x undercount and a category error** (Section 3).

### What both documents miss

* **The fold they recommend building already exists** (`_walk.py::_replay_events`), one import away
  from the seam, discarding the exact number the whole arc is about. This is the largest miss, and it
  is what makes the recommendation affordable instead of aspirational.
* **The write-time clock corrupts payment SPLITS, not just the opening** ($7,643.80 of real cash
  reclassified as a Refund asset; the entire Schedule-A interest figure erased).
* **`account_posting_service` already implements the correct pattern** (no `as_of`, no clock). The
  loan walk is the only holdout. Neither document notices that the fix is already in the codebase.
* **The origination event is dropped from the event stream** -- the true root of B-11/FU-4.
* **There is no runtime reconciliation and no DB invariant** requiring an originated loan to have an
  opening posting. The ledger is a cache with no coherence check anywhere except in tests.
* **The cash side has never been audited -- and it is wrong by $1,331.26 on real data today,** with
  your dashboard and your own balance sheet already disagreeing. Section 6.
* **The fold is 6.6x FASTER than the machinery built to avoid it** (measured, Section 11). The
  complexity is not buying performance either.
* **A NEW five-figure hole in the same family as B-6: the mortgage-interest tax deduction
  double-counts** if one filter is deleted, and **5,741 tests stay green** ($7,181.97 overstated,
  $1,580.03 of phantom tax savings). Section 7.
* **The single root cause of every vacuous guard: no fixture has a loan that was ever PAID.** Neither
  document names it, and it is a one-fixture fix.

---

## 10. Where you are, and where to go

**Where you are:** the seam is correct; the surfaces around it are not; and the machinery keeping the
seam correct is growing faster than the correctness is. You are not lost -- you are one layer above
the right abstraction, and everything you have built is either reusable or deletable.

**Do NOT build the audit's S1-S9 as scoped.** Build this instead. Each step is independently green,
independently revertable, and each one *deletes* more than it adds.

| step | work | why |
|---|---|---|
| **F0** | **Two one-line gates, today.** Gate `PATCH /accounts/<id>/true-up` on account kind (B-15 -- I set your Mortgage to $1.00 through it with an HTTP 200), and gate the grid's account picker on cash kinds (B-3 -- `GET /grid?account_id=3` renders a rising mortgage today). | Stops the bleeding on the two **reachable, live** defects while everything else is designed. Neither touches the seam. |
| **F0b** | **Add a SETTLED-PAYMENT loan shape to `test_every_loan_shape`.** | One fixture. It closes the suite's only structural blind spot and immediately reds two live vacuous paths ($4,449.72 and the $7,181.97 tax double-count). **Highest leverage per line in the entire plan.** Do it before you change any balance code, so the next commit lands on a net that works. |
| **F1** | **Delete `as_of` from the loan write walk.** Post every anchor; let the readers bound (they already do). | Kills B-1, the split corruption, and B-5's mechanism. It is not a design decision -- it applies the rule `_walk.py:237-252` already states and `account_posting_service` already follows. **Smallest, highest-value structural commit available.** |
| **F2** | **Put the ORIGINATION into the event stream** (`_opening_anchor_fact`). | The true root of B-11/FU-4. The event is in your database and is excluded. Makes the stream complete. |
| **F3** | **Expose the fold's running balance:** `loan_balance_series(loan, scenario) -> steps`, memoized on `BalanceContext` (which already has the memo). **Use it as an ORACLE first** -- parallel-run against the seam over generated shapes and real data. | This is the audit's S1, and it is ~60 lines, not an arc. It would have caught B-4, B-3, and C3's $197k phantom. **Build the net before anything else moves.** |
| **F4** | **Switch the seam's loan producers onto the fold.** Wire the forward arm to payment RECORDS + `project_monthly_escrow`. | Then DELETE: the splice, `projection_seed`, `owed_from`, `is_originated`, the two zeros, `LoanLedgerNotOpenedError`, `compute_forward_*`, `balance_from_schedule_at_date`, `_forward_rows`, `LoanState.current_balance`, and W9905. |
| **F5** | **The CASH side** (Section 6): the settled-post-anchor drop, the period-flat scalar, the pre-anchor fabrication, the column-vs-history split. Then decide what reconciles the anchor to the ledger. | **Live, $1,331.26, on the account you spend from.** It has never been audited. Do not do a naive read switch -- cash is not the complete-data case. |
| **F6** | The remaining surfaces: property chart (B-2), year-end (B-7/B-10), balance sheet (B-5), taxes (B-6). | Most collapse once one total function exists. Do them *after* F3's oracle, never before. |
| **F7** | **Structural enforcement, retire the fence.** Engine cluster private inside `balance_at/`; distinct types for cash-flow vs kind-correct balance; kill `AmortizationRow.remaining_balance` as a public field. | Deletes ~60 allowlist entries. Keep a thin W9906 as a smoke alarm. |

### The rulings

**R1 -- ship the B-1 fix now?** **Yes, and it is not a design decision.** The rule is already written
in the same file and already implemented on the cash side. F1.

**R2 -- does an overdue, unpaid installment pay the loan down?** **The fold answers this; it does not
need a product ruling so much as an implementation.** Walk payment **RECORDS**, not schedule rows. An
installment with no record behind it never happened and pays nothing down (the delinquent case). One
with a Projected record behind it is a planned event and projects normally (your mid-period mortgage).
Your data supports this today: both real loans carry a full Projected record set to 2028. Beyond the
materialized horizon, the schedule extrapolates -- which is legitimate, because no record *could*
exist there yet.

**R3 -- should the grid render a loan at all?** **No.** Option (a). The grid is a cash-flow view and a
loan's balance is not a transaction sum; the number it wants does not exist in its model. Gate the
account picker on kind, **and** gate `PATCH /accounts/<id>/true-up` on kind (B-15) so a loan can never
grow a second stored balance (both are F0). Better still, make it a type error (F7).

**R4 -- is one-fold the direction?** **Yes -- and the premise of the question is wrong.** It is not a
speculative redesign to be costed. It exists, I ran it, and it reproduces your baseline exactly.

### One NEW ruling I need from you

**R5 -- what did your mortgage owe in 2020?** For a mid-life import (origination 2018, tracking start
2026), once the origination is back in the event stream (F2), the fold will report the **full
origination principal held flat** for the untracked years -- honest, but crude. The candidates:

* **(a) Omit** -- assert nothing where there is no record. Charts start at the tracking date.
* **(b) Contractual back-projection, explicitly labelled "estimated."** The primitive already exists
  (`contractual_schedule_from_origination`), and the property equity chart already has a two-tier
  "estimated vs confirmed" visual concept.
* **(c) Origination principal held flat** -- the naive fold. Wrong, and visibly so.

**My recommendation: (b)**, with the estimated tier visually distinct everywhere it appears. It is the
only one that is both honest and useful, and it generalizes the fold cleanly: *predictions fill the
gaps in the record, in both directions -- and never where a record exists or should exist.*

### What to STOP doing

* **Stop adding rules to manage the partiality.** Every flag (`is_originated`, `owed_from`,
  `projection_seed`, `is_retired`) is a symptom. Making the function total deletes the class.
* **Stop growing the fence.** It is 40:1 apparatus to logic, it has been breached identically three
  times, and it cannot see the majority of your producers because they are read, not called.
* **Stop writing 1,500-line plans before the code.** Your own record is unambiguous: the plans were
  reasoned, and every probe against running code found the plan wrong. The 60-line prototype in
  Section 2 taught more than the 11,502 lines of docs. **Probe first, then plan short.**
* **Stop treating a green suite + 10.00 pylint as evidence.** C3 had both and carried $197,049.32.

---

## 11. Risks, and what I have NOT proven

Stated plainly, because a false claim of certainty here is exactly the failure this arc is about.

* **The fold is a prototype, not a migration.** v3 agrees with the seam on **every day of the ledger's
  domain** (111 days, both real loans, zero mismatches). But I did **not** drive it through the 17
  exotic shapes, and the escrow-inflation arm (Mortgage, $0.34-$4.22) is unwired. **And my own v1
  passed a 14-day sample while being wrong by $178,103.41** (Section 2) -- so treat this prototype as
  a demonstration of feasibility, not as a verified producer. **F3 exists precisely to prove it
  properly, exhaustively, before anything switches.**
* ~~Performance is argued, not benchmarked.~~ **MEASURED, and it is not a risk -- it is an argument
  FOR the fold.** Same work, 2 real loans x 59 periods, dev clone
  (`scratchpad/probe_perf.py`, mean of 20 runs):

  ```
  CURRENT seam  build_maps(2 loans x 59 periods):     41.7 ms
  FOLD          walk + answer all 59 periods     :      6.3 ms
  fold is 6.6x FASTER
  ```

  This is the expected direction: the current design resolves each loan through a 273-360 row
  amortization walk *and* queries the posting ledger *and* splices two maps, to answer what a 6-event
  fold answers in one pass. I have not benchmarked a full `/savings` render end to end.
* **The postings ledger must stay** for the double-entry balance sheet and the audit trail. Nothing
  here proposes deleting it. What changes is *who answers "what do I owe."* The invariant becomes
  *"the postings reconcile to the fold"* -- checkable, and best asserted at write time.
* **The cash side was probed, not exhaustively audited** (F5). Four defects are proven live; there are almost certainly more. It has the
  same disease with less scar tissue.
* **The B-1 outage window is bounded by container restarts**, which I read (`entrypoint.sh:259`) but
  did not execute.

---

## 12. The one sentence

> **You built a fence around a facade to protect an answer that a six-element fold -- already written,
> in your own codebase, discarded at write time -- gives you exactly, totally, and for free.**
