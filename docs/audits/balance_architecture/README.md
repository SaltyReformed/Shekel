# The cash balance architecture: the plan of record

**This is the ONLY live document for the balance arc.** It covers the CASH half -- the work that
remains. The LOAN half is COMPLETE and SHIPPED TO PRODUCTION (PR #64, merge `88c79857`,
2026-07-25); its as-built record is
`archive/loan_arc_as_built_2026-07-26.md`, and everything else that ever governed this work is in
`archive/` (read-only history, indexed by `archive/README.md`). The rules for this document are at
the bottom (Section 9); the short version: amendments are edits HERE, a shipped step gets its
checkbox ticked with its commit hash HERE, and no new planning documents get written for this arc.

**Trimmed to the cash side 2026-07-26** on the developer's instruction, at 2,713 lines. The loan
narrative, the loan rulings (D1-D5, R-A, R-C, R-D, R-E), Phases A-E, F2/F3, and the 75 CLOSED
findings all moved to the as-built record. What stayed: the cash work, every UNRESOLVED finding
whichever half it came from, and the arc's verification standard, process lessons and rules --
because the cash work is held to them. Where a surviving cash ruling cited an archived loan
ruling, the cited rule is now restated inline, so **no live sentence depends on an archived one**.
Three carried-forward rows were re-verified against the code during the trim and corrected: B-7 /
B-10 (closed at F2, the row never updated), B-16 and B-17 (both still LIVE, and B-17 turned out to
be the N-63 / N-67 class for the third time).

**State as of 2026-07-27.** Phase X is IN FLIGHT and BOTH of its cutovers are DONE -- the CASH one
at X-c2b2 and the MODELLED one at X-g2b, after which no step in the arc can move a figure. Shipped:
**X-a** `929b3a72` (the cash walk leaf, additive), **X-a1** `47dd4bbb` (the calendar refuses a
loan -- finding B-3 live on the one cash-flow surface the archived kind-gate ruling D4 did not
enumerate; ruling R-J below restates what it gates and why), **X-b**
`2aedc21c` (the fold, additive and unwired; ZERO unclassified days over 840 days x 8 real
accounts), **X-c0** `5b3764a7` (a purchase is something that happened -- ruling R-M's write-door
guard, plus the latent SECOND clock it uncovered), **X-c1** `9b8c9fdd` (the per-period view, its
identity holding on 360 of 360 real (account, period) pairs), **X-c2a** `0c89da2f` (interest
accrues forward of the assertion, not the period start -- ruling R-L's clock half), **X-c2b1**
`9835d2af` (the grid's three producer passes become ONE view, 360 of 360 cells byte-identical),
**X-c2b2** `d3489728` -- **THE cutover, and the only step where money moved** -- **X-c2b2-adj**
`7de04f0c` (a scenario's first settle opens that scenario's ledger; N-61 + N-62), and **X-c2b3**
`82557ca9` (the replaced producers delete: 7614 -> 7598 tests, exactly the 16 the four retired
suites held), and **X-c2c1** `b42dda42` (the reservation's as-of window deletes; see below).

**What the cutover bought, measured on the prod-shape clone and signed off:** Checking today
`$2,791.78` -> **`$2,824.26`** -- the figure the app's OWN persisted double-entry ledger already
carried, so the screens stopped contradicting its own books; its eight blank past grid columns
gained real balances; the Money Market moved `-$2,000.00` on every column (a transfer marked Paid
a week after that account was last anchored, reducing no balance on any screen); `/savings` went
from 0 to 6 history points; and **both loans and every investment map were unmoved**. Findings
cash D1, cash D2, cash D3 and B-18 all closed together, because one total fold subsumes all three.

**X-c2c1 SHIPPED `b42dda42` (2026-07-26)** -- ruling R-M's window deletion. What a cash row is
WORTH is now a function of the row alone, so `as_of` does exactly ONE job anywhere in the cash
path: ruling R-G's clamp, which decides WHEN a row lands and never what it is worth. 900 grid
cells, 12,600 daily points, 75 dated scalars and 19 accounts byte-identical across both databases.

**THE COMPENSATOR IS WITHDRAWN: plan step X-c2c3 is CANCELLED and X-g takes its place** (developer
ruling, 2026-07-26, as recommended). X-c2c3 was to WINDOW the investment / property bases onto the
fold to keep them out of `_merge_balance_sources`' way -- a fix this document already recorded as a
COMPENSATOR rather than a fix (**N-72**), which X-g then deletes along with the merge. Three
measured facts decided it: its benefit is near-vacuous today (all four affected accounts hold ZERO
transaction rows in both databases, so the swap moves `$0.00` and any producer passes -- its own
correction (a)); its cost is four hand-built discriminating fixtures plus a `ctx`-threading
signature change across `_investment`'s three public entries and four callers, every bit of which
X-g redoes; and it is a MONEY-MOVING cutover, so cancelling it removes an entire cutover with its
own oracle and sign-off rather than deferring one. Ruling **R-V** below records it.

**X-c2c2 SHIPPED (2026-07-26)** in three sub-commits -- `227c2479` / `ed7e220c` / `690fdd5d`. The
cash leaf's rules are now tested against the leaf: `test_cash_amounts.py` (what ONE row is worth),
`test_cash_flows.py` (what a SET sums to), `test_cash_ledger.py` the FACTS file.
`test_balance_calculator_entries.py` deleted whole. No production change in any sub-commit.
**One correction the developer approved from the trace: the step MIGRATES, it does not DELETE** --
the `_calculator`-discriminating tests stay until X-c2c4 deletes the module, because R-V moved that
step past X-g and `_calculator` is live production code until then.

**X-g's TRACE SHIPPED (2026-07-26) -- no code, five rulings.** Its five forks (R-R, R-S, R-T, R-U
and R-W, the last of which the trace itself found) are ANSWERED in Section 4 and its decomposition
is DECIDED at the step: **X-g1** (the replay, additive and unwired) -> **X-g2** (the cutover) ->
**X-g3** (ruling R-W's grid) -> **X-g4** (the deletion). Three findings recorded: **N-74**, **N-75**,
**N-76**.

**X-g1 SHIPPED `17ead4c5` (2026-07-26)** -- the modelled replay, ADDITIVE and unwired
(`balance_at/_asset_fold.py`). A modelled asset IS its cash fold plus two more event kinds: it takes
`_cash_fold.assemble`'s whole record and resolves CONTRIBUTION and daily ACCRUAL onto the same
running total in ONE sequential pass, after which the shipped `sample_cumulative` reads it exactly
as it reads the cash and loan folds. Graded on 25 hand-computed oracle tests plus a 9-test parallel
run that CLASSIFIES every divergence from the three shipping bases; **22 firing controls, and every
one of the 34 tests fires on at least one of them**. Baseline BYTE-IDENTICAL on both databases
(345,213 and 393,217 bytes of seam figures, HEAD run from a `git worktree`). Its three forks were
ruled first: **R-X**, **R-Y**, **R-Z** in Section 4. Two findings recorded: **N-77**, **N-78**.

**X-g2 IS DECOMPOSED into X-g2a / X-g2b (developer ruling 2026-07-26, as recommended), and its four
forks -- R-AA, R-AB, R-AC, R-AD -- are ANSWERED in Section 4.** Its trace found the step roughly two
thirds refactor and plumbing that cannot move a cent, so it splits on the arc's own
additive-then-cutover line: **X-g2a** the SHAPE, then **X-g2b** THE cutover. The trace also found a
CONSTRAINT rather than a fork -- the grid's interest row must move in the same commit as `/savings`,
because INTEREST is byte-identical across both surfaces today -- and that is what forces X-g2a's
assembly-sharing refactor. One finding recorded: **N-79**. One closed: **N-77**.

**X-g2a SHIPPED `5cb26d09` (2026-07-26)** -- the SHAPE, and no production reader changed, so the
baseline does not move (byte-identical on both databases). The contribution tier split into `balance_at/_asset_contributions.py`
(`_asset_fold.py` stood at 958 of pylint's 1000 lines); assembly split from resolution on BOTH sides
(`_cash_fold.period_view_of`, `_asset_fold.resolve` / `period_columns`) so the grid can regroup ONE
`AssembledCashFold` into cash columns AND resolve the modelled tiers over it; the three loose
projection inputs became `ContributionInputs` with an `absent()` constructor, killing three
`too-many-arguments` disables; and the two entries the cutover consumes shipped ADDITIVE and unwired
(`asset_seed_at`, `asset_growth_at`) on 8 hand-computed oracle tests. Finding **N-77** closed with
it.

**X-g2b SHIPPED `560b3339` (2026-07-27) -- THE modelled cutover, and the last step of the arc that
can move a figure.** Every non-loan account's balance is now one event replay; the kernel's per-kind
ladder is gone (R-AD); the kind-correct scalar is date-precise for all five kinds, closing **N-71**
and **N-29**; and `investment_seed_map` deleted with no successor. Both loans UNMOVED, PLAIN `$0.00`
on all 60 columns and the scalar, ruling R-K's identity holding on 0 breaks of 59 period pairs.
Findings **N-29, N-71, N-74, N-75, N-80, N-81, N-84** closed with it; **N-85** and **N-86** recorded.
Its two prerequisites shipped first: **X-g2b-0** `1fd41e1f` (the harness reads the modelled scalar at
a DATE) and **X-g2b-1** `ca0bd00d` (the investment dashboard splits into a package -- it stood at
exactly 1000 of pylint's 1000 lines). **TWO adversarial reviews ran before it and both found real
defects**; the step's own entry carries them, and both were compensators whose premise the step had
deleted.

**X-g2b's TRACE (2026-07-27) -- no code, three rulings, five findings.** Its ONE recorded
open fork was RULED along with two the trace itself found: **R-AE**, **R-AF**, **R-AG** in Section 4,
all as recommended. **The recorded fork's premise INVERTED**: this document predicted the chart's
Today junction would be `$0.00` while the three investment accounts hold zero transaction rows; they
do, in both databases, and it is not -- rulings R-U and R-AB correct ONE overlap TWICE, so the
projection line would start up to **`$292.11`** below its own history line (**N-80**). R-AE drops the
second correction, which deletes `asset_seed_at` unwired and leaves `investment_seed_map` with no
successor at all. R-AF then removes the junction rather than captioning it, by opening the axis the
day after the history line ends -- verified seed-equals-history-point on both databases, and it lands
the synthetic axis on the real pay calendar, closing the near half of **N-79** for free. Four
corrections to the step and four more findings: **N-81** (a defect the step would introduce, fixed
in-commit), **N-82**, **N-83**, **N-84**. The step gains a prerequisite commit, **X-g2b-0**, because
the regression harness is blind to exactly the region the cutover moves most.

**NEXT: X-g3** (ruling R-W's grid), then **X-g4** (the deletion), then **X-c2c4** (the deletion
X-g's cutover is what makes possible, and which carries the 52-period drift-oracle PORT as a
prerequisite -- see the step), then X-d / X-e / X-f, then E2.

**E2 IS RATIFIED (developer ruling 2026-07-26) and runs LAST.** The super-package boundary is now a
committed step in Section 5, not a Section 6 option: the read seam, the write cluster and the
shared leaves move under ONE package whose internals are private to it, and W9909's 58-name
classification registry dissolves into the same structural property W9910 already gives
`balance_at`. Scanned before ratifying: **17,612 lines, 113 files outside the cluster carrying an
import that moves**, and only `loan_ledger` has zero outside consumers. It is sequenced last for a
measured reason rather than a cautious one -- **every structural step ahead of it deletes code it
would otherwise move and then delete**, and X-c2c2 is about to rewrite 4,431 lines of the very test
surface E2 re-points. Its first commit is a TRACE (E2-0), because six of its seven candidate
members have live outside consumers, so the step is "decide a public re-export surface" and not
"make them private".

**X-g is the last structural step on the MONEY, and it is the from-scratch design** (E2 comes after
it and reorganizes packages, not figures). A modeled asset is an
event stream: ACCRUAL becomes the fourth event kind and all five account kinds read ONE sequential
replay, which deletes the three-source merge, the reverse growth projection, the interest-layering
pass, and `investment_seed_map`'s reason to exist -- and makes an investment's balance answerable
at a DATE rather than at a period end. Its target shape is Section 3.

**X-g's TRACE IS DONE and its five forks are RULED (developer ruling 2026-07-26, all as
recommended): R-R, R-S, R-T, R-U, and R-W, which the trace itself found.** No code was written for
it; the trace was the step's stated first action and it changed the step in five ways, four of them
here and the fifth (the decomposition, decided from what the trace found) at the step.
(1) **R-S inverts**: the reverse projection is not a ruled model the fold would damage, it is the
defect -- on the prod-shape clone it overrides 5 of the Roth's 6 recorded assertions and the fold
reproduces every one of them to the cent (N-74). (2) **R-R's double count is confirmed as a
mechanism and measured, and it is NOT live**: no deduction targets an investment account and no
investment account holds a shadow contribution row in either database, so the two feeds are both
empty today -- but they are also structurally DISJOINT (a payroll deduction never creates a
transaction row; a transfer always does), which is what makes ruling R-R a partition rather than a
de-dup rule. (3) **R-T is cheap**: a daily accrual grain costs **+0.5 ms** per account per
full-horizon read, measured. (4) **A fifth fork was found**: the grid and `/savings` already answer
one modeled account two ways, by up to **$17,642.13**, with no row explaining it (N-76) -- ruling
R-W closes it.

**One correction to this document's own design, from the trace: `sample_cumulative` does NOT become
balance-dependent.** Section 3.2 proposed generalising it; it does not need to be generalised, and
generalising it would change a primitive the LOAN fold shares. Because the balance is constant
between events, the ACCRUAL deltas resolve in ONE sequential pass and merge into the step list, after
which the existing sampler is untouched. Same math, no blast radius on the loan side.

---

## 1. The problem, in plain words

The app answered "what is this cash account's balance at time T?" in three places, three ways, and
the ways disagreed. On real production data, before the cutover: the checking projection silently
dropped every transaction you settled after your last balance assertion (so you re-asserted the
balance ~3 times a week to force it back -- **52 assertions in 119 days**, one every 2.3 days,
with **$2,108.15 invisible at that instant** and **$53,880.81 gross across 130 rows in 45
assertion gaps** historically); the scalar and the daily series stood **$15.96** apart on Checking
that day and **$246.36** at the worst day of the period; and a date before the latest assertion
read TODAY's balance from the scalar (**$2,932.41** fabricated for 2026-06-03) while the period map
had no entry at all for the same eight periods.

Underneath all three was ONE root cause, the cash form of the loan side's: **the honest balance
function was PARTIAL.** The projection started at the latest anchor and summed only
still-Projected rows forward, so it could not answer a past date, could not see a settled row, and
had to be composed with a seed, a flag or a fallback at every call site -- and every composition
was a new producer that could disagree with the others. Plan step X-c2b2 replaced all three with
ONE total fold and they closed together.

**What remains, and it is the same disease in four more places:**

1. **A modeled asset's balance is three producers merged by a preference order, and the merge
   overrides the user's own recorded facts.** An INVESTMENT account's map is the anchor-forward cash
   base, a forward growth projection, and a REVERSE growth projection, picked between by
   `_merge_balance_sources` (`_investment.py:397`, the preference loop at `:418-426`); a property
   substitutes a flat anchor carry for the reverse tier. Finding N-43 is a bug in that preference
   order, and the X-g trace measured what it costs: the three modeled accounts carry **15 recorded
   balance assertions between them** and the map reads only the LATEST, re-deriving every earlier
   period from a model -- **$6,315.57** of rendered net-worth history that contradicts the
   assertions the user typed in (N-74), and a FUTURE contribution that rewrites a PAST balance
   (N-75). Plan step **X-g** deletes the merge, and since ruling R-V cancelled X-c2c3 nothing ships
   a compensator for it in the meantime (N-72).
2. **Three kinds still answer a DATE with a PERIOD.** `_kind_correct.py:193-197` documents it as
   intended: "INTEREST / INVESTMENT / APPRECIATING are period-granular." Measured on the
   prod-shape clone at period 30, the scalar answers the same value on the period's FIRST day as
   its last, so the whole period's growth lands on day one -- **$328.50** on the Empower 401(k),
   **$261.24** on the Money Market, **$114.07** on the Roth IRA. That is finding cash D2's exact
   shape, closed for PLAIN and AMORTIZING and still open for the other three (N-71). Plan step
   **X-g** closes it.
3. **The write side and the read side are still two statements of one rule.** The posted account
   ledger is written by its own walk while the projection folds another; they are currently proven
   byte-identical, which is a test keeping two implementations in step rather than a structure that
   cannot drift. Plan step **X-d**.
4. **A derived cache is still read as a source of truth.** `Account.current_anchor_*` is a
   denormalized copy of the latest `AccountAnchorHistory` row; `cash_ledger.resolve_anchor` detects
   the divergence and only LOGS it (`EVT_ANCHOR_CACHE_RECONCILED`), never repairs it (cash D4).
   Plan step **X-e**.
5. **Nothing in the app records WHEN money moved.** `paid_at` is `db.func.now()` at the click and
   the API refuses any other value; the amounts are right and the DATES are guesses. The
   reconciliation row plan step X-c2b2 put on screen is the INSTRUMENT that measures that noise --
   `$36,323.99` of gross swing across 51 assertions against a true four-month bookkeeping error of
   `-$159.73`. Plan step **X-f** shrinks it at the source.

## 2. What is already shipped and correct (the foundation this plan builds on)

| shipped | where | reference |
|---|---|---|
| The whole LOAN arc: one total fold, no partial function, no splice, no name fence | prod | PR #64, merge `88c79857`, 2026-07-25 (see `archive/loan_arc_as_built_2026-07-26.md`) |
| `balance_at` seam (one read surface, kind-correct dispatch) | prod | PR #45, 2026-06-27 |
| Double-entry posting ledger: transfers, cash/envelopes, loan REAL-split postings | prod | PR #48 2026-06-28; 2026-06-29; PR #51 2026-07-01 |
| Actuals reporting (Step 5) | prod | PR #58 |
| The cash walk leaf (`cash_ledger._events` / `_walk`) + `settled_cash_leg` | dev | `929b3a72` (X-a) |
| The cash FOLD (`balance_at._cash_fold`), total over every date and account | dev | `2aedc21c` (X-b) |
| The per-period view: one valued row set grouped two ways plus a named remainder | dev | `9b8c9fdd` (X-c1) |
| **The cash cutover: every cash figure the app renders is one fold, read at three grains** | dev | `d3489728` (X-c2b2) |
| The replaced cash producers deleted | dev | `82557ca9` (X-c2b3) |
| **What a cash row is WORTH is a function of the row alone** -- no reservation clock | dev | `b42dda42` (X-c2c1) |
| The MODELLED replay: the cash fold plus CONTRIBUTION and daily ACCRUAL, one sequential pass | dev | `17ead4c5` (X-g1, additive and unwired) |

**The loan baseline is still a LIVE regression gate for CASH commits.** Mortgage (account 3)
**$177,277.97**, Van Loan (account 8) **$15,663.59**. Re-derive both from the seam before and
after every commit in this phase: a cash change that moves a loan figure is wrong. Plan steps
X-c2b2 and X-c2b3 each verified "both loans UNMOVED" against exactly these numbers, and that check
is what caught nothing precisely because it was run.

**Verified for cash, independently of the producers under test:** the X-b fold reproduces the
app's own persisted double-entry ledger to the cent on both real accounts that carry postings
(Checking `$2,824.26` over 177 postings, Money Market `$3,659.51` over 10) -- an oracle no cash
producer participates in. Ruling R-K's grid identity holds on **360 of 360** real (account,
period) pairs, twice over. Do not trust prose figures older than their write date; pin oracles in
tests.

## 3. The solution

An account's balance is a fold over its event stream. The fold is TOTAL: it cannot return `None`,
cannot raise, and answers any date -- asked about a date before every event it answers the seed,
not an error. That single property deletes the partiality and everything built to manage it. The
loan side proved it (`archive/loan_arc_as_built_2026-07-26.md` Section 3); cash is the harder case,
because a cash account's assertion legitimately survives as a periodic reset (a bank-statement
fact) rather than being a one-time origination.

### 3.1 The cash fold, as shipped

```text
CashEvent = (instant, kind, payload)                    -- cash_ledger._events
kind = ASSERTION  balance := anchor_balance             (AccountAnchorHistory, EVERY row)
     | ACTUAL     balance += settled_cash_leg (signed)  (settled transaction rows)

walk_cash_ledger(account, scenario) = replay(events, seeded 0.00)  -- cash_ledger._walk
dated_deltas(walk) -> [(visible_civil_date, delta)]               -- the ONE clock
cash_balance_at(account, T) = sample_cumulative(seed, ACTUAL + PLANNED steps, T)  -- the seam's fold
```

* **The leaf reads no clock.** `cash_ledger` is built from SOURCE facts, so a PLANNED row (whose
  effective date depends on the READER's as-of, ruling R-G) is not in it: the loader returns the
  rows and the seam's fold dates and values them. A walk that read a clock is what made the posted
  ledger a function of when the sync happened to run.
* **One walk, both consumers (ruling R-H).** The read fold folds it today; at plan step X-d the
  posting WRITER consumes the same walk, so the projection and the posted ledger cannot drift by
  construction rather than by a test keeping two implementations in step.
* **Three tiers, ONE `sample_cumulative`, no branch.** The R-I seed (the first assertion
  back-projected over the records it already contains), the ACTUAL steps, and the PLANNED steps are
  one running total. There is no past-producer / future-producer join, and every such join in this
  codebase's history is a place the two sides disagreed.
* **Three readers of that ONE row set (ruling R-K).** A scalar samples it at one date, the period
  map at each period's end, and `cash_period_view` samples the same period ends AND regroups the
  very same rows by the period each was BUDGETED to -- so the grid's balance row and its subtotal
  rows reconcile by construction, with a named remainder for what neither clock alone explains.

### 3.2 The modeled asset, as designed (plan step X-g)

The three modeled kinds -- INTEREST, INVESTMENT, APPRECIATING -- are not a different question. They
are the cash fold plus a modeled return, and `_interest.py:3-11` already says so in its own words:
"that module models an INVESTMENT's growth and an APPRECIATING asset's appreciation on top of their
cash bases, and this one models an INTEREST account's accrual on top of its folded cash balance."
One sentence, three kinds, and today it is implemented three ways.

The target shape extends the grammar above by exactly ONE kind:

```text
AssetEvent = (effective_date, kind, payload)

kind = ASSERTION    balance := asserted_value             (AccountAnchorHistory -- EVERY row)
     | ACTUAL       balance += settled_cash_leg           (settled rows)
     | PLANNED      balance += reservation                (still-projected, clamped per ruling R-G)
     | CONTRIBUTION balance += modeled_rate               (payroll deductions + employer match)
     | ACCRUAL      balance += balance * rate_over(d1,d2) (modeled return)

resolve(events) -> [(date, delta)]      -- ONE sequential pass; ACCRUAL reads the running total
balance_at(account, T) = sample_cumulative(seed, resolve(events), [T])   -- the SHIPPED sampler
```

**ACCRUAL is the only MULTIPLICATIVE kind, and that is the whole structural difference.** Its delta
is a function of the running balance at its own instant, so resolving it must be sequential. Three
facts make that fit the shipped code rather than replace it:

* **The loan side already does multiplicative replay.** A loan's walk is sequential precisely
  because a payment's split depends on the running principal (`loan_ledger/_walk.py` -- "the
  reset-aware running balance") -- its interest term IS an ACCRUAL. An investment's growth is the
  same shape with the sign flipped.
* **`sample_cumulative` STAYS AS IT IS** (corrected 2026-07-26 from the X-g trace; the earlier text
  here proposed generalising it). Between two consecutive events the balance is CONSTANT, so every
  ACCRUAL delta on the horizon can be resolved by one sequential pass over the already-sorted step
  list and appended to it -- after which `_fold.sample_cumulative` (`_fold.py:59`, `running += delta`
  at `:92`) is unchanged and still shared with the loan fold. Generalising the sampler to be
  balance-dependent would put X-g's blast radius on the LOAN side for no gain.
* **The daily grain makes the resolved list exact at every date** (ruling R-T). A step exists for
  every day, so a sampled date never falls inside an unresolved span and the answer never depends on
  which OTHER dates were asked for. Measured cost: `sample_cumulative` over 900 steps and 840 dates,
  plus the resolving pass, is **0.70 ms** against **0.20 ms** for today's 60-step shape -- +0.5 ms
  per account per full-horizon read, against the ~500 ms `/investment` + `/savings` render N-72
  measured.

Per kind, the ONLY difference is where the rate comes from:

| kind | ACCRUAL rate | today's implementation |
|---|---|---|
| PLAIN | none -- no ACCRUAL events | already the fold |
| INTEREST | `InterestParams.apy` at its compounding frequency | `_interest._layer_interest`, a second pass over a finished base map |
| INVESTMENT | `InvestmentParams.assumed_annual_return` | `growth_engine.project_balance` + `reverse_project_balance`, merged with the base |
| APPRECIATING | `AssetAppreciationParams.annual_appreciation_rate` | `growth_engine.project_balance` + a flat carry, merged with the base |
| AMORTIZING | the contract rate on outstanding principal | ALREADY an event replay (`loan_ledger._walk`) |

**Ruling R-L stops being a rule each layer restates and becomes one line of the event BUILDER:**
ACCRUAL events exist only forward of the latest ASSERTION -- everything at or before it is a bank
fact the user typed in, and modelling across those days adds money the assertion already contains.
**And there is no backward direction at all** (ruling R-S, 2026-07-26): before the FIRST assertion
the balance holds FLAT, which is ruling R-I's rule for cash and already the Property's rule
(`build_appreciation_balance_map`: "a manually-asserted point-in-time market value has no historical
basis to compound backward from"). The earlier text here proposed running the same events in the
un-growing direction; the trace declined it, because on real data that region is empty for both IRAs
(each account's first assertion falls INSIDE its earliest pay period, so no period end reads it) and
worth about `$7` on one period of the 401(k) -- a second rule and a surviving reverse projection for
a figure no screen shows. `growth_engine.reverse_project_balance` therefore leaves the balance path
entirely rather than becoming a direction.

**Three rules the BUILD had to settle that this design did not carry, ruled at X-g1 (Section 4).**
(1) **R-Y sharpens R-L above:** "forward of the latest ASSERTION" includes the assertion's OWN day
and therefore the remainder of the PERIOD holding it -- which INTEREST has done since plan step
X-c2a but INVESTMENT and APPRECIATING have never done, so the anchor period earns nothing at all
today (measured `$105.26` / `$44.95` / `$76.59` / `$170.11` on the four real modelled accounts).
(2) **R-X fixes the grain's rounding:** a day's accrual is computed at full precision and CREDITED
in whole cents with the sub-cent remainder carried, so every step stays an exact cent AND the
cumulative equals `round(exact)`. Rounding each day on its own -- the per-PERIOD convention, scaled
down -- makes a sub-half-cent daily accrual round to zero forever, so a `$50` HYSA at 3.29% would
earn `$0.00` a year. (3) **R-Z dates a CONTRIBUTION at its pay period's `start_date`**, which the
`PayPeriod` model calls the payday, and admits it only STRICTLY after the latest assertion.

**What X-g deletes, and which defect CLASSES stop being representable:**

* `_merge_balance_sources` and its preference order -- and with it the **whole N-43 class**. No join
  means no join rule to get wrong.
* `growth_engine.reverse_project_balance` from the balance path (it survives for the retirement
  what-if that legitimately projects a scenario).
* `_interest._layer_interest`'s second pass, `_investment.investment_base_balance_map`,
  `get_anchor_period_index`, `_assemble_investment_projection_inputs`, `_forward_project_periods`,
  `_reverse_project_periods`, and `build_appreciation_balance_map`'s `anchor_carry` -- all splice
  plumbing.
* **The period grain for three kinds** (N-71, measured above), which closes cash D2 for the last
  time.
* **The day-count class.** `_interest.py:230-243` records that counting 13 days of a 14-day period
  "understated a HYSA's yield by ~1 day in 14 (~7%), the interest-path twin of the growth_engine
  day-count defect." Date-keyed ACCRUAL events have no period-boundary convention to misstate.
* **The two accrual BASES stop disagreeing.** `_interest._layer_interest` accrues on the period's
  END balance (`_interest.py:226`, `running_balance = base_bal + interest_cumulative`, where
  `base_bal` is the fold sampled at `period.end_date`) while `growth_engine._project_one_period`
  grows the period's START balance (`growth_engine.py:388-393`). Two conventions for one question,
  and they bracket the truth from opposite sides: a deposit made mid-period earns a FULL period of
  interest on the first rule and would earn none on the second. A daily replay accrues on the balance
  actually held on each day, so there is no convention left to pick -- and no test can pin the wrong
  one, because there is no boundary to pin it to.
* **`Account.current_anchor_*` stops being read by any balance path**, which is what lets plan step
  X-e answer its own question. That includes `_investment.get_anchor_period_index`
  (`_investment.py:114`), which is where the CACHE column and the dated SoT diverge (the latent miss
  X-c2c3's correction (b) ruled on and this step inherits).
* **`investment_seed_map` loses its reason to exist.** It is a separate seam entry solely because
  today's design cannot express "the same balance without the modeled tier" -- its docstring
  (`_kind_correct.py:282-324`) warns that seeding a chart from the modeled map "would compound
  growth on top of growth." Under one replay that is a FILTER on the event stream: omit ACCRUAL. A
  producer whose existence is a workaround for an unexpressible query.

**Measured scope, so the step is not mis-sized.** `growth_engine` looks like 21 `project_balance`
call sites, but only **three** are inside the balance seam (`_investment.py:309` and `:478`, plus
the `reverse_project_balance` at `:381`; line numbers re-verified 2026-07-26 -- the earlier `:307`
/ `:379` / `:476` drifted by two lines at `c649b322`).
The five real sites outside it -- `retirement_projection.py:593`, `retirement_readiness.py:631`,
`investment_dashboard_service.py:367` and `:972`, `savings_dashboard_service/_horizon.py:413` --
answer "what would this be worth if I changed X", not "what is the balance at T" (each verified
2026-07-26). They keep `growth_engine` as the pure math engine, untouched. X-g replaces three call
sites and a merge.

**Why not the minimal alternative** (keep the merge and window its base, which is what plan step
X-c2c3 was to ship): it leaves the defect GENERATOR in place. The merge is the cash form of the
splice this arc exists to delete, N-43 is a bug in its preference order, and a window that keeps
one source out of the merge's way is a compensator for it -- fix the symptom and the next reader
inherits it with no record that it is one. That was written as a stated trade, with N-72 recording
the debt. **Ruling R-V (2026-07-26) took the alternative off the table instead of paying it**: the
window's benefit is `$0.00` on today's data, its cost is redone by X-g, and it is a money-moving
cutover in its own right -- so cancelling it removes a cutover rather than deferring one. The debt
is not incurred, and cash D1 / cash D3 stay open for the modeled kinds until X-g closes them, which
on today's data costs nothing measurable and is stated in R-V as the price.

## 4. Decisions

### Locked (developer ruling, 2026-07-16; restated here because cash consumes it)

| # | ruling | consumed by |
|---|---|---|
| **R-B** | The cash projection counts a settled transaction iff `COALESCE(paid_at, period start)` is after the latest anchor's `created_at` -- SHARED with the posting walk's existing rule, never copied. The archived X0 "post-anchor period" rule is dead: it double-counts on 15 measured real-data pairs. **Sharpened at R-F/R-H (2026-07-25): the comparison is INSTANT vs INSTANT, not civil date vs civil date** -- on prod the Checking anchor asserted 12:57:08 UTC and two expenses settled 13:07 the SAME UTC day, so a date-keyed partition would leave them invisible; and the sharing is STRUCTURAL (one walk, R-H), not one rule written twice. | X-a / X-c |

### Answered (developer ruling, 2026-07-25: Phase X's five forks, all as recommended)

| # | ruling | consumed by |
|---|---|---|
| **R-F** | **Phase X ships FOLD-FIRST, not partition-patch-first.** The plan's X1 ("patch the instant partition into today's period-granular engine") is DECLINED as scoped. Traced 2026-07-25: the rule would have to land in FOUR sites at once -- `balance_at._calculator.calculate_balances`, `_cash_engine.balance_as_of_date`, `_daily_series._net_by_attribution_day`, and `cash_ledger._flows.sum_projected`, the last of which the grid's SUBTOTAL row shares (`balances[p] - balances[p-1] == subtotals[p].net` binds them to one row set) -- moving live money on grid / dashboard / pulse / calendar / accounts-detail / net-worth kernel with **no independent oracle**, since the only reference for the new answer would be the producer computing it (Section 7.2's forbidden shape). It would also put an INSTANT rule inside a PERIOD walk, Section 8's signature defect. So Phase X follows the sequence the loan side proved five times (C3a->C3b, C6a->C6b, C8a->C8d, C9a->C9b, E1c->E1d): the walk and the fold ADDITIVE and unwired, graded on a hand-computed oracle plus an every-day parallel run, then ONE cutover in which the settled drop, the scalar/daily fork and the pre-anchor fabrication all close together because the fold subsumes all three. | X-a .. X-c |
| **R-G** | **A still-Projected item whose date has passed is CLAMPED FORWARD, never absorbed by an anchor.** Its effective instant is `max(its attribution date, as_of + 1 day)` -- "a plan cannot have already happened".  It is the SAME rule the loan side already ships (archived ruling D1: an overdue installment with a Projected record projects normally, clamped to `max(due, as_of + 1d)`; one with NO record pays nothing down), so cash and loans state one rule rather than two. Rejected: landing it on its nominal date and letting the anchor's reset erase it, which on real data (52 Checking re-anchors in 119 days, one every 2.3 days) would silently delete nearly every unpaid past-due bill from the projection within days of it being entered. Worked, on the real Checking shape: anchor 2026-07-24 12:57 UTC `$2,932.41`, a settle at 13:07 `-$108.15` (the X-c recovery), and a still-projected `$50.00` bill due 07-20 -> `$2,774.26` under the ruling, vs `$2,824.26` if the reset erased the bill. **Consequence for the WALK:** a PLANNED event's date depends on `as_of`, so -- exactly as C6a ruled for the loan plan -- the projected tier lives in the READER (the seam's fold), never in the clock-free `cash_ledger` walk. | X-b (the fold's PLANNED tier) |
| **R-H** | **ONE walk, designed for both consumers from the start.** `cash_ledger` gains the walk (`_events` + `_walk`), built from SOURCE facts (`AccountAnchorHistory` + the account's transaction rows) -- the shape `loan_ledger` has had since B0. The read fold folds it, and at X-d the posting writer consumes the SAME walk, so the projection and the posted ledger cannot drift by construction rather than by a test keeping two implementations in step (rule 1). Today they are genuinely two statements: the read projection is period-granular over transaction rows while `account_posting_service._walk` is instant-granular over the POSTINGS it is correcting -- and their disagreement IS the defect this phase exists to close. Rejected: leaving the write side alone (keeps the asymmetry Section 8 names, "the loose side is where the next hole is") and reconciling two walks by assert only (two implementations of one rule). | X-a (leaf), X-d (writer) |
| **R-I** (N-37; answered 2026-07-25) | **Before an account's first assertion the fold BACK-PROJECTS over the records, holding flat before the earliest one.**  An assertion is a RESET, not an origination, so seeding the read at zero before it fabricates a balance the account never had -- and the zero seed is the ONLY reason the pre-opening prefix reads as it does.  Traced 2026-07-25 and the reason the loan side's rule does NOT transfer: a loan's `origination_date` is a FACT, so `0.00` before it is true, while a cash account's first `AccountAnchorHistory` row is a TRACKING start -- and on the real data it is a BACKFILL row (migration `cfb15e782f86`), created days to weeks AFTER the account row (Fidelity Savings created 03-27 13:05, first anchor 04-06; the Money Market created 04-06, first anchor 05-01; only accounts added after the factory carry a real `origination` row).  The account existed and held money; answering `0.00` would claim it did not, and would put a false cliff from `$0` to full value on the day tracking began in every net-worth history.  Worked, on the two real accounts carrying the shape: Fidelity Savings (assertion 2026-04-06 `$5,363.56`, one `+$500.00` record on 03-27) reads `$5,363.56` on 03-27 and `$4,863.56` on 03-26; the Money Market (assertion 2026-05-01 `$4,879.26`; `+500` 04-06, `+500` 04-09, `+500` 04-11, `-1500` 04-23) reads `$4,879.26` before 04-06, `$5,879.26` on 04-10, `$6,379.26` on 04-22 (the withdrawal not yet taken) and `$4,879.26` from 04-23.  Rejected: flat-carry (contradicts the recorded `-$1,500.00`), `0.00` (above), and ratifying the zero-seeded prefix (`$500.00` / `$1,000.00` / `$1,500.00` -- ties to the posted ledger, which holds the same partial sum, but is not a balance the account ever had).  **Mechanism: ONE `sample_cumulative`, seeded at `assertion - sum(pre-assertion source deltas)`, with the FIRST assertion booking no correction and every later one keeping its reset -- so no branch, and the post-assertion region stays byte-identical.**  The LEAF is untouched: `dated_deltas` keeps emitting pre-opening rows at their own dates, so X-d's walk-vs-ledger equality still holds; this is the FOLD's read rule, which is why the leaf recorded the fact and deferred it. | X-b |
| **R-J** (N-38; answered 2026-07-25) | **A loan is refused at the SOURCE; the cash producers stay TOTAL and kind-blind.**  Every resolver feeding a cash-flow surface gates on kind, so no screen can ask the cash view about a loan.  That is the archived ruling D4's answer -- the grid and every cash-flow surface refuse an amortizing account, on the picker, on `?account_id=` and on the true-up route, because a loan's balance is not a transaction sum -- applied here to the surface its enumeration missed.  **The plan's cited door was wrong and the real one was open:** `resolve_grid_account` has gated all four steps since step A1 (grid, dashboard, pulse), and the cash detail page 404s a loan through `_cash_detail_wrong_type` -- but `resolve_analytics_account` checked ownership only, so the calendar's `?account_id=` reached `cash_balance_at` / `cash_daily_balance_series` with any kind.  Measured live on a dev clone 2026-07-25, BEFORE the fix: the Mortgage rendered `$178,103.41` against `$177,277.97` owed and the Van Loan `$531.94` against `$15,663.59` -- finding B-3, live, and the X-b fold would have inherited it and answered `$181,925.31` / `$1,063.88` instead.  Rejected: dispatching on kind inside the fold (the cash-flow view is DEFINED as the no-dispatch view -- its balance must reconcile with the transaction rows rendered beside it, and the kind-correct entry already exists), refusing inside the fold (reintroduces the partiality Section 3 deletes, and 500s a URL a user can type), and ratifying it behind a caption (Section 8: a safety that is a predicate is not a safety; a label is weaker still).  The refusal returns `None` rather than falling through to checking as the grid does: an explicit `account_id` is a question about THAT account, so a substitute is a wrong answer rather than a missing one.  Shipped AHEAD of X-b as **X-a1** because it is a defect today, not one the fold introduces. | X-a1 (shipped `47dd4bbb`) |

### Answered (developer ruling, 2026-07-25: X-c's four forks, all as recommended)

Tracing X-c found the step's one-liner wrong in three places and its central invariant
unachievable as written.  The measured basis for all four: on the prod-shape clone
`shekel_f3_final` the app's OWN persisted double-entry ledger (four months of postings written
incrementally at each mutation) totals **`$2,824.26`** on Checking (linked ledger account 8, 177
postings) and **`$3,659.51`** on the Money Market (account 10, 10 postings).  The X-b fold
reproduces both to the cent.  The shipping projection answers `$2,791.78` and `$5,659.51` -- so the
screens contradict the app's own books by `$32.48` and `$2,000.00` TODAY, and the cutover is the
step that ends that.

| # | ruling | consumed by |
|---|---|---|
| **R-K** (N-41; answered 2026-07-25) | **The grid's balance row and its subtotal rows become ONE step list grouped two ways, plus a named remainder.**  The plan's "re-prove `balances[p] - balances[p-1] == subtotals[p].net`" is impossible and was measured so: the fold counts money that MOVED, the subtotal counts only rows that are still UNPAID (`_flows.py:128` filters through `is_projected`), so on the real Checking account the identity breaks on **8 of 59 period pairs, worst `$2,505.17`** in the current column -- and every PAST column reads `$0.00` income and `$0.00` expenses while thousands moved.  So the subtotal rows change basis: **Total Income / Total Expenses count EVERY row attributed to the period** -- settled at its confirmed cash leg, still-projected at its entries-aware reservation -- which is budget-vs-actual and fixes the `$0.00` past columns as a side effect.  What the rows still cannot explain gets ONE conditional **Reconciliation** row: money that moved in a different period than its budget column (**19 of 130** settled Checking rows; nets to `$0.00` across history, swings to `+/-$2,007.46` in a period) plus the balance ASSERTIONS (the `anchor_equity` ledger account, **46 postings**, `-$2,906.31` since the opening).  The identity becomes `balance[p] - balance[p-1] == net[p] + reconciliation[p] + increments[p]` for EVERY kind, and it holds BY CONSTRUCTION -- both sides are the same valued row set grouped on two clocks, not two producers a test keeps in step.  Rejected: keeping the subtotals unpaid-only (the reconciliation row becomes a garbage bucket holding all real past activity -- `+$2,082.46` in one measured column -- which is hiding data inside a residual), and shipping no reconciliation row (leaves a visible contradiction, which the developer rules is never acceptable). | X-c1 / X-c2 |
| **R-L** (answered 2026-07-25) | **Modeled interest begins forward of the account's LATEST balance assertion.**  Everything at or before it is records and assertions -- facts -- so layering modeled accrual across the past would add interest to periods whose balance is already an asserted bank fact, money the assertion already contains.  This is closest to today's behaviour (`_kernel._account_interest_projection` accrues from the anchor PERIOD forward) and consistent with the investment branch, which also models forward from the anchor.  Rejected: accruing forward of TODAY only (drops interest genuinely earned since the last assertion, which can be a month -- the real Empower anchor is 2026-06-23), and keeping the anchor-PERIOD rule (the period starts before the assertion instant, so it models interest across days the assertion already accounts for).  Note the seed also changes: the interest walk is seeded off the `current_anchor_*` CACHE columns today, and reads the fold after the cutover.  **SHARPENED 2026-07-25 (developer ruling, as recommended): the assertion's OWN day accrues** -- the assertion's period accrues over `[assertion civil date .. period end]` inclusive, which is the exact analogue of the day-count convention every other period already uses (`_calculator._layer_interest` passes `end_date + 1 day` so a 14-day period accrues all 14 days the money is held).  Worked on the real Fidelity Savings (`$5,363.56` at 3.29% APY, about `$0.48/day`, asserted 2026-04-06 inside the 03-26..04-08 period): `$1.45` over 3 days, against `$6.77` over 14 today.  Rejected: accruing from the day AFTER the assertion (`$0.97`; a different day-count convention from every other period), and starting at the next period boundary (`$0.00`; silently drops real interest, the same argument this ruling already declined for "forward of today only").  **The two halves are separable and ship apart:** the CLOCK (accrual starts at the latest assertion, seed unchanged) has no fold dependency and ships as X-c2a; the SEED (the fold) ships in the cutover. | X-c2a (clock), X-c2b (seed) |
| **R-M** (N-39; answered 2026-07-25) | **A future `entry_date` is REFUSED at the write door, and the reservation's `as_of` window then DELETES.**  The fork is closed rather than ruled.  An entry is "an individual purchase recorded against a parent transaction" (`transaction_entry.py:15`) -- something that HAPPENED; a purchase not yet made is what the envelope's remaining budget already models.  Traced: the add form cannot create one (`_transaction_entries.html:179` posts a HIDDEN `entry_date` fixed to today), the edit form can (`:79`, an unbounded `<input type="date">`, and `EntryUpdateSchema.entry_date` carries no bound), and ZERO such rows exist in either database (newest entry anywhere 2026-07-24).  Worked on the live Groceries envelope #2280 (`$780.00` budgeted, 4 credit + 2 debit entries, hold-back `max(780.00 - 226.42 - 493.03, 0.00) = $60.55`): one `$150.00` entry dated 2026-08-05 moves the projected balance **`-$89.45`** as a debit (the `max()` floor takes over: `$150.00` held back) or **`+$60.55`** as a CC entry (`$0.00` held back) -- so it moves money in EITHER direction for a purchase that has not happened.  With the guard in place the window drops nothing: every entry is then dated at or before today, and the only production reader that pins a non-default `as_of` is `tax_report_service.py:373`, which reaches `loan_interest_in_year` and no cash producer -- so the parameter is dead and deletes, which is what ends the shipping divergence (the calendar windows, the grid and the daily ramp do not) rather than picking a winner.  Backdating stays fully allowed and is used (the real 05-21 Groceries row carries entries from 05-18).  **Both halves are SHIPPED**: the guard at X-c0 `5b3764a7`, the deletion at X-c2c1 `b42dda42`, whose as-built entry records the sharper reason -- a purchase that happened belongs in the reservation whatever date the reader asks from, so the parameter was not merely dead but wrong to keep. | X-c0 `5b3764a7` (guard), X-c2c1 `b42dda42` (deletion) |
| **R-N** (N-42; answered 2026-07-25) | **The cutover ships FIRST; recording when money actually moved is a follow-up (X-f).**  Measured: `Transaction.paid_at` is stamped `db.func.now()` at the click (`status_seam.py:105`) and no API accepts any other value (`schemas/validation/transactions.py:62` is `dump_only`), while `TransactionEntry.entry_date` is hidden and fixed to today on the only creation door -- so NOTHING in the app records when money moved.  The cost is the reconciliation row's size, not its correctness: `$36,323.99` of gross swing across 51 assertions against a true four-month bookkeeping error of `-$159.73` (the amounts are right; the dates are guesses).  Re-dating the read side alone is rejected because the walk and the posted ledger are currently proven byte-identical, so it would have to drag X-d's writer unification and a resync of four months of posted dates ahead of the read side proving itself.  So X-c ships, the Reconciliation row becomes the INSTRUMENT that measures the date noise, and X-f then shrinks it visibly. | X-c2b, then X-f |

### Answered (developer ruling, 2026-07-25: X-c2's four forks, all as recommended)

| # | ruling | consumed by |
|---|---|---|
| **R-O** (answered 2026-07-25; MOBILE half ruled at R-P) | **The Reconciliation row is "Timing & true-ups", it sits in the sticky footer above the balance, and it renders whenever ANY visible column is non-zero.**  Three display forks ruling R-K left open, decided together because they are one row.  PLACEMENT: the `<tfoot>`, with the Interest row and Projected End Balance, so the whole "how this balance is reached" chain is one visual block and the row set R-K's identity binds is the block the eye reads as bound.  Rejected: seating it under Net Cash Flow in the expense `<tbody>` (groups it with the FLOW rows, which is defensible, but then the identity spans two table sections and the footer stops being the reconciliation).  LABEL: "Timing & true-ups" -- what it IS in the developer's own terms.  Rejected: "Reconciliation" (accounting jargon on the daily screen; the plan's word stays the plan's word) and "Moved in another period" (says what the number is but wraps in the sticky first column at compact widths, and omits the true-up half).  VISIBILITY: present for the whole visible window when at least one visible period is non-zero, `$0.00` in the columns that carry none -- the rule the Interest row already follows, so the grid has ONE conditional-row convention rather than two.  Rejected: always-on (a permanently-zero row on the forward-looking windows, which are the ones most used) and past/current-only (hides the row on an all-zero PAST window, which reads as "not measured" rather than "nothing to explain"). | X-c2b |

### Answered (developer ruling, 2026-07-26: X-c2b's three forks, all as recommended)

| # | ruling | consumed by |
|---|---|---|
| **R-P** (answered 2026-07-26) | **Every surface that renders the subtotal figures renders ruling R-O's row, on R-O's own non-zero rule.**  R-O placed "Timing & true-ups" in the DESKTOP `<tfoot>` and left the three mobile surfaces that show the same income / expense / net figures unruled -- the "This Period" summary card, the Plan tab recap, and the mobile grid's section headers.  They gain the line under Net Cash Flow, shown only when non-zero, which is the convention the Interest row already follows on both form factors, so the app has ONE conditional-row rule rather than one per width.  Rejected: desktop-only, which leaves the mobile card showing `$3,153.22` of net cash flow against a balance change of `$2,364.54` with the `-$788.68` invisible -- the visible contradiction ruling R-K refused to ship, reintroduced on the form factor the developer uses to mark rows paid; and always-on, which puts a permanently-`$0.00` line on every future period (the ones most viewed) and reflows nothing in exchange. | X-c2b1 (shape), X-c2b2 (figures) |
| **R-Q** (N-51; answered 2026-07-26) | **The SEAM owns the live override map and the route reads it back; the `amount_overrides` parameter DELETES from all eight signatures.**  The fold builds its own map inside `_cash_plan` (`live_amount_overrides` over the loaded plan), so the parameter that existed to keep two walks on ONE income basis has nothing left to keep in step -- and with it go the STORED-vs-LIVE asymmetry `_kind_correct.balance_map` documents as "load-bearing for callers" and `grid_balance_view`'s `None -> {}` normalization for INTEREST.  `GridBalanceView` carries the map the projection was computed with; the grid route annotates `txn.live_estimated_amount` off THAT map instead of building a second one, so the cells and the balance row are identical BY CONSTRUCTION rather than by the argument finding N-48 currently has to make ("the two maps are provably identical, since both seams filter their candidates through `is_projected` over the same account/scenario row set").  Measured 2026-07-26 on the prod-shape clone: **ZERO of 60 columns differ** between the stored and live bases on any real account (Checking carries 51 live overrides whose stored values already match), so the deletion moves no money today; and the render drops the second ~90 ms build (N-48's `193.5 ms` two-build shape becomes `96.2 ms`).  Rejected: both sides building their own (two ~90 ms builds and two maps identical only by reasoning), and threading the parameter into the fold (keeps alive an argument a caller can get wrong, which Section 8 rules a defect rather than a contract -- a wrong map here ships a wrong balance silently). | X-c2b1 (the view carries it), X-c2b2 (the fold builds it) |


### Answered (developer ruling, 2026-07-26: the compensator is withdrawn)

| # | ruling | consumed by |
|---|---|---|
| **R-V** (answered 2026-07-26, as recommended) | **Plan step X-c2c3 is CANCELLED; X-g replaces the modeled bases outright, and no compensator ships in the meantime.**  X-c2c3 was to WINDOW `investment_base_balance_map` and `build_appreciation_balance_map` onto the fold so their ruled pre-anchor models survived `_merge_balance_sources`' preference order (N-43).  This document already called that window a COMPENSATOR rather than a fix (**N-72**) and named X-g as what makes it unnecessary; the ruling stops paying for it.  **Three measured grounds.**  (1) Its benefit is near-vacuous on today's data: its own correction (a) measured all four affected accounts holding ZERO transaction rows in BOTH databases, so `balances_for` and the windowed fold are both "the latest assertion carried flat" and any producer passes -- finding N-69's shape, in the step's own words.  (2) Its cost is redone: four hand-built discriminating fixtures with firing controls, plus the `ctx`-for-`scenario` signature change across `_investment`'s three public entries and four callers, all of which X-g rewrites when the base becomes a replay.  (3) **It is a MONEY-MOVING cutover**, so cancelling it removes an entire cutover -- with its own independent oracle, its own every-figure sign-off and its own revert risk -- rather than deferring one; the alternative ran TWO money-moving swaps over the same four accounts to reach one end state.  **The price, stated so it is not discovered later:** findings cash D1 and cash D3 stay OPEN for INTEREST / INVESTMENT / APPRECIATING until X-g ships, alongside N-71 and N-43 which were already going to.  That price is `$0.00` on both databases today and is a property of the DATA, not the design -- it flips the day a contribution is recorded as a transaction and settles, which is the same caveat X-g's own entry already carries.  **Sequence becomes X-c2c2 -> X-g -> X-c2c4**, and X-c2c4's deletion of `_cash_engine` / `_calculator` is unblocked by X-g's cutover exactly as it would have been by X-c2c3's -- both remove the last two callers of `balances_for`.  Rejected: shipping the window anyway (pays for a compensator whose measured benefit is zero and whose artifacts are discarded), and deleting `_cash_engine` early (its two callers are live; the C3b3 prove-the-successor-first precedent). | X-c2c3 (cancelled), X-g, X-c2c4 |

### Answered (developer ruling, 2026-07-26: X-g1's three forks, all as recommended)

Three forks the X-g trace did not have, found while tracing X-g1 itself. Each carries what was
MEASURED before it was asked, and R-T's own `+$0.14` / `+$1.73` figures were reproduced to the cent
first so the comparisons are like-for-like.

| # | ruling | consumed by |
|---|---|---|
| **R-X** (answered 2026-07-26) | **A day's ACCRUAL is computed at FULL precision and CREDITED in whole cents, carrying the sub-cent remainder.**  Every emitted step is therefore an exact cent -- the property ruling R-K's identity needs, and the property every other step in this arc already has -- while the cumulative accrual at any date equals `round(exact)`, so the daily grain introduces no rounding bias of its own.  **Rejected: rounding each day independently**, which is the codebase's per-PERIOD convention (`_layer_interest`, `growth_engine`) and is what R-T measured -- but at the daily grain it fails structurally on small balances, because a sub-half-cent daily accrual rounds to zero EVERY day, forever.  Measured, at 3.29% APY: a `$50` HYSA earns **`$0.00` a year and never grows**, against `$1.56` today and a true `$1.65`; `$100` earns `$3.65` against a true `$3.29` (+11%); `$500` earns `$18.25` against `$16.45`.  On the growth side at 10.5% a `$20` holding grows `$3.65` a year against a true `$2.00` (+82%) and a `$9` holding never grows at all.  On the REAL accounts the two rules differ by `$0.10` and `$0.09` over 840 days, so the cost of the wrong choice is invisible on today's data and total on a small one.  It costs one accumulator.  Measured effect on R-T's own figures: the daily-vs-shipped delta moves from `+$0.14` / `+$1.73` to **`+$0.04` / `+$1.64`** on the Fidelity Savings and the Money Market. | X-g1 |
| **R-Y** (answered 2026-07-26) | **Ruling R-L generalises to ALL modelled kinds: ACCRUAL runs from the LATEST assertion's OWN day forward, including inside the period that holds it.**  INTEREST has worked that way since plan step X-c2a; INVESTMENT and APPRECIATING do not -- `_investment` splits its periods on `period_index > anchor_idx`, so the anchor PERIOD is served by the flat cash base and earns nothing at all.  That is the same defect R-L fixed for INTEREST, in the other direction: R-L closed a window that opened up to 13 days too EARLY, and this opens one that today never opens at all.  Measured on `shekel_f3_final`, the one-off lift at the anchor period (every later column then shifts up by it, compounding): Roth IRA **`$105.26`** (its assertion falls on the period's own start day, so a full 14 days), Traditional IRA **`$44.95`**, Empower 401(k) **`$76.59`** (9 days), and on `shekel` the Property **`$170.11`** (6 days).  It recurs on every re-assertion, which on the real data is every few weeks.  Rejected: keeping "growth starts the period AFTER the anchor" for the two growth kinds, which preserves today's figures at the cost of two rules for one question and of silently dropping up to a full period of return each time the user re-asserts. | X-g1 |
| **R-Z** (answered 2026-07-26) | **A modelled CONTRIBUTION lands on its pay period's `start_date` -- the PAYDAY -- and exists only when that payday is STRICTLY AFTER the latest assertion.**  The date is a fact, not a fork: `PayPeriod`'s own docstring says "start_date (payday)", and it is already the date `investment_projection.build_contribution_timeline` stamps on every `ContributionRecord`.  Its consequence is a design decision and is taken deliberately -- the money is in the account from the payday, so it earns a full period of return, where `growth_engine._project_one_period` adds a period's contribution AFTER its growth and it earns none in its own period.  The BOUNDARY is the fork, and it is strict: a contribution on a payday at or before the assertion is money the asserted balance already contains, and modelling it again double counts -- an over-count that looks exactly like real growth and so cannot be detected later.  The ACCRUAL rule beside it is INCLUSIVE (`>=`) for a reason that does not transfer: a day count has to tile the calendar with no gap (the "13 of 14 days" defect), while a contribution is a discrete event that either is or is not inside the assertion.  Rejected: `>=` for both, which is one boundary expression instead of two but double counts the paycheck of a user who asserts a balance ON payday -- which is when someone checking their accounts after a paycheck would assert one.  Moves `$0.00` today: no account has an employee contribution feed, and the one live employer feed (Empower's flat 5%) has its anchor period's payday five days before the assertion, so both options agree there. | X-g1 |

### Answered (developer ruling, 2026-07-26: X-g2's four forks, all as recommended)

The ruling scheme runs past `R-Z` here. IDs stay APPEND-ONLY (rule 2), so the
next letter is `R-AA` rather than a renumbering of the 26 that exist.

| # | ruling | consumed by |
|---|---|---|
| **R-AA** (answered 2026-07-26) | **X-g2 ships in TWO commits: X-g2a the SHAPE, byte-identical, then X-g2b THE cutover.**  The b1/b2/b3 line this arc has now drawn eight times, applied to the modelled half.  The trace measured the step's diff as roughly two thirds refactor and plumbing that cannot move a cent -- the `_asset_fold` module split (it stood at **958 lines against pylint's 1000 default**), the assembly-sharing seam the grid needs, the two new replay entries, and finding N-77's fixture restamp -- against one third that moves money on five surfaces at once.  Mixing them makes a plumbing slip read exactly like a fold slip, and a revert would throw away the refactor with the cutover.  Rejected: one commit (above), and a seam-first / consumers-second split, which would ship a state where `/investment` renders its headline and history from the replay while its chart still seeds from the old cash base -- the Today junction wrong in a shipped commit. | X-g2a, X-g2b |
| **R-AB** (answered 2026-07-26) | **The forward-projection SEED becomes a DATE read at `window_start - 1 day`, and both `current_period_transfer_contribution` subtractions DELETE.**  Ruling R-U's second half, confirmed with the mechanism the trace measured rather than from its shape.  `investment_seed_map` (a period map read at `current_period.id`) becomes `investment_seed_at` -- the replay with ACCRUAL filtered out, sampled the day before the first projection period starts.  **Why the date is what makes the partition exact, traced:** `build_contribution_timeline` dates every transfer-based `ContributionRecord` on its pay period's `start_date` (`investment_projection.py:639`) and `_project_one_period` looks it up by the projection period's `start_date` (`growth_engine.py:399`), so on the retirement fallback axis (real periods from the current one, `retirement_projection.py:379-382`) the engine genuinely re-applies a recorded current-period contribution -- the compensator is LOAD-BEARING there and cannot simply be dropped.  Read the seed the day before the window opens and a $500.00 transfer settling on the window's first payday is not in the seed and is applied once by the engine, whichever axis is in use.  **The consequence, stated so it is not discovered later:** a recorded NON-contribution row dated INSIDE the window (a withdrawal next week) leaves the seed and the engine never re-creates it, so it drops out of the projection; today it is smuggled in through the seed and then compounded for the whole window, which is wrong in the other direction.  Costs `$0.00` on both databases -- all three investment accounts hold zero transaction rows (ruling R-R's measurement).  Rejected: keeping the period map and the subtraction (preserves a compensator ruling R-U already declined, and it stays load-bearing), and a date read at the current period's END (buys the new shape without the property that justifies it -- the overlap survives and still needs the compensator). | X-g2b |
| **R-AC** (answered 2026-07-26) | **The growth chip becomes a sum over the replay's ACCRUAL and CONTRIBUTION tiers, and hides ONLY with no investment params or no current period.**  `investment_growth_since_anchor` today reads the forward growth projection and returns `None` -- the page hides the chip -- when no post-anchor period exists, because `_investment` splits on `period_index > anchor_idx`.  **Ruling R-Y removes that arm's premise**: an account anchored in the current period HAS earned its own days (measured at the anchor period: Roth `$105.26`, Trad IRA `$44.95`, Empower `$76.59`, Property `$170.11`), so hiding the chip would deny a figure the balance beside it already contains -- the visible contradiction ruling R-K refused to ship.  "Since the anchor" then needs no window arithmetic and that is what makes it ONE sample rather than a difference: ACCRUAL exists only from the latest assertion's own day (R-L / R-Y) and a CONTRIBUTION only strictly after it (R-Z), so the cumulative total at a date IS the total since the anchor.  Consequence: `contributed` counts MODELLED contributions only -- a recorded transfer is a cash event under ruling R-R -- where the shipped engine folds recorded transfers into `periodic_contribution` as an average.  Moves `$0.00` today (no account has a contribution feed).  Rejected: keeping the `None` arms exactly (above), and leaving the chip on `growth_engine` until X-g4 (its own docstring's reconciliation, `growth + contributed == balance_map[current] - anchor_balance`, becomes false the moment the map moves). | X-g2b |
| **R-AD** (answered 2026-07-26) | **The kernel's per-kind ladder COLLAPSES at X-g2b; the producers it replaces stay dead until X-g4.**  Under one replay `build_account_balance_map`'s `classify_account` ladder (INVESTMENT -> growth engine, APPRECIATING -> appreciation curve, INTEREST -> layer, else cash fold) has nothing left to dispatch on, and `base_account_balance_map` exists only to route PLAIN vs INTEREST -- so both are what the step REPLACES and both delete with it, exactly as plan step X-c2b2 deleted the `stale_anchor_warning` flag it replaced.  What does NOT delete: `_investment`'s builders, `_interest.layer_account_interest` and `_cash_engine.balances_for` stay alive and unwired, because X-g1's parallel run grades the replay AGAINST them and the arc's rule is to prove the successor before deleting the incumbent (the C3b3 precedent, now applied eight times).  Rejected: deleting nothing (leaves `base_account_balance_map` as a live second producer for an INTEREST balance, and a future caller pointing it at an INVESTMENT would silently drop the contribution tier), and folding X-g4's deletion list into the cutover (the parallel-run tests would have to be rewritten in the commit that moves the money). | X-g2b, X-g4 |

**One constraint the trace found, which is NOT a fork and is recorded so the
cutover is not mis-scoped: the GRID's interest row must move in the SAME commit
as `/savings`.** Finding N-76's own evidence is that INTEREST accounts are
byte-identical between `/grid` and `/savings` today -- both layer
`_interest._layer_interest` over the same fold. Moving `/savings` to the daily
replay while the grid keeps the per-period layer would separate them again by the
grain (measured at X-g1: `$0.04` on the Fidelity Savings and `$1.64` on the Money
Market over 840 days), re-creating the two-producer shape N-76 exists to close.
That is what forces X-g2a's assembly-sharing refactor: `grid_balance_view`
already assembles the cash fold once for `cash_period_view`, and reaching the
replay through its own entry would have assembled the account a second time --
undoing plan step X-c1's "one walk, one plan load, one valuation, whichever
reader is asking."

### Answered (developer ruling, 2026-07-27: X-g2b's three forks, all as recommended)

X-g2b's own trace, run before any code as this arc's steps now always are. It
found the ONE fork this document recorded as open (the chart junction) and two
more, and it INVERTED the recorded fork's premise: the junction is not
`$0.00`-because-the-accounts-are-empty, it is non-zero BECAUSE two rulings
correct one overlap twice. Every figure below was measured on 2026-07-27 against
both databases, and the three modelled accounts hold ZERO transaction rows in
both -- so none of it is a property of the data.

| # | ruling | consumed by |
|---|---|---|
| **R-AE** (N-80; answered 2026-07-27) | **The forward-projection SEED is the modelled balance at the day before the window, with NOTHING filtered out -- `asset_seed_at` DELETES and `investment_seed_map` leaves with no successor at all.**  Rulings R-U and R-AB are two corrections for ONE overlap, and stacking them UNDER-counts.  R-U's filter was written when the seed was a period MAP read at the current period's END, where the projection window genuinely re-grew days the seed had already grown; R-AB then moved the read to `window_start - 1 day`, after which the window opens STRICTLY after the seed and `growth_engine.project_balance` grows only the periods it is handed -- so no modelled growth can be re-applied and the filter has nothing left to prevent.  **Measured, and it is the amount the projection line would start BELOW its own history line:** Roth `$161.31`, Trad IRA `$109.10`, Empower `$292.11` on `shekel`; `$82.67` / `$35.30` / `$292.11` on `shekel_f3_final`.  That is a balance the history line beside it has not rendered since the assertion date -- the visible contradiction ruling R-K refused to ship, on one chart rather than across two.  **R-U's CONTRIBUTION half is untouched and still does all the work it was ruled for**: a contribution dated inside the window is not in the seed and the engine applies it exactly once, which is what lets `current_period_transfer_contribution` delete.  With the filter gone the seed is not a special entry at all -- it is `balance_at(account, ctx, window_start - 1 day)`, the ordinary date-precise scalar THIS SAME STEP makes total -- so Section 3.2's "`investment_seed_map` loses its reason to exist" becomes literally true instead of a rename.  Rejected: shipping both (above), and reverting R-AB's date so the filter is load-bearing again (restores the contribution double count R-AB measured, and leaves the seed read three days AFTER the window opens on today's data -- an inverted overlap). | X-g2b |
| **R-AF** (answered 2026-07-27) | **The `/investment` chart's projection axis starts the day AFTER the history line's last valued date, seeded from the balance ON that date, so the two lines join BY CONSTRUCTION and there is no junction to caption.**  The recorded fork's option space was (a) leave it unlabelled, (b) caption it, (c) force the first projected point to equal the history's last.  The trace found a fourth that removes the step instead of explaining or hiding it, because the seed is now a DATE and a date can be CHOSEN: `_build_history_series` ends at the current pay period's `end_date` while `_assemble_chart_context` opens its axis at `date.today()`, so the two are 10-13 days out of step for a reason no ruling ever chose.  **Verified on both databases: the seed then equals the history line's last point EXACTLY on all three accounts** (`$27,537.61` / `$11,759.26` / `$31,751.40` on `shekel_f3_final`), and the first projected step (`$105.66`) is indistinguishable from the second (`$106.07`) and the third (`$106.47`) -- an ordinary step of one line, not a seam.  It also lands the synthetic axis exactly on the real pay calendar (`2026-07-30..2026-08-12` against real period 9's own dates, verified `True` on both databases), so `_project_one_period`'s `contribution_lookup` finds real `ContributionRecord`s over the near horizon instead of falling back to a flat average -- the near half of finding **N-79**, closed as a side effect rather than as a claim.  Rejected: (b), because a caption explains a step that has no reason to exist and Section 8 rules a label weaker than a predicate, which is already not a safety; (c), because overwriting the first projected point with the history's last hides a real difference -- the shape ruling R-K refused for the grid subtotals and the shape of the very compensator R-AB deletes; (a), because the step does not merely persist, it changes size and SIGN at this step (`-$301.96` to `+$277.27` on the Empower) and an unexplained jump that MOVES is worse than one that does not. | X-g2b |
| **R-AG** (N-82; answered 2026-07-27) | **The modelled scalar stays TOTAL past the pay-period horizon, and the contribution tier's silence out there is RECORDED rather than compensated.**  Today `_kind_correct.balance_at` resolves a date to its period and reads the map, so a date past the user's last pay period clamps to the final period's balance (`find_period_containing_date` falls back to the latest period that ended earlier).  The replay answers the date.  Measured at 2029-01-01, six months past the horizon: Empower **`+$2,501.92`**, Roth `+$1,754.08`, Property `+$5,427.07`, Money Market `+$272.24`.  **Not live**: the ONLY non-loan caller of this scalar in production is `investment_dashboard_service.compute_balance_hero_cell`, at today -- the four other call sites are loan-gated (`debt_strategy.py:140` behind `loan_terms is None: continue`, `loan/_helpers.py:183`, `savings_dashboard_service/_projections.py:131` inside `_compute_loan_account`, `home_equity_service.py:142` over secured loans).  Keeping the fold total is the single property this whole arc turns on, and clamping would put a partial answer back inside a total fold for a figure nothing reads.  What IS recorded (**N-82**): past the calendar the ACCRUAL tier keeps running while the CONTRIBUTION tier stops, because a contribution is dated on a real payday and there are none out there -- a half model, honest about what the app knows and worth naming before someone reads it.  Rejected: clamping at the horizon (a boundary rule nothing else in the seam has, reintroducing the partiality Section 3 deletes), and extending the payday cadence past the calendar so both tiers stop together (correct in principle, invents a calendar the app does not have, and is materially larger than this step). | X-g2b |

### Answered (developer ruling, 2026-07-26: X-g's five forks, all as recommended)

**The trace ran first and no code was written for it** -- the step's own stated first action. Each
ruling below carries what the trace MEASURED on the prod-shape clone `shekel_f3_final` (and, where
it differs, on `shekel`), not what the shape of the problem suggested. Two of the five inverted
their own premise once measured, and a fifth fork (**R-W**) did not exist until the trace found it.

| # | ruling | consumed by |
|---|---|---|
| **R-R** (answered 2026-07-26) | **A contribution is partitioned by SOURCE, so the two feeds are disjoint BY CONSTRUCTION and there is no de-dup rule to get wrong.**  A recorded transfer is an ACTUAL / PLANNED event (it HAS a transaction row); a payroll deduction is a modeled CONTRIBUTION event (it never has one).  The replay therefore never reads `_average_transfer_contribution`, which today folds both feeds into ONE scalar at `investment_projection.py:444-447` -- mixing them into one number is exactly what makes them indistinguishable.  **The mechanism was confirmed and measured, and it is not live.**  The two row sets provably overlap: `loan_loaders.query_shadow_income` (transfer-linked Income rows) and `cash_ledger._facts._unwindowed_contributing_rows` (what the fold counts) select the same rows.  Measured by creating six `$500.00` projected Checking -> Roth transfers inside a ROLLED-BACK transaction: the fold read `$30,432.35` at period 14 while the shipped map read `$31,098.91` -- the map had DISCARDED the rows and applied its own `$500.00`/period, so today's single count is a side effect of the merge X-g deletes.  A naive union would have added **`$3,000.00` over six periods** (~`$26,000` over the horizon, before compounding).  Not live today: **no deduction targets an investment account** (all 12 `paycheck_deductions.target_account_id` are NULL in both databases) and **the three investment accounts hold ZERO transaction rows of any kind** in both databases, so `periodic_contribution` is `$0.00` for all three.  The partition is SOUND because nothing in the app creates a transaction from a deduction -- AST-scanned over `app/` + `scripts/` (never a regex, Section 8): the 5 modules that IMPORT `PaycheckDeduction` (`models/__init__`, `routes/salary/items`, `investment_dashboard_service`, `projection_inputs`, `retirement_projection`) and the 5 that CONSTRUCT a `Transaction` (`routes/transactions/create`, `carry_forward_service/_execute`, `credit_workflow`, `recurrence_engine`, `transfer_service`) have an EMPTY intersection.  **Two consequences the ruling owns, so X-g1 does not rediscover them.**  (a) The EMPLOYER half is not partitioned: `growth_engine.calculate_employer_contribution` sizes a MATCH from the period's employee contribution, so it reads the RESOLVED employee total for that period whichever feed produced it (a flat-percentage employer -- the real Empower shape, `type_id` flat at 5% of `$3,631.74` gross -- does not depend on it at all).  (b) `_average_transfer_contribution` SURVIVES for the what-if surfaces under ruling R-U: "keep contributing at this rate for 30 years" is a legitimate question about a rate, and the synthetic long-horizon periods have no dated record to fall back from.  It leaves the BALANCE path, it is not deleted from the tree.  Rejected: rows-only (a payroll-funded 401(k) would stop growing from contributions, which have no row by construction), rate-only (the transfer's expense leg still leaves checking while its income leg never arrives -- broken double entry), and both-with-an-explicit-de-dup (today's compensator at `investment_projection.current_period_transfer_contribution:523`; it works, and it is a rule a reader must remember rather than a shape that cannot go wrong). | X-g |
| **R-S** (answered 2026-07-26) | **An ASSERTION always wins, and before the FIRST one the balance holds FLAT -- ruling R-I's rule, for all five kinds.**  **The fork inverted once traced.**  Its own text assumed the reverse projection was a ruled model the fold would damage; the measurement says the reverse projection is the DEFECT.  The three modeled accounts carry **15 recorded assertions** (Roth 6, Trad IRA 6, Empower 3) and `build_investment_balance_map` reads only the LATEST, re-deriving every earlier period from a model.  At the period ending 2026-04-08 the app renders Roth `$26,604.63` against the user's own 2026-04-06 assertion of **`$23,851.08`**, Trad IRA `$11,360.85` against `$10,175.49`, Empower `$29,289.22` against its earliest record `$26,912.56` -- N-43's `-$6,315.57`, re-verified to the cent, now with its SIGN established: the fold reproduces every assertion and the model contradicts them (**N-74**).  The rule therefore is one rule, not three: `sample_cumulative` seeded at `first_assertion - sum(pre-assertion source deltas)` (R-I's own mechanism) replays every ASSERTION as a reset, so a period at or after an assertion reads that assertion plus its recorded rows, and the pre-tracking prefix holds flat.  It is ALREADY the Property's rule, stated in `build_appreciation_balance_map`.  Rejected: un-growing before the first assertion (worth about `$7` on ONE period of the 401(k) -- both IRAs' first assertions fall inside their earliest pay period, so no period end reads the region at all -- and it costs a second rule plus a surviving `reverse_project_balance` in the balance path), and keeping today's model (`/savings` keeps rendering `$6,315.57` of history that contradicts recorded facts, and keeps N-75 with it). | X-g |
| **R-T** (answered 2026-07-26) | **ACCRUAL events are DAILY, resolved in ONE sequential pass, and `sample_cumulative` is NOT changed.**  Between two events the balance is constant, so the whole horizon's accrual deltas resolve in one pass over the sorted step list and merge into it; the shipped sampler -- shared with the LOAN fold -- is untouched.  A daily step means a sampled date never lands inside an unresolved span, so the answer is exact at every date and can never become a function of which OTHER dates were asked for (the shape rulings R-G / R-H kept out of the leaf).  **Measured, both halves, and each scoped to what was actually run.**  COST: a synthetic bench of the resolving pass plus `sample_cumulative` over **900 steps and 840 dates** takes **0.70 ms**, against **0.20 ms** for the same sampler over today's 60-step shape -- **+0.5 ms** per account per full-horizon read, where the real `fold_cash_balances` over the 840-day horizon already costs **2.7-13.8 ms** per account (load included) and one `/savings` + `/investment` pair costs ~500 ms (N-72).  BENEFIT: not the total.  For INTEREST, measured against the SHIPPED `_interest._layer_interest`, a day-by-day replay of the same `calculate_interest` rule differs by **`$0.14`** over 840 days on the HYSA and **`$1.73`** on the Money Market (the daily replay is the higher of the two in both cases).  For the three INVESTMENTS the comparison is grain-only -- the same `period_return_rate` at two grains with contributions held out of both, since their contribution feeds are empty -- and it differs by at most **`$0.05`**.  What the grain actually costs is WHEN the money lands.  N-71 re-verified at period 30: the scalar returns the IDENTICAL value on the period's first and last day (Empower `$38,617.11` against `$328.50` of growth in that period, Money Market `$9,090.81` / `$261.24`, Roth `$29,843.76` / `$114.07`).  Rejected: segment-per-compounding-interval-plus-event (exact and cheaper -- 2 to 60 segments per account today instead of 840 -- but a date INSIDE a segment needs a partial-accrual read that is not booked as a step, which is one more rule for a `$0.5 ms` saving), and keeping the pay-period grain (N-71 stays open forever and "balance at a DATE" stays a lie for three of five kinds). | X-g |
| **R-U** (answered 2026-07-26) | **The replay owns the SEED and the history; the forward WHAT-IF keeps `growth_engine`.**  The chart is not a balance-at-T surface: `investment_dashboard_service` projects over SYNTHETIC periods (`growth_engine.generate_projection_periods`, a slider-driven horizon clamped to 40 years, `:721`) and re-projects the whole series for the what-if overlay with `contributions=None` (`:972`); `retirement_projection.py:593` does the same under a `return_rate_override` and a per-period `salary_basis`; `savings_dashboard_service/_horizon.py:413` runs a 30-year band.  A fold over STORED facts cannot answer a hypothetical, and cannot answer a date past the user's pay-period horizon at all.  So what changes is the SEED: `investment_seed_map` (`:249`, and `retirement_projection.py:492`) becomes the replay with ACCRUAL filtered out -- the FILTER Section 3.2 names -- and the surfaces keep their engine.  **The de-dup subtraction goes with it**: both seeds today subtract `current_period_transfer_contribution` (`investment_dashboard_service.py:318`, `retirement_projection.py:580`) because the seed includes recorded contributions the engine then re-applies for the current period.  Under R-R's partition the seed is read at the day BEFORE the projection window opens, so the overlap does not exist and deep-quality-hunt #9 / #14's compensator deletes rather than being ported.  Precedent, and it points the same way: plan step C5 made the property equity chart's DEBT line read `positions()` (finding B-2, `$299,701.35` wrong on 8 of 13 shapes) while its forward what-if kept projecting. | X-g |
| **R-W** (N-76; answered 2026-07-26) | **The grid renders the MODELED balance, with a "Growth" row that is the accrual producer's own answer -- ruling R-K's identity then holds for all FIVE kinds.**  A fork the plan did not have: the trace measured that the grid and `/savings` already answer ONE modeled account two ways.  `_grid.grid_balance_view` layers an accrual for INTEREST only (`_grid.py:271-277`), so an INVESTMENT or APPRECIATING account's grid balance is its kind-blind cash-flow balance while `balance_map` returns the modeled one.  Measured at the last projected period on `shekel_f3_final`: Empower 401(k) grid `$31,070.06` vs `/savings` `$48,712.19` (**`$17,642.13`**), Roth `$5,916.95`, Trad IRA `$2,526.68`; on `shekel` the Property is `$21,675.99` apart.  The grid's interest column is `None` for every one of them, so nothing on screen explains the gap -- and both surfaces are reachable for these kinds (`account_resolver.is_cash_flow_account:41` admits every non-amortizing kind).  INTEREST accounts are byte-identical on both surfaces, which is the proof the unification WORKS: the Interest row already is the general shape.  Under one replay a typed grid row IS an event in the same stream, so the objection `_grid.py` records today ("a typed grid row would not move their modeled balance") stops being true and `balance[p] - balance[p-1] == net[p] + reconciliation[p] + accrual[p]` becomes a property of the construction for every kind.  Rejected: keeping the cash-flow basis with a caption (leaves `$17,642.13` of visible contradiction, the shape ruling R-K refused to ship for the cash subtotals -- and Section 8: a label is weaker than a predicate, which is already not a safety), and refusing modeled kinds on cash-flow surfaces the way ruling D4 refuses a loan (removes the contradiction by removing screens the developer uses; a loan is refused because its balance is not a transaction sum, while a modeled asset's IS one plus a rate -- the same shape an HYSA already renders correctly). | X-g |

## 5. The steps

Each commit is independently green (full suite + `pylint app/` with the full `--fail-on` set) and
independently revertable. Tick the box with the commit hash when it ships. Detail beyond what is
written here is decided in the commit itself, not in a new document. **The remaining arc is Phase X
then Phase E2**; Phases A-F are complete and archived. E2 was ratified 2026-07-26 and re-promoted
from a Section 6 option, which is why it is a phase of its own rather than a Phase X step: it
reorganizes the whole balance cluster including the WRITE side, and it runs after every Phase X
step because each of them deletes code it would otherwise move.

### Phase X -- cash (the fold; the loan arc proved the machinery)

**REDESIGNED and DECOMPOSED 2026-07-25** on rulings R-F / R-G / R-H (all as recommended). The old
X1 -> X2 order is superseded; the old IDs are kept in the mapping below so archived references
resolve. Phase X was the whole remaining arc until E2 was ratified on 2026-07-26; it is now
everything remaining EXCEPT that final reorganization.

**How to read the step IDs.** A suffix is a DECOMPOSITION of the step before it, appended when
tracing measured that step too large for one revertable commit: `X-c` became `X-c0` / `X-c1` /
`X-c2`, `X-c2` became `X-c2a` / `X-c2b`, `X-c2b` became `X-c2b1` / `b2` / `b3`. So depth is
decomposition HISTORY, not priority, and `X-c2c1` is simply the first commit of the last piece of
`X-c`. **Execution order is the order they appear on this page**, never the alphabet -- `X-g` runs
before `X-d`. **ONE EXCEPTION, and it is the only one: `X-c2c4` sits above `X-g` on this page but
runs AFTER it** (ruling R-V, 2026-07-26). It stays inside the `X-c2c` block because that is where
it belongs structurally and the IDs are append-only, so the page is ordered by DECOMPOSITION there
rather than by execution. The live order from here is **X-g -> X-c2c4 -> X-d -> X-e -> X-f -> E2**;
both steps say so at their own entries, and the state block at the top of this document is the
authority if they ever disagree. **The IDs are append-only** and are NOT renumbered for
readability: they are cited
164 times across 49 files in `app/` / `tests/` / `tools/` (`X-c2b2` alone 96 times in 33), and
they are in the commit messages, so a rename would add a second scheme rather than replace the
first. Considered and declined 2026-07-26.

**Measured on a fresh PROD-shape clone, 2026-07-25** (`shekel_f3_final`, verified identical to prod
on `alembic_version`, `max(transactions.created_at)` and `max(account_anchor_history.created_at)`).
**Every figure in this table was a LIVE defect when it was written and all four are now CLOSED**
(`d3489728`, plan step X-c2b2); they are kept because they are the measured case for the design,
and because a step that ships a figure has to be checkable against the figure it promised:

| what | measured |
|---|---|
| the re-anchor treadmill | **52** anchor assertions on Checking in **119 days** -- one every 2.3 days |
| settled money counted by NO producer | **$2,108.15 invisible right now** (Checking $108.15, Money Market $2,000.00); historically **$53,880.81 gross across 130 rows in 45 assertion gaps** |
| scalar vs daily-series fork | **$15.96** apart on Checking TODAY; **$246.36** at the worst day of the current period |
| pre-anchor | the scalar FABRICATES `$2,932.41` for 2026-06-03; the map has **no entry at all** for the same 8 periods |
| period standing in for instant | **22** settled rows whose `paid_at` civil date falls OUTSIDE their own pay period; **8** settled rows with NULL `paid_at` (the fallback rule is load-bearing) |

The sharpest single case, and the reason the partition keys on INSTANTS rather than dates: the
Checking anchor was asserted 2026-07-24 at **12:57:08 UTC**, and two expenses settled at
**13:07:11** and **13:07:18** -- ten minutes later, the SAME UTC civil day. They are in neither the
anchor nor the projection. A date-keyed partition leaves them invisible; an instant-keyed one
recovers them.

Target shape, which is the loan side's, name for name (R-H):

```text
CashEvent = (instant, kind, payload)                     -- cash_ledger._events
kind = ASSERTION  balance := anchor_balance              (AccountAnchorHistory, every row)
     | ACTUAL     balance += effective_amount (signed)   (settled transaction rows)

walk_account_ledger(account, scenario) = replay(events, seeded 0.00)   -- cash_ledger._walk
dated_deltas(walk) -> [(visible_civil_date, delta)]                    -- the ONE clock
cash_balance_at(account, T) = sample_cumulative(dated_deltas) + PLANNED tier   -- the seam's fold
```

- [x] **X-a** `refactor(cash): the account walk is a leaf, not the posting package's private`
  -- **SHIPPED `929b3a72` (2026-07-25).**
  -- the B0 move, for cash. `cash_ledger` gains `_events` (anchors + settled rows merged into ONE
  instant-ordered stream) and `_walk` (the reset-aware replay -> `CashLedgerWalk`, plus
  `dated_deltas` re-keying each event onto its VISIBLE civil date as its ONE attribution
  instant's UTC day -- provably the same date `app.utils.dates.to_utc_civil_date` gives the
  posting writer, in both the `paid_at` and the NULL-`paid_at` branch, but derived once
  rather than resolved twice). Built from
  SOURCE facts, clock-free, takes no `as_of` -- so PLANNED events are NOT in it (R-G). ADDITIVE:
  nothing consumes it yet, so the baseline cannot move. The existing
  `account_posting_service._walk.walk_account_ledger` STAYS untouched here and is retired at X-d;
  do not delete it early (the C3b3 / E1e deletion-list lesson -- prove the successor first).
  **Its adversarial review changed the step four times, and two of the four are the reason
  X-a is not just "write a walk".** (1) The walk valued a settled row at `effective_amount`;
  the posting writer's own rule is `effective_amount - Sigma(credit entries)`, because an
  envelope's CREDIT-card purchases leave via their CC Payback sibling and never touch
  checking. Measured on prod: the two disagreed on **10 of the real Checking account's 130
  settled rows, by up to $181.58**, and on three rows by the row's whole amount. The fix was
  not to copy the formula but to MOVE it -- `posting_service._signed_cash_leg` was private to
  the module that WRITES the ledger, the same inversion B0 corrected on the loan side -- so it
  is now the leaf's shared `cash_ledger.settled_cash_leg` and the writer imports it.
  (2) `dated_deltas`' docstring claimed its deltas were the NEGATIVES of what the writer
  books; they are the amounts themselves (the loan twin says "negated" and is right to, because
  a loan tracks OWED against a credit-normal ledger -- cash is ledger-native). A sign flip
  there still balances every entry, so nothing downstream would have caught it; X-d wires the
  writer onto this feed. A test now compares the walk's delta against the posted linked-ledger
  leg. Also: `settled_cash_leg` made TOTAL (an excluded row carrying credit entries valued at a
  fabricated `+$80.00` INFLOW), the settled narrowing moved into SQL, and the "one rule by
  construction" claim scoped honestly (a TRANSFER shadow is posted by `_settle_effective`, a
  different rule that agrees only via Transfer Invariant 3 -- X-d must unify or except it).
  **Verification:** every one of the 52 assertion corrections on all 7 real accounts is now
  BYTE-IDENTICAL to the postings-sourced walk's, and re-running the account anchor sync on a
  prod clone writes nothing (221 entries in, 221 out) -- the X-d invariant already holding
  before X-d ships. 5 mutations of the walk's rules each shown to fire their intended tests.
- [x] **X-a1** `fix(analytics): the calendar refuses a loan, like every other cash-flow surface`
  -- **SHIPPED `47dd4bbb` (2026-07-25).** NOT in the plan as decomposed: it exists because tracing
  N-38 for X-b's ruling found the finding's own citation wrong and a LIVE defect behind it.  The
  cited door (`resolve_grid_account`) has been closed since step A1; the OPEN one was
  `resolve_analytics_account`, which gated ownership only, so the calendar's `?account_id=` reached
  the cash-flow view with any kind.  Measured on the dev clone: the Mortgage rendered
  `$178,103.41` (owed `$177,277.97`) and the Van Loan `$531.94` (owed `$15,663.59`) -- finding B-3
  itself, on a surface the archived kind-gate ruling D4 never enumerated, and a defect TODAY
  rather than one X-b
  introduces, which is why it ships ahead of the fold rather than inside it (the C8c / C9a
  precedent for seating a prerequisite ahead of a cutover).  Ruling R-J: refuse at the SOURCE, so
  the producers stay TOTAL and kind-blind and X-b inherits nothing.  `is_grid_account` ->
  `is_cash_flow_account` (one predicate, both resolvers, because it is one question -- the
  grid-scoped name is what made a shared kind rule read as a grid preference).  Two docstrings
  whose stated premise this changes were corrected with it (`balance_at._cash_flow` and
  `cash_ledger._walk` both justified their kind-blindness with "`resolve_grid_account` can point at
  any kind", false since A1).  Its adversarial review fixed two of its own defects pre-commit: an
  uncited "every consumer" claim that omitted the cash detail page's separate gate, and a toothless
  assertion (`result is not <account>` cannot fail once `result is None`) rewritten to drive BOTH
  resolvers against one loan, which is what gives the fall-through-vs-None distinction teeth.
  Negative control fires (3 of 5 assertions fail with the gate removed; the two pinning
  non-over-refusal correctly stay green).  Baseline unmoved to the cent; pylint 10.00, suite 7533.
- [x] **X-b** (`2aedc21c`) `feat(balance): a cash account is an event stream` -- the FOLD, seam-private
  (`balance_at/_cash_fold.py`), sampling X-a's walk through the SAME `_fold.sample_cumulative` the
  loan past and forward folds share, plus the PLANNED tier (projected rows at
  `max(attribution_date, as_of + 1d)`, R-G) as the cash twin of `_plan.fold_forward`, and the
  pre-assertion BACK-PROJECTION (R-I): seeded at `assertion - sum(pre-assertion source deltas)`
  with the first assertion booking no correction, so the whole fold stays ONE `sample_cumulative`
  with no branch and the post-assertion region is byte-identical to the zero-seeded walk. TOTAL: any
  date, any account, a `Decimal`; no `None`, no raise (the pre-assertion answer is R-I's, NOT the
  loan side's `0.00` -- a cash assertion is a reset, not an origination; see the ruling). ADDITIVE and
  unwired -- only its oracle calls it. Graded on a HAND-COMPUTED oracle (never the shipping
  producer as its own reference, N-7) PLUS an every-day parallel run against all three shipping
  producers over generated shapes and real data, with **every divergence explained and signed off**
  -- they are the defects, so equality is NOT the pass condition here (this is where X-b differs
  from B2, and saying so is part of the step). Sampling is forbidden (B2's 14-day sample scored
  perfect while wrong by $178,103.41 on 22% of days).
  **SHIPPED `2aedc21c`.**  22 hand-computed oracle tests (R-I's own worked figures on BOTH real
  production shapes; R-G's worked Checking `$2,774.26` vs `$2,824.26`) plus a 7-test every-day
  parallel run.  The clean-shape half is what proves the fold is not simply a DIFFERENT producer:
  on a shape triggering no finding it matches the daily series on all 140 days, the scalar at all
  10 period ends and the map at all 10 periods.  **Real dev-clone run, 840 days x 8 non-loan
  accounts: ZERO unclassified days** -- every divergence is R-I, cash D3, or post-assertion, and
  the tail difference equals EXACTLY the settled money attributed after the last assertion on all
  eight (`-$2,000.00` Money Market, `+$2,000.00` Checking, `$0.00` on the other six).  Every
  region-F day off that tail sits inside the R-G clamp window and NONE falls after `as_of`.
  Firing controls: five mutations, each firing precisely its intended tests -- removing R-I fires
  the 5 R-I tests and correctly leaves the clean-shape parallel GREEN, removing the R-G clamp
  fires 2, the half-applied R-I (compensator kept, seed zeroed) fires 27 of 29 including the
  equality pin, the N-39 fork taken the other way fires exactly 1, and bypassing the shared
  valuation fires exactly 1.  Two duplications died at the root because the gates said so:
  `duplicate-code` on the new PLAN loader (both halves now narrow one PRIVATE
  `_unwindowed_contributing_rows`, so structure retires the W9909 ruling rather than adding one)
  and the walk suite's assertion fixtures (moved to `_test_helpers`).  W9909 fired at definition
  on `planned_cash_rows`, as designed.  Recorded, not ruled: **N-39**.  pylint 10.00, suite 7562.
**X-c is DECOMPOSED into X-c0 / X-c1 / X-c2** (2026-07-25, on rulings R-K..R-N).  Tracing the
one-liner found it larger than any step this arc has shipped whole -- three seam entries, the
kernel's two cash branches, two investment/property bases, six rendering surfaces, a grid footer
whose stated invariant the fold makes UNACHIEVABLE (R-K), and an interest rule the plan never
named (R-L) -- and found two claims in it wrong (see N-43, N-44).  It splits on the arc's own
additive-then-cutover line: a write-door guard that moves nothing, then the period view ADDITIVE
and unwired, then ONE cutover that moves money on every cash surface at once and deletes what it
replaces.

- [x] **X-c0** `fix(entries): a purchase is something that happened` -- **SHIPPED `5b3764a7`
  (2026-07-25).** Ruling R-M's guard, and a prerequisite rather than part of the cutover (the
  C8c / C9a / X-a1 precedent).  Both entry write doors refuse `entry_date > today` on ONE shared
  derivation; backdating stays allowed.  ADDITIVE: zero rows in either database carry a future date
  (newest anywhere 2026-07-24), so no figure moves.  **The step found a latent SECOND clock, and
  fixing it was the guard's precondition:** `entry_date` is a civil date the user types on their own
  clock, so the boundary is `display_today()` -- but the add form's hidden default and the grid's
  past-period styling both used the server's `date.today()`, so a UTC-running process would stamp
  TOMORROW's date for the evening hours the two frames disagree, and the app would post a value its
  own guard rejects.  Both now use the display clock and the edit picker gains a matching `max`.
  Controls each shown to fire: with the guard disabled exactly the 2 service and 2 route refusal
  tests fail while the 4 permissive tests (today, backdating, backdated correction, partial update)
  correctly stay GREEN; removing the picker bound fails exactly the bound test.  Full suite 7572,
  pylint 10.00.
- [x] **X-c1** `feat(balance): the cash period view is one row set grouped three ways`
  -- **SHIPPED `9b8c9fdd` (2026-07-25).**  The ADDITIVE producer R-K's identity needs,
  seam-private and unwired (`balance_at._cash_fold.cash_period_view` ->
  `CashPeriodFigures`).  Over one account and one period
  list it returns the fold's period-end BALANCES, the budget-clock SUBTOTALS (every attributed row:
  settled at its confirmed cash leg, projected at its entries-aware reservation) and the
  RECONCILIATION remainder, all from ONE valued row set grouped on two clocks plus the assertion
  steps -- so `balance[p] - balance[p-1] == net[p] + reconciliation[p]` is a property of the
  construction, not a coincidence a test polices.  Graded on hand-computed oracles per component
  (timing, true-up, clamp) plus a real-data run over every account and period; the identity is
  asserted with its components computed INDEPENDENTLY, never as a residual proving itself
  (Section 7.2).
  Two design points settled while tracing, so the step does not re-derive them: (a) the identity is
  stated per period as `balance(p.end) - balance(p.start - 1 day) == net[p] + reconciliation[p]`,
  NOT as a delta between adjacent periods -- the boundary form covers the FIRST period too, where
  there is no predecessor to subtract, and it costs nothing because the fold takes a date LIST; and
  (b) `CashSourceFact` carries no `pay_period_id`, so the budget-clock grouping cannot be built from
  the walk as it stands -- the leaf gains that one field (the loader already joins `pay_period`),
  the same one-field fact enrichment plan step E1c made when the loan split had to carry its
  `due_date`.
  **As built, four corrections to the above.**  (1) Point (b) undercounted: the leaf gains TWO
  fields, not one.  The budget clock also has to SPLIT a settled row into the Income and Expense
  rows, and that split is the transaction TYPE -- so `CashSourceFact` carries `is_income` beside
  `pay_period_id`.  Deriving the type back from the sign of the leg is inverting a lossy function
  (`settled_cash_leg` signs BY the type), and the two disagree on a reachable row: a settled expense
  whose `actual_amount` was corrected below its credit-card entries has a POSITIVE leg and is still
  an expense.  That shape is the ONLY firing control for the field -- a sign test passes every other
  test in the file -- so it is pinned
  (`test_a_row_counts_on_its_TYPE_row_even_when_its_cash_leg_inverts`).
  (2) The remainder is computed DIRECTLY (what moved in the column, minus what was budgeted to it,
  plus the assertions), never as `balance_delta - net`: the residual form makes the identity
  arithmetically true and therefore untestable (Section 7.2's forbidden shape), and it would
  silently ABSORB a row the budget grouping got wrong.
  (3) The step is a REFACTOR before it is an addition.  "ONE valued row set" is only true if the
  two readers share the assembly, so `fold_cash_balances` was re-expressed over `_cash_plan` ->
  `_planned_day_nets` -> `_running_steps` and the period view reads the same three; one walk, one
  plan load, one valuation, whichever reader is asking.  Baseline UNMOVED: 360 real-data figures
  (6 non-loan accounts x 60 periods on the prod-shape clone) are byte-identical HEAD-vs-post.
  (4) X-b's documented RE-DERIVATION is retired rather than re-pinned: an assertion's dated delta
  now lives on `CashAnchorCorrection.visible_on` / `.delta` (the twin of `CashSourceFact`'s), so
  `dated_deltas`, the R-I seed and the period view read ONE statement instead of three.  W9909's
  cash-ledger ruling gains `delta` with its why.
  **Verification.**  Real-data run on `shekel_f3_final`: the identity holds on **360 of 360**
  (account, period) pairs, zero breaks, across PLAIN / INTEREST / INVESTMENT / APPRECIATING.  The
  run also reproduced every figure ruling R-K was decided on, independently: **19 of 130** settled
  Checking rows outside their own pay period, worst timing swing **`-$2,007.46`** in one period and
  **`$0.00`** across history, **51** assertions after the opening netting **`-$2,906.31`**, the
  eight past columns reading **`$0.00`** under the shipping subtotal, and the current column
  **`-$140.63` -> `$3,153.22`** against a fold delta of **`+$2,364.54`**.  Every FUTURE column is
  unchanged to the cent (nothing has settled there, so the two bases coincide) -- which is the
  measured bound on what X-c2 will move.  Five firing controls, each shown to fire precisely its
  intended tests: counting the opening assertion (2), swapping the budget clock for the cash clock
  (3), giving the day index `find_period_containing_date`'s nearest-period fallback (1), classifying
  the leg by sign (1), and dropping ruling R-G's clamp (3, one of them X-b's own).  Full suite 7585,
  pylint 10.00.
- **X-c2 (DECOMPOSED, 2026-07-25)** `the cash seam reads the fold` -- the CUTOVER, and the only
  step where money moves. Closes **old X1** (settled counted from its instant), **old X2** (the
  Projected-only premise, `_detect_stale_anchor` -- nothing left to detect -- and the scalar/daily
  fork) and **old X3** (pre-anchor: the fold replays EVERY anchor, so a past date reads the anchor
  in force THEN, killing both the scalar's fabrication and the map's omission -- B-18), because the
  fold subsumes all three. Every moved figure individually explained and signed off (Section 7.1).
  **Measured on the prod-shape clone 2026-07-25, and this is what the whole phase buys:** Checking
  today `$2,791.78` -> `$2,824.26` (`+$32.48`), its current grid column `$2,791.78` -> `$2,683.63`
  (`-$108.15`), its worst day of the current period `-$1,078.82`, and all 60 columns move (8 that
  render blank today gain real balances); the Money Market moves `-$2,000.00` on EVERY column and
  the scalar; the three IRAs and the Home move `$0.00` on every anchor-forward column.
  **Decomposed on the developer's ruling (2026-07-25), because tracing measured a coupling that
  fixes the shape:** the INTEREST branch cannot lag the cash map by even one commit --
  `_grid._accruing_grid_view` renders `kind_correct - cash` as the "Interest" row, so folding the
  cash map while the interest map still seeds off `current_anchor_balance` puts **`$2,007.01` of
  "Interest"** on the Money Market's current grid column, the missing `$2,000.00` relabelled as
  earnings. The kind-correct PLAIN scalar is likewise the SAME function as the cash-flow scalar
  today (`_kind_correct.py:252-255` and `_cash_flow.cash_balance_at` both reach
  `_cash_engine.balance_as_of_date`), so splitting them makes /savings and the dashboard disagree;
  the pulse hero and its chart (`_pulse_hero` scalar vs `build_pulse_section` map) are one screen;
  and the grid's balance row and subtotal rows are one screen with one identity. So the cutover is
  ATOMIC (ruling R-F's "ONE cutover"), and only the two genuinely uncoupled pieces come out of it:
  ruling R-L's CLOCK half (no fold dependency) ahead, and the investment / appreciation bases
  (measured `$0.00` of movement anchor-forward) behind.
  - [x] **X-c2a** `fix(balance): interest accrues forward of the assertion, not the period start`
    -- **SHIPPED `0c89da2f` (2026-07-25).** Ruling R-L's clock half, seeded unchanged off the
    `current_anchor_*` cache columns, so it moves ONLY the accrual window;
    `_kernel._account_interest_projection` accrued from the anchor PERIOD's start, up to 13 days
    before the balance was asserted, and now opens at the LATEST assertion's UTC civil day read
    through the dated SoT (`resolve_anchor`), with the assertion's OWN day accruing (R-L's
    sharpening). Moves both readers of that one walk -- the INTEREST balance map AND
    `interest_by_period_for_account`, the account-detail "Interest, next 12 mo" chip the one-liner
    never named (N-47).
    **The rule is ONE expression and needs no branch:** `period_start=max(period.start_date,
    accrual_start)`, because `calculate_interest` already returns zero for an inverted window -- so
    a wholly pre-assertion period earns nothing arithmetically rather than through a guard, and
    keeps its place in BOTH maps carrying its base balance (dropping it would hole a column the
    caller is projecting).
    **Real data:** the Money Market is UNMOVED to the cent (`$7.01`/period, `$793.56` total -- its
    assertion falls on its anchor period's own start day); the only account whose window moves is
    the ARCHIVED Fidelity Savings, first period `$6.77` -> `$1.45` (14 days -> 3) and `-$5.38` at
    today, reproducing R-L's own worked figure live. The cockpit renders an archived account's raw
    anchor column, so the one user-visible surface is that account's detail page.
    **Its real cost was a fixture finding, the N-8 shape again:** `account_service.create_account`
    stamps the opening assertion with the WALL CLOCK while `tests/test_services` freezes today to
    2026-03-20, so every factory-built HYSA was asserted MONTHS after its own last pay period -- a
    state production cannot reach (a true-up files against `get_current_period`) and one that
    accrues nothing anywhere. 19 tests failed on THAT, not on the rule. `create_hysa_account` now
    pins the opening to its anchor period's first day, which keeps every existing hand-computed
    interest figure valid UNCHANGED (the window is then the full period, exactly what it was
    before) -- and that unchangedness is itself the proof the rule moves no existing fixture.
    Two firing controls, each shown to fire precisely: reverting the `max()` fails exactly the 5
    new tests with all 91 pre-existing green; deriving the window from the anchor period's start
    in the kernel fails exactly the 3 seam tests. Full suite 7590, pylint 10.00 on all three trees,
    146 checker tests.
  - **X-c2b (DECOMPOSED, 2026-07-26)** `the cash seam reads the fold` -- THE cutover. All three cash
    entries (`cash_balance_map` / `cash_balance_at` / `cash_daily_balance_series`), the kernel's
    PLAIN fall-through, the INTEREST branch's SEED, **and the kind-correct scalar's PLAIN and
    degraded-AMORTIZING branches** (N-47) read the fold; the grid reads ONE `cash_period_view` for
    its balance row, its subtotal rows and ruling R-K's remainder, rendered per rulings R-O / R-P.
    Deletes what it replaces: `_daily_series`, `_cash_engine.balance_as_of_date` +
    `_project_to_period_before`, `_calculator._detect_stale_anchor` + the `stale_anchor_warning`
    flag + its banner and OOB swap, `cash_ledger.period_subtotal` / `period_subtotals` /
    `PeriodSubtotal`, and the net-worth trend's cash history gate (N-44). `_cash_engine.balances_for`
    SURVIVES this commit for the two investment / appreciation bases (the C3b3 KEEP precedent:
    prove the successor before deleting the incumbent).
    **Decomposed on the developer's ruling (2026-07-26) after tracing measured the step's shape:**
    ruling R-F's "ONE cutover" binds the MONEY-MOVING surface, and the four couplings were
    re-verified as real (N-49's premium, the shared PLAIN scalar, the pulse hero vs its chart, the
    grid's balance row vs its subtotal rows) -- but two thirds of the diff is render plumbing and
    dead-code deletion, neither of which moves a cent, across ~15 production modules, 6 templates and
    **34 test files**. Mixing them makes a plumbing slip read exactly like a fold slip. So the step
    splits on the arc's own additive-then-cutover line (C3a->C3b, C6a->C6b, C8a->C8d, C9a->C9b,
    E1c->E1d), applied to the RENDER side for the first time, and the test churn partitions with it:
    route tests in b1, answers-that-move in b2, dead producers in b3.
    - [x] **X-c2b1** `refactor(grid): the grid's three producer passes become one view`
      -- **SHIPPED `9835d2af` (2026-07-26).** The SHAPE, baseline byte-identical.  `GridBalanceView` is now
      ONE `GridColumn` per period (balance, income, expense, net, reconciliation, interest) plus
      ruling R-Q's live override map; the grid route drops `_build_grid_subtotals` and
      `_grid_amount_overrides` and all three self-refresh endpoints read the one view; the desktop
      `<tfoot>`, the mobile This Period card and the Plan recap gain ruling R-O / R-P's "Timing &
      true-ups" row.  Internals stay the SHIPPING producers with the remainder `0.00`, which the
      E-25 invariant makes exact and R-O's rule then HIDES -- so the rendered grid is unchanged.
      **Verification: 360 of 360 (account, period) cells byte-identical HEAD-vs-post** on the
      prod-shape clone (6 non-loan accounts x 60 periods, comparing balance / income / expense /
      net / interest / stale flag / the whole override map, HEAD run from a detached worktree).
      Producer work per render `219.9 ms -> 127.6 ms`; per-endpoint figures and the one endpoint
      that gets SLOWER are in N-56, and N-57 corrects N-48's baseline arithmetic.
      **Four things it settled that the one-liner did not carry.** (1) Ruling R-O's visibility rule
      is now ONE rule for BOTH conditional rows, so an INTEREST account whose visible window accrues
      nothing loses its labelled row of blanks -- a rendering unification, not a figure move, and
      the reason R-O's "the rule the Interest row already follows" is now true rather than
      approximately true. (2) `_accruing_grid_view` -> `_accruing_balances`, returning maps instead
      of a view, with N-52's deletion note stated at the site. (3) The no-account shape moved into
      the seam as `empty_grid_view()` -- three route entries each carried their own. (4) N-55's two
      stale docstrings corrected.
      **Its adversarial review found and fixed a defect it had introduced:** the mobile This Period
      card renders `periods[0]` alone but the initial include was handed the DESKTOP window's row
      flags, so a window carrying a figure in another column would have turned the card's bar on for
      a period that has none -- and the `mobileCardSettled` refresh, which sees one period and no
      window, would have turned it back off.  Fixed by a period-scoped `period_row_flags`, and the
      redundant per-cell `is not none` guard that was masking it was DELETED so the flag is
      load-bearing; pinned by a data-driven route test (an HYSA anchored ahead of today: the window
      accrues, its leftmost column does not) whose control fires.
      Eight firing controls, each shown to fire precisely its intended tests: the remainder constant
      forced non-zero (2), R-O's rule forced always-on (4), the desktop row deleted (2), the mobile
      bar deleted (1), the Plan figure deleted (1), the kc walk left on the stored basis (1), the
      view returning an empty override map (1), and the mobile card handed the window's flags (1).
      Full suite 7609, pylint 10.00 on all three trees, 146 checker tests, djlint clean.
    - [x] **X-c2b2** `refactor(balance): the cash seam reads the fold`
      -- **SHIPPED `d3489728` (2026-07-26).** THE cutover, and the only step where money moves.
      All six sites read the fold, `grid_balance_view` reads ONE `cash_period_view`, and the two
      deletions that would otherwise become LIES went with it: the `stale_anchor_warning` flag +
      banner + OOB swap, and the net-worth trend's cash history gate (N-44).
      **Every moved figure, measured on the prod-shape clone and signed off:** Checking today
      `$2,791.78` -> `$2,824.26` (the app's own posted ledger already said `$2,824.26`), its current
      grid column -> `$2,683.63`, its eight blank past columns gain real balances; the Money Market
      `-$2,000.00` on every column and `$5,666.52` -> `$3,664.04` kind-correct; the account-detail
      interest chip `$292.43` -> `$225.76` (N-54's window corrected in its row); `/savings` 0 -> 6
      history points.  **Both loans UNMOVED** (Mortgage `$177,277.97`), and the three IRAs and the
      Home unmoved on the kind-correct map -- N-43 avoided, the investment and appreciation bases
      stay on `balances_for` until X-c2c.  Ruling R-K's identity holds on **360 of 360** real
      (account, period) pairs TWICE over: as `net + reconciliation` against an independently-sampled
      fold, and as the RENDERED `balance[p] - balance[p-1] == net + reconciliation + interest`.
      N-52's premium equals the cumulative accrual to the cent (`$658.26`); R-Q's override map is
      byte-identical to HEAD on all six real accounts.
      **Four things it settled that the one-liner did not carry.** (1) The interest-layering rule
      MOVED to `balance_at/_interest.py` with the base it layers over, taking
      `calculate_balances_with_interest` (no caller left) with it -- forced, because
      `duplicate-code` correctly refuses two copies of the walk, and it resolves the plan's own
      contradiction between X-c2b3 ("`_layer_interest` survives") and X-c2c ("`_calculator` deletes
      WHOLE"), which is now true.  (2) `_interest.accrual_params` states "does this account model
      interest?" ONCE, for the kernel and the grid.  (3) `GridColumn.balance` stops being optional
      (the fold is total); composing it out of `CashPeriodFigures` was REJECTED because it would put
      TWO balances on the one object the templates read -- the duplicate-code disable carries that
      reasoning.  (4) `_daily_series` is orphaned here (zero importers) and deletes at b3.
      **Its adversarial review found four defects it had introduced or inherited, all fixed
      in-commit:** the account-detail page folded TWICE per render (N-64), a guard of its own that
      did not guard (`isinstance` accepts a `datetime` -- it subclasses `date` -- while an
      exact-type test rejects the suite's frozen-clock shim), a SHIPPED guard passing on a docstring
      MENTION rather than a call site (N-63), and ~15 contract statements the cutover made false --
      including this package's own per-kind rule ("pre-anchor periods are OMITTED") and
      `cash_ledger._flows`' headline invariant.  Three findings recorded: **N-58** (the calendar's
      chip vs its balance step -- a fork the developer ruled RECORDED, to be decided at its own
      step), **N-59** and **N-60**, both found by the suite and fixed in-commit.
      Eight firing controls, each shown to fire precisely its intended tests.  Full suite 7612,
      pylint 10.00 on all three trees, 146 checker tests, djlint clean.
    - [x] **X-c2b2-adj** `fix(posting): a scenario's first settle opens that scenario's ledger`
      -- **SHIPPED `7de04f0c` (2026-07-26).** NOT in the plan as decomposed: the cutover's own
      fixture work exposed **N-61**, and the developer ruled it fixed now rather than recorded.  A
      settled row in a scenario whose linked ledger held no entry left that ledger opening-less
      (`-$70.00` where the account holds `$930.00`), because the effect-time self-heal tested only
      whether a posted correction had gone STALE and a brand-new scenario has none to stale.  The
      predicate is now stated as a SKIP whose precondition is that the reconcile would write nothing
      -- both arms must hold.  **N-62** shipped with it.  Latent, not live: production creates
      baseline scenarios only.  Two controls fire; suite 7614.
    - [x] **X-c2b3** `refactor(balance): the replaced cash producers delete`
      -- **SHIPPED `82557ca9` (2026-07-26).** Pure deletion, no behaviour, and the suite's arithmetic
      says so: **7614 -> 7598 tests, a delta of exactly the 16 the four retired suites held**
      (`TestBalanceAsOfDate` 9, `TestPeriodSubtotal` 3, `TestPeriodSubtotalsBatch` 2,
      `TestBalanceResultContract` 2), with 4 executable lines ADDED across `app/` and 2,382 deleted.
      Gone: `_daily_series` WHOLE (zero importers -- the calendar's per-day line is the fold sampled
      at every day), `_cash_engine.balance_as_of_date` + `_project_to_period_before`, `BalanceResult`
      (N-50; `balances_for` returns the map itself), `cash_ledger.period_subtotal` /
      `period_subtotals` / `PeriodSubtotal` + `_subtotal_from_transactions` with their two W9909
      rulings, and `_kernel.load_account_period_transactions` (N-53) -- after which the kernel issues
      no QUERY at all.  `sum_projected` survives BYTE-IDENTICAL, verified by diff.
      **`_detect_stale_anchor` and the `calculate_balances` arity change DEFERRED to X-c2c on the
      developer's ruling (2026-07-26).**  Collapsing the 2-tuple reaches 2 production sites and ~92
      test call sites in 11 files, and `_calculator` deletes WHOLE one step later taking
      `test_balance_calculator.py` + `test_balance_calculator_entries.py` (4,431 lines, 80 tests)
      with it -- so the mechanical edit would be written and then discarded.  The flag is computed
      and discarded inside `balances_for` for one more step; nothing has read it since X-c2b2 deleted
      its banner, so it cannot ship a wrong figure.  A left-behind `balances, _ =` would unpack dict
      KEYS rather than raise, which is why the edit belongs in ONE attentive pass rather than half of
      one here.
      **Three defects found and fixed, all X-c2b2 residue rather than this step's:** **N-67** (the
      N-63 shape AGAIN -- the `/accounts` detail static guard green on 3 docstring mentions with 0
      call sites), **N-68** (a fixture dating its entries in a FUTURE period, a state ruling R-M /
      X-c0 refuses at both write doors) and **N-69** (two seam scalar tests vacuous on a row-less
      fixture).  One recorded and then CLOSED in its own follow-up commit on the developer's
      instruction: **N-70**.
      **List corrections.** (a) From X-c2b2 as built: `calculate_balances_with_interest` was ALREADY
      gone (it went with the interest-layering rule at b2) and `_layer_interest` MOVED rather than
      survived, which is what makes X-c2c's "`_calculator` deletes WHOLE" true.  (b) From this step
      as built: `app/utils/balance_predicates.account_period_scope_clause` was NOT on the list and
      goes dead with the loader it served (**N-66**), so it deleted here.
      **Nine firing controls, each shown to fire precisely its intended tests:** the three rewritten
      guard arms (each fired alone), R-K's basis reverted (8 tests, including the re-pointed calendar
      column), the remainder forced to zero (all 5 cross-page cases; the grid class correctly stays
      green, its remainder being `0.00`), the probe helper's two clocks conflated (all 5 pay-period
      suites), the PLAIN and degraded-AMORTIZING branches routed off the fold (2), the live income
      override dropped (1), and the entries-aware reduction dropped (4).  Full suite 7598, pylint
      10.00 on all three trees, 146 checker tests, djlint clean.
  - **X-c2c (DECOMPOSED, 2026-07-26)** `the last cash producer deletes` -- ruling R-M's reservation
    `as_of` window deletes, the test surface re-points to the leaves that own each rule, and
    `_cash_engine`, `_calculator` and `cash_ledger.load_balance_transactions` delete WHOLE (N-46).
    **Decomposed on the developer's ruling (2026-07-26) after tracing measured its shape**: it
    carried a money-moving base swap, ruling R-M's behaviour deletion, **4,431 lines of test file
    that mostly does not test the dying module at all**, and a ~2,400-line pure deletion. Mixing
    them makes a test-migration slip read exactly like a base slip, which is the same argument that
    split X-c2b into b1/b2/b3.

    **The base swap is no longer part of it (ruling R-V, 2026-07-26).** X-c2c3 was to WINDOW the
    investment and appreciation bases to anchor-forward off the fold so their ruled pre-anchor
    semantics survived untouched (N-43); that window was a COMPENSATOR (N-72) and is CANCELLED, so
    **X-g replaces those bases and then X-c2c4 deletes**. What remains under this ID is
    X-c2c1 (shipped), X-c2c2 and X-c2c4. The trace findings below are kept because three of the
    four corrections are about the DELETION and the test surface, which are unchanged; correction
    (a)'s vacuity measurement and correction (b)'s dated-SoT ruling are what R-V and X-g consume.

    **What the trace VERIFIED.** The windowed fold reproduces `balances_for`'s domain and its
    figures exactly on real data: `old_keys == new_keys` and **ZERO differing (account, period)
    pairs** on all four affected accounts in BOTH databases (`shekel_f3_final` 212 pairs, `shekel`
    214). Cost: `/investment` + `/savings` renders together **500.0 ms -> 522.6 ms**, 18 windowed
    folds, and **zero** template-context figures differ on either surface.

    **Four corrections to this step's own text, all measured.**
    (a) **"Measured `$0.00` of movement anchor-forward" is true and nearly VACUOUS.** All four
    accounts (3 IRAs + the Home) hold **ZERO transaction rows** in both databases, and their anchor
    period is the CURRENT one -- so `balances_for` and the windowed fold are both "the latest
    assertion carried flat" and ANY producer passes. That is finding N-69's shape exactly ("a test
    whose fixture has no data cannot distinguish two producers"), so this step's verification
    cannot rest on real data. It needs fixtures carrying the FOUR discriminating shapes: a settled
    contribution after the latest assertion (the fold counts it, the old base drops it -- cash D1
    for the modeled kinds), a past-due still-projected contribution (ruling R-G clamps it forward,
    the old base leaves it in its own past period), a pre-anchor period (where the window is what
    keeps the reverse projection alive), and a property that HAS transaction rows.
    (b) **The window closes a latent defect, and which ANCHOR it reads is a ruling, answered.**
    `balances_for` windows on the DATED SoT (`resolve_anchor(...).period.id`) while
    `_assemble_investment_projection_inputs` pivots on the `current_anchor_period_id` CACHE column
    -- two notions of "the anchor period" in one function. When they disagree (the state
    `resolve_anchor` LOGS `EVT_ANCHOR_CACHE_RECONCILED` for and deliberately does not repair), the
    pivot's `base_balances.get(current_anchor_period_id, ZERO)` misses and the whole investment map
    projects from `ZERO`. **Developer ruling 2026-07-26: the DATED SoT governs**, and the window,
    the pre/post split and the growth pivot all come from ONE derivation of it -- so the miss stops
    being representable, and the three modeled kinds read the same fact the same way ruling R-L
    already made the INTEREST accrual read it. It also keeps the `resolve_anchor` RAISE on the
    modeled path, which is correct and deliberate: a modeled layer must fail loud without an
    assertion, because a property with no assertion has no market value to compound (the argument
    `_interest.py:102-115` already makes for the accrual, extended to its two siblings).
    (c) **The test surface is bigger than the count, and two of its guards go GREEN rather than
    red.** `calculate_balances` has 21 call sites in 8 other suites -- the one-liner's "~20" is
    accurate for that name. NOT counted: 3 `balances_for` sites in 3 further suites, 1
    `load_balance_transactions` site (`test_cash_period_view.py:154`), `test_balance_resolver.py`
    entirely (12 call sites, `TestSumProjectedAsOfBound`'s 5 tests, and both module-surface
    guards), and -- the important ones -- **two static guards asserting the doomed name is ABSENT**
    (`test_grid.py:4791`, `test_accounts.py:3551`). An AST call-scan cannot see them, and when the
    name dies they pass forever on a shape that has become impossible: the archived N-63 / N-67
    class (a static guard that greps for a NAME cannot tell code from prose, so it goes on
    reporting the wiring intact while what it locks has moved), arriving from the opposite
    direction -- and an arm whose forbidden name no longer EXISTS is not a weak guard, it is a
    sentence that can never fail, so it deletes with the name.
    (d) **The bulk of those 4,431 lines does not test `_calculator`.**
    `test_balance_calculator_entries.py` (27 tests) is the three-bucket reservation formula, which
    lives in `cash_ledger._amounts`; `TestEffectiveAmountFix` (8) is `Transaction.effective_amount`;
    `TestIncomeOverridesSeam` (4) is `income_amount`; and `TestTransferInvariantsBalanceRegression`
    (4) is the TRANSFER INVARIANTS -- two shadows, matching amounts, matching statuses and periods
    -- which hold whatever producer reads them, so three of its four tests touch no producer at all
    and the fourth re-points. They reach `cash_ledger` rules and model invariants THROUGH the
    dying producer. Re-pointing them at each rule's own home keeps every hand-computed figure
    **unchanged** and puts each test where its subject lives -- strictly better than re-pointing at
    the seam, where the fold's settled-row counting would force every number to be re-derived.
    **One test has no fold equivalent and must be PORTED, not retired:**
    `test_52_period_penny_accuracy` (275 lines) walks 52 periods of mixed statuses against an
    independent `Decimal` oracle for cumulative drift, and `test_cash_fold_parallel.py` has no
    long-horizon drift oracle. Deleting it IS the coverage hole a deletion step must not open.

    - [x] **X-c2c1** `refactor(cash): the reservation's as-of window deletes`
      -- **SHIPPED `b42dda42` (2026-07-26).** Ruling R-M's second half, and a behaviour deletion
      rather than a pure one, so it shipped alone and FIRST. `sum_projected`'s `as_of`,
      `_expense_amount`'s and `_entry_aware_amount`'s window all gone.
      **The payoff is larger than a dead parameter going, and that is what the step is FOR:** what
      a cash row is WORTH is now a function of the row alone -- the property `settled_cash_leg`
      beside it already had -- so `as_of` does exactly ONE job anywhere in the cash path, ruling
      R-G's clamp. It decides WHEN a row lands, never what it is worth.
      **Premises re-verified independently of the 2026-07-25 measurement, not carried:** 0 of 74
      entries in `shekel_f3_final` and 0 of 47 in `shekel` are dated after today (newest 2026-07-24
      / 2026-06-29), and all 24 production `BalanceContext.build` sites were traced -- only
      `tax_report_service.py:373` pins a non-default `as_of` and it passes the DISPLAY today, whose
      ctx reaches `loan_interest_in_year` and no cash producer.
      **Why deleting it is RIGHT and not merely safe** (the argument the step should be read for):
      ruling R-M answered N-39 at the source because an entry RECORDS a purchase that happened, and
      a purchase that happened belongs in the reservation whatever date the reader asks from. The
      window could then fire only on a HISTORICAL read -- whose plan is TODAY's still-Projected
      rows clamped forward rather than the plan as it stood then, so windowing its entries was a
      partial as-of purity inside a tier that has none.
      **Verification.** Real data, both databases, HEAD vs post: **900 grid cells** (balance /
      income / expense / net / reconciliation / interest), **12,600 daily-series points**, **75
      dated scalars** and every kind-correct period map across **19 accounts**, all BYTE-IDENTICAL;
      sampling avoided, the series covering every day of the horizon. Both loans unmoved (Mortgage
      `$177,277.97`, Van Loan `$15,663.59`). That run is the REGRESSION check, NOT the proof --
      with `as_of` = today and no entry dated after today, any correct producer passes it, which is
      N-69's shape stated before it could be mistaken for evidence. **The proof is the firing
      control: restoring the pre-change production code fails EXACTLY ONE test** -- the repointed
      N-39 pin -- and leaves the other 69 in the affected suites green.
      **Two corrections to this step's own text, both as built.** (a) "taking
      `TestSumProjectedAsOfBound` (5 tests)" was WRONG: three of the five pin properties that
      SURVIVE the deletion (every loaded entry counts, an override beats the entry formula, the
      no-entries short-circuit precedes the status read), so deleting the class whole would have
      opened a coverage hole. Two deleted, three repointed, the class renamed to what it now pins.
      (b) `test_an_entry_dated_after_the_reader_now_does_not_clear_early` was REPOINTED, not
      deleted -- it named itself "PINNED, NOT ENDORSED -- finding N-39, for plan step X-c to rule
      on" and said "this test flips if X-c rules the other way", so under R-M it becomes the
      NEGATIVE guard for the deletion. Deleting it would have left the step with no firing control
      at all.
      Also corrected: `test_daily_balance_series.py`'s docstring stated as a contract that "the
      fold values a planned row's reservation at the reader's now", which this makes false.
      **Scoped honestly:** `_flows`' docstring does NOT claim the leaf is clock-free --
      `live_amount_overrides` can still be built from a wall-clock read one package over
      (`loan_payment_service.live_loan_transfer_amounts` calls `date.today()` for a derive-mode
      loan-payment shadow), which is finding **N-40**, unchanged here.
      Full suite 7592 (7594 at HEAD; the delta is exactly the two deleted tests, confirmed by
      diffing collected test ids rather than by subtraction). pylint 10.00 on all three trees.
    - [x] **X-c2c2** `test(cash): the reservation and flow rules move to their own leaf`
      -- **SHIPPED in three sub-commits (2026-07-26): X-c2c2a `227c2479`, X-c2c2b `ed7e220c`,
      X-c2c2c `690fdd5d`.** NO production change in any of them, so the baseline provably cannot
      move -- and the arithmetic says so per sub-commit, reconciled by DIFFING collected test ids
      rather than by subtraction: a 23-out / 23-in at a, a 10 / 10 at b, and 5 out / 3 in at c
      (7592 -> 7590), the only non-zero delta in the step and a DE-DUPLICATION rather than a loss.
      The split is by LEAF SUBMODULE, mirroring the cohesion split D1c made in production:
      `test_cash_amounts.py` (what ONE row is worth), `test_cash_flows.py` (what a SET sums to),
      and `test_cash_ledger.py` left as the FACTS file -- the relocation that file's own docstring
      anticipated as "its own commit", now done, including the promotion it named
      (`_test_helpers.add_entry` gained the three bucket flags, so neither new suite carries a
      private entry builder). `test_balance_calculator_entries.py` deleted whole.

      **The KEEP correction (developer ruling 2026-07-26, on the trace).** This step's text said
      tests discriminating a `_calculator` rule are DELETED here. **They are NOT; they stay until
      X-c2c4 deletes the module.** Ruling R-V moved X-c2c4 to after X-g, and `_calculator` is LIVE
      production code until then -- `_cash_engine.balances_for:188` calls it and `_investment.py:109`
      / `:458` call that for the modelled bases -- so deleting its tests here would leave a live
      producer untested across the arc's largest step, and X-g grades its successor against exactly
      that producer. The rule is the arc's own: **delete a test with the code it tests, never
      before it** (X-c2b3's "a delta of exactly the 16 the four retired suites held"). Two
      anchor-arm tests therefore MOVED INTO `test_balance_calculator.py` rather than dying, and 79
      `calculate_balances(` call sites across 10 files stay put for X-c2c4.

      **Four corrections to this step's own text, all measured.**
      (a) "`test_balance_calculator_entries.py` (27 tests) is the three-bucket reservation formula"
      -- it is **18**. Seven are status gates and reductions over a SET (that is `_flows`), and two
      discriminate which ARM of `_calculator` calls the shared reduction.
      (b) "`TestEffectiveAmountFix` (8) is `Transaction.effective_amount`" -- **one** of those eight
      grades the property; seven assert through the walk. What actually graded it was a
      156-line test in a class the text never named, and **six of its nine cases duplicated**
      `test_computed_properties.py`'s `TestTransactionEffectiveAmount`. The three that did not (a
      Projected row preferring its actual, a Projected row with a ZERO actual, a soft-deleted row)
      moved there as tests of their own; the duplicates deleted rather than moving a weaker second
      copy beside the stronger one.
      (c) `TestTransferInvariantsBalanceRegression`'s "three of four touch no producer" understates
      it: those three DUPLICATE `test_transfer_service.py`'s `TestInvariants`, which is stronger
      (it re-checks each invariant after an UPDATE). Deleted with a citation.
      (d) **The 52-period drift oracle is NOT ported here.** The plan put the port in this step
      because this step deleted the file; under the KEEP correction
      `test_52_period_penny_accuracy` discriminates `_calculator`'s roll-forward, stays with it, and
      the port becomes a **prerequisite of X-c2c4**. Porting early would grade a producer against a
      successor that does not exist yet. `test_interest_accrual.py`'s `_layered` base builder moves
      with it for the same reason.

      **Two firing controls found real defects in the NEW tests, both the N-69 shape, and both were
      inherited rather than introduced.** (1) Routing the income leg through `_expense_amount`
      failed NOTHING: an income row carrying no entries prices identically under either rule, so
      the test named "income never takes the entry formula" did not test that -- and neither did
      the version it moved from. It now carries a `$500.00` credit entry, the only shape that can
      tell the rules apart. (2) Deleting the `is_projected` gate fails the SETTLED test alone; a
      Cancelled or Credit row is OVER-DETERMINED, because `effective_amount` independently returns
      `0` for a status flagged `excludes_from_balance`. Those two tests stay (the property is real
      and user-visible) with the class docstring stating plainly that they are not evidence the
      gate works.
      **Twelve firing controls across the three sub-commits, each shown to fire precisely its
      intended tests.** pylint 10.00 on app/ and every touched test file; tests/ decimal gate clean.
    - [~] **X-c2c3** `refactor(balance): the modelled bases read the fold`
      -- **CANCELLED 2026-07-26 (developer ruling R-V, as recommended). NOT superseded by a
      renumbering: the ID is retired in place, per rule 2's append-only discipline, so the ~30
      citations of it resolve to this cancellation rather than to nothing.** It was to window
      `investment_base_balance_map` and `build_appreciation_balance_map` onto the fold over the
      anchor-forward periods, keeping their ruled pre-anchor models out of `_merge_balance_sources`'
      preference order (N-43). **X-g replaces those bases outright instead**, which is what N-72
      already said would make this unnecessary; R-V's three measured grounds are in Section 4.
      Two things it had settled are NOT discarded and carry forward to X-g, because they are facts
      about the code rather than about the window: **correction (b)'s ruling that the DATED SoT
      governs** (`resolve_anchor(...).period.id`, not the `current_anchor_period_id` CACHE column
      `_assemble_investment_projection_inputs` pivots on -- when the two disagree the pivot misses
      and the whole investment map projects from `ZERO`), and **correction (a)'s four discriminating
      fixture SHAPES**, which X-g needs for the same reason this step did: real data cannot grade a
      modeled base when all four accounts hold zero transaction rows.
    - [ ] **X-c2c4** `refactor(balance): the last cash producer deletes` -- pure deletion, no
      behaviour. **RUNS AFTER X-g, not after X-c2c2** (ruling R-V): its precondition is that
      `_cash_engine.balances_for` has no caller left, and X-g's replay is what takes the last two
      -- exactly as the cancelled X-c2c3's window would have. Do not attempt it earlier; the
      C3b3 prove-the-successor-first precedent is the whole reason this is a separate commit.

      **It now carries what the KEEP correction deferred into it, and none of this is optional.**
      (i) The `_calculator`-discriminating tests die HERE, with the module: the anchor arithmetic,
      the roll-forward, the pre-anchor skip, `_detect_stale_anchor`,
      `TestTheAnchorArmAppliesTheSharedReduction`, `TestEffectiveAmountFix`'s seven
      walk-composition tests, and `TestTransferInvariantsBalanceRegression`'s surviving one.
      (ii) **`test_52_period_penny_accuracy` must be PORTED onto the fold FIRST, not deleted with
      the rest.** It walks 52 periods of mixed statuses against an independent `Decimal` oracle for
      cumulative drift, and `test_cash_fold_parallel.py` has NO long-horizon drift oracle -- so
      deleting it unported is the coverage hole a deletion step must not open. The port needs 52
      real pay periods and their rows (the fold queries; the original uses `FakeTxn`), and its
      oracle must stay a test-local running total, never the fold reading itself (Section 7.2).
      (iii) `test_interest_accrual.py`'s `_layered` base builder becomes a test-local roll-forward
      over `sum_projected` -- correct rather than a mirror, because the production roll-forward is
      what deletes and `_layer_interest` takes any base it is given.
      (iv) **79 `calculate_balances(` call sites across 10 files** re-point or die with the module
      (`test_audit_fixes.py`, `test_hostile_qa.py`, `test_loan_payment_pipeline.py`,
      `test_workflows.py`, `test_accounts.py`, `test_transfers.py`, `test_grid.py`,
      `test_interest_accrual.py`, `test_carry_forward_service.py`, `test_balance_calculator.py`),
      along with the `calculate_balances` 2-tuple collapse deferred here from X-c2b3 -- read every
      site, never sed, because a left-behind `balances, _ =` unpacks dict KEYS instead of raising.
      `_cash_engine` and `_calculator` WHOLE (with `_detect_stale_anchor` and the
      `calculate_balances` 2-tuple, deferred here from X-c2b3 because the module deletes and the
      mechanical arity edit would be written and then discarded),
      `cash_ledger.load_balance_transactions` with its W9909 non-producer ruling
      (`balance_seam.py:261`), and the two vacuous static-guard arms from correction (c). The doc
      sweep is part of the deletion, not a follow-up: ~30 sites
      in `app/` name a deleted producer, and three state a contract the change makes FALSE --
      `pay_period_service._reject_overlapping_batch` ("the surviving index-ordered walks ... skip
      the out-of-order period outright and silently drop its transactions"),
      `savings_dashboard_service/_net_worth.py:118` (the dense-domain rationale, whose RULE
      survives -- the modeled bases still need the anchor period present -- while its producer name
      does not), and `cash_ledger._facts.resolve_anchor`, which cites `balances_for` for its own
      `scenario_id` rationale. Also `_pulse.html:166`, a template comment naming `balances_for` as
      the source of the chart's end-station figure.
  **Deletion-list corrections.** (a) **X-c1, 2026-07-25:** `cash_ledger.period_subtotal` /
  `period_subtotals` / `PeriodSubtotal` belong on the list and the one-liner omitted them.  R-K
  changes what a subtotal COUNTS, so `X-c1`'s `CashPeriodFigures` is their successor, not a peer:
  leaving them would ship two per-period reductions on two different bases, which is the shape this
  phase exists to end.  Their W9909 rulings go with them.  Their only production consumer is
  `grid._build_grid_subtotals` (plus the three mobile / plan templates reading `.income` /
  `.expense` / `.net`), and `sum_projected` -- the shared per-row engine both bases call -- STAYS.
  (b) **X-c2 trace, 2026-07-25:** `cash_ledger.load_balance_transactions` joins it and
  `period_subtotal` (singular) is ALREADY production-dead -- see **N-46**.
  (c) **X-c2b2 as built, 2026-07-26:** `calculate_balances_with_interest` came OFF X-c2b3's list
  (deleted at b2 with the rule it composed), `_layer_interest` came off it (moved, not kept), and
  `_daily_series` is orphaned from b2 -- zero importers, deleting at b3 as scheduled.
- [ ] **X-g** `feat(balance): a modeled asset is an event stream` -- **the from-scratch design, and
  the last step of the arc that can move a FIGURE.** ACCRUAL becomes the fourth event kind and all five account
  kinds read ONE sequential replay. Target shape, deletion list and measured scope: **Section 3.2**.
  Forks: **R-R, R-S, R-T, R-U and R-W in Section 4, and all five are ANSWERED** (developer ruling
  2026-07-26, all as recommended) -- scope AND design are now traced.  **X-g1's own trace then found
  three more -- R-X, R-Y, R-Z -- and they are ANSWERED too** (same date, all as recommended);
  Section 3.2 carries what they changed about this step's design.

  **Why it exists, in one line:** the modeled kinds are the last place where a balance is three
  producers spliced by a preference order, which is the shape this whole arc exists to delete.

  **It is sequenced after X-c2c2 and BEFORE X-c2c4 (ruling R-V, 2026-07-26), and its prerequisite
  is now MET.** The earlier plan put X-c2c3's window in front of it -- a COMPENSATOR for the very
  merge this step deletes (**N-72**) -- and that step is CANCELLED, so this one replaces the
  modeled bases directly and X-c2c4's deletion follows it. **X-c2c2 SHIPPED 2026-07-26**
  (`227c2479` / `ed7e220c` / `690fdd5d`), which is what this step wanted from the X-c2c block: the
  `cash_ledger` rules and model invariants that used to be reached THROUGH the dying producer are
  now tested against their own homes, so this step's cutover does not have to reason about them.
  **What is NOT done, and belongs to X-c2c4 rather than here:** `_calculator` is still live and
  still carries its own tests (the KEEP correction), so this step's parallel run has a fully-pinned
  incumbent to grade against -- which is the point of that correction. What it INHERITS from the
  cancelled X-c2c3 is stated at that step: correction (b)'s ruling that the DATED SoT governs the
  anchor pivot, and correction (a)'s four discriminating fixture shapes.

  **The price of the cancellation, carried here so this step owns it:** findings cash D1 and cash D3
  stay open for INTEREST / INVESTMENT / APPRECIATING until this ships. That is `$0.00` on real data
  only because all four affected accounts hold zero transaction rows, which is a property of the
  DATA and flips the day a contribution is recorded as a transaction and settles.

  **THE TRACE IS DONE (2026-07-26) and all five forks are RULED.** It was this step's stated first
  action -- "answer them with evidence rather than from the shape of the problem" -- and no code was
  written for it. What it changed, so the build starts from the corrected picture rather than from
  the one this entry carried before:

  * **R-S inverted.** The reverse projection is not a ruled model the fold would damage; it is the
    defect. 15 recorded assertions across the three modeled accounts, of which each map reads only
    its own LATEST -- **3 honoured, 12 overridden by a model** (**N-74**) -- and a future
    contribution rewrites a past balance (**N-75**). The `-$6,315.57` this step was braced to justify
    is a move TOWARD the user's own records.
  * **R-R's double count is a confirmed mechanism and is NOT live.** Both feeds are empty on today's
    data (0 deductions targeting an investment account, 0 shadow contribution rows), and the
    partition ruling makes them disjoint by construction rather than by a de-dup rule. Measured on
    six rolled-back `$500.00` transfers: `$3,000.00` over six periods under a naive union.
  * **R-T is cheap and the SAMPLER does not change** -- `+0.5 ms` per account per full-horizon read,
    and `_fold.sample_cumulative` (shared with the loan fold) is untouched. Section 3.2's earlier
    "make the step's delta a function of the running total" is corrected there.
  * **A fifth fork existed and nobody had named it (R-W / N-76).** The grid and `/savings` already
    answer one modeled account two ways -- `$17,642.13` on the Empower 401(k), `$21,675.99` on the
    Property in `shekel` -- so this step also unifies the GRID, which grows its render surface and
    is why the decomposition below is four commits rather than three.
  * **Two accrual BASES disagree today** (period-END for INTEREST, period-START for growth), which
    the daily replay dissolves; recorded in Section 3.2's deletion list rather than as a finding,
    because nothing survives it to record against.

  The rulings and every figure behind them are in Section 4. The probes that produced them were
  read-only (the R-R one wrote inside a transaction it rolled back) and live in the session
  scratchpad, not the repo -- `tests/manual/verify_balance_baseline.py` is the saved harness this
  step's cutover is DIFFED with (Section 7.2).

  **X-g1 IS BUILT and this paragraph is the record of how** -- kept because X-g2 / X-g3 are held to
  the same standard.  **Build it the way the loan fold was built, and the way the cash fold was
  built** -- the discipline
  the arc has now proved seven times (C3a->C3b, C6a->C6b, C8a->C8d, C9a->C9b, E1c->E1d, X-b->X-c2b2,
  X-c1->X-c2b1): the replay ADDITIVE and unwired, graded on a HAND-COMPUTED oracle (never a shipping
  producer as its own reference, finding N-7) PLUS an every-period and every-day parallel run against
  all three shipping bases, with **every divergence explained and signed off** -- equality is NOT
  the pass condition, because the divergences ARE findings N-71 and N-43's class. Sampling is
  forbidden (a 14-day sample once scored perfect while wrong by `$178,103.41` on 22% of days). Then
  the cutover, then the grid, then the deletion -- the four commits below.
  **The real-data side of that run does not need writing: use
  `tests/manual/verify_balance_baseline.py`** (Section 7.2), which already captures
  `investment_seed_map` and `investment_growth_since_anchor` -- the two seam entries this step
  changes -- precisely so its cutover can be DIFFED rather than argued. It is the regression check;
  the hand-computed oracle and the firing controls are the proof.
  **One inherited claim the trace CORRECTED, and it matters for how this step is graded.** X-c2c3's
  correction (a) said the real-data half is nearly VACUOUS on these four accounts because they hold
  zero TRANSACTION rows -- true for the CASH basis, and false for this step. They hold **15 ASSERTION
  rows** (Roth 6, Trad IRA 6, Empower 3), and an assertion is exactly what discriminates the replay
  from the reverse projection: the shipped map answers `$26,604.63` where the assertion says
  `$23,851.08` (N-74). So real data DOES grade the pre-anchor half here. It still cannot grade the
  contribution half (both feeds empty, ruling R-R), which is why X-c2c3's four discriminating FIXTURE
  shapes are inherited for that half alone.

  **The decomposition, DECIDED FROM THE TRACE** (2026-07-26; the entry previously said to decide it
  here, which is what the trace was for). Four commits, and the split is the arc's own
  additive-then-cutover line with the render side taken out of the money-moving commit exactly as
  X-c2b1 / b2 / b3 did:

  * [x] **X-g1** -- the modelled replay, seam-private and UNWIRED
    (`app/services/balance_at/_asset_fold.py`). **SHIPPED `17ead4c5` (2026-07-26).**

    **Its three forks were traced and RULED before any code was written** -- R-X (the accrual's
    rounding), R-Y (does the anchor period accrue?), R-Z (the contribution's date and boundary),
    all in Section 4, all as recommended. R-T's own `+$0.14` / `+$1.73` measurement was reproduced
    to the cent FIRST so R-X's comparison was like-for-like, and every figure N-43 / N-71 / N-74 /
    N-76 carries was independently re-derived on both databases before being relied on.

    **The structural result, and it is the step's whole claim:** a modelled asset IS its cash fold
    plus two more event kinds. The replay takes `_cash_fold.assemble`'s whole record -- the seed,
    the three tiers' dated deltas, and the WALK it reads the latest assertion off -- and resolves
    CONTRIBUTION and daily ACCRUAL onto the SAME running total in ONE sequential pass, after which
    `_fold.sample_cumulative` (shared with the loan fold) is untouched. So there is no second cash
    basis to keep in step, and `_cash_fold._assemble` / `_AssembledFold` became `assemble` /
    `AssembledCashFold` rather than being reached through a private import.

    **Three shared rules were EXTRACTED rather than restated**, which is what keeps the daily
    reader from carrying a second copy of a financial rule: `interest_projection.accrued_interest`
    (the day-count rule, UNROUNDED, with `calculate_interest` now its cent-quantized wrapper), and
    `investment_projection.deduction_contribution_per_period` / `employer_contribution_params`
    (promoted from private, because ruling R-R needs the DEDUCTION half of a contribution feed
    without `_average_transfer_contribution` folded into it).

    **One thing the trace found that this document did not carry: the contribution tier is NOT dead
    on real data.** Ruling R-R measured both EMPLOYEE feeds empty and this plan reads as if the
    whole tier moves `$0.00`; the Empower 401(k) carries `employer_contribution_type =
    flat_percentage` at 5% of `$3,631.74` gross = **`$181.59` a period, `$9,624.27` over the
    horizon**, and a flat-percentage employer does not read the employee amount at all.

    **A second: the daily grain also fixes a MONTHLY / QUARTERLY day-count defect.**
    `calculate_interest` reads `monthrange(period_start)` once for the whole window, so a 14-day pay
    period straddling a month boundary prices every one of its days against the FIRST month's
    length. Measured on `$10,000` at 3.29% over 2026-01-29 .. 02-11: **`$12.38` against a
    day-by-day `$13.42`, short by `$1.04` on ONE period** (8.4%). The real Money Market compounds
    MONTHLY. Same class as the "13 days of a 14-day period" note the interest path already carries,
    on the two frequencies that note did not reach.

    **Verification.** Baseline **BYTE-IDENTICAL on BOTH databases** (`verify_balance_baseline.py`,
    345,213 and 393,217 bytes of seam figures, the HEAD side run from a `git worktree`) -- what an
    additive step must prove, and load-bearing here because the step DID change two live modules.
    Full suite **7590 -> 7624**, a delta of exactly the 34 tests added. `pylint app/` 10.00/10 under
    the full `--fail-on` set, `scripts/` 10.00, the `tests/` decimal gate clean.

    **34 tests and 22 firing controls, with every one of the 34 firing on at least one control.**
    25 hand-computed oracle tests (`test_asset_fold.py`) -- never a shipping producer as a reference
    (N-7), because the producers are wrong about exactly the cases the replay exists for -- plus a
    9-test parallel run (`test_asset_fold_parallel.py`) that CLASSIFIES every divergence into three
    named classes rather than demanding equality. Its strongest single result is an EQUALITY: on the
    shape that isolates the grain, the daily replay tracks `growth_engine.project_balance` (seeded
    at the replay's own anchor value) to **within a cent on every post-anchor period, eight of nine
    EXACTLY** -- because `period_return_rate` over a 14-day span is the 14th power of the one-day
    rate, so the grain is a re-grouping of ONE curve rather than a second model.

    **Its adversarial review found a defect in its own tests: the R-R partition pin was VACUOUS.**
    The fixture had no contribution feed at all, so the modelled tier was absent and the test could
    not tell a partition from a union -- N-69's shape, and Section 7.3's whole argument, since the
    firing control caught it and the reasoning did not. A second test named itself "lands on the
    payday" while asserting only at the PERIOD, where a payday and a period end are
    indistinguishable; it now asserts the day-by-day balances (`$20,051.97` on 01-15, `$20,555.78`
    on 01-16). Three of the file's hand-computed figures were wrong on first write and the code was
    right in all three.

    **Measured cost, and it is a saving.** `asset_period_view` over 60 periods, best of five, on the
    prod-shape clone against the producer it replaces: Roth `15.4 -> 3.8 ms`, Trad IRA
    `14.7 -> 3.7`, Empower `14.5 -> 5.5`, Money Market `15.2 -> 16.2`, Fidelity Savings
    `3.8 -> 5.7`, Home `1.6 -> 3.2`. The daily grain costs `+0.6` to `+2.9 ms` over the cash fold
    alone (R-T budgeted `+0.5`, from a synthetic bench that did not include the per-day rate call);
    the investments get CHEAPER because the growth engine's per-period walk and its whole input
    assembly are gone.

    **Three of this document's own citations drifted with the extraction and are corrected here**
    (Section 7.6's rule, applied to what this step touched): ruling R-R's
    `investment_projection.py:421-424` -> `:444-447` and
    `current_period_transfer_contribution:498` -> `:523`, and Section 3.2's
    `growth_engine.py:387-392` -> `:388-393`. Each was re-read at its new line, not arithmetic.

    **Headroom for X-g2, stated so it is not discovered:** `_asset_fold.py` is **961 lines** against
    pylint's 1000-line default (`.pylintrc` sets no `max-module-lines`). If X-g2 needs more than
    ~35 lines here, split the contribution tier into its own module deliberately -- plan step D1c's
    cohesion split, again -- rather than trimming a docstring to fit under a gate.
  * **X-g2 (DECOMPOSED, 2026-07-26, ruling R-AA)** -- THE cutover, split on the arc's own
    additive-then-cutover line because tracing measured roughly two thirds of its diff as refactor
    and plumbing that cannot move a cent. Its four forks are **R-AA, R-AB, R-AC and R-AD in
    Section 4, and all four are ANSWERED** (developer ruling 2026-07-26, all as recommended).
    * [x] **X-g2a** `refactor(balance): one assembly serves the cash columns and the modelled tiers`
      -- **SHIPPED `5cb26d09` (2026-07-26).** The SHAPE, baseline-identical because NO production
      reader changed. Four pieces,
      each a prerequisite the cutover would otherwise carry: (1) the contribution tier splits into
      `balance_at/_asset_contributions.py` -- `_asset_fold.py` stood at **958 lines against
      pylint's 1000 default**, the headroom X-g1 flagged, and the split is plan step D1c's cohesion
      line (that module answers "what is this asset worth on each day", this one answers "what does
      payroll put in, and when"); (2) assembly splits from resolution on BOTH sides --
      `_cash_fold.period_view_of(folded, periods)` and `_asset_fold.resolve(account, ctx, cash,
      horizon_end, inputs)` / `period_columns(folded, periods)` -- so the grid can regroup ONE
      `AssembledCashFold` into cash columns AND resolve the modelled tiers over it, instead of
      assembling the account twice (the constraint recorded under Section 4's R-AA block); (3) the
      three loose projection inputs become `ContributionInputs`, a named bundle with an `absent()`
      constructor, because SEVEN call sites now hand them over and THREE of those hand nothing --
      three `too-many-arguments` disables die with it; (4) **finding N-77**: `make_investment_account`
      and `make_appreciating_account` pin their opening assertion to the anchor period's first day,
      the X-c2a precedent, because X-g2b makes that date load-bearing for every such fixture in the
      suite. Plus the two entries the cutover consumes, ADDITIVE and unwired: `asset_seed_at`
      (ruling R-AB's date-keyed seed) and `asset_growth_at` (ruling R-AC's decomposition).

      **As built, three corrections and one thing the one-liner did not carry.**
      (a) **`resolve` takes NO context**, which the one-liner's signature did. Its adversarial
      review found that a `(ctx, cash)` pair is exactly the disagreement mode Section 8 names: a
      caller could hand a fold assembled at one scenario and a context carrying another, and the
      modelled tier would then load its contribution feed against a row set the cash tiers beneath
      it never saw. `AssembledCashFold` gained a `scenario_id` -- the record now says what it was
      scoped by -- and `resolve` reads it off the fold, so the disagreement is removed rather than
      documented and the signature loses a parameter.
      (b) **A firing control found one of the new tests VACUOUS**, which is the N-69 shape and
      Section 7.3's whole argument again. The kind-guard pin read the ANCHOR period alone, where
      ruling R-Z's STRICT boundary skips a payday for every kind -- so deleting the guard it exists
      to lock left it GREEN. It now reads period 1, whose payday is strictly after the assertion,
      and the control fires.
      (c) The `_401k` fixture wrapper's stated reason ("`make_investment_account` leaves the opening
      at the wall clock") became FALSE with N-77 and was rewritten to what it is now for: several
      cases need the assertion on a day OTHER than the period's start.
      (d) `_salaried_deduction` was promoted from a method to a module-level helper, because three
      classes now build the feed and a fixture WITHOUT one cannot tell ruling R-R's partition from a
      union.

      **Verification.** Baseline **BYTE-IDENTICAL on BOTH databases** (`verify_balance_baseline.py`,
      393,217 bytes on `shekel` and 345,213 on `shekel_f3_final`, the HEAD side run from a
      `git worktree`) -- and it is load-bearing rather than vacuous here, because the step DID change
      two LIVE modules: `_cash_fold.assemble` gained a field and `cash_period_view` now routes
      through `period_view_of`, which the grid reads on every render. Both loans unmoved (Mortgage
      `$177,277.97` on both databases; Van Loan `$15,663.59` / `$15,205.63`). The test arithmetic
      agrees, reconciled by DIFFING collected test ids rather than by subtraction:
      **34 -> 41 in the two files, seven ADDED and ZERO removed**, full suite **7624 -> 7631**. 8 hand-computed oracle tests (three of the four figures the seed tests
      assert were computed by hand from this file's own daily accrual table before the code was
      run). **Six firing controls, each shown to fire precisely its intended tests:** the seed
      stops filtering ACCRUAL (3), the seed also filters CONTRIBUTION (1), the decomposition swaps
      its two tiers (2), a tier total is read a day early (5), the contribution tier drops its KIND
      arm (1), and the decomposition hides a zero result the way the shipped producer does (1).
      N-77's own control: reverting the helper's restamp fails
      `test_pre_anchor_periods_agree_and_the_anchor_period_accrues`, the one Property fixture that
      no longer restamps for itself. `pylint app/` 10.00/10 under the full `--fail-on` set,
      `scripts/` 10.00, 146 checker tests, the `tests/` decimal gate clean.
      **Scoped honestly:** the assembly SHARING has no firing control of its own here, because
      nothing reads both halves until X-g2b wires the grid -- a test asserting
      `period_view_of(assemble(x)) == cash_period_view(x)` would be arithmetically true and
      therefore vacuous (Section 7.2's forbidden shape). What is pinned here is that the extraction
      changed no answer; the payoff is verified at X-g2b.
    * [x] **X-g2b-0** `test(manual): the baseline harness reads the modelled scalar at a DATE`
      -- **SHIPPED `1fd41e1f` (2026-07-27).** Correction (d) below, and a PREREQUISITE rather than
      part of the cutover (the X-c0 / X-a1 precedent). `verify_balance_baseline.py` gains the
      kind-correct scalar at its fixed dates, so the two regions X-g2b moves most -- a mid-period
      date-precise read (N-71) and the pre-horizon back-projection (N-74) -- are visible in the diff
      instead of falling between the today scalar and the period-end map. A SIXTH date joined the
      tuple (2029-01-01, past the horizon) so both ends where a period-keyed producer and a total
      fold differ are pinned; ruling R-AG lives at the far one. No production change and no
      test-suite change: the file is outside pytest's collection, so the arithmetic that proves it
      moves nothing is that `app/` is untouched. Verified DETERMINISTIC over two consecutive runs,
      on both databases.
    * [x] **X-g2b-1** `refactor(investment): the dashboard service splits on its chart line`
      -- **SHIPPED `ca0bd00d` (2026-07-27).** NOT in the plan as decomposed: it exists because
      X-g2b's additions found `investment_dashboard_service.py` standing at **EXACTLY 1000 lines
      against pylint's 1000-line default**, so the next change of any size would have fired the gate
      whatever it was. Splitting on a cohesion line rather than trimming a docstring to fit is plan
      step D1c's rule, restated in X-g1's own headroom note; the developer chose the PACKAGE shape
      its sibling `savings_dashboard_service` has carried since Loop B, so the two dashboards are
      organised one way instead of two: `_context` (321) / `_cards` (292) / `_chart` (330) /
      `_orchestrator` (112). The package keeps the module's name, so no importer moved. Full suite
      7631, the SAME count as HEAD, reconciled by diffing collected ids.
    * [x] **X-g2b** -- THE cutover, and the only step of X-g that moves a figure.
      **SHIPPED `560b3339` (2026-07-27).**
      `build_investment_balance_map`, `build_appreciation_balance_map` and
      `_interest.layer_account_interest` are replaced by the replay, and the kernel's per-kind
      ladder collapses with `base_account_balance_map` (ruling R-AD); the kind-correct SCALAR goes
      date-precise for INTEREST / INVESTMENT / APPRECIATING, which is what closes **N-71** and takes
      `find_period_containing_date` and the pre-horizon anchor fallback out of `_kind_correct`
      entirely (**N-29** closes with them); the GRID's interest row reads the replay in this same
      commit, because INTEREST is byte-identical across `/grid` and `/savings` today and must stay
      so; **`investment_seed_map` DELETES with no successor** (ruling R-AE corrects R-AB's
      `investment_seed_at`: with the seed read a day before the window there is no overlap left for
      a filter to prevent, so the seed is `balance_at(account, ctx, window_start - 1 day)` and
      X-g2a's additive `asset_seed_at` deletes unwired) and
      `investment_growth_since_anchor` becomes a sum over the replay's two modelled tiers (ruling
      R-AC) -- both are seam entries `verify_balance_baseline.py` already captures, so the cutover is
      DIFFED rather than argued; the two chart seeds (`investment_dashboard_service.py:249`,
      `retirement_projection.py:492`) re-point and their `current_period_transfer_contribution`
      subtractions delete with the overlap; the `/investment` chart's projection axis opens the day
      after its history line's last valued date (ruling R-AF), which is what makes the two lines
      join. Two contract statements the change makes FALSE are
      corrected in-commit rather than swept later: `savings_dashboard_service/_net_worth.py:117-122`
      ("the INVESTMENT and APPRECIATING paths still seed off the anchor-forward producer ... a
      forward-only period list would starve them of their seed") and `_kernel.py:23-29`. Money
      moves; every figure signed off. **N-73's five NULL-anchor guards STAY** -- the replay stops
      READING the anchor period, but deleting the `| None` contract reaches every consumer and is
      X-e's, per that finding's own text.

      **THE OPEN FORK IS RULED (developer ruling 2026-07-27, all three as recommended) and the
      trace that ruled it found two more; R-AE, R-AF and R-AG are in Section 4.** No code was
      written for it. What it changed, so the build starts from the corrected picture:

      * **The recorded fork's premise INVERTED.** This entry predicted the junction would be
        `$0.00` while the three investment accounts hold zero transaction rows. They do hold zero
        rows, in BOTH databases, and the junction is `-$76.99` / `-$73.97` / `-$15.96` on `shekel`
        under R-AB as written. It is not a property of the data: rulings R-U and R-AB correct ONE
        overlap TWICE, and ruling **R-AE** drops the ACCRUAL filter that was the second correction.
        `asset_seed_at` therefore DELETES rather than shipping (X-g2a built it additive and
        unwired, which is exactly what made this cheap to reverse), and the seed becomes the
        ordinary date-precise scalar.
      * **The junction is removed rather than captioned** (ruling **R-AF**): the axis starts the day
        AFTER the history line's last valued date. Verified on both databases -- seed == the
        history line's last point EXACTLY, first step `$105.66` against a second of `$106.07`.
      * **The scalar stays total past the horizon** (ruling **R-AG**), and finding **N-82** records
        the contribution tier's silence out there.

      **Four corrections to this entry's own text, all traced 2026-07-27.**
      (a) **Two seam accessors are missing from the list and move with it.**
      `_kernel.interest_projection_for_account` and `interest_by_period_for_account` share
      `_account_interest_projection` -> `_interest.layer_account_interest`, which this step
      replaces, so the account-detail balance chart AND its "Interest, next 12 mo" chip
      (`routes/accounts/detail.py:280`) move in the same commit. That is finding N-47's shape a
      THIRD time -- a reader the one-liner did not name because it shares a producer rather than a
      call site.
      (b) **This step introduces a defect unless it is fixed in-commit.**
      `investment_dashboard_service.compute_balance_hero_cell` (the anchor editor's Cancel / Escape
      / 409 revert target) reads the SCALAR while the headline it restores reads
      `balance_map[current_period]`. They are identical today only because the scalar IS the period
      read; the date-precise switch separates them by the accrual from today to the period's end --
      measured `$9.65` to `$26.05` across the three accounts on both databases. Both read ONE
      producer here (**N-81**); the deeper question of whether a "current balance" should be a DATE
      or a period END is NOT opened -- `/savings` has shown the period-end figure for every kind
      since X-c2b2 (Checking `$2,824.26` at today against `$2,683.63` in its current column), and
      changing that convention is a different step.
      (c) **Two citations drifted.** Finding N-29's seam call site for
      `find_period_containing_date` is `_kind_correct.py:265`, not `:278`; and N-73's five
      NULL-anchor guards become FOUR here, because `base_account_balance_map` dies with its guard
      (ruling R-AD). The remaining four still stay for X-e, per N-73's own text.
      (d) **The regression harness is BLIND to the region this step moves most, and that is fixed
      FIRST.** `verify_balance_baseline.py` captures the kind-correct scalar only at today and the
      kind-correct map only at period ENDS -- so a mid-period date-precise move (N-71) and the
      pre-horizon back-projection (N-74) are both invisible in its diff. It gains kind-correct
      scalars at its five fixed dates as **X-g2b-0** before the cutover runs, on the same ground the
      harness itself was a commit of its own (`60dbc117`): the instrument is built before the
      measurement, never alongside it.

      **What the cutover moves, measured 2026-07-27 on BOTH databases** (the sign-off list this
      step is checked against; the three investment accounts and the Property hold ZERO transaction
      rows in both, so none of it is data-dependent):

      | account | scalar at today | current column | worst column | columns moving |
      |---|---|---|---|---|
      | Checking / S8 Checking (PLAIN) | `$0.00` | `$0.00` | `$0.00` | **0 of 60** |
      | Fidelity Savings (INTEREST) | `-$1.46` | `$0.00` | `+$0.04` | 52 of 60 |
      | Money Market (INTEREST) | `-$249.69` (`shekel`) / `+$0.45` | `+$1.34` / `+$1.42` | `+$2.75` | 52-53 of 60 |
      | Roth IRA (INVESTMENT) | `+$53.87` / `+$82.67` | `+$76.99` / `+$105.26` | **`-$3,408.27`** / `-$2,753.55` | 60 of 60 |
      | Trad IRA (INVESTMENT) | `+$19.33` / `+$35.30` | `+$29.00` / `+$44.95` | `-$1,234.76` / `-$1,185.36` | 60 of 60 |
      | Empower 401(k) (INVESTMENT) | `+$52.52` | `+$78.57` | `-$2,670.63` | 60 of 60 |
      | Property (APPRECIATING) | `+$85.24` (`shekel`) | `+$170.50` | `+$180.67` | 54 of 60 |
      | Mortgage / Van Loan | **UNMOVED** -- the standing regression gate | | | |

      **PLAIN moving `$0.00` on every one of 60 columns and on the scalar, for all three real plain
      accounts across both databases, is what makes ruling R-AD's ladder collapse safe** rather than
      merely intended: an account with no modelled return IS its cash fold, and the replay proves it
      on real data before the ladder that used to route it is deleted. The INTEREST pennies are the
      daily grain plus X-g1's monthly day-count fix; the INVESTMENT pre-anchor columns are N-74
      (the fold reproducing assertions the merge overrode); the Property column is ruling R-Y's
      anchor-period accrual.

      **AS BUILT.** Every figure above verified, plus four the table did not cover: the growth chip
      now reconciles to the cent with the headline it explains (Empower `31,070.06 + 318.16 +
      363.18 == 31,751.40`), and `/retirement`'s projected balances move **`+$4,764.02` /
      `+$601.52` / `+$257.00`** because the seed stops discarding the anchor-to-today growth
      (correction (e) below). Ruling R-K's grid identity holds on **0 breaks of 59** period pairs
      for every non-loan account on both databases; ruling R-R's partition was exercised on real
      data inside a ROLLED-BACK transaction -- six `$500.00` projected transfers into the Roth are
      counted ONCE each and then compound (`+$501.92` .. `+$3,040.54` across their six periods)
      where a naive union adds `$3,000.00`; and **17 of 20 surfaces live-render** on both databases
      (the other three are documented deprecation redirects). Nine firing controls each fire
      precisely their intended tests, plus a tenth that fires in BOTH directions. Full suite 7632,
      `pylint app/` 10.00/10 under the full `--fail-on` set, `scripts/` 10.00, 146 checker tests.

      **TWO adversarial reviews ran before the commit and BOTH found real defects** -- which is the
      entry worth reading, because each was a compensator whose premise the step had deleted.
      (i) Pointing the property equity hero at the seam **double-counted appreciation in its own
      chart**: the value became as-of the current period's END while `_value_series` still
      compounded from `today`, so every forward month carried that span twice. Its deeper point was
      that finding **N-83 is ruled out of scope for a money-moving cutover by this document itself**
      -- and the collateral proved it, because the second half (a period-end value netted against a
      today-dated debt leg) could not be fixed without dragging the loan tile's own date convention
      in too. REVERTED; the cross-page tests now assert N-83's gap EXPLICITLY from both sides, so it
      cannot drift and N-83's own commit must update them. (ii) **The annual-limit YTD seed was one
      pay period behind its window.** `ytd_contributions_seed` is the total STRICTLY BEFORE the
      current period, and its justification was that the engine's window CONTAINED that period and
      would charge it; ruling R-AF moved the window past it, so nothing charged it at all. On a
      `$23,500` limit at `$1,000` a period with today in the year's 15th, the chart prices `$9,500`
      of remaining room where `$8,500` is left, spends it, and compounds it for the whole horizon --
      and disagrees with the limit CARD beside it, which always counted the current period. `$0.00`
      on today's data (no account has a contribution feed), so the harness was blind to it. Fixed as
      ONE derivation beside the window it depends on (`_context._projection_ytd`);
      `retirement_projection` keeps the strictly-before seed and is RIGHT to, because both of its
      axes open at or inside the current period.

      **Five corrections to this entry's own text, as built.**
      (e) **Two money-moving surfaces were outside the sign-off table**, both invisible to
      `verify_balance_baseline.py` because neither is a seam entry: `/retirement`'s projected
      balances (measured above) and `/savings`' Horizon retirement / investment bands. The Horizon
      band is NOT exercised on either real database (its composition is empty on both), so its
      figures are pinned by its own suite rather than measured here -- stated rather than claimed
      unmoved.
      (f) **`_interest.layer_account_interest` goes UNWIRED here, not at X-g4**, and needed the same
      banner `_investment` got. All three of its readers moved to the replay; only `accrual_params`
      stays live. Without the banner the X-g4 deletion list read off these docstrings would
      under-count, and a maintainer editing it to "fix" a rendered accrual would change nothing.
      (g) **`AssetPeriodFigures.balance_without_accrual` deletes with `asset_seed_at`.** It is the
      same retired filter at a period grain, with no reader; leaving either behind would leave a
      docstring instructing the next caller to reproduce N-80.
      (h) **`_make_property` in `test_appreciation_projection.py` was a THIRD unpinned fixture
      builder** (finding N-77's shape, which X-g2a closed for the two shared helpers). Its opening
      assertion landed on the DATABASE clock -- months past its own seeded horizon -- so a Property
      accrued nothing anywhere and the file's appreciation assertions all read the flat market value.
      Pinned to the anchor period's first day, the N-65 / X-c2a rule.
      (i) **`_kernel.account_balance_map_from_inputs` deletes and its slice moves to `_inputs`**,
      beside the bundle it slices. That entry existed to DUCK-TYPE its parameter -- "any bundle
      exposing three fields qualifies" -- precisely because `_AssembledInputs` lives in the consumer
      module the engine must not import. With the kernel taking the narrow named
      `ContributionInputs`, the structural contract stated in prose becomes an ordinary typed one.

      **Recorded, not fixed:** **N-85** (`interest_by_period_for_account` has no production caller
      and survives on its own tests -- the dead-code-alive-for-its-own-tests shape, deleted with the
      module at X-g4) and **N-86** (the `/investment` limit CARD and the projection beside it read
      two different YTD boundaries by design; the card is always through-current, and only the
      projection's depends on its axis).
  * **X-g3** -- ruling R-W's grid. `grid_balance_view` renders the modeled balance and generalises
    the conditional Interest row to a "Growth"/accrual row on R-O's own non-zero rule, on BOTH form
    factors (R-P). Render plumbing only, no new figure the seam did not already answer at X-g2.
  * **X-g4** -- the deletion. `_merge_balance_sources`, `_reverse_project_periods`,
    `_forward_project_periods`, `_forward_project_rows`, `_assemble_investment_projection_inputs`,
    `investment_base_balance_map`, `get_anchor_period_index`, and `_interest._layer_interest`'s
    second pass. `_cash_engine.balances_for` loses its last two callers here, which is X-c2c4's
    precondition. **NOT on this list, and the review caught it:**
    `investment_projection._average_transfer_contribution` and
    `projection_inputs.build_investment_projection_inputs` SURVIVE -- the what-if surfaces keep them
    (ruling R-R consequence (b)); only the balance path stops reading them.

  Do NOT collapse X-g2 and X-g3: the whole point of the b1/b2/b3 split was that mixing render
  plumbing with a money-moving cutover makes a plumbing slip read exactly like a fold slip. Do NOT
  split R-R into a prerequisite step (the entry previously flagged that possibility): the trace
  measured both contribution feeds EMPTY on real data, so there is no live figure to seat ahead of
  the cutover -- unlike X-c0, which was refusing a write door that was genuinely open.

  **Prerequisite for X-e.** Once this ships, no balance path reads `Account.current_anchor_*`, which
  is the condition X-e's question ("a reconciled cache or it is nothing") needs in order to be
  answerable at all.

- [ ] **X-d** `fix(cash): the posted account ledger is a checked projection` -- E1a's shape for
  cash. The posting writer consumes X-a's walk instead of its own, and the per-visible-date assert
  (`sum(postings) == fold(ACTUAL events)`) makes a stale posting a detectable, repairable cache
  inconsistency. Ship-gated on a prod-data sweep for walk-invisible legacy rows, exactly as E1a
  was; any found row is an F1-class human decision, never a silent exclusion.
- [ ] **X-e** (old **X4**) `refactor(accounts): current_anchor_balance is a reconciled cache or it
  is nothing` -- today `cash_ledger.resolve_anchor` detects the divergence from the history table
  and only LOGS it (`EVT_ANCHOR_CACHE_RECONCILED`), never repairs it. Decide the column's fate once
  the fold reads history directly (cash D4).
- [ ] **X-f** `feat(transactions): the app records when money moved` -- ruling R-N's follow-up, and
  the step that shrinks the Reconciliation row X-c2 puts on screen.  Today NOTHING in the app
  records when money actually moved: `paid_at` is `db.func.now()` at the click (`status_seam.py:105`)
  and the API refuses any other value (`schemas/validation/transactions.py:62`, `dump_only`), while
  the add-entry form posts a HIDDEN `entry_date` fixed to today.  Two halves, each measurable
  against the row: (a) un-hide the entry date (default today, correctable), and (b) let a settle
  carry the date the money moved.  Measured headroom: re-dating the 19 envelope rows by their
  purchases alone cuts gross reconciliation swing `$36,323.99 -> $31,680.05` and collapses the
  assertions that follow an envelope settle (`$779.53 -> $38.23`, `$537.20 -> $35.60`,
  `-$572.35 -> -$56.88`).  Sequenced AFTER X-d, because re-dating the read side alone would break
  the walk-vs-posted-ledger equality X-a established until the writer follows.

### Phase E2 -- the super-package boundary (RATIFIED 2026-07-26; runs LAST)

**Committed on the developer's instruction 2026-07-26**, promoted from a Section 6 option to a
step. It dissolves the LAST name-keyed gate: move the read seam, the write cluster and the shared
leaves under ONE package whose shared internals are private to it, so W9909's 58-name
classification registry stops being a list a human maintains and becomes the same structural
property W9910 already gives `balance_at` -- a public name born inside is unreachable until someone
deliberately re-exports it on the super-package's `__init__`, "the reviewed, one-place act the
fence always wanted" (`balance_seam.py:50-53`, which is exactly why `balance_at` is NOT scoped
today).

**Scanned 2026-07-26 (AST over `app/` + `scripts/` + `tests/` + `tools/`, never a regex -- the
Section 8 lesson), so the step starts from evidence rather than from its one-liner:**

| candidate member | lines | app/ importers OUTSIDE the cluster | names they reach |
|---|---|---|---|
| `balance_at` | 8,025 | 21 | 8 |
| `cash_ledger` | 1,863 | 5 | 6 |
| `loan_ledger` | 1,093 | **0** | **0** |
| `loan_posting_service` | 2,133 | 7 | 3 |
| `loan_resolver` | 1,355 | 3 | 1 |
| `loan_payment_service` | 893 | 2 | 3 |
| `account_projection` | 168 | 12 | 3 |
| `posting_service` + `account_posting_service` (the write cluster) | 2,082 | 3 | -- |

**17,612 lines, and 113 files outside the cluster carry an import that has to move** (41 in `app/`,
71 in `tests/`, 1 script). `loan_ledger` is the only member with zero outside consumers, which is
what makes the step FEASIBLE -- the hardest leaf is already clean.

**Three things the scan settled that the one-liner did not.**

1. **Six of the seven members have live consumers outside the cluster, so E2 is not "make them
   private".** It is "decide a public re-export surface for the cluster, then make everything else
   private." That surface is where the registry's judgment MOVES to -- one `__init__` instead of
   seven scoped packages, which is the win, but it is a relocation with a design in it, not a
   deletion.
2. **`account_projection`'s membership is an OPEN question, not a given.** Its
   `classify_account` / `AccountProjectionKind` are reached by 11 `app/` modules each -- routes,
   templates, transaction creation -- asking a general account-kind question that has nothing to do
   with balances. Putting it inside a BALANCE super-package means that package's public surface
   carries a classifier no balance consumer wants. The alternative is to leave it out and keep its
   two-name W9909 entry, which is a 2-of-58 residue rather than a 58-of-58 dissolution. Decide it
   from the trace.
3. **It gets strictly CHEAPER by waiting, which is why it runs last.** Every structural step ahead
   of it DELETES code it would otherwise move and then delete: X-c2c4 takes `_cash_engine` (199
   lines), `_calculator` (137) and `cash_ledger.load_balance_transactions` (1 of the 58 ruled
   names); X-g dissolves most of `_investment` (635 lines -- the merge, both projection passes,
   `get_anchor_period_index`, `_assemble_investment_projection_inputs`,
   `investment_base_balance_map`) plus `investment_seed_map` and `_interest._layer_interest`'s
   second pass; X-d takes `account_posting_service._walk.walk_account_ledger` out of the write
   cluster. And X-c2c2 is about to REWRITE 4,431 lines of the very test surface E2 re-points --
   doing E2 first moves files that step then rewrites, while doing it last leaves E2 a purely
   MECHANICAL import re-point over files already at their final semantic home.

**Why it is last rather than never (the ratification's own reason).** The 2026-07-24 ruling that
recorded it as an option was right on its own terms: the registry's rot direction is SAFE -- a
stale entry names a dead function and can never permit a bypass (`balance_seam.py:29-32`) -- so E2
buys STRUCTURE, not correctness, and could not outrank a step that buys correctness. What changed
is not the argument but the commitment: the developer has ruled that the fences become
structurally unnecessary, and a 58-name list maintained by hand is the last place that is not true.

**It also SUBSUMES finding N-35, and more cheaply than N-35's own proposed fix.** That finding is a
measured W9909 scope GAP: `ledger_report_service` holds every ingredient of a posted balance-at-T
outside W9910's protection, and a public `account_balance_on(...)` folding `dated_account_nets`
inside it rates 10.00/10 with every gate silent. N-35's resolution is to SCOPE the package, which
costs classifying all 9 of its public names. If it is instead an E2 MEMBER the gap closes with no
classification at all. Whether it is one is an E2-0 question, not a given -- it is the STATEMENT
tier, not the read seam, the write cluster or a shared leaf. **Do not scope it under W9909 in the
meantime without deciding that first**, or the classification is written and then thrown away.

- [ ] **E2-0** `the membership trace` -- NO code. Answer the four questions above from the code:
  which modules are members, what the public re-export surface is, whether `account_projection` is
  in or out, and whether `ledger_report_service` is (N-35). Also re-run the scan (it will have
  shrunk) and check the arrow risk the original ruling named: D0b's scoping found the
  reorganization would ADD four fence entries, and the equivalent question here is whether any
  member imports a NON-member that would then have to move too. Expect the step to DECOMPOSE from
  what this finds, exactly as X-c and X-c2 did.
- [ ] **E2-n** the move itself, and the registry deletion. Decide the decomposition from E2-0, not
  here. The deletion of `_FENCED_MODULE_RULINGS` is the LAST commit, never the first: prove the
  boundary holds before removing the gate that currently compensates for its absence (the C3b3
  prove-the-successor-first precedent, which this arc has now applied eight times).


## 6. The findings ledger

**Only UNRESOLVED findings are here.** The 75 the arc CLOSED, with the commit that closed each,
are the closed register in `archive/loan_arc_as_built_2026-07-26.md` Section 7. IDs keep their
archive names so old references resolve either way. Unfinished work stays here whichever half of
the arc it came from: a loan-side question that is still open is still open.

**Three rows were RE-VERIFIED against the code at the 2026-07-26 trim and two of them were wrong.**
B-7 / B-10 were closed by F2 (`3aecceb0`) and the row had never been updated -- archived. B-16 and
B-17 both named steps that have since shipped as their resolvers, and both are still LIVE; their
rows below are rewritten with what the code actually does now. The rest are carried forward
UNCHANGED and were NOT re-measured at the trim -- their figures and citations were true on their
own write date, per Section 7.6.

| id | finding (one line) | worst measured | status | closed by |
|---|---|---|---|---|
| B-16 | **The /savings horizon asks the debt-line question with the CONGRATULATION predicate.** `LoanFigures` states the contract at `_loan_figures.py:176-177` -- "Use `is_retired` to decide whether a loan has a debt line; use `is_paid_off` to decide whether to CONGRATULATE the user" -- and `_horizon.py:144` (which loans are ACTIVE for the debt-free date) and `:561` (which payoff milestones to plot) both ask the debt-line question with `is_paid_off`. `is_paid_off` is strictly narrower: it also requires at least one CONFIRMED payment, a badging guard against a degenerate `$0` opening anchor. So a loan retired by a lump-sum balance TRUE-UP with no payment rows reads `is_retired=True, is_paid_off=False`, stays in the horizon's ACTIVE set, and -- being retired -- has `payoff_date is None`, which fires the "an ACTIVE loan with no payoff never clears" branch: **no debt-free date at all, and the user reported not loan-free, on a loan that owes nothing.** The same misconfiguration class produced the `$197,049.32` equity-chart defect `_loan_figures.py:178-184` cites | the debt-free milestone suppressed entirely; not yet measured in dollars | **OPEN -- RE-VERIFIED against the code 2026-07-26** (the trim). Its old status ("latent -- collapses at C3b/C4") was WRONG: both steps shipped and both call sites are live. Not fixed here: it is a loan-surface predicate swap with its own control to write | own commit |
| B-17 | **The debt-track `is_originated` guard proves where the value comes FROM and never that production puts it there -- the N-63 / N-67 class, third instance.** `test_balance_at.py:3583-3592` builds its OWN `_ad` dict with `"is_originated": figures.terms.is_originated` and asserts `_metrics` behaviour on it. Production builds that dict at `_projections.py:241-243`, with the same expression PLUS a `loan_result is None` fallback of `True` the test's dict has no branch for. Change the production key to `False`, to `is_retired`, or drop it, and the test stays GREEN -- it never executes the builder. The value's source is proven; the WIRING is not | the debt track counting an unclosed mortgage as 100% paid (the defect the flag was added to stop) | **OPEN -- RE-VERIFIED 2026-07-26** (the trim). Its old status ("flag deleted at C3b") was WRONG: `is_originated` is live on `LoanFigures.terms`, read through that dict at `_metrics.py:362`. The fix is the Section 8 rule N-63 wrote: assert the CALL, or assert behaviour through the real builder | own commit |
| FU-1 / F1 | **The Van Loan's one unexplained true-up STEP -- an operator question, not a code fix.** RE-SCOPED 2026-07-25 on a fresh PROD clone: the duplicate same-day anchors the finding named are DEV-CLONE pollution (created 2026-07-07 during arc development), not production. Prod's account 8 carries exactly THREE anchors (origination 2023-02-14 `$32,402.45`, user_trueup 2026-05-22 `$17,020.47`, user_trueup 2026-06-23 `$15,663.59`) and an audit trail of 6 INSERTs / 0 UPDATE / 0 DELETE -- the shape was never there, so it was not silently repaired either. What DOES remain: the 2026-06-23 true-up moves the balance `$905.33` beyond what the recorded payment explains (after the 06-22 installment's `$451.55` principal the walk stands at `$16,568.92`; the anchor asserts `$15,663.59`). That is a user ASSERTION, which the architecture treats as authoritative by design, not a defect -- the Mortgage's own 2026-05-22 true-up reconciles to the cent (`$177,829.83` == the walk after two payments), so the machinery is not suspect | `$905.33` against the servicer's statement | **OPEN -- awaiting the OPERATOR.** Whether the `$905.33` matches the servicer's statement is a question only you can answer; it blocks nothing, and the ledger is self-consistent under E1a's assert either way. Converted from a Phase F step to a finding at the 2026-07-26 trim, because it is a question and not a commit | operator |
| FU-3 | Standing overpayment resolves at today for any as-of | -- | latent | C-phase note |
| cash D4 | Anchor column vs history table: divergence detected, only logged | latent | latent | X-e |
| N-4 (A1) | Pay-period reset re-anchors EVERY kind, refreshing loan cash-anchor rows (balance-preserving `stage_anchor_true_up` inside the reset's deferred-FK transaction; same-value, not user-supplied) | -- | **OPEN** -- residue of the archived B-15 (a kind-blind true-up wrote a CASH anchor onto a LOAN; both real loans carried such rows), whose mechanism closed at A1 while these two writers did not | X-e, with the column's fate (see also N-73) |
| N-5 (A1) | Account-create factory writes an origination cash anchor for every kind -- a loan created with a balance seeds the column at birth (entangled with loan onboarding) | -- | **OPEN** -- residue of the archived B-15, as above: the mechanism that RENDERED the wrong anchor closed at A1, the writers that create one did not | X-e, with the column's fate (see also N-73) |
| N-18 (C8d) | **The recurrence bound and what was GENERATED can disagree, in both directions.** `create_payment_transfer` syncs the bound, generates, then re-syncs (C8d added the second call, because the payoff folds the forward PLAN and the first call cannot see the payments it is about to generate). But `RecurrenceRule.end_date` only gates FUTURE generation (`recurrence_engine.match_periods`) -- it neither backfills nor prunes -- so generation ran between two different bounds and is never revisited. Measured on a 1-month $12,000 loan originated 2026-03-01, read 2026-03-20, paid manually at $6,100: bound 1 (folded with no payment records) is `2026-04-01`, bound 2 (with the generated shadows) is **`2026-03-01`** -- EARLIER, and a PAST date, because the generated shadows include overdue slots that clamp forward to `as_of + 1d` and pay the loan down, and the clearing installment's DUE date is past (the edge `plan_payoff_date`'s docstring names). So the stored bound can sit BEFORE shadows that already exist. The opposite direction (a manual amount below contract truncating generation) is argued reachable but I could NOT construct a firing control for it across three fixtures. **A re-generation after the second sync was written and then REVERTED**: it addresses only the truncation direction, and shipping a write-path change whose control never fires violates Section 7.3. The over-generation direction needs a PRUNE, which is a pre-existing gap shared with `update_payment_settings`. **The concrete cost of deferring, stated so it is not mistaken for cosmetic:** a shadow generated past a bound that later moves earlier keeps its CHECKING-side expense leg, so the cash projection debits a payment for a loan already at zero -- money on a screen, not just a stale column. Unlike the truncation direction, this one HAS a firing control (measured above), so the prune is testable when it is built | bound 1 `2026-04-01` vs bound 2 `2026-03-01` (measured) | recorded, deferred | own commit (write-side / E1) |
| N-19 (C8d) | **A RETIRED loan's recurrence bound does not exclude the CURRENT pay period.** `recurrence_end_date` returns `ctx.as_of` for a retired loan (developer ruling), and `recurrence_engine.match_periods` admits a period when `period.start_date <= end_date` -- so the current period, which started before today, still matches, and only `should_skip_period` (an existing row) stops another payment generating into a loan that owes nothing. Pre-C8d this varied rather than being reliably better: a retired loan WITH history got its last payment date (same wart), one WITHOUT got `origination_date` (which did exclude everything). Excluding it properly means bounding at the current period's `start_date - 1 day`, a different rule than the one ruled. Second-order: a retired loan mutated across days rewrites `end_date` to each new day and emits a BUSINESS audit event, so the write is idempotent only within a day | -- | recorded, deferred | own commit |
| N-23 (C9b) | **A refused loan payment now fails an entire carry-forward batch.** Carry-forward moves transfers via `update_transfer(pay_period_id=...)`, which since C9b runs the archived ruling R-C guard (the transfer write boundary REJECTS a loan payment dated at or before the loan's origination), and `routes/transactions/carry_forward.py` rolls the whole batch back on `ValidationError` -- so one un-movable loan payment costs the user every other carried item. The guard's DECISION is correct there (the moved payment would still be erased); the blast radius is the defect. Reachable on a row the C9a purge deliberately leaves: an ad-hoc (template-less) or `is_override` pre-origination payment on a future-originating loan. Worked: loan originates 2026-08-01 payment_day 1, current period 2026-07-10..07-23, no due_date -> installment 2026-08-01 `<=` origination -> refused -> 400, nothing carries. Fixing it means skip-and-report (leave the row in the source period, count it in the message), which is a change to carry-forward's batch semantics rather than to the guard -- a developer call, not a touch-up. Both stale docstrings that claimed the old raise conditions are corrected in-commit | whole batch lost | recorded, deferred | own commit |
| N-24 (C9b) | **Three generation call sites have no `ValidationError` handler, so a refused write 500s.** `create_transfer` can now raise the archived ruling R-C refusal -- a loan payment dated at or before origination -- (as it already could raise `_reject_transfer_out_of_loan`), and the recurrence engine fans out through it. `transfers/templates.py:690` wraps generation correctly and C9b added the same wrap to `create_payment_transfer`; `period_population.py:86` (pay-period EXTEND / regenerate -- one bad loan template breaks the whole extension) and `transfers/templates.py:457` (unarchive) do not. Largely closed in practice by C9a: every loan-payment rule now carries a `start_date` (migration-backfilled + synced + bound at creation), and `first_installment_date` is strictly `>` origination for every input, so a bounded rule cannot generate a refused installment. This is the residual exposure for an unbounded rule, and it is partly PRE-EXISTING (the out-of-loan guard has the same reach) -- C9b widens an existing hole rather than inventing one | 500 on extend / unarchive | recorded, deferred | own commit |
| N-25 (D0a) | **A real runtime import cycle in the balance cluster was invisible to `cyclic-import`, because a TYPE-ONLY import of the same module excluded the edge.** pylint's `_add_imported_module` drops an edge into `_excluded_edges` when `in_type_checking_block(node)`, keyed by the `(importer, imported)` MODULE pair -- so one type-checking import silences the check for EVERY import of that module, including a runtime one elsewhere in the file. `resolution_context.py` had exactly that: a `TYPE_CHECKING` `PlannedPayment` import (line 73) masking the lazy runtime `loan_plan` import inside the method (line 305), which closed a genuine cycle with `balance_at._plan`. Measured both directions on this repo: neutralise the type-only edge on the PRE-D0a code and pylint reports `R0401 (app.services.balance_at._plan -> app.services.resolution_context)`; neutralise it on the D0a code and it reports nothing. Reproduced from scratch on a 3-file probe (8.75/10 -> 10.00/10 by adding a type-only import and nothing else). **The instance is fixed; the CLASS is not** -- the masking still applies anywhere a module imports another both for types and at runtime. Residual risk is bounded by two accidents rather than a gate: a top-level re-import would `ImportError` at load (`_plan` imports `BalanceContext` at module scope), and a function-level one now trips stock `import-outside-toplevel` since D0a deleted the scoped disable. The remaining path is re-adding the lazy import WITH a rationale comment -- which is what the pre-D0a code was, and it passed every gate | a cycle + an inverted dependency, gate-green | **instance closed (`8285fcad`)**; class recorded | D0a (instance); own commit (class, if ever) |
| N-29 (D1b) | **The balance seam's NON-loan branch now reaches into a loan-named package for a generic calendar primitive.** D1b moved `find_period_containing_date` to `loan_ledger/_visible.py` -- correct on cohesion (it is chronology, it sat in a kind CLASSIFIER, and `_visible` had to import that classifier to reach its own primitive) and correct on the fence (`loan_ledger` is W9909-scoped WHOLE, so the ruling travelled with the name -- the archived N-28: relocating a public name OUT of a W9909-scoped module silently un-scopes it). But its seam call site is `_kind_correct.py:278`, the INTEREST / INVESTMENT / APPRECIATING fallthrough -- HYSAs, brokerages, properties -- which now imports from the loan fold leaf, two lines below an existing `pay_period_service.get_all_periods` call. `pay_period_service` is the neutral home that owns the calendar and carries no loan semantics; the reason D1b did NOT use it is that it is W9909-UNSCOPED, so relocating a classified public name there would drop its classification -- the archived N-28's hole exactly (a module's W9909 scope is its module IDENTITY, so moving a name across a module boundary moves it out of scope silently). So the honest fork is: leave it in the loan leaf (a naming wart), or move it to `pay_period_service` AND scope that module for W9909 (a registry entry on a module holding the T of balance-at-T but no money). Not a correctness defect either way -- the function is pure and its two callers are proven -- and worth deciding before D-fold locks the leaf's surface | -- | **CLOSED at X-g2b (`560b3339`).** The fork dissolved rather than being decided: the seam's non-loan branch stopped needing a calendar primitive at all when the scalar became a fold over dated events, so the import into the loan-named leaf is gone and there was nothing left to relocate or scope | X-g2b (`560b3339`) |
| N-33 (D-gate) | **13 cross-package private-NAME imports -- the measured residual OUTSIDE D-gate's ruled scope.** The zero-exception scan for D-gate (AST over `app/` + `scripts/` + `shekel_checkers/`, confirmed by the shipped checker) found ZERO private-module crossings but 13 private NAMES imported across a package boundary from PUBLIC modules -- all one shape: `app/routes/accounts/{anchor,crud,detail,types}.py` and `app/routes/loan/params.py` import `_anchor_schema` / `_create_schema` / `_validate_update_account` / `_account_type_is_visible` / `_visible_account_types` / `_appreciation_params_schema` / `_interest_params_schema` / `_crosses_posting_boundary` / `_owned_account_type` / `_type_create_schema` / `_type_update_schema` / `_validate_account_type_boundary_edit` / `_validate_collateral_link` from `app.utils.account_validation`. The names lie about their visibility: routes ARE their consumers, so they are cross-package API. The honest fix is a RENAME to public (not a checker extension carrying an allowlist); once renamed, extending W9910 to private NAMES (owner = the defining module's package) would be a zero-exception tightening -- every other private-name import in the tree is intra-package (the `_helpers` convention). Not a live defect: a guard-scope observation | -- | recorded, deferred | D3-adjacent (rename, then optionally tighten W9910) |
| N-35 (E1e) | **The statement tier `app.services.ledger_report_service` is not W9909-scoped, so a public balance-at-T born there is unguarded.** E1e's rationale for deleting W9906 whole rests on "no public single-account balance-at-T producer exists outside the seam" -- true, and the claim was NARROWED to that wording in review, because `compute_balance_sheet(user_id, as_of)` does fold every posted source attributed on or before a date into per-account cumulative positions. It is the ruled exception (a whole-chart statement whose sections articulate only because the trial balance ties; pulling ONE line out to answer "what is this account worth on date T" is the named misuse), and it never sat on W9906's producer list, so the deletion cedes nothing. The GAP is the completeness half: the package holds every ingredient of a posted balance-at-T -- `dated_account_nets`, the chart load, the class-id sectioning -- OUTSIDE W9910's protection, exactly the shape that put `cash_ledger`, `loan_ledger`, `loan_resolver` and `account_projection` on the registry. **Measured on this tree:** a public `account_balance_on(user_id, ledger_account_id, as_of)` folding `dated_account_nets` inside the package rates **10.00/10** under the full `--fail-on` set. Scoping it is its own step because every public name in the package must then be classified (2 report entries + 7 attribution names).  **E2's ratification (2026-07-26) gives this a SECOND and cheaper resolution, and the two are alternatives rather than a sequence:** if `ledger_report_service` is a MEMBER of the super-package, the gap closes structurally and NOTHING has to be classified -- the same trade E2 makes for the other seven.  Whether it is a member is an E2-0 question: it is the STATEMENT tier over the postings, not the read seam, the write cluster or a shared leaf, so it is outside E2's stated membership and inside its own rationale.  Do not scope it under W9909 in the meantime without deciding that first, or the classification work is done and then thrown away | a balance-at-T on a screen outside the seam, every gate green (the archived N-28 / N-31 class: a public balance producer born in a module W9909 does not scope is unclassified, and the scope is keyed by module identity in BOTH directions -- moving a name out un-scopes it, and moving a module IN un-scopes what it holds) | **recorded, NOT fixed** -- the false absolute claim was corrected in-commit (checker header, the `loan_posting_service` ruling, the package's own docstring); the scope entry is deferred | own step |
| N-36 (C2b) | **The resolver's money-blind replay keys its rate on the PAY-PERIOD START, where the genesis walk now keys on the DUE date -- one question, two rules, deliberately.**  C2b re-keyed every split input onto contract time (archived ruling D5: the split inputs -- ordering, rate and escrow -- key on the DUE date, so out-of-order or late settlement can never re-split an installment), but `rate_period_engine._replay_from_anchor` (`:893`) was left on `payment.period_start`.  The reason is measured, not a preference: it consumes payments that have been through `loan_payment_service._redistribute_to_distinct_months`, which INVENTS a due date for a payment colliding on an already-allocated schedule month, so keying its rate on that date would let a schedule-alignment artifact move a replayed balance -- trading the archived N-34's defect (the split's rate and escrow keyed on the pay-period START rather than the DUE date, fixed at C2b) for a subtler one.  Containment, verified: the replay's rows and balance are DISCARDED whenever a ``confirmed_view`` is supplied (`_build_forward_inputs` keeps only `next_pay_date` / `remaining_months_as_of`), which is every production read since E1d-b, so the two keys can differ only on the unseeded what-if path and never inside one rendered figure.  The honest fix is to carry a payment's REAL installment alongside the redistributed one so the replay can key on the fact rather than the artifact -- which is a schedule-alignment change, not a split change | none measured (the divergent surface is discarded on every production read) | **recorded, deliberate, NOT fixed** -- stated at the site in `rate_period_engine`, so it cannot be rediscovered as an accident | own step (with the schedule-alignment rework) |
| N-42 (X-c trace) | **Nothing in the app records WHEN money moved.** `Transaction.paid_at` is stamped `db.func.now()` inside the status seam (`status_seam.py:105`) and the API refuses any other value (`schemas/validation/transactions.py:62` is `dump_only`); the only entry-creation door posts a HIDDEN `entry_date` fixed to today (`_transaction_entries.html:179`). So the balance engine's ACTUAL clock is a data-entry click. Measured on the real Checking account: `paid_at - due_date` is median **2 days**, p75 **6 days**, max **25 days**, and **81 of 130** settled rows were marked in same-minute batches (one batch of 6 spanning due dates 04-09..04-23). The corrections this produces swing `+/-$1,000` a month against a true four-month net of **`-$159.73`** -- the amounts are right and the dates are guesses. Not introduced by the fold (the posted ledger dates cash the same way); made VISIBLE by it, since the Reconciliation row is where the noise lands | `$36,323.99` gross swing vs a `-$159.73` true error; headroom measured at `$4,643.94` from entry-dating alone | **RULED 2026-07-25 (R-N)**: cut over first; X-f records the real dates after X-d | X-f |
| N-43 (X-c trace) | **The plan's "the investment contributions base reads the fold" would silently rewrite pre-anchor net-worth history.** An investment's pre-anchor periods come from a REVERSE growth projection and a property's from a flat anchor carry, but `_merge_balance_sources` (`_investment.py:403-410`) prefers the base map whenever it has the period -- and the fold, being TOTAL, always does. So pointing the shared base at the fold replaces both ruled models with a raw contribution sum. Measured at the earliest period: Roth `$26,604.63 -> $23,851.08`, Empower `$29,289.22 -> $26,912.56`, Trad IRA `$11,360.85 -> $10,175.49`. The property is unaffected by coincidence, not design -- R-I's back-projection reproduces the flat carry exactly (`$350,000.00`) because the Home carries no transaction rows | **`-$6,315.57`** of net-worth history at one period, with no ruling behind it | **OPEN, its WINDOW is CANCELLED (ruling R-V), and its SIGN is now established (ruling R-S, 2026-07-26).** X-c2c3 would have kept the fold out of the merge's way; X-g removes the merge instead. The figures above are re-verified to the cent -- and the trace established which side is right: each of them is the fold reproducing an assertion the user actually made, against a model that overrides it (**N-74**). So this row's "silently rewrite" reads backwards: the rewrite is what SHIPS today, and X-g reverts it. Nothing regresses in the meantime -- the bases still read `balances_for`, which is what they read before this finding was written | X-g (via R-S) |
| N-40 (X-b) | **`live_amount_overrides` reads the wall clock, so a fold given an explicit `as_of` is not fully as-of-pure.** `loan_payment_service.live_loan_transfer_amounts` calls `date.today()` (after two early-outs, so only for a derive-mode loan-payment transfer shadow) to resolve the loan's current P&I + escrow. The cash fold takes a pinned `as_of` and threads that map into its PLANNED tier, so a historical read values such a shadow at TODAY's loan state rather than the state at `as_of`. Bounded: the planned tier only contributes to dates after `as_of`, and this is inherited UNCHANGED from all three shipping producers (every one of them builds the same map), so X-b introduces nothing -- but the fold is the first cash producer to carry an explicit as-of at all, which is what makes the impurity visible and worth naming before X-c makes it a rendered number | latent; scoped to derive-mode loan-payment transfer shadows on a historical read | recorded | X-c (or its own commit) |
| N-45 (X-c1) | **A checker unit test is green only because a DIFFERENT test class in the same file warms astroid's module cache.** `TestShekelPackagePrivacyChecker::test_allows_seam_submodules_importing_each_other` parses `from app.services.balance_at._context import _memoize_once` inside a synthetic `app.services.balance_at._plan` and asserts NO message. The checker's `_names_a_module` resolves the base through astroid, and under pytest's `tools/pylint/tests` rootdir the real `app` package is NOT importable -- so a cold cache raises `AstroidBuildingError`, the checker fail-CLOSES (correctly, by design), and the assertion fires. It passes only because `TestShekelBalanceSeamChecker` runs first in the same file and `astroid.parse(module_name="app.services.balance_at._context")` REGISTERS its synthetic module under that real dotted name, so the later resolve hits the cache. **Reproduced deterministically at HEAD, independent of this step:** the class alone fails 3/3 (both `./scripts/test.sh` and serial `-c /dev/null`), the whole file serially passes 5/5, and the whole file under `pytest.ini`'s `-n 12 --dist=loadgroup` fails ~2/3 depending on which worker gets the class. **The merge gate is NOT at risk** -- CI and pre-commit both run `pytest tools/pylint/tests -c /dev/null -q`, serial and whole-file (`ci.yml:186`, `.pre-commit-config.yaml` `shekel-checker-tests`), and `pytest.ini`'s `testpaths = tests` excludes the directory from the default suite. The honest fix is for the test to stop depending on a cross-class cache side effect (give the synthetic importer a real `path=` so `_importer_file_inside` decides it, as the checker's own file-arm tests already do). Out of scope here: a test-isolation defect in a file this step does not touch | a gate's own suite green by accident; ~2/3 flake under xdist | recorded, deferred | own commit (test isolation) |
| N-46 (X-c2 trace) | **Two more names belong on X-c2's deletion list, and one of them is dead ALREADY.** AST-scanned over `app/` + `scripts/` + `tests/`: (a) `cash_ledger.period_subtotal` (singular) has **ZERO production callers today** -- `period_subtotals` (plural) is the only one the grid reaches, and the singular adapter survives on 5 test files alone, the dead-code-alive-for-its-own-tests shape C3b4 / D2a / F2 / E1e each deleted; (b) `cash_ledger.load_balance_transactions` has exactly three callers -- `_cash_engine`, `_daily_series` and `_flows.period_subtotals` -- and X-c2 deletes all three, so it goes to ZERO at the cutover. The fold does not use it (`planned_cash_rows` and `settled_cash_facts` both go through `_facts._unwindowed_contributing_rows`, which owns its own query with both eager loads). Neither name was on the step's list; leaving them would keep ~120 lines of production code alive for its own suite | -- | **half closed (`82557ca9`)**: `period_subtotal` deleted at X-c2b3 with its plural sibling, ruling R-K having changed what a subtotal counts.  `load_balance_transactions` SURVIVES -- X-c2b3 deleted two of its three callers and `_cash_engine.balances_for` is the third, so it goes to zero when that producer does | X-c2b3 / X-c2c |
| N-56 (X-c2b1) | **The desktop grid's two self-refresh endpoints now compute the SAME per-period view, twice per `balanceChanged`.**  ``#grid-summary`` (the sticky ``<tfoot>``) fires ``/grid/balance-row`` and ``#grid-subtotals-income`` fires ``/grid/subtotal-rows``, and since X-c2b1 both read one ``grid_balance_view`` -- which is what makes ruling R-K's identity survive the live swap, but means the browser pays for the projection twice.  Measured on the prod-shape clone 2026-07-26 (real Checking, 60 periods, 5 runs): per endpoint ``272.3 -> 165.4 ms`` (balance row) and ``87.9 -> 165.6 ms`` (subtotal rows), so the PAIR is ``360.2 -> 331.0 ms`` -- no aggregate regression, because the balance row stopped building a second override map, but the duplication is now visible and avoidable.  The fix is the pattern ``subtotal_rows`` already uses for its own two ``<tbody>`` blocks: let the balance-row response carry the two subtotal sections as ``hx-swap-oob`` fragments, so ONE GET refreshes the whole reconciling block and the rows are one response as well as one row set.  Not done here because it changes the refresh topology (a user-visible behaviour change in a commit whose contract is "the rendered grid is unchanged") and it has to clear the ``<template>`` parser constraint ``_balance_row.html`` documents | ``165.6 ms`` of duplicate producer work per refresh | recorded, deferred | own commit (or X-c2b2) |
| N-58 (X-c2b2 review) | **The analytics calendar renders a flow on one day and the balance step for it on another, with no row to explain the gap.**  A day cell shows BOTH its flow chips and its end-of-day balance.  The chips are placed on the BUDGET attribution date (`calendar_service._get_display_day`: `due_date` clamped into the period, falling back to the period start); the balance line is the fold, which steps on the day the money MOVED -- `paid_at`'s UTC civil day for a settled row, `max(attribution, as_of + 1)` for a still-projected one (ruling R-G).  They agreed by construction until X-c2b2, because the retired ramp distributed the same still-projected rows over the same attribution days.  **Measured (finding N-42, same data):** `paid_at - due_date` is median **2 days**, p75 **6**, max **25** across 130 settled Checking rows, so on production essentially every past chip is now displaced from its own balance step.  This is the split the GRID met and ruling R-K answered with the "Timing & true-ups" row; the calendar has no such row.  The step's own fixture demonstrates it: `test_flow_strip_low_trough_warning_cells` renders a `$600` chip on Jan 2 with `$1,000.00` under it and the `$400.00` drop on Jan 5.  The false docstring that still claimed the two share one clock is corrected at the site | chip and step up to 25 days apart; median 2 | **recorded, NOT fixed (developer ruling 2026-07-26: record, rule at its own step).**  The option space: (a) place the chip on the cash clock, which changes which MONTH a row appears in; (b) give the calendar R-O's treatment (a reconciling figure); (c) rule the divergence acceptable and label it.  Plan step X-f shrinks the date noise at its source, so ruling after it may change the answer | own step (after X-f) |
| N-65 (X-c2b2) | **The suite's frozen clock does not reach the DATABASE clock, so a fixture's settle lands months after the read that is supposed to see it.**  `tests/test_services` freezes today to 2026-03-20, but `status_seam` stamps `paid_at` with `db.func.now()` and `AccountAnchorHistory.created_at` server-defaults the same way -- both the real wall clock.  Every producer before the cutover read the LATEST anchor row and ignored its date, so nothing noticed; the fold dates every event, so an unpinned settle or assertion lands outside the seeded period range entirely and contributes to nothing.  This is the archived N-8 / X-c2a shape a THIRD time -- a fixture's stored instant coming from the real wall clock while the test's own clock is frozen elsewhere (the loan walk's stamp, then `create_account`'s opening, now `paid_at`), and the lesson is the same one: a fixture whose clock disagrees with its own data builds a state production cannot reach.  Mitigated rather than closed: `tests/_test_helpers.override_anchor` and `conftest._pin_opening_to` stamp assertions inside their own period, and the suites that needed a dated settle pass `paid_at` explicitly | a fixture asserting against a state production cannot reach | recorded; mitigated per-fixture.  The structural fix is for the test clock to patch the DB default too, which is its own change | own commit (test infrastructure) |
| N-14 (C6b) | **`contractual_schedule_from_origination` is computed twice per pass on the property page** -- once inside the (now-memoized) `ctx.loan_plan` and once in the equity chart's `_back_projection_by_month` (both call it for the same loan). Deferred (developer ruling): pure-CPU (no query), only 2x, property-page only, and a full dedup via a fourth context memo must FIRST prove the two call sites' rate-change inputs are identical (`load_rate_changes(id)` vs `resolved.context.rate_changes`) -- a correctness check better done in its own focused change | -- | recorded, deferred | own commit (or Phase D) |
| E2 | **RATIFIED 2026-07-26 -- promoted OUT of this ledger and back into Section 5 as a committed step; see "Phase E2" there for the scan, the three open questions and the sequencing.** The row stays so the id resolves. Original text: **The super-package boundary: the option that would dissolve the last name-keyed gate.** Move the read seam, the write cluster and the shared leaves (`loan_ledger` / `cash_ledger`) under ONE package whose shared internals are private to it, so the W9909 classification registry -- the last name-keyed surface -- dissolves structurally the way the W9906 call allowlist already did. Large reorganization with its own arrow risks (the D0b class, where scoping the step showed it would ADD four fence entries); W9910's per-boundary membership would need extending | -- | **OPEN, recorded, NOT committed to** (developer ruling 2026-07-24). The registry's residue is small, fail-closed and self-attest-pinned, so the reorg must earn its churn on its own merits. Recorded so the option cannot be forgotten. Converted from a Phase E step to a finding at the 2026-07-26 trim, because it is an option and not a commit. **CLOSED as a finding and RE-PROMOTED to a step the same day**: the developer ruled the fences are to become structurally unnecessary, which is the one argument the 2026-07-24 ruling did not weigh -- it asked whether the reorg earned its churn on correctness grounds, and it does not; it earns it on the standing goal. Sequenced LAST because every structural step ahead of it deletes code it would otherwise move and then delete (measured at the step) | Section 5, Phase E2 |
| N-71 (X-c2c trace) | **Three account kinds still answer a DATE with a PERIOD, and it is documented as intended.** `_kind_correct.py:195-197`: "INTEREST / INVESTMENT / APPRECIATING are period-granular: they answer 'what is the balance at the end of the period containing *as_of*?'" So the whole of a period's modeled growth is credited on the period's FIRST day. **Measured 2026-07-26 on the prod-shape clone, period 30 (2027-05-20..06-02): the scalar returns the IDENTICAL value on the first and last day of the period** -- Empower 401(k) `$38,617.11` against `$328.50` of growth in that period, Money Market `$9,090.81` against `$261.24`, Roth IRA `$29,843.76` against `$114.07`. This is finding cash D2's exact shape ('the scalar is period-flat; it contradicts a date-precise read'), closed for PLAIN and AMORTIZING at plan step X-c2b2 and still live for the other three | a whole period's growth on the wrong day: `$328.50` measured, unbounded in principle | **recorded, NOT fixed; RE-VERIFIED to the cent 2026-07-26** (all three figures reproduced at period 30 by the X-g trace, plus Trad IRA `$12,744.04` / `$48.71` and Fidelity Savings `$5,571.99` / `$7.03`). The fix is structural, not a patch: a period-flat answer is what a period-keyed MAP can give, and a date-precise one needs the event replay. **Ruling R-T sets the grain that closes it** -- daily, `+0.5 ms` per account per read, measured | **CLOSED at X-g2b (`560b3339`).** The scalar is date-precise for all five kinds; `find_period_containing_date` and the pre-horizon anchor fallback left `_kind_correct` with it | X-g2b (`560b3339`) |
| N-72 (X-c2c trace) | **A modeled asset's balance is three producers merged by a preference order; the window that would have compensated for it is CANCELLED and the merge is DELETED instead.** `_merge_balance_sources` (`_investment.py:395-424`) picks forward projection, else the cash base, else reverse projection, per period. Finding N-43 is a bug in that preference order -- the fold, being TOTAL, always has the period, so it always wins and silently replaces two RULED pre-anchor models. Two fixes exist: keep the base out of the merge's way (the window), or have no merge (one replay). **X-c2c3 was to ship the window; ruling R-V (2026-07-26) CANCELLED it and X-g ships the replay instead** -- so this row records a band-aid that was recorded, priced and then NOT paid for, which is the outcome this ledger exists to make possible. Also measured here, and NOT introduced by X-c2c: one `/savings` render builds the modeled base **14 times for 4 accounts** (3x per IRA from two general `build_maps` passes plus retirement's own, 1x more from `investment_seed_map`, 2x for the Home) and `/investment` **4 times for one account** -- a pre-existing redundancy whose cause is upstream (consumers not sharing a read pass), which the developer ruled recorded, not fixed, at X-c2c | `-$6,315.57` of net-worth history is what N-43 measured the preference order silently rewriting | **recorded; NO compensator ships (ruling R-V), and the merge is DELETED at X-g.** The `/savings` and `/investment` redundancy half of this row is UNAFFECTED by R-V and stays open | X-g |
| N-74 (X-g trace) | **A modeled account's map DISCARDS the user's own recorded balance assertions and renders a model over them.** The three modeled accounts carry **15 `AccountAnchorHistory` rows** between them (Roth IRA 6, Traditional IRA 6, Empower 401(k) 3, spanning 2026-03-31 .. 2026-07-16). `_investment.build_investment_balance_map` reads only the LATEST -- `_cash_engine.balances_for` starts at `resolve_anchor`'s single anchor and `_calculator.calculate_balances:117-118` skips every pre-anchor period -- and `_reverse_project_periods` then re-derives every earlier period from a growth curve, which `_merge_balance_sources` prefers because the base has no entry there. Measured on `shekel_f3_final` at the period ending 2026-04-08: Roth renders `$26,604.63` against the 2026-04-06 assertion of `$23,851.08`; Trad IRA `$11,360.85` against `$10,175.49`; Empower `$29,289.22` against its earliest record `$26,912.56` (2026-04-09). The cash fold reproduces all three assertions to the cent. It renders on `/savings`' net-worth history (`_net_worth.py:151` -> `build_maps`) | **`$6,315.57`** of net-worth history contradicting the user's own bank facts, at ONE period | **CLOSED at X-g2b (`560b3339`).** Ruling R-S shipped: an ASSERTION always wins and there is no backward model.  Verified on the prod-shape clone -- the period ending 2026-04-08 now renders the Roth at its own `$23,851.08` assertion (was `$26,604.63`), the Trad IRA at `$10,175.49` and the Empower at `$26,912.56` | X-g2b (`560b3339`) |
| N-75 (X-g trace) | **Entering a FUTURE contribution rewrites a PAST balance.** `_reverse_project_periods` passes the FORWARD `periodic_contribution` into `growth_engine.reverse_project_balance`, which un-contributes it walking backward -- so the pre-anchor half of the map is a function of the plan for the future. Measured by creating six `$500.00` projected Checking -> Roth transfers inside a ROLLED-BACK transaction: the Roth's period-7 (past) balance moved `$27,327.49 -> $26,829.40` while the user's recorded assertion for that period is `$27,332.33`. Both numbers are wrong and the second is `$502.93` wrong. Not reachable on today's data only because no investment account has a contribution feed at all (ruling R-R's measurement) | `-$498.09` on one past period from six future rows; unbounded in the contribution amount | **CLOSED at X-g2b (`560b3339`).** It died with the reverse projection, which left the balance path entirely: a replay has no backward direction to pass a forward contribution into | X-g2b (`560b3339`) |
| N-76 (X-g trace) | **The grid and `/savings` answer one modeled account two ways, with no row explaining the gap.** `_grid.grid_balance_view` layers an accrual for INTEREST only (`_grid.py:271-277`), so an INVESTMENT or APPRECIATING account's grid balance is the kind-blind cash-flow fold while `_kind_correct.balance_map` returns the modeled map -- and both surfaces are reachable for those kinds (`account_resolver.is_cash_flow_account:41` admits every non-amortizing kind). Measured at the last projected period: Empower 401(k) grid `$31,070.06` vs `/savings` `$48,712.19`; Roth `$5,916.95` apart; Trad IRA `$2,526.68`; and on `shekel` the Property `$21,675.99`. The grid's interest column is `None` for every one of them. INTEREST accounts are byte-identical on both surfaces, which is what shows the unification works | **`$17,642.13`** on one account, growing with the horizon | **recorded, NOT fixed; RULED at R-W** -- the grid renders the modeled balance with a Growth row, so ruling R-K's identity holds for all five kinds | X-g3 (via R-W) |
| X5 | **Anchor `effective_date`: an optional feature, not a step.** An `AccountAnchorHistory` row is dated by its `created_at` -- the instant it was ASSERTED -- so a user cannot enter a balance they read off last month's statement and have it land on last month. Adding an `effective_date` column would separate "when this was true" from "when it was typed", which is what a backdated statement assertion needs. Nothing depends on it: every shipped step and every remaining one (X-c2c .. X-g) works on the assertion instant | -- | **OPEN, optional, NOT committed to.** Converted from a step to a finding at the 2026-07-26 trim, on the same ground as E2: an optional feature nothing sequences against is a recorded option, not a commit -- and as the last numeric ID in a letter-suffixed scheme it read as a step whose position in the order was ambiguous. Its old text also said "NOT a prerequisite for X-a .. X-e", which had gone stale twice over (X-f and X-g did not exist when it was written) | own arc, if ever |
| N-77 (X-g1) | **Two shared account factories leave their opening assertion on the WALL CLOCK, which plan step X-g2 makes load-bearing.** `create_hysa_account` pins its opening to the anchor period's first day and says why (the N-8 / N-65 shape, ruled at plan step X-c2a); `make_investment_account` and `make_appreciating_account` do not, so `account_service.create_account` stamps `AccountAnchorHistory.created_at` with the real clock while `tests/test_services` freezes today to 2026-03-20. Nothing fails TODAY because no shipped producer reads an INVESTMENT's or a Property's assertion DATE -- `_investment` pivots on the `current_anchor_period_id` cache column instead. **X-g1's own parallel run hit it immediately:** an unpinned Property opening dated 2026-07-27 is the LATEST assertion, lands past the seeded horizon, and the account then accrues NOTHING anywhere -- a state production cannot reach, and one in which a test asserting "the anchor period accrues $113.44" reads $0.00. Pinned per-fixture in X-g1's two files rather than in the shared helpers, because that is the X-c2a precedent exactly: `create_hysa_account` was pinned by the step that made the date load-bearing, with that step's full-suite run as the evidence | a fixture asserting against a state production cannot reach; every INVESTMENT / APPRECIATING fixture in the suite | **CLOSED at X-g2a.** Both helpers now pin the opening to the anchor period's first day and say why, and X-g1's own per-fixture restamp for the Property was deleted with the compensator it was. Its firing control: reverting the helper's restamp fails `test_pre_anchor_periods_agree_and_the_anchor_period_accrues`, the one test whose Property fixture does not restamp for itself | X-g2a |
| N-78 (X-g1) | **The investment balance map seeds the growth engine's YTD with the THROUGH-current total, which the field's own contract says double-charges the annual limit.** `InvestmentInputs` documents the pair precisely (`investment_projection.py:33-45`): `ytd_contributions` is the displayed limit-card value (`<=` the current period) and `ytd_contributions_seed` is "the `ytd_contributions_start` handed to the growth engine" (`<`), because "the engine's own per-period walk then applies and counts the current period's contribution against the limit" and "seeding the through-current value instead would charge the current period against the annual limit twice" (deep-quality-hunt #10). Three consumers obey it -- `investment_dashboard_service.py:374` and `:979`, `retirement_projection.py:600`. **`_investment._forward_project_rows:316` is the one that does not**: it passes `proj_inputs.ytd_contributions`, and its reference period is `post_anchor[0]` -- the FIRST period the forward walk then projects. So a recorded contribution in that period consumes the year's limit twice and the modelled amount for it is capped too low | not live: `ytd_contributions` is `$0.00` for all three real investment accounts (no shadow contribution rows anywhere), so the wrong field and the right one are the same number | **recorded, NOT fixed -- it dies with the module at X-g4.** X-g1's replay does not inherit it: the replay walks the recorded feed per period itself and caps against what is left, so there is no seed to get wrong. Recorded rather than patched because the correct fix in `_investment` is a one-word change to a module that is deleted two steps later, and shipping it would move a figure in a step whose contract is "the baseline cannot move" | X-g4 (deletion) |
| N-79 (X-g2 trace) | **The investment chart's projection axis and its contribution timeline are on two different calendars, so the chart answers differently depending on the day it is opened.** `_assemble_chart_context` projects over SYNTHETIC periods starting at `date.today()` (`growth_engine.generate_projection_periods`, `investment_dashboard_service.py:721-723`), while `ctx.contributions` is `build_contribution_timeline(..., periods=all_periods)` -- REAL pay periods, each `ContributionRecord` dated on its period's `start_date` (`investment_projection.py:639`). `growth_engine._project_one_period` looks a record up by the projection period's own `start_date` (`:399`), so the two align only when today IS a pay-period start: on a payday every synthetic period inside the real horizon matches its record and the chart applies the RECORDED amounts, and on the other thirteen days none matches and every period falls back to the flat `periodic_contribution`. Same account, same inputs, two answers | not yet measured in dollars; the gap is `recorded amount - periodic average` per period over the real horizon, and it is `$0.00` on today's data because no account has a contribution feed at all (ruling R-R) | **recorded, NOT fixed.** It is inside the forward WHAT-IF engine ruling R-U deliberately KEEPS, not the balance path, so X-g touches neither half -- but it is the same two-clocks-in-one-figure shape as plan step C6c-ii's double count and the plan's Section 8 lesson names it ("when a rule says period, ask if it means instant"). The honest fix is for the synthetic axis to carry the recorded feed by DATE rather than by period identity. **Its NEAR half closes at X-g2b as a side effect of ruling R-AF**: an axis opening the day after the current period's end lands on the real pay-period boundaries exactly (verified `2026-07-30..2026-08-12` against real period 9's own dates on both databases), so every synthetic period inside the real horizon matches its record whatever day the page is opened. The FAR half survives -- past the user's last pay period there is no record to match, which is where the flat fallback is the honest answer anyway | own commit (investment chart); near half at X-g2b (via R-AF) |
| N-73 (X-c2c trace) | **Five balance sites guard against a NULL anchor on two `nullable=False` columns.** `Account.current_anchor_balance` and `current_anchor_period_id` are both `nullable=False` (`app/models/account.py:91`, `:100`) with a `current_anchor_balance IS NOT NULL` check constraint (`:55`), and there are **0 NULLs across all 19 account rows in both databases**. Yet `_kernel.build_account_balance_map:508`, `_kernel.base_account_balance_map:341`, `_kernel.interest_projection_for_account:395`, `_kernel.interest_by_period_for_account:440` and `_investment.get_anchor_period_index:127` each branch on `is None`, and two of them return a `None` / empty map that every caller must then handle -- so a state the schema refuses is propagating optionality through the seam's signatures (line numbers re-verified 2026-07-26; the originals drifted by two at `c649b322`) | -- | **recorded, NOT fixed** (out of X-c2c's scope, rule 6). It is not merely dead: X-g removes the anchor PERIOD from the balance paths entirely, at which point four of the five guards have nothing left to test. **Confirmed at X-g2's trace and re-scoped**: the replay reads no anchor period at all, so from X-g2b the guards test a state the schema refuses AND that the producer beneath them no longer consults -- but deleting them changes `balance_map`'s `\| None` contract, which every net-worth consumer handles, so they stay for X-e. **Corrected at X-g2b's trace: FIVE becomes FOUR**, because `base_account_balance_map` deletes with the ladder (ruling R-AD) and its guard at `:341` goes with the function rather than surviving as one of the five | X-g2b (the need), then X-e (the guards) |
| N-80 (X-g2b trace) | **Two rulings correct ONE overlap twice, and stacking them starts the `/investment` chart's projection line below its own history line.** Ruling R-U made the forward-projection seed "the replay with ACCRUAL filtered out"; ruling R-AB then moved that read to `window_start - 1 day`. The filter's whole purpose is to stop the growth engine re-growing a period the seed already grew -- and R-AB's date makes that impossible, because `growth_engine.project_balance` grows only the periods it is handed and every one of them starts at or after `window_start`. So the filter subtracts growth the window never touches: the accrual since the account's latest assertion, measured 2026-07-27 as Roth **`$161.31`**, Trad IRA `$109.10`, Empower `$292.11` on `shekel` and `$82.67` / `$35.30` / `$292.11` on `shekel_f3_final`. The result is a projection line starting from a balance the history line beside it has not rendered since the assertion date. NOT data-dependent: all three accounts hold ZERO transaction rows in both databases, which is what this document predicted would make the junction `$0.00` | the projection line up to **`$292.11`** below its own history line at the junction; the junction itself `-$76.99` / `-$73.97` / `-$15.96` on `shekel` under R-AB as written | **CLOSED at X-g2b (`560b3339`).** The filter went, `asset_seed_at` and `AssetPeriodFigures.balance_without_accrual` deleted unwired, and the seed is the ordinary date-precise scalar.  Found because this document required the junction be MEASURED before the display fork was recommended, on a step whose own additive commit had already shipped the filtered entry -- which is the argument for additive-then-cutover, from the other end | X-g2b (`560b3339`) |
| N-81 (X-g2b trace) | **The `/investment` balance hero and the cell that RESTORES it read two different producers.** `investment_dashboard_service.compute_balance_hero_cell` -- the anchor editor's Cancel / Escape / 409 revert target, whose own docstring says it returns "the model-from-anchor balance the headline shows" -- reads the seam SCALAR (`balance_at.balance_at(account, ctx, ctx.as_of)`, `:684`), while `compute_dashboard_data`'s headline reads `balance_map[current_period]` via `_resolve_current_balance` (`:221`). They agree today only by accident: the kind-correct scalar for an INVESTMENT is period-granular, so it IS the map read at the containing period. X-g2b makes the scalar date-precise and they separate by the accrual from today to the period's end -- measured `$22.59` / `$9.65` / `$26.05` on `shekel_f3_final` and `$23.12` / `$9.67` / `$26.05` on `shekel`. Cancelling the anchor editor would then restore a figure the page was not showing | up to **`$26.05`** between a rendered balance and the value its own revert restores | **CLOSED at X-g2b (`560b3339`)**: one cell, one producer. The broader question it sits on -- whether a "current balance" tile should be a DATE or a period END -- is NOT opened here: `/savings` has shown the period-end figure for every kind since X-c2b2 (Checking `$2,824.26` at today against `$2,683.63` in its current column), so the modelled kinds are joining a shipped convention, not setting one | X-g2b (`560b3339`) |
| N-82 (X-g2b trace) | **Past the pay-period horizon the replay's ACCRUAL keeps running while its CONTRIBUTION tier stops.** A contribution event is dated on a real payday (`_asset_contributions._dated_events` walks `pay_period_service.get_all_periods`), and there are no paydays past the user's last pay period; the accrual window's end is the caller's own furthest requested date. So a date beyond the calendar reads growth-only. Today's producer has the opposite wart -- `_kind_correct.balance_at` resolves the date to a period and `find_period_containing_date` falls back to the latest period that ENDED earlier, so a past-horizon date clamps to the final column. Measured at 2029-01-01, six months past a horizon ending 2028-07-12: Empower **`+$2,501.92`**, Roth `+$1,754.08`, Property `+$5,427.07`, Money Market `+$272.24` | `+$5,427.07` at six months past the horizon; `$0.00` on any rendered surface | **RULED at R-AG (2026-07-27): let the fold answer and record this.** Not live -- the only non-loan caller of that scalar in production is the `/investment` hero cell, at today (the other four call sites are loan-gated), so nothing reads a past-horizon modelled date. The honest fix is a payday cadence that outlives the calendar, which is materially larger than X-g2b and belongs with whatever step extends the pay-period horizon | own commit (if ever) |
| N-83 (X-g2b trace) | **A Property's value is answered two ways on adjacent screens, and X-g2b widens the gap.** `/savings` net worth and the grid read the modelled map (which appreciates from the latest assertion's own day, ruling R-Y); the property detail page's equity HERO (`home_equity_service.resolve_home_equity:137`) and its equity CHART (`property_equity_chart._value_series`) BOTH read `Account.current_anchor_balance` -- the denormalized CACHE column -- and deliberately flat-carry it through today ("the last honest value"). Measured on `shekel`: the modelled current-period value is `$350,794.53` today against `$350,000.00` flat on the property page, a **`$794.53`** gap that X-g2b widens to **`$965.03`**. It is ruling R-W's shape on a different pair of surfaces (one account, two producers, no row explaining the difference), and it is also cash D4's cache column reaching a screen | **`$965.03`** after X-g2b, growing with the time since the last assertion | **recorded, NOT fixed -- and X-g2b TRIED and reverted, which is the row's most useful content.** Pointing `resolve_home_equity` at the seam inside the cutover produced two new defects its adversarial review caught: the equity CHART then double-counted the appreciation between today and the current period's end (its value became as-of the period end while `_value_series` still compounded from `today`), and the hero netted a period-end value against a today-dated debt leg. The second could not be fixed without moving the loan tile's own date convention as well, which is a third surface. So this stays PRE-EXISTING -- X-g2b changes its size, not its existence -- and the cross-page tests now assert the gap EXPLICITLY from both sides, so it cannot drift and this finding's own commit must update them. Its resolution is R-W's: one producer, with the difference rendered rather than implied, and ONE date for the whole property page. Note both readers take the CACHE column, so plan step X-e touches them too | own commit (property surfaces), with X-e |
| N-84 (X-g2b trace) | **The `/investment` chart's de-dup compensator subtracts a contribution the engine never re-applies on that axis.** `current_period_transfer_contribution` is subtracted from the chart's seed (`investment_dashboard_service.py:318`) so the growth engine does not double-count a recorded current-period contribution. But the chart's axis is SYNTHETIC and opens at `date.today()`, while `_project_one_period` looks a `ContributionRecord` up by the projection period's own `start_date` and every record is dated on a REAL pay-period start (finding N-79) -- so on thirteen days in fourteen no synthetic period matches the current period's record and the engine applies the flat fallback instead. The subtraction is then a pure UNDER-count on that surface, not a de-dup. It IS load-bearing on `retirement_projection`'s real-period fallback axis (`:379-382`), where the dates do match, which is why ruling R-AB had to establish the date before the compensator could delete | `$0.00` today (no account has a recorded contribution feed -- ruling R-R's measurement); the current period's recorded contribution, per open of the chart, when one exists | **CLOSED at X-g2b (`560b3339`)** -- it deleted with the overlap ruling R-AB removed. Kept as a row because it is the measured reason the compensator was never a de-dup on one of its two surfaces -- a compensator that was wrong in both directions at once, which is the shape this ledger exists to make visible before it is quietly ported | X-g2b (`560b3339`) |
| N-85 (X-g2b) | **`interest_by_period_for_account` has no production caller and survives on its own tests.** The account-detail page reads `interest_projection_for_account` for BOTH halves of its projection (the balance chart and the "Interest, next 12 mo" chip) since finding N-64 collapsed the two seam calls into one; this sibling entry -- which returns the interest map alone -- is exported from the seam, exercised by 5 test call sites in 3 files, and called by nothing in `app/`. That is the dead-code-alive-for-its-own-tests shape plan steps C3b4 / D2a / F2 / E1e each deleted. It was PORTED to the replay at X-g2b rather than deleted, deliberately: that step's contract is to move producers onto one event stream, not to prune the seam's public surface (rule 6), and deleting a public entry with its tests is a different commit's work | -- | **recorded, NOT fixed.** Delete it with the rest of the incumbent at plan step X-g4, which is already reading this list; a two-line port is the cheap way to keep it honest until then | X-g4 |
| N-86 (X-g2b review) | **The `/investment` limit CARD and the projection beside it read two different YTD boundaries, and only one of them is a function of the window.** `_compute_limit_info` renders `ytd_contributions` (the total THROUGH the current period -- what the user has actually put in this year), while `growth_engine.project_balance`'s `ytd_contributions_start` must hold exactly the periods the projection's window EXCLUDES. Those coincide only when the window opens past the current period, which is the case on this surface after ruling R-AF and is NOT the case on either of `retirement_projection`'s axes. X-g2b resolves the projection's half once, beside the window it depends on (`_context._projection_ytd`), so the two call sites cannot disagree; what is recorded is that the app now carries TWO correct YTD boundaries whose difference is invisible in the rendered figures. The durable version is for the engine to take the axis and derive the boundary itself, which is the same "one derivation" argument ruling R-AF made for the seed | `$1,000.00` of annual-limit room per period of divergence, compounded over the horizon -- the defect this finding's fix closed, measured on a `$23,500` limit at `$1,000`/period with today in the year's 15th period. `$0.00` on today's data: no real investment account has a contribution feed | **recorded, NOT fixed** -- the boundary is correct on both surfaces today and pinned in both directions by `TestTheAnnualLimitSeedFollowsTheWindow`. It is recorded because a THIRD projection surface would have to know the rule to get it right, and knowing a rule is what this arc replaces with structure | own commit (the growth engine's axis) |

## 7. Verification standard (what "done" means for every step)

1. **The baseline must not move** (Section 2) unless the step's design says it moves (C2), in
   which case every moved number is individually explained and signed off.
2. **Oracles are exhaustive and independent.** Every day, every shape; never a sample; never two
   producers that share code proving each other. The fold is the reference; optimized readers
   are proven equal to it over GENERATED shapes.
   **The REAL-DATA half of this has a saved harness:**
   `tests/manual/verify_balance_baseline.py` (added 2026-07-26, `60dbc117`) dumps every figure the
   seam can answer about every account in a database -- all five kinds' scalars and period maps,
   the whole grid view including R-K's remainder and R-Q's override map, cash scalars at five
   fixed dates, and the day-by-day series over the entire horizon. Run it before and after,
   `diff` the blobs. Use `git worktree` for the HEAD side, never `git checkout`. It is
   DETERMINISTIC (verified over three consecutive runs) and it is a REGRESSION check, never a
   proof: it answers "did anything move", and two figures identical in it can both be wrong
   (finding N-69). Every figure is read at the seam's default `as_of`, so a step whose change is
   scoped to a pinned historical as-of moves nothing in it -- X-c2c1 was exactly that, and its
   firing control, not this, was its proof.
3. **Every guard gets a negative control** that is shown to fire. A guard whose control does not
   fire is not a guard.
4. **The fixture matrix must contain the shape the feature exists for** (a paid loan, an
   off-schedule payment, a delinquent loan).
5. **Green gates are necessary, never sufficient.** A $197,049.32 defect passed pylint 10.00 and
   a 7,387-test suite. Live-render the five loan surfaces against the dev clone per CLAUDE.md
   rule 9.
6. **No uncited claims in this document.** Anything stated here as fact about the code was
   verified on 2026-07-16 or carries its own commit hash; when you edit this file, re-verify
   what you touch.

## 8. Process lessons (paid for repeatedly; do not pay again)

* Probe before you design; the 60-line probe has repeatedly beaten the 1,500-line plan.
* Two wrong implementations agreeing is not a proof.
* **A shared primitive reached through a private import is telling you the package boundary is
  wrong.** B1's own recipe needed four private names out of `loan_posting_service`; the fix was
  not to import them but to notice the walk was owned by the wrong package (B0).
* **An argument a caller can get wrong is a defect, not a contract.** The fold TOOK the pay
  periods its visibility rule needs, documented as "so a caller cannot fold against one period
  set and read against another" -- which was exactly backwards: nothing else took that
  argument, so it was the only way to disagree, and the grid passes a WINDOW ($150,000.00,
  measured). Load it, do not take it.
* A DRY refactor of a PREDICATE can move money -- prove two rules answer the same question
  before merging them; otherwise make one BUILD ON the other.
* **When two figures PARTITION a set -- a settled half that INCLUDES and a projected half that
  EXCLUDES -- both must draw the split from ONE set on ONE clock.** C6c-ii's settled sum keyed on
  the DISPLAY paid year (`walk.payment_splits`) while its de-dup keyed on the UTC
  `confirmed_shadows_through`; an evening-Eastern settle fell in the gap and double-counted a tax
  deduction ($495.01). The exclusion set must be the SAME set the inclusion sum draws from -- and the
  plan's own "airtight because as_of=today" reasoning was the trap, because `as_of` is a display date,
  not a UTC one.
* **Scan imports with an AST, not a regex.** A line-anchored grep cannot see
  `from app.services import (\n    balance_resolver,\n)`, and that is the form this codebase
  actually uses. D1's scope was set with one and undercounted its consumers 4 -> 2, the names they
  reach 7 -> 2, and the test files 18 -> 14 -- which inverted the step's whole design, because the
  names it could not see were the ones that decide where the boundary goes. The same grep was run
  again at D1's rebuild and reproduced the same wrong answer before an AST scan caught it. A
  measurement that silently under-reports is worse than none: it reads as evidence.
* **A fail-CLOSED gate is scoped by module identity, so creating a module is how you escape it.**
  W9909 exists because a name-keyed deny list fails open; its own scope is a module list, which
  fails open the same way one level up. D1a moved four names into two NEW modules and they left the
  scope silently -- a balance-at-T folded from them, and a route rendering it, both rated 10.00/10.
  When a refactor RELOCATES a public name, ask what gate was scoping its old home and carry the
  scope with it. Related: two fence lists that look alike can answer different questions ("may this
  module CALL a producer?" vs "must a new public function here be CLASSIFIED?"); treating them as
  one is what opened the hole.
* **When two sides of ONE problem have different SHAPES, the loose side is where the next hole is.**
  The loan side's facts / split / chronology are one package the fence scopes with one key; the cash
  side's equivalents were three flat modules plus five functions stranded inside a producer, scoped
  by a hand-written list its own review found self-attesting. Nothing was failing -- the asymmetry
  itself was the finding, and structuring the second side like the first is what let a fence surface
  be DELETED instead of maintained. Ask of every guard: is this compensating for a shape the other
  half of the codebase already got right?
* **A static guard that greps for a NAME cannot tell code from prose.**
  `test_grid_balance_computation_routed_through_resolver` asserted
  `"balance_at.cash_balance_map" in grid_source` and stayed green for a whole
  step after the route stopped calling it -- the string survived in a
  docstring (N-63). Match the CALL (`name(` with its paren), and prefer a
  behavioural assertion where one exists. **Measured twice now**: N-63's own
  "the codebase has several" was right, and the second one (N-67, the
  `/accounts` detail guard) was WORSE -- 3 prose mentions, 0 call sites, and no
  eventual docstring rewrite to fail it, so it would have stayed green
  indefinitely. When a step MOVES a call, grep every static guard for the old
  name before assuming the suite will notice. A guard's positive arm should
  also carry a NEGATIVE twin naming the shape it replaced, or the guard proves
  a call exists without proving the wrong one is gone.
  **Measured a THIRD time, from a new direction (B-17, found re-verifying a
  carried-forward row at the 2026-07-26 trim).** A guard can also prove where a
  value comes FROM while never proving that production puts it there: the
  debt-track test builds its OWN `_ad` dict with
  `"is_originated": figures.terms.is_originated` and asserts behaviour on it,
  while production builds that dict elsewhere -- with a `None`-loan fallback the
  test's dict has no branch for. Change the production key and the test stays
  green, because it never executes the builder. And note the arm that DELETES:
  when the name a negative arm forbids is itself removed, the arm stops being a
  guard and becomes a sentence that can never fail. Delete such an arm WITH the
  name; a guard against an impossible shape reads as coverage and is not.
* **A test whose fixture has no data cannot distinguish two producers.** Two
  seam scalar tests asserted `balance_at(...) == cash_balance_at(...)` over an
  account holding one opening assertion and nothing else, so both sides were
  the anchor and any producer passed (N-69). Neither reasoning nor review
  caught it; the firing control did, which is the whole argument for Section
  7.3. Ask of every equality test: what value would a WRONG implementation
  return here, and is it different from the right one?
* **A skip is safer to state than a fire.** When the operation being guarded is
  idempotent, write the predicate as "we may skip because X and Y", not as "we
  must run because X": a fire-predicate that misses a case silently leaves the
  data wrong, while a skip-predicate that misses one only costs work. The
  effect-time anchor self-heal was a fire-predicate and missed the case where
  the correction had never been written at all (N-61).
* A safety that is a predicate is not a safety.
* Boundary predicates standing in for instants or records are this codebase's signature defect
  (the walk clock, the period-start payment date, the archived X0 rule). When a rule says
  "period", ask if it means "instant"; when it says "schedule", ask if it means "record".
* Documents rot in days here. This file is the only one allowed to rot, and every edit re-dates
  it.

## 9. Rules for this document

1. **This is the only live planning document for the balance arc.** The archive is read-only
   history. If a step needs more design than Section 5 carries, the design happens in the
   commit/PR that ships it -- or amends this file. New standalone plans, audits, and follow-up
   documents for this arc are prohibited; findings become rows in Section 6.
2. When a step ships: tick its box, append the commit hash, and move anything it closed in
   Section 6 to status "closed (hash)". **Step IDs are append-only** -- a decomposition appends a
   suffix, and nothing is renumbered for readability; the Phase X header carries the measurement
   behind that (164 citations in 49 files, plus the commit messages).
3. When a ruling in Section 4 is answered: record the answer and date in place.
4. Keep the PLANNING surface small; ~500 lines is the target for it. Growth from marking work
   COMPLETED -- ticking boxes with hashes, "as built" step detail, moving findings to closed -- is
   fine and may push the file past ~500; that is the ledger doing its job, and it is NOT trimmed for
   length. The limit exists to catch NEW planning/design prose accumulating (the "documents rot"
   lesson), not to cap the record of what shipped.
5. **A COMPLETED half of the arc is archived whole, not trimmed piecemeal** (added 2026-07-26, on
   the developer's instruction, at 2,713 lines). When a half finishes -- every step shipped to
   production, every finding it opened either closed or carried -- its narrative, its rulings, its
   steps and its CLOSED findings move to one as-built record in `archive/`, and the live document
   keeps only the work that remains. Three conditions make it safe, and all three are requirements
   rather than courtesies:
   * **Unfinished work stays here whichever half it came from.** An open loan-side question is
     still open; Section 6 is the single home for every one of them.
   * **No live sentence may depend on an archived one.** Where a surviving ruling cited an archived
     one, restate the cited rule inline at the citation. The archived record may reference the live
     document; never the reverse.
   * **Re-verify what you carry.** A row that says "open" because nobody re-read it is worse than
     no row. The 2026-07-26 trim re-verified three and two were wrong (B-7/B-10 had been closed for
     a week; B-16 and B-17 named shipped steps as their resolvers and were still live). Rows carried
     WITHOUT re-verification must say so.
