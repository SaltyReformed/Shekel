# The cash balance architecture: the plan of record

**This is the ONLY live document for the balance arc, and it carries the work that REMAINS.**
Amendments are edits HERE, a shipped step gets its checkbox ticked with its commit hash HERE, and no
new planning documents get written for this arc. The rules are Section 9; read rule 6 first if you
are recording a finding.

**Two as-built records hold what is already done. Neither governs anything.**

| record | what it holds |
|---|---|
| `archive/cash_arc_as_built_2026-07-27.md` | Phase X as built: the running state narrative, every shipped step from **X-a** to **X-g3b** with its measurements and firing controls, and the 10 findings they closed. On DEV, not production |
| `archive/loan_arc_as_built_2026-07-26.md` | The LOAN half, complete and in production (PR #64, merge `88c79857`). Phases A-F, rulings D1-D5 / R-A / R-C / R-D / R-E, and the 75 findings that arc closed |

Everything else that ever governed this work is in `archive/`, indexed by `archive/README.md`.

**How this document works changed on 2026-07-27 (rulings R-AO and R-AQ).** The findings ledger was
triaged against the CODE: of 41 open rows, 10 named a live step that owned them and 29 did not, four
of those naming a resolver that had already SHIPPED. Nine new steps now carry them, **no finding is
unowned**, and there is no deferred category at all -- **Section 9 rule 6 fixes a closed owner
vocabulary and rule 7 rules that cost is never a ground for deferral.** Rule 6 is a GATE, shipping
as plan step X-h's fifth commit. Read those two rules before recording a finding.

## Where the arc stands

**The LOAN half is COMPLETE and in production.** The CASH half is in flight on `dev` and **both of
its cutovers are DONE** -- the cash one at X-c2b2 (`d3489728`) and the modelled one at X-g2b
(`560b3339`), after which no remaining step can move a figure except by fixing a defect. The grid
cutover X-g3b then landed on 2026-07-27, closing finding N-76 byte-exactly on 900 of 900 (account,
period) pairs. Every non-loan account's balance is now ONE event replay, date-precise for all five
kinds; the three-source merge, the reverse growth projection, the per-period interest layer and the
kernel's per-kind ladder are all out of the read path and awaiting deletion.

**What the cash cutover bought, measured on the prod-shape clone and signed off:** Checking today
`$2,791.78` -> **`$2,824.26`**, the figure the app's own persisted double-entry ledger already
carried, so the screens stopped contradicting its own books; eight blank past grid columns gained
real balances; `/savings` went from 0 to 6 history points; and both loans and every investment map
were unmoved. **What the modelled cutover bought:** an investment's balance answerable at a DATE
rather than a period end, and the user's own recorded balance assertions winning over a model that
had been overriding 12 of the 15 they had entered.

**X-g4 and X-c2c4 are DONE**, shipped 2026-07-27 as X-g4a (`2ee817b4`, the ported 52-period drift
oracle) and X-g4b (`17c57cde`, the deletion). The deletion turned out to be a CLOSED import cluster with
zero `app/` entry points -- `_investment` -> `_cash_engine` -> `_calculator`, plus `_interest` WHOLE
and two loaders orphaned by them -- so **1,347 production lines and 4,937 test lines went, and the
`verify_balance_baseline.py` harness stayed BYTE-IDENTICAL on both databases.** Every producer this
arc set out to replace is now gone from the tree rather than merely unwired: a non-loan balance is
ONE event replay, a loan is its `positions()` fold, and there is no third answer left to pick.

**With X-g4 the whole of X-g is DONE**, and its header is ticked with the last of its four commits
(rule 6's convention for a decomposed step). The ledger tail that step left is closed too: N-43,
N-46, N-78 and N-95 were resolved BY `17c57cde` while still reading "OPEN" against a ticked owner,
and they are now in the archive's closed register -- the exact class plan step X-h's gate exists to
make impossible, found by hand one more time.

**NEXT: X-o, then X-q and X-r**, then the steps after them in the order Section 5 lists. X-o is a
one-predicate fix to a LIVE defect on a rendered screen and depends on nothing. **X-o's trace opened
the other two** (rulings R-AV / R-AW): the debt-free date has a SECOND producer that the predicate
fix does not merge -- measured 19 years apart on the developer's own data, 28 on an independent
fixture -- and B-16's ROOT is that the per-account projection dict re-flattens the seam's
`LoanFigures` field by field and dropped the one field the debt-line question needed.

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

**Two of those four places have since closed, and both closed at plan step X-g2b (`560b3339`).**
Kept here in one paragraph because they are the measured case for the design and because Section 3.2
is still written against them: a modelled asset's balance WAS three producers merged by a preference
order that overrode the user's own recorded facts -- `$6,315.57` of rendered net-worth history
contradicting 12 of the 15 balance assertions the three modelled accounts carry (finding N-74), plus
a future contribution that rewrote a past balance (N-75) -- and three kinds answered a DATE with a
PERIOD, so a whole period's growth landed on its first day (`$328.50` on the Empower 401(k), N-71).
One event replay closed both. `_merge_balance_sources` and the reverse projection were dead code with no
live caller and were DELETED at plan step **X-g4b** (`17c57cde`). The full record is
`archive/cash_arc_as_built_2026-07-27.md`.

**What remains, and the first three are the same disease this document opened with:**

1. **The write side and the read side are still two statements of one rule.** The posted account
   ledger is written by its own walk while the projection folds another; they are currently proven
   byte-identical, which is a test keeping two implementations in step rather than a structure that
   cannot drift. Plan step **X-d**.
2. **A derived cache is still read as a source of truth.** `Account.current_anchor_*` is a
   denormalized copy of the latest `AccountAnchorHistory` row; `cash_ledger.resolve_anchor` detects
   the divergence and only LOGS it (`EVT_ANCHOR_CACHE_RECONCILED`), never repairs it (cash D4). It
   reaches 23 files in four roles, including one rendered property value. Plan step **X-e**.
3. **The pay calendar is a PARTIAL function -- the same shape, on the other axis.**
   `pay_period_service.get_all_periods` returns the materialized rows and stops, so past the last
   one every consumer improvises: the replay's accrual keeps running while its contribution tier
   goes silent (N-82), and the investment chart's projection axis has no record to match (N-79).
   Plan step **X-l**.
4. **Nothing in the app records WHEN money moved.** `paid_at` is `db.func.now()` at the click and
   the API refuses any other value; the amounts are right and the DATES are guesses. The
   reconciliation row plan step X-c2b2 put on screen is the INSTRUMENT that measures that noise --
   `$36,323.99` of gross swing across 51 assertions against a true four-month bookkeeping error of
   `-$159.73`. Plan step **X-f** shrinks it at the source.
5. **A surface still picks which producer answers for its account.** `/dashboard`'s hero, its pulse
   and the analytics calendar read the kind-blind cash view while `/grid` and `/savings` read the
   modelled one, so the same account renders two balances for the same period -- **`$681.34` apart
   on the developer's own default screens today**, one click apart, with the pulse's chips linking
   straight to the disagreeing figure (N-87). Plan step **X-j**.
6. **The read pass pins a clock it does not hand to its loaders.** `BalanceContext` fixes the
   pass's `as_of` and `scenario` and memoizes three loan derivations; every other input is loaded
   ad hoc at the wall clock, which is both a redundancy (the calendar three times per modelled grid
   render) and an impurity (a historical read models contributions at TODAY's gross). Plan step
   **X-i**.

7. **One question, two producers -- on the debt-free date this time.** `/savings` derives "when is
   this user debt-free" twice from one `account_data`: the cockpit caption selects loans by their
   BALANCE, the Horizon chart's flag by the debt-line predicate, and they part on a mortgage that
   has not closed yet (**19 years** apart on the developer's own data). Plan step **X-q**.

**Nine more steps carry what the seven roots above do not name**, each owning findings the
2026-07-27 triage grouped by root: X-o, X-r, X-h, X-k, X-m, X-n, X-p, plus E2's two. (X-q is root 7
above, so it is not counted again here.) Section 5 has them in execution order; Section 6 records who
owns every open finding, with no row unowned.

## 2. What is already shipped and correct (the foundation this plan builds on)

**The table is the FOUNDATION the remaining steps stand on, not the log of how it was built** --
that is `archive/cash_arc_as_built_2026-07-27.md` Section 2 for the cash half and
`archive/loan_arc_as_built_2026-07-26.md` for the loan half. What follows the table is LIVE: the
loan regression baseline is a gate every remaining commit is run against.

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
* **The period grain for three kinds** (N-71 -- a whole period's growth landing on its first day,
  `$328.50` measured on the Empower 401(k)), which closes cash D2 for the last
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

### Answered (developer ruling, 2026-07-27: X-g3's four forks, all as recommended)

X-g3's own trace, run before any code. It answered the three forks the step
entry recorded, found a fourth, and INVERTED the third: what the entry filed as
"where the step's cost actually is" is the difference between the step working
for the INVESTMENT kind and doing nothing for it. Every figure below was
measured on 2026-07-27 against BOTH databases, and the three investment accounts
and the Property hold ZERO transaction rows in both, so none of it is a property
of the data.

| # | ruling | consumed by |
|---|---|---|
| **R-AH** (answered 2026-07-27) | **The grid renders the two modelled tiers as TWO conditional rows, on ruling R-O's own non-zero rule and on both form factors (R-P).**  Ruling R-W wrote ONE "Growth" row and this document already recorded that its identity was short a term; the trace measured the term and then found a second reason the rows must stay apart.  **The term:** on the real Empower 401(k) the horizon accrual is `$8,152.58` and the CONTRIBUTION `$9,624.27`, and R-W's identity as written (`net + reconciliation + accrual`) breaks on **53 of 59** period pairs, worst **`$181.59`** a column -- the employer's flat 5% of `$3,631.74`.  The four-term form (`+ contribution`) BREAKS on **0 of 59** -- it holds on all 59 -- for every non-loan account on both databases.  **The second reason, and it is what decides ONE row against TWO:** a single summed row can render POSITIVE on an account that lost money.  Measured inside a ROLLED-BACK transaction on the real Empower at a `-10.5%` return -- a rate BOTH schemas explicitly permit (`asset_appreciation_params.py:40-42` says so in its own words, above the constraint at `:43-45`, "A negative rate is permitted so a future depreciating asset (e.g. Vehicle) reuses this table unchanged"; `investment_params.py:49` carries the same bound) -- the market takes **`-$7,366.83`** while payroll puts in **`+$9,624.27`**, so one row shows **`+$2,257.44`**; in the worst single column an accrual of `-$142.11` beside a contribution of `+$181.59` renders **`+$39.48`**.  A figure that is neither what the market did nor what the user put in, reporting a gain on a loss.  Ruling **R-AC already ruled these same two tiers apart** on `/investment`, and the shipped chip renders them apart -- the accrual as its value, the contribution as its caption (`investment/dashboard.html:93-100`).  The reason is stated at the producer they both read: "they answer different questions -- what the market did, and what the user put in" (`_asset_fold.asset_growth_at`, `:803-805` -- the review corrected this citation, which this ruling first attributed to R-AC's own text).  So ONE row here would also be the app answering one question two ways on two screens.  ROW ORDER is the MODEL's own, corrected from the review: Timing & true-ups, **Contributions, then the accrual**, Projected End Balance.  The ruling first said accrual-then-Contributions; the review pointed at the mechanism and it is the other way round -- a contribution lands on its pay period's `start_date` (`_asset_contributions.py:255-262`) and `_asset_fold._resolve_days` applies the day's deltas and THEN accrues on the balance the day ENDS holding (`:559-570`), so the money is contributed and then earns.  Addition is commutative and the identity holds either way; reading order is not, and the rows should tell the story in the order the replay does.  **The accrual row's SIGN must reach its styling** (finding N-88, fixed at X-g3a): the mobile card hard-codes `text-success` on it (`_mobile_tp_summary.html:106`), which is safe only while INTEREST is the sole kind reaching it, because `interest_params` bounds `apy >= 0` (`:33-35`) where `investment_params` (`:48-51`) and `asset_appreciation_params` (`:43-45`) bound only `> -1`.  A depreciating asset would render `-$142` in success green.  It follows `/investment`'s shipped convention, whose own comment states it: "Gain in the done token, loss in danger; the rendered sign carries the meaning so color is never the only signal."  Density is bounded by R-O's rule and by the tier itself -- `_asset_contributions.contribution_events` returns `[]` for every kind but INVESTMENT, so the Contributions row can never appear on an HYSA or a Property, and on real data it appears on ONE account (the Empower, 53 of 60 columns).  Rejected: one summed row (above), and folding the contribution into Total Income (the income subtotal is the sum of the rows the grid renders beside it -- ruling R-K's basis -- and a modelled contribution has no row). | X-g3a (shape), X-g3b (figures) |
| **R-AI** (answered 2026-07-27) | **The accrual row's label is PER KIND, resolved in the ROUTE from `classify_account`; the contribution row is unconditionally "Contributions".**  INTEREST -> "Interest", INVESTMENT -> "Growth", APPRECIATING -> "Appreciation".  It is not a new vocabulary: the app already speaks all three words, each on that kind's own page -- "Interest, next 12 mo" (`accounts/_cash_band.html:41`), "Growth since Jun 23" (`investment/dashboard.html:95`), and "appreciation" / "at 3.0%/yr" (`accounts/property_detail.html:92`, `:95`).  Those three are PHRASES with their own windows baked in, not instances of one string, so this map is the canonical source for the GRID's row rather than a fourth copy of anything -- stated because the review asked the question.  The map is keyed on the `AccountProjectionKind` ENUM, never a `.name` string (CLAUDE.md's reference-table rule), and it lives in the presentation layer because `_grid` carries no display strings and `GridRowFlags`' own docstring earns its place in the seam by "carr[ying] no money and decid[ing] no figure" -- a label decides no figure but it is also not a balance rule, so it goes where the render entries already build their context.  **It is a TOTAL function of `Account \| None`, not a three-entry subscript** (corrected from the review): `classify_account` returns FIVE values (`account_projection.py:69-73`) and `_build_grid_view` legitimately carries `account=None` for the zero-accounts user (`routes/grid.py:279-280`), so a three-key lookup would `KeyError` on the PLAIN checking account every default `/grid` render resolves to, and `classify_account(None)` would `AttributeError` beside it.  PLAIN and AMORTIZING map to the generic word and can never render it -- neither kind resolves an ACCRUAL tier, so `row_flags.accrual` is `False` for both -- and a `None` account has no columns, so no flag is ever true.  A default-less `.get()` was rejected for the same reason the codebase rejects a bare `except`: it turns a missing case into a silently wrong label.  Ruling R-O's one-conditional-row rule governs VISIBILITY and is untouched by what the row is called.  Rejected: ONE word for every kind ("Growth", R-W's own word), which introduces a fourth vocabulary contradicting three shipped pages and renames the "Interest" row an HYSA has carried since PR #47; and keeping "Interest" everywhere, which labels a house's `$21,856.66` of appreciation and a 401(k)'s market return "Interest". | X-g3a |
| **R-AJ** (answered 2026-07-27) | **`grid_balance_view` assembles the account's REAL `ContributionInputs`, its kind GATE deletes, and `GridColumn.interest` becomes a non-optional `accrual` beside a new `contribution`.**  Three structural corrections, two of which the step entry did not have.  **(a) The assembly is not a cost, it is the whole INVESTMENT half.**  `_asset_fold._modelled_return` reads the CALLER's `investment_params` on the INVESTMENT arm (`_asset_fold.py:394-397`) while INTEREST reads `_interest.accrual_params(account)` and APPRECIATING reads `account.asset_appreciation_params` off the row -- so under today's `ContributionInputs.absent()` an INVESTMENT models NO return at all.  Measured: the last column stays `$28,000.00` / `$11,675.48` / `$31,070.06` on `shekel` and `$27,432.35` / `$11,714.31` / `$31,070.06` on `shekel_f3_final`, byte-identical to what the grid renders today.  It loads through `_inputs._contribution_inputs_for_account(account)` -- the same entry `_kind_correct._modelled_scalar` and `investment_growth_since_anchor` already call, so the app keeps ONE definition of "what does this account's payroll put in".  (**Corrected at plan step X-g3b-0**, which deleted the `_assemble_inputs` / `_contribution_inputs` pair this ruling originally named, along with the four-field bundle they existed to build and slice; per Section 7.6 the citation is re-pinned here rather than left to drift.)  Cost, best of five with a fresh `BalanceContext` per run, re-measured under the review on BOTH databases: an INVESTMENT grid account `2.6-2.8 -> 13.0-15.0 ms` (the deductions query plus the raise-aware gross fetch, the same assembly `/savings` already pays for the same account); the real Checking `99.9 -> 100.6 ms` on `shekel` (804 rows) and `92.8 -> 93.7 ms` on `shekel_f3_final` (815); every other kind within noise.  **Stated as a cost, not a saving:** an earlier draft read `102.1 -> 101.8 ms` on Checking, which the review could not reproduce -- it measures `+0.7` to `+0.9 ms`, inside the run-to-run noise either way, and a claimed improvement that is really noise is the kind of figure this document must not carry.  **(b) The gate deletes.**  `_asset_fold.resolve` already returns the cash fold unchanged for an account that models nothing, so `if _interest.accrual_params(account) is not None` (`_grid.py:346`) is a second statement of a decision the producer makes -- Section 8's signature defect, and the module's own "the replay says the same thing by having no ACCRUAL tier to resolve" becomes literally true instead of a note.  Measured: `$0.00` on **60 of 60** columns for all three PLAIN accounts across both databases, at `99.9 -> 99.9 ms` on `shekel`'s Checking (804 rows; the load dominates), `92.8 -> 93.5 ms` on `shekel_f3_final`'s (815), and `2.7 -> 2.8 ms` on the small PLAIN account, which exists on `shekel` only.  **(c) The field.**  Under one replay every requested period carries a `Decimal` accrual and a `Decimal` contribution, so `Decimal \| None` is a state the producer cannot be in, and the **TWO** accrual-specific template guards -- `_balance_row.html:81` and `_mobile_plan.html:128`, both `col.interest is not none` -- go with it, and `row_flags`' `not in (None, ZERO)` loses its `None` member -- a guard against an impossible shape, which Section 8 rules "reads as coverage and is not".  **Only the `None` member goes:** the `!= ZERO` half IS ruling R-O's visibility rule (`_grid.py:221-223`) and stays load-bearing, stated because the shorter phrasing invited deleting the rule with the dead half.  **TWO, corrected from the review, and the count matters:** the other `is not none` tests in those three templates are `col is not none` / `bal is not none` (`_balance_row.html:64, 92-94`; `_mobile_tp_summary.html:70, 112, 130`; `_mobile_plan.html:63, 65, 109, 135`) and every one of them is LOAD-BEARING -- a period is absent from `columns` exactly in the zero-accounts empty view (`_grid.py:383`), which is the state those guards exist for.  An implementer working from a count of four would delete two of them and crash `/grid` for a user with no accounts.  Rejected: keeping the name `interest` (one kind's word for a figure that is now three kinds', the same reason ruling R-AI exists), and keeping a gate (a second statement of one rule for a measured saving of ~0 ms). | X-g3a (c), X-g3b (a, b) |
| **R-AK** (N-87; answered 2026-07-27) | **The dashboard, the pulse and the analytics calendar STAY on the kind-blind cash view; the contract statement X-g3 makes false is corrected in-commit and the divergence is RECORDED, not fixed.**  A fork the trace found: `dashboard_pulse_service.py:123-133` reads `cash_balance_map` and gives as one of its reasons that the modelled map would be "diverging from the grid that deliberately keeps the SAME account on the cash-flow view".  **That reason has been false since PR #47.**  Measured at the last projected period TODAY, before X-g3: the dashboard renders the Fidelity Savings at `$5,363.56` against the grid's `$5,779.68` (**`$416.12`**) and the Money Market at `$16,644.27` against `$17,348.99` (**`$704.72`**).  X-g3 extends the same gap to `$6,263.60` (Roth), `$2,662.70` (Trad IRA), `$17,776.85` (Empower) and `$21,856.66` (the Property).  The three surfaces stay because the pulse's SECOND argument is untouched by R-W and was never weighed against it: modelled growth inflates the "lowest point ahead", so a real future dip below zero could be hidden -- a RUNWAY-safety argument about the question `/dashboard` asks ("will I run out of money"), not the question the grid asks.  It deserves its own ruling with its own measurement.  Rejected: moving the dashboard and pulse inside this step (overrides a stated safety argument with no ruling, inside a money-moving render cutover -- the exact shape X-g2b's reverted property-hero attempt cost two defects), and moving all three (stacks a second unruled change onto the calendar, whose flow chips and balance line are already on two clocks by up to 25 days, finding N-58). | X-g3a (the correction), N-87 (the record) |

### Answered (developer ruling, 2026-07-27: X-g3a's three build forks, all as recommended)

Three forks this document did not have, raised DURING X-g3a's build and its two adversarial
reviews. Each supersedes something written above, so each is recorded here rather than in a commit
message alone: a ruling whose only record is a code comment is one X-g3b re-derives from the stale
pin.

| # | ruling | consumed by |
|---|---|---|
| **R-AL** (answered 2026-07-27) | **The accrual row's CSS marker renames kind-neutral: `interest-row` -> `modelled-accrual-row`, and the new row is `modelled-contribution-row`.**  The shared `accrual-row` class on both stays.  The row serves three kinds from ruling R-W onward, so a marker naming one of them is the same defect ruling R-AJ (c) fixed one level up by renaming the FIELD -- "the seam has ONE vocabulary for the tier rather than a field called `accrual` behind a flag called `interest`".  Measured cost before ruling: the class has **no rule anywhere in `app/static/`** (grepped across CSS and JS), so it is a semantic hook and a test marker only and the rename drops no styling; it moved 6 assertions in `tests/test_routes/test_grid.py`.  Rejected: keeping `interest-row` (zero test churn, but it leaves a row labelled "Appreciation" for a house carrying a class named for an HYSA -- and finding N-88 exists precisely because the last kind-specific assumption baked into this row's markup went unnoticed). | X-g3a |
| **R-AM** (answered 2026-07-27) | **The accrual figure's sign treatment is THREE-WAY -- `> 0` green with an explicit `+`, `< 0` the danger token, `== 0` neither -- and NOT `/investment`'s `>= 0`.**  **This document's own X-g3a pin was wrong**: it said N-88's styling "follows `investment/dashboard.html:93-100` **verbatim**", and verbatim imports a boundary the source never exercises.  The chip renders once; ruling R-O renders `$0` in **every column** of a window the row is on for, so `>= 0` would paint every empty column of an accruing window as a gain and print `+$0` in success green.  Three-way makes colour and the rendered sign agree by construction, which is what `/investment`'s own comment asks for ("the rendered sign carries the meaning so color is never the only signal").  It also uses the GRID's tokens (`text-success` / `balance-negative`) rather than the chip's (`pulse-chip--done` / `invd-verdict__val--neg`), because the mobile card's Net Cash Flow bar three rows up already uses exactly that pair -- a better reading of "verbatim" than the literal one.  The rule is stated ONCE for all three surfaces, in `accrual_class` / `accrual_money` (`templates/grid/_grid_row_macros.html`), and deliberately NOT hoisted into `_money_macros.html`: `accounts/_cash_band.html` and `investment/dashboard.html` render a signed figure on the `>= 0` boundary, and merging two predicates that answer slightly different questions is the move Section 8 rules can move money.  Rejected: verbatim `>= 0` (above). | X-g3a |
| **R-AN** (answered 2026-07-27) | **The two MODELLED rows render CENTS; the four rows around them stay whole-dollar.**  Raised by X-g3a's code review, and the reason is ruling R-O's own rule rather than taste: the row is on screen because at least one visible column carries a NON-ZERO accrual, and at whole-dollar precision a sub-50-cent accrual rounds to `$0` -- so **the row can appear showing nothing but zeros and the rule that put it there is unverifiable from what is rendered**.  Reachable, not theoretical: a 14-day accrual under 50 cents is any INTEREST account below roughly `$460` at 4% APY, and also the FIRST column after a mid-period assertion, where ruling R-Y's window is only a few days wide.  It also removes a state where the same visible text was treated two ways -- a 31-cent gain rendered `+$0` in success green beside a genuinely-zero `$0` in no colour.  The balance, subtotal and remainder rows keep whole dollars: they carry hundreds to thousands, where cents are noise.  Rejected: whole dollars everywhere with the macro's over-stated docstring corrected (leaves the invisible-reason row), and deciding the colour on the ROUNDED figure (fixes the contradiction but leaves the row of zeros, and makes the template round the money a second time to pick a class). | X-g3a |

### Answered (developer ruling, 2026-07-27: the findings-ledger triage)

Not a step's forks -- the LEDGER's. Section 6 had grown to 51 rows and the developer asked which
of them the remaining Section 5 steps actually close, which need a plan of their own, and whether
any would be better answered by a structural change than by a patch. The count and the grouping
below were measured against the CODE on 2026-07-27, not read off the rows, which is how the four
stale resolvers and finding **N-95** were found.

| # | ruling | consumed by |
|---|---|---|
| **R-AO** (answered 2026-07-27, as recommended) | **The homeless half of the findings ledger becomes FOUR named steps, and two existing steps widen to absorb the rest.**  Counted 2026-07-27 across Section 6's 51 rows: **10 CLOSED, 41 open**; of the 41 one is the E2 pointer row and one an operator question, **10 name a live step that owns them, and 29 do not**.  "Own commit" is not an owner -- and the trim's own lesson says an unowned row does not wait, it rots: FOUR of the 29 (**N-14** "Phase D", **N-33** "D3-adjacent", **N-40** "X-c", **N-56** "X-c2b2") named a resolver that has SHIPPED, which is the B-16 / B-17 class recurring, and N-40 was re-verified LIVE on this date (`loan_payment_service.py:864` calls `date.today()` and `_cash_fold.py:482` feeds that map into a fold holding a pinned `as_of`).  Grouped by ROOT rather than by symptom the 29 collapse to five: **X-h** the blind controls (B-17, N-45, N-65, N-94), **X-i** the read pass that does not own its inputs (FU-3, N-14, N-40, N-56, N-72b, N-89, N-91, N-92, N-93), **X-j** the surface that picks its own producer (N-58, N-83's display half, N-87, N-90), **X-k** the recurrence bound (N-18, N-19, N-23, N-24, per R-AP), plus **X-e** widening (X5, and N-83's cache half named explicitly) and **E2-0** widening (N-33, N-35).  **Six were left as "own commit" residue -- B-16, N-25's class, N-36, N-79's far half, N-82, N-86 -- and ruling R-AQ the SAME DAY overturned that half of this ruling**: they became X-o / X-n / X-l / X-m / X-p plus one dated `developer-decision`, and the words "residue" and "own commit" left the owner column entirely.  **Twenty-nine unowned rows become ZERO** (R-AO got them to six; R-AQ got them to none).  **Order (as amended by R-AQ): X-g4 -> X-c2c4 -> X-o -> X-h -> X-i -> X-j -> X-k -> X-l -> X-m -> X-n -> X-d -> X-e -> X-f -> X-p -> E2.**  X-h first on the X-g2b-0 precedent (the instrument before the measurement -- four controls that cannot fail are what currently grade the steps after them); **X-i before X-j on a MEASURED ground, not a preference**: X-j moves the pulse, the hero and the calendar onto the modelled view, whose contribution load N-93 measured at `2.7 -> 14.8 ms` per render entry for an INVESTMENT account and whose pulse scans the whole horizon, so X-j first ships that regression onto three more surfaces.  Rejected: leaving the 29 as own-commits (the state that produced four stale resolvers, and 9 of them would each re-decide the same `BalanceContext` question separately), one combined cleanup step (X-h and X-i1 move nothing while X-i2 and X-j move money -- the mix that makes a plumbing slip read as a fold slip, which is why this arc split X-c2b, X-g2 and X-g3), and new planning documents (rule 1) | X-h, X-i, X-j, X-k, X-e, E2-0 |
| **R-AQ** (answered 2026-07-27, **NOT as recommended -- it goes further**) | **There is no DEFERRED category. Every finding is owned by a step, and a wake condition is not an owner.**  The recommendation was to keep six rows as "residue" and give four of them stated wake conditions -- the premise that a finding costing `$0.00` on today's data can wait for the data to change.  **The developer ruled that a finding waiting on a condition is a time bomb with a note attached**, and that effort is not a reason to leave a defect in place: correctness takes priority, there is no QA team, and a deferred fix is one a future session pays for with interest.  So the six become five owned steps and one developer decision.  **It SUPERSEDES the record-not-fix half of ruling R-AG** (N-82: "let the fold answer and record this"), whose three grounds were one concession -- "correct in principle" -- and two costs, "invents a calendar the app does not have" and "materially larger than this step"; cost is now explicitly not a ground.  R-AG's OTHER half STANDS and is what keeps X-l safe: the fold stays TOTAL and is never clamped at a horizon.  **It also retires the word "residue" and the value "own commit" from Section 6's owner column**, which is what makes the gate below expressible: an owner is a live step ID, `operator`, or `developer-decision`, and nothing else.  **N-25's class is the single `developer-decision`** -- the developer has taken the custom-checker-versus-declined fork for its own session rather than having it decided here, which is an owner with a name on it and not a defer.  Where the roots landed: **X-o** B-16 (a LIVE defect, and the recommendation under-triaged it as residue), **X-l** the pay calendar's partiality (N-82 + N-79's far half -- one root, this arc's own disease on the other axis), **X-m** the projection engine's boundary arguments (N-86), **X-n** the loan schedule's destroyed installment date (N-36), **X-p** the calendar's two clocks (N-58, sequenced after X-f by its own prior ruling -- scheduled, not deferred).  Rejected: the wake-condition scheme (a condition nobody is watching is prose, and three of the four premises are properties of the DEVELOPER'S DATA that the suite cannot see at all -- so the guard would have been claimed and not held), and a staleness clock over the ledger (turns a real signal into recurring busywork, which is how a check stops being read) | X-l, X-m, X-n, X-o, X-p; the Section 9 gate |
| **R-AP** (answered 2026-07-27, **NOT as recommended**) | **The write-side recurrence cluster STAYS in this arc as plan step X-k.**  The recommendation was to hand N-18 / N-19 / N-23 / N-24 to the recurring-transfer arc with pointer rows left here, on the ground that they are recurrence and transfer-WRITE semantics rather than balance reads.  **The developer ruled against it**: they stay in this ledger and get a step of their own.  Recorded as declined rather than silently adopted, because Section 4's other 2026-07-27 blocks all read "as recommended" and this one does not -- and because the ground the recommendation rested on survives the ruling and is what the step's own scoping has to respect: X-k touches the recurrence engine and the transfer write door, NOT the seam, so it shares no file with any other remaining step and must not grow into one | X-k |

### Answered (developer ruling, 2026-07-27: X-g4's four forks, all as recommended)

**The trace ran first and no code was written for it.** It scanned `app/` + `tests/` + `scripts/` +
`tools/` with an AST (never a regex, Section 8) and then READ every consumer, which is how the name
trap in R-AR and the already-blind CRIT-01 controls in R-AU were found. Three further forks it
raised are trace DECISIONS taken under these four and recorded at the X-g4 entry as forks 3, 5 and
7; they change no figure and are named so the build is not re-deciding them.

| # | ruling | consumed by |
|---|---|---|
| **R-AR** (answered 2026-07-27, as recommended) | **X-g4 and X-c2c4 ship as TWO commits: X-g4a the PORT, then X-g4b the WHOLE deletion**, and both boxes tick with X-g4b's hash.  The trace measured the three modules as a CLOSED import cluster -- `_investment` -> `_cash_engine` -> `_calculator`, with **zero `app/` importers of any of them** -- so the page's stated X-g4-then-X-c2c4 split would produce an intermediate commit whose entire content is "delete a module nobody imports and keep its 815-line test file alive one more commit", a state neither endpoint has.  What the split that SURVIVES buys is the C3b3 prove-the-successor-first precedent applied to COVERAGE rather than to a producer: X-g4a lands the ported drift oracle while its incumbent is still alive and green, so the deletion cannot be the commit that first discovers the port is wrong.  **The deletion follows the MODULE, never the name, and the trace found why that matters**: `investment_growth_since_anchor` is defined TWICE -- dead in `_investment.py:575`, LIVE in `_kind_correct.py:260` (re-exported at `__init__.py:218`, called at `investment_dashboard_service/_orchestrator.py:64`) -- so a name-keyed deletion takes a live seam entry.  Rejected: three commits (above) and one commit (the port is never proved green before its incumbent dies) | X-g4a, X-g4b |
| **R-AS** (answered 2026-07-27, as recommended) | **`_interest.py` DELETES WHOLE; its one surviving predicate folds into its one caller.**  After the layering pair goes, `accrual_params` is 37 lines with a single consumer (`_asset_fold._modelled_return:392`) -- and it re-runs `classify_account(account)` inside a branch whose caller has ALREADY classified, so inlining it removes a redundant second classification rather than merely moving code.  All three modelled kinds then read ONE shape: resolve this kind's params, `None` if absent.  The non-ORM-fake guard survives exactly, as `getattr(account, "interest_params", None) or None`; `_asset_fold` goes 883 -> ~890 lines, under the ceiling.  **What must NOT be lost with the file:** its docstring carries ruling R-L's window history and the day-count lesson Section 3.2 cites (`_interest.py:230-243`, counting 13 days of a 14-day period), so both are restated in `_asset_fold`'s docstring -- deleting the record of a defect class is how it comes back.  Rejected: keeping a one-function module whose name ("modelled INTEREST accrual over a folded base") no longer describes it and whose duplicate classification survives with it | X-g4b |
| **R-AT** (answered 2026-07-27, as recommended) | **The ported 52-period drift oracle exercises ALL THREE of the fold's tiers, not the one the original had.**  `test_52_period_penny_accuracy` walks an anchor plus still-projected rows -- one tier -- so a faithful port would be a long-horizon drift oracle for a THIRD of the producer it now grades.  The ported shape is: an opening assertion, a SETTLED past half, **a mid-horizon RE-ASSERTION** (the reset the original could not express at all, and the rule the whole cash fold turns on), and a still-projected future half read at an `as_of` that makes ruling R-G's clamp a no-op, asserted at all 52 period ends against a test-local `Decimal` running total plus a cumulative cross-check.  The oracle stays a test-local total and never the fold reading itself (Section 7.2).  Same cost, three tiers instead of one.  Rejected: the faithful port, which reproduces exactly today's coverage and nothing more | X-g4a |
| **R-AU** (answered 2026-07-27, as recommended) | **`test_asset_fold_parallel.py` KEEPS the two classes graded against something other than the dying incumbent, and loses the three that are not.**  Neither entry mentioned the file, and it is this step's largest test decision.  Three of its five classes compare the replay to `_investment` / `_interest`; the grain class's first test (`:96`) compares it to `growth_engine.project_balance` -- the INDEPENDENT engine ruling R-U deliberately keeps -- and `TestTheTwoGrainsAreOneRunningTotal` asserts properties of the replay alone.  Those two survive untouched; the three classes go, along with the grain class's second test, which reaches `_investment` at `:167`.  The module docstring is rewritten: it currently says the file runs the replay "in parallel with the shipping bases", which stops being true.  Rejected: merging the survivors into `test_asset_fold.py` and deleting the file (graded-against-an-independent-engine and graded-on-a-hand-computed-oracle are different KINDS of evidence and Section 7.2's independence rule turns on the distinction), and deleting the file whole (loses the only place the replay is graded against a second implementation).  **A fourth suite needed no ruling because the trace found its guard already blind:** `test_balance_resolver.py`'s CRIT-01 pair C5-1 / C5-2 asserts that a producer's value does not depend on the caller's eager-loading, and E-25 ALSO softened `_entry_aware_amount` to read `getattr(txn, "entries", ())` through the descriptor -- so stripping the loader's `selectinload` leaves both paths at `$160.00` and costs only queries.  The property is proved directly at the rule's own home by `test_cash_amounts.py::TestTheEntriesRelationshipIsNotASeam`; the pair deletes with its subject and opens nothing.  It is the B-17 class, found the same way | X-g4b |

### Answered (developer ruling, 2026-07-27: X-o's two forks, both as recommended)

**The trace ran first and no code was written for it.** It measured the defect on the developer's
own two loans by rewriting the projection dicts in memory (no DB write) and re-running both
producers, which is how the SECOND producer in R-AV was found: the fix that closes B-16 leaves a
19-year contradiction standing on the same page, and the entry's own scope could not have shown
that.

| # | ruling | consumed by |
|---|---|---|
| **R-AV** (answered 2026-07-27, as recommended) | **X-o ships the predicate alone; the second debt-free producer gets its OWN step, X-q, and both are built now.**  The trace found that `/savings` derives the debt-free date twice from one `account_data` -- `_metrics._compute_debt_summary` for the cockpit caption (membership: the loan's BALANCE) and `_horizon._resolve_horizon_domain` for the chart flag (membership: the debt-line predicate) -- and that fixing B-16's predicate does not merge them: they still disagree on a NOT-YET-ORIGINATED loan, measured at **`2029-02-22` caption against `2048-12-01` chart** on the developer's own mortgage rewritten into that state, and at **`2028-03-01` against `2056-06-01`** on an independent fixture built by X-o's adversarial review.  Two commits, not one: X-o moves no figure on any data and X-q1 moves a rendered caption, and mixing them makes a caption slip read as a predicate slip -- the b1/b2/b3 line this arc has now split on five times.  **Rejected: recording X-q for a later session** (rule 7 -- a measured contradiction on a rendered screen is sequenced, never deferred, and the trace that measured it is the cheapest context it will ever have) **and one combined commit** (above) | X-o, X-q |
| **R-AW** (answered 2026-07-27, as recommended) | **The per-account projection dict carries the seam's `LoanFigures`, and the six flat copies go -- in its OWN commit, AFTER the defect is fixed.**  B-16's root is not the predicate at the call site, it is that `_project_one_account` re-flattens a value object field by field and dropped the one field the debt-line question needed.  A dropped copy cannot fail loudly: the consumer simply asks the nearest question the dict can answer.  The ruling that closes it already exists ONE LAYER DOWN -- `_types._LoanAccountResult` composes `LoanFigures` precisely because "the copy silently went stale the moment the seam grew `is_originated`" (`_types.py:107-111`; `_loan_figures.py:130-131` states the same rule from the seam's side) -- so this applies a decided rule rather than taking a new one.  Ordering: X-o first (one line, the live defect, depends on nothing), then the bundle, so the fix does not wait behind a refactor that touches two Jinja templates where a missed attribute renders EMPTY instead of raising.  **Rejected: adding `is_retired` and stopping** (the next field the seam grows is dropped the same way, silently) **and doing the bundle first** (the defect waits on it) | X-o, X-r |

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
| **R-W** (N-76; answered 2026-07-26) | **The grid renders the MODELED balance, with a "Growth" row that is the accrual producer's own answer -- ruling R-K's identity then holds for all FIVE kinds.**  A fork the plan did not have: the trace measured that the grid and `/savings` already answer ONE modeled account two ways.  `_grid.grid_balance_view` layers an accrual for INTEREST only (the gate is `_grid.py:435`, `accrual_params(account) is None` on its CASH arm since X-g3a `320a4641`, which both MOVED it and INVERTED it; this row's earlier citations `:271-277` and `:345-362` / `:346` drifted at X-g2b and X-g3a respectively and are corrected here per Section 7.6), so an INVESTMENT or APPRECIATING account's grid balance is its kind-blind cash-flow balance while `balance_map` returns the modeled one.  Measured at the last projected period on `shekel_f3_final`: Empower 401(k) grid `$31,070.06` vs `/savings` `$48,712.19` (**`$17,642.13`**), Roth `$5,916.95`, Trad IRA `$2,526.68`; on `shekel` the Property is `$21,675.99` apart.  The grid's interest column is `None` for every one of them, so nothing on screen explains the gap -- and both surfaces are reachable for these kinds (`account_resolver.is_cash_flow_account:41` admits every non-amortizing kind).  INTEREST accounts are byte-identical on both surfaces, which is the proof the unification WORKS: the Interest row already is the general shape.  Under one replay a typed grid row IS an event in the same stream, so the objection `_grid.py` records today ("a typed grid row would not move their modeled balance") stops being true and the identity becomes a property of the construction for every kind.  **Corrected 2026-07-27, after X-g2b measured it:** the identity is `net + reconciliation + accrual + CONTRIBUTION`, not `+ accrual` alone -- a modelled asset has two modelled tiers, and on the real Empower 401(k) the contribution is the larger of them (`$9,624.27` against `$8,152.58` over the horizon).  X-g3's entry carries what that opens.  Rejected: keeping the cash-flow basis with a caption (leaves `$17,642.13` of visible contradiction, the shape ruling R-K refused to ship for the cash subtotals -- and Section 8: a label is weaker than a predicate, which is already not a safety), and refusing modeled kinds on cash-flow surfaces the way ruling D4 refuses a loan (removes the contradiction by removing screens the developer uses; a loan is refused because its balance is not a transaction sum, while a modeled asset's IS one plus a rate -- the same shape an HYSA already renders correctly). | X-g |

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
runs AFTER it** (ruling R-V, 2026-07-26). It sat inside the `X-c2c` decomposition block, which the
2026-07-27 archive extraction took whole (its three shipped siblings are in
`archive/cash_arc_as_built_2026-07-27.md` Section 2.1), so it is now a top-level entry that has kept
its decomposed ID -- append-only, and never renumbered for readability. **Ruling R-AR (2026-07-27)
then folded its CONTENT into `X-g4b`**, so the exception now costs no commit of its own: the entry
stays where it is, its content is what X-g4b carries, and its box ticks with X-g4b's hash. The live
order from here is
**X-o -> X-q -> X-r -> X-h -> X-i -> X-j -> X-k -> X-l -> X-m -> X-n -> X-d -> X-e -> X-f -> X-p ->
E2** (X-g4a and X-g4b, which used to open this line, have SHIPPED) (X-h .. X-k added 2026-07-27 by ruling R-AO, X-l .. X-p the same day by R-AQ, and
X-q / X-r the same day by R-AV / R-AW out of X-o's trace; they
appear on this page in EXECUTION order, which is why X-o precedes X-h and X-p follows X-f -- the
alphabet has never been this page's order, exactly as X-g running before X-d already showed);
both steps say so at their own entries, and the "Where the arc stands" section at the top of this
document is the authority if they ever disagree. **The IDs are append-only** and are NOT renumbered for
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

**X-a through X-c2c3 SHIPPED and their as-built entries are in
`archive/cash_arc_as_built_2026-07-27.md` Section 2.1** -- the cash walk leaf, the fold, ruling
R-M's write-door guard, the per-period view, ruling R-L's clock, the three-commit cash cutover, the
producer deletion, the reservation window's deletion, the test migration, and the CANCELLED X-c2c3.
Read that file for what each measured and which findings it closed. **X-c2c4 did NOT ship and stays
here**, re-parented to the top level now that its `X-c2c` decomposition header is archived; its
preconditions cite entries in that file.

- [x] **X-c2c4** `refactor(balance): the last cash producer deletes` -- **SHIPPED dev 2026-07-27
  INSIDE X-g4b (`17c57cde`)**, ruling R-AR. Pure deletion, no
  behaviour. **RUNS AFTER X-g, not after X-c2c2** (ruling R-V): its precondition is that
  `_cash_engine.balances_for` has no caller left, and X-g's replay is what takes the last two
  -- exactly as the cancelled X-c2c3's window would have. Do not attempt it earlier; the
  C3b3 prove-the-successor-first precedent is the whole reason this is a separate commit.

  **X-g4's trace (2026-07-27) proposes this entry ship INSIDE X-g4b rather than as a commit of its
  own, and that is fork 1 at the X-g4 entry -- unruled here.** Its precondition is met in every
  sense but the physical removal, and the three modules it and X-g4 name are one closed import
  cluster, so a separate commit's whole content would be "delete a module nobody imports and keep
  its test file alive one more commit". The ID is append-only either way: it stays cited and is
  ticked with X-g4b's hash if the fork rules that way. Its content below is UNCHANGED and is what
  X-g4b would carry; three of its measurements were re-verified by that trace and one was wrong
  (the `calculate_balances` count is 73 calls in 10 files, not 79 -- corrections 3 and 4 there).

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
- [x] **X-g** `feat(balance): a modeled asset is an event stream` -- **SHIPPED dev 2026-07-26 and
  -27 across its four decomposed STEPS, which are ten commits** (the archive's Section 2.2 lists them
  all; the last of each step is X-g1 `17ead4c5`, X-g2 `560b3339`, X-g3 `920366a9`, X-g4 `2ee817b4` +
  `17c57cde`). The header ticks with the last of them, the convention rule 6 fixed for a
  DECOMPOSED step. Its leaves are in the archive, not here, which is why only X-g4's are checkboxes
  on this page. **the from-scratch design, and
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

  **X-g1 through X-g3b SHIPPED and their as-built entries are in
  `archive/cash_arc_as_built_2026-07-27.md` Section 2.2** -- the modelled replay (additive), the
  X-g2a/X-g2b shape-then-cutover pair with its two prerequisite commits, and the X-g3a/X-g3b-0/X-g3b
  grid trio. The design they implement is Section 3.2 above, which stays live because X-g4 is held to
  it. **X-g4 did NOT ship and stays below.**

  * [x] **X-g4** -- the deletion. **SHIPPED** as X-g4a + X-g4b. `_merge_balance_sources`, `_reverse_project_periods`,
    `_forward_project_periods`, `_forward_project_rows`, `_assemble_investment_projection_inputs`,
    `investment_base_balance_map`, `get_anchor_period_index`, and `_interest._layer_interest`'s
    second pass. `_cash_engine.balances_for` loses its last two callers here, which is X-c2c4's
    precondition. **NOT on this list, and the review caught it:**
    `investment_projection._average_transfer_contribution` and
    `projection_inputs.build_investment_projection_inputs` SURVIVE -- the what-if surfaces keep them
    (ruling R-R consequence (b)); only the balance path stops reading them.

    **Re-measured 2026-07-27 (ruling R-AO's pass), and the step is LARGER and SIMPLER than this list
    reads.** An AST scan over `app/` finds ZERO callers of `build_investment_balance_map` or
    `build_appreciation_balance_map` outside `_investment.py` itself, and `_cash_engine.balances_for`'s
    only two callers (`_investment.py:124`, `:473`) sit inside those already-dead functions. So the
    whole of `_investment` is production-dead except for what its tests grade, and **X-g4 and X-c2c4
    are one deletion front**: X-c2c4's stated precondition ("`balances_for` has no caller left") is
    already met in every sense but the physical removal. Finding **N-43** is corrected to match --
    it reads OPEN and the defect has not been reachable since `560b3339`.

    **It also carries finding N-95's doc sweep, which is part of the deletion and not a follow-up**
    (the rule X-c2c4's entry already states). `app/services/balance_at/__init__.py:33` and `:36-37`
    still describe an INVESTMENT as reverse-projected and an APPRECIATING asset as flat-carrying its
    pre-anchor periods -- the contract rulings R-S and R-Y retired at X-g2b -- and
    `savings_dashboard_service/_net_worth.py:294` carries the same pair. The fix is NOT to correct
    the sentences: **delete the per-kind narrative**, because the code no longer has per-kind paths.
    Those five bullets are residue of the dispatch ladder X-g2b deleted, and `_kernel.py:10-22`
    already shows what replaces them -- one paragraph, one replay, tiers that exist only if the
    account's own parameters put them there.

    **THE TRACE IS DONE (2026-07-27), NO code was written for it, and ALL FOUR of its forks are
    RULED** (developer ruling R-AR / R-AS / R-AT / R-AU, Section 4, all as recommended); the three
    remaining forks below are trace DECISIONS taken under them, which move no figure. It ran an AST
    scan over `app/` + `tests/` + `scripts/` + `tools/` (never a regex, Section 8) and read every
    consumer it found. What it CONFIRMED and what it CORRECTED:

    **CONFIRMED, and the deletion is a CLOSED cluster rather than a list of functions.** The three
    modules import only each other -- `_investment` -> `_cash_engine` -> `_calculator` -- and
    **nothing in `app/` imports any of the three**. So this is not "delete eight functions and see
    what breaks": it is the removal of a subgraph with zero entry points, and every remaining
    reference to it is a TEST or a docstring. Line counts, re-measured on this date:
    `_investment.py` 650, `_cash_engine.py` 201, `_calculator.py` 137.

    **CORRECTION 1 -- the AST evidence has a NAME TRAP, and following the name would delete a live
    seam entry.** `investment_growth_since_anchor` is defined TWICE: in `_investment` (dead) and in
    `_kind_correct.py:260` (live -- re-exported at `__init__.py:218` and called at
    `investment_dashboard_service/_orchestrator.py:64`, which reaches
    `_asset_fold.asset_growth_at`). A name-keyed scan reports a live production caller for the dead
    module's function. **The deletion follows the MODULE, never the name**; every name it removes is
    re-scanned for a surviving twin before the line goes.

    **CORRECTION 2 -- two more things fall with the cluster, and neither entry lists them.**
    (a) `_interest.layer_account_interest` and `_interest._layer_interest`: `_asset_fold.py:392` is
    the module's ONLY `app/` consumer and it reaches exactly one name, `accrual_params`. So
    `_interest.py` (272 lines) is 235 lines of dead producer wrapping a 37-line predicate -- fork 2.
    (b) `cash_ledger.load_balance_transactions` loses its last `app/` caller (`_cash_engine.py:174`)
    and its last consumer of ANY kind is one test line -- fork 3.

    **CORRECTION 3 -- the `calculate_balances` count.** X-c2c4's entry says "79 call sites across 10
    files". The AST call-scan finds **73 calls in 10 files** (72 in `tests/`, 1 in `app/`) -- the
    FILE list is exactly right and the 79 was a textual count. The other 8 textual mentions are the
    definition and the two static-guard string arms, which is the archive's correction (c) already
    naming them; recorded so the sign-off checks the right number.

    **CORRECTION 4 -- the doc sweep is 22 `app/` sites, not "~30", and its "contract made FALSE"
    list is wrong in one place and short in four.** `savings_dashboard_service/_net_worth.py:118` is
    named by X-c2c4 and was ALREADY corrected at X-g2b (it now reads "Since plan step X-g2b every
    kind is a TOTAL fold"); what is still false there is `:294-296`, which N-95 already owns. NOT on
    either list and each stating a contract the deletion makes false: `cash_ledger/_amounts.py:194`
    ("the canonical producer `balance_at._cash_engine.balances_for` always eager-loads" -- the
    rationale for the surviving lazy-load `getattr`), `cash_ledger/_flows.py:12-16`
    (`sum_projected` is "the shared engine BOTH cash bases reduce through" -- after this there is
    ONE), `interest_projection.py:117` (cites `balance_at._interest._layer_interest` as the
    per-PERIOD reader), and `_asset_fold.py:35` / `:472` (cite `_investment._merge_balance_sources`
    and `_layer_interest`'s period-END accrual base -- the second is the WHY for the daily replay
    having no convention to pick, so it is restated, never just cut).

    **CORRECTION 5 -- the coverage question is bigger than the one test X-c2c4 names, and the answer
    is that only ONE hole is real.** Read every dying suite against its successor:
    * `test_balance_calculator.py` (2,326 lines) -- its per-row rules are already pinned at their own
      homes by X-c2c2 (`test_cash_amounts.py` for the entry-aware reservation, the cleared flag and
      the live override; `test_cash_flows.py` for the status gate), and its degenerate shapes by
      `test_cash_fold.py`'s `TestTotality`. `test_52_period_penny_accuracy` IS the real hole and
      ports -- fork 6. `TestBalanceCalcIgnoresDueDate` deletes without a successor DELIBERATELY: it
      pins "the balance ignores `due_date`", a rule ruling R-G REVERSED.
    * `test_balance_resolver.py` (815 lines) -- **C5-1 / C5-2, the CRIT-01 preload-independence
      pair, are already controls that cannot fire**, and that is why deleting them opens nothing.
      E-25 also softened `_entry_aware_amount` to read `getattr(txn, "entries", ())` through the
      descriptor, so removing the loader's `selectinload` would leave both paths returning
      `$160.00` and cost only queries. The property is proved directly, at the rule's own home, by
      `test_cash_amounts.py::TestTheEntriesRelationshipIsNotASeam`. Its C5-8 static guard is a
      different matter -- fork 5.
    * `test_asset_fold_parallel.py` (456 lines) -- **neither entry mentions it and it is this step's
      largest test decision.** Three of its five classes grade the replay against the dying
      incumbent; two grade it against `growth_engine` (which ruling R-U KEEPS) or against itself --
      fork 4.
    * `test_hostile_qa.py`'s four, `test_audit_fixes.py`'s three, `test_workflows.py`'s four,
      `test_loan_payment_pipeline.py`'s two, `test_accounts.py`'s one, `test_transfers.py`'s two and
      `test_carry_forward_service.py`'s two all use the calculator as a convenient REFERENCE for
      "did the shadow move the balance". They re-point onto the seam, which is strictly stronger --
      they then assert what the app renders. Read every site: a left-behind `balances, _ =` unpacks
      dict KEYS instead of raising.

    **THE THREE TRACE DECISIONS, taken under R-AR..R-AU and recorded so the build is not
    re-deciding them.** None moves a figure.

    * **`cash_ledger.load_balance_transactions` DELETES** -- with its `__init__` re-export and its
      W9909 non-producer ruling (`balance_seam.py:261`), whose reverse-staleness meta-test
      (`tools/pylint/tests/test_shekel_checkers.py:940`) fails the build if the ruling outlives the
      name. Its one surviving consumer, `test_cash_period_view.py:154`'s non-vacuity reference,
      re-expresses on `planned_cash_rows`: the figure is unchanged at `$75.00` because
      `sum_projected` re-applies `is_projected` over whatever it is handed, and the re-pointed test
      is STRICTER -- it then references the loader the producer under test actually uses instead of
      a second loader that happens to agree.
    * **C5-8 (`test_seam_removed`) MOVES to `test_cash_amounts.py` and WIDENS.** It is the static
      guard asserting the CRIT-01 silent-degrade pattern is absent from source, and it dies with its
      host file. It moves to the home of the rule it guards, drops the two now-deleted file paths,
      and scans `cash_ledger` PLUS `balance_at` (both `rglob`'d) rather than `cash_ledger` alone --
      its own docstring already argues the widening ("a scan keyed on a file NAME would have gone
      quiet at exactly the moment the code it guards moved", finding N-28) and every balance
      producer now lives in `balance_at`. Verified free on this date: neither forbidden pattern
      appears in either package. Note it fails LOUD if left alone (`read_text` on a deleted path
      raises), so this is a decision and not a trap.
    * **The ported oracle lives in `test_cash_fold.py`**, under its own class. It is a
      hand-computed oracle, not a parallel run, and `test_cash_fold_parallel.py`'s subject (the
      three seam entries agreeing with the fold on every day) is a different question.

  * [x] **X-g4a** `test(balance): the drift oracle walks 52 periods of the fold` -- **SHIPPED dev
    2026-07-27 (`2ee817b4`)**, ruling R-AT's shape, ADDITIVE: `git diff app/` empty, so the harness
    is byte-identical by construction and the incumbent stayed alive and green beside it. Full
    suite 7666 (baseline 7664).

    **What it took beyond the ruling, recorded because X-g4b leans on it.** Two independent
    adversarial reviews found the first draft's controls were partly PROSE: one test was an
    arithmetic tautology (`after != before + net` after asserting `after == before - 10156.34`),
    a second was dominated by its neighbour's hand-written literal, the ruling R-B comment credited
    discriminating power to a detail that carried none (the shape had no settle sharing an
    assertion's civil day at all), and the claimed firing-control count was WRONG -- a false
    statement about coverage, which this arc rates worse than the gap it describes. Both dead tests
    were DELETED rather than reworded, and the shape gained what the reviews measured missing: the
    R-B straddle, an ACTUAL-over-estimate settled row, `attribution_date`'s two clamp arms, and
    period 16 holding the SETTLED and PLANNED tiers in ONE column -- the shape every user's current
    period has, which the draft had nowhere (Section 7.4). **EIGHT one-line production mutations now
    fail it**, listed in the class docstring with the THREE classes that do not and why, so the
    boundary is stated rather than discovered. Ruling **R-I is NOT gradeable at this grain** (no
    period end precedes the opening) and the docstring says so instead of claiming it.

  * [x] **X-g4b** `refactor(balance): the modelled and cash producers delete` -- **SHIPPED dev
    2026-07-27 (`17c57cde`)**, ruling R-AR's ONE deletion, carrying X-c2c4's whole content.
    **1,347 production lines and 4,937 test lines removed; `verify_balance_baseline.py`
    BYTE-IDENTICAL on BOTH databases, run HEAD-vs-post in a `git worktree`.** Loan gate unmoved
    (Mortgage `$177,277.97`, Van Loan `$15,663.59`). Full suite 7588; the **-79** against X-g4a's
    7666 reconciles exactly against the deleted test counts (47 + 10 + 15 + 4 + 5, less the two
    added). `pylint app/` 10.00/10 with the full `--fail-on` set; the 146 checker tests green,
    which is what proves the W9909 ruling deleted WITH its name rather than going stale.

    **Two things the deletion FOUND, neither in its entry.** (a) `interest_projection.`
    `calculate_interest` -- a two-line `round_money` wrapper -- was orphaned by removing the
    per-period interest layer, its only caller. It deletes here rather than being inherited as a
    public function read by nothing but its own tests; its 24 unit tests re-point onto
    `accrued_interest`, every hand-computed figure unchanged, and the MED-05 / PA-06 audit
    rationale that lived on its docstring MOVED onto the function that has always owned the
    day-count rule (losing it is the failure mode a deletion makes easy). (b) `create_hysa_account`
    hardcoded DAILY compounding, so **no test anywhere ran a MONTHLY or QUARTERLY account through a
    balance PRODUCER** -- and the developer's real Money Market compounds MONTHLY. The gap
    pre-dates this step (the deleted tests graded the unwired layer, never the replay) but became
    total with it, so the helper is parameterised and a hand-computed MONTHLY test added. Its
    firing control: hardcoding DAILY in the replay's rate resolver reads `$12.63` against the
    ruled `$12.39`.

    **Three claims the adversarial review proved FALSE, all written by this step and all fixed
    rather than reworded** -- the same class X-g4a's reviews caught, which is why the review ran
    twice. (i) A new sentence in `interest_projection.py` named surviving consumers for
    `calculate_interest` that do not exist, papering over (a) above. (ii) The ported drift oracle's
    docstring credited a TELESCOPE arm that cannot fail: measured, all three mutations
    (per-day rounding, a stale accrual base, a halved rate) fail through the MONOTONICITY arm and
    none through the telescope, which holds by construction. The docstring now says which arm
    discriminates, that the telescope is a consistency check and not evidence, and what
    monotonicity does not catch. (iii) The `test_loan_payment_pipeline` step deleted here was
    justified by ruling R-J, which forbids the kind-BLIND view and not the seam. The review was
    right that a re-point existed in principle and WRONG that it holds: measured, the pipeline's
    payment is still PROJECTED at that point, so the loan reads `$250,000.00` on both sides and
    there is no property to grade. It is deleted with the measurement recorded, not the guess.

    **What it removed.** Production: `_investment.py` (650), `_cash_engine.py` (201),
    `_calculator.py` (137), `_interest.py` (272, ruling R-AS -- its one surviving predicate folded
    into `_asset_fold._modelled_return`, deleting a redundant second `classify_account`),
    `cash_ledger.load_balance_transactions` with its re-export and W9909 ruling, and
    `interest_projection.calculate_interest`. Tests: `test_balance_calculator.py` and
    `test_balance_resolver.py` whole (C5-8 relocated to `test_cash_amounts.py` FIRST, and WIDENED
    from the `cash_ledger` package to `cash_ledger` + `balance_at` -- the review proved the
    widening fires), `test_interest_accrual.py` whole (its long-horizon no-drift claim PORTED),
    `test_asset_fold_parallel.py`'s three dead classes (R-AU) and `test_hostile_qa`'s section 6,
    the two vacuous static-guard arms, and all 73 `calculate_balances` calls -- re-pointed onto the
    seam or deleted with their subject, every site read. Four re-points got STRONGER: the fold
    loads the account's own rows instead of taking a hand-picked list, and two conditional `if`
    guards that could never fail became unconditional asserts. Docs: 26 `app/` sites.

  Do NOT collapse X-g2 and X-g3: the whole point of the b1/b2/b3 split was that mixing render
  plumbing with a money-moving cutover makes a plumbing slip read exactly like a fold slip. Do NOT
  split R-R into a prerequisite step (the entry previously flagged that possibility): the trace
  measured both contribution feeds EMPTY on real data, so there is no live figure to seat ahead of
  the cutover -- unlike X-c0, which was refusing a write door that was genuinely open.

  **Prerequisite for X-e.** Once this ships, no balance path reads `Account.current_anchor_*`, which
  is the condition X-e's question ("a reconciled cache or it is nothing") needs in order to be
  answerable at all.

- [ ] **X-o** `fix(savings): the debt-line question uses the debt-line predicate` -- closes
  **B-16**. **Sequenced FIRST of the new steps because it is a LIVE defect on a rendered screen and
  the fix is one predicate**; it depends on nothing and nothing depends on it.

  `LoanFigures` states the contract at `_loan_figures.py:176-177` -- "Use `is_retired` to decide
  whether a loan has a debt line; use this to decide whether to CONGRATULATE the user" -- and
  `savings_dashboard_service/_horizon.py:144` (which loans are ACTIVE for the debt-free date) and
  `:563` (which payoff milestones to plot) both ask the debt-line question with `is_paid_off`
  (re-verified 2026-07-27). `is_paid_off` is strictly narrower: it also requires at least one
  CONFIRMED payment, a badging guard against a degenerate `$0` opening anchor. So a loan retired by
  a lump-sum balance TRUE-UP with no payment rows reads `is_retired=True, is_paid_off=False`, stays
  in the horizon's ACTIVE set, and -- being retired -- has `payoff_date is None`, which fires the
  "an ACTIVE loan with no payoff never clears" branch: **no debt-free date at all, and the user told
  they are not loan-free, on a loan that owes nothing.**

  **The same collapse is what produced the `$197,049.32` equity-chart defect** that
  `_loan_figures.py:178-184` cites in its own docstring -- a mortgage paid off by a lump sum
  recorded as a true-up, whose empty schedule let the back-projection clip admit its whole 360-row
  contractual walk. This is that defect's twin on the debt-free date, and the contract that names
  the right predicate was written BY that incident.

  **Its control is the fixture the contract describes:** a retired loan with a true-up and ZERO
  confirmed payments. It must fail against `is_paid_off` and pass against `is_retired`, shown both
  ways. Read both call sites before changing either -- `:563` plots milestones and `:144` decides
  membership, and they can need different treatment even though they take the same predicate today.

  **THE TRACE IS DONE (2026-07-27), no code was written for it, and it MEASURED the defect on the
  developer's own two loans rather than describing it.** The projection dicts were rewritten in
  memory (no DB write) into the reachable state -- the Van Loan retired by a lump-sum true-up with no
  settled payment rows -- and both producers re-run over them:

  | state | horizon domain | Debt-free flag | cockpit caption, same page |
  |---|---|---|---|
  | as it stands, both loans active | `2049-12-31` | `2048-12-01` | Debt-free Dec 2048 |
  | Van retired, `is_paid_off=False` | **`2036-12-31`** | **none, and no payoff flags** | Debt-free Dec 2048 |
  | same loan, a payment row exists | `2049-12-31` | `2048-12-01` | Debt-free Dec 2048 |

  So the presence of a payment ROW -- a badging detail -- swings the chart's x-axis by **13 years**
  and deletes every STRUCTURAL flag on it (the net-worth crossing flags are built from the trajectory
  and survive, which is why this entry does not say "every flag"). **On today's real data neither
  loan is retired, so the fix moves nothing**; the fixture is the proof and the harness is not
  (`verify_balance_baseline.py` reads the seam, not this producer).

  **What the trace CORRECTED in this entry.** (i) `:563` is NOT a second defect: a retired loan's
  `payoff_date` is `None` by construction. On the ORIGINATED arm -- the only arm `is_retired` can be
  true on -- `_kernel._projection_seed` returns the SAME
  `fold_from_walk(ctx.loan_walk(account), [ctx.as_of])` expression `_is_retired` tests, and
  `plan_payoff_date` returns `None` on `seed <= 0`, so the milestone loop's own `payoff is not
  None` test already excluded it. (The other arm matters for X-q and is stated there: a
  NOT-yet-originated loan seeds from its opening anchor and therefore HAS a payoff date.) That half is behaviour-neutral and can carry no firing control,
  which the commit says in its own docstrings rather than leaving to be discovered. (ii) The claim
  "the user told they are not loan-free" is HALF true and the half matters: `is_loan_free` is a
  producer output `_serialize_horizon` (`routes/savings.py:113-132`) does not emit and no template
  reads, so nothing captions it -- **finding N-100**. What the user actually sees is the missing flag
  and the truncated axis.

  **What the trace FOUND, and neither is in this entry's scope** (both are recorded with owners, and
  the second was found by the step's adversarial review): the debt-free date has a SECOND producer
  whose membership rule is different again (**N-98**, plan step X-q), and the "Debt-free" flag
  ignores revolving debt entirely (**N-99**, plan step X-q).

  **The build decision, taken under R-AV:** both call sites select through ONE new
  `_horizon._debt_line_loans` helper rather than each testing the predicate. Two call sites asking
  one question is how they came to disagree in the first place; the shared selection makes "the axis
  a payoff sizes and the flags drawn on it are the same set" a property of the construction.

- [ ] **X-q** `fix(savings): one debt-free date, one derivation` -- closes **N-98**, **N-99** and
  **N-100** (ruling R-AV, 2026-07-27). **Opened BY X-o's trace, and it is the same disease one
  question over:** `/savings` derives "when is this user debt-free" TWICE, from the same
  `account_data`, with two different membership rules, and renders both answers on ONE page.

  * `_metrics._compute_debt_summary` -> the cockpit's `Debt-free <month>` caption
    (`_cockpit.html:259`) and the dashboard debt track (`_tracks.html:91`). Membership:
    `_loan_ad_current_principal` (`_metrics.py:282`), which skips a loan whose `current_balance` is
    `<= 0`.
  * `_horizon._resolve_horizon_domain` -> the Horizon chart's `Debt-free` flag and its x-axis.
    Membership: `_debt_line_loans` (`is_retired`, as of X-o).

  **They differ on the NOT-YET-ORIGINATED loan, and the caption is the wrong one.** A mortgage that
  has not closed yet owes `$0.00` today, so the balance rule drops it from the debt-free date
  entirely while the debt-line rule keeps it -- its whole 30-year line is ahead of it. Measured
  twice: rewriting the developer's own Mortgage into that state gives caption **`2029-02-22`**
  against chart **`2048-12-01`** (19 years); an independent two-loan fixture built by X-o's
  adversarial review gives caption **`2028-03-01`** against chart **`2056-06-01`** (28 years). This
  is B-16's third call site -- the finding's own row cites only the two in `_horizon` -- and the
  reason X-o alone does not close the question.

  **Rejected before it is proposed: aligning the two rules.** Two producers kept in step by a rule a
  reader must remember is the shape this arc exists to delete (Section 8). ONE derivation answers
  the question and both surfaces read it: a three-state value -- a DATE, `loan-free` (no debt line at
  all), or `never clears` (a debt line with no payoff at its current payment) -- which is exactly the
  distinction both producers already make separately and neither names.

  **The money aggregates keep their own membership and that is not an inconsistency**: `total_debt`,
  `total_monthly_payments` and the weighted rate answer "what do you owe TODAY", where a loan that
  has not closed contributes nothing and its payment is not yet being made. Two questions, two sets,
  one place each -- what X-q refuses is one question answered twice.

  **A FORK the trace will not decide by itself (N-99).** The debt-free date is derived over
  `loan_params`-carrying accounts only, so a revolving Credit Card -- which the seam holds FLAT
  because it has no forward model, and which the Horizon's own liability BAND sums -- cannot affect
  it. A user carrying a card balance is flagged "Debt-free" on the date their last LOAN clears.
  Including revolving debt means nobody carrying a card ever gets a date (a flat balance never
  reaches zero), which is honest and possibly useless; excluding it means the caption means "loan
  free" and should probably say so. Options, costs and a recommendation go to the developer BEFORE
  any code, on the X-g / X-g4 precedent.

  **Decomposed on the arc's own line, because one half moves a rendered figure and the other cannot:**

  * [ ] **X-q1 THE DERIVATION** -- one debt-free outlook, both surfaces read it. MOVES the caption in
    the not-yet-originated shape, so it carries its own fixture, its own firing control (the old
    balance rule, substituted, must produce the wrong date) and its own sign-off. It also decides
    what a payoff date in the PAST means: `plan_payoff_date` returns a DUE date and an
    overdue-but-projected installment that clears the loan folds at a past one, which `_metrics`
    counts in its `max()` and `_horizon` filters out -- one question, two rules, again.
  * [ ] **X-q2 THE SURFACE** -- the horizon publishes what is read (**N-100**). `is_loan_free` and
    `horizon_end` are producer outputs `_serialize_horizon` does not emit and no template reads, and
    `savings_dashboard_service.compute_net_worth_horizon` is a PUBLIC export with zero `app/`
    callers, alive on 10 test call sites in one file. Same class as N-85 / N-96 in the seam. No behaviour: the
    proof is that the rendered payload is byte-identical.

- [ ] **X-r** `refactor(savings): the projection dict carries the seam's figures, not six copies` --
  closes **N-101** (ruling R-AW, 2026-07-27). **NO behaviour change**, and it is B-16's ROOT rather
  than another of its symptoms.

  `_projections._project_one_account` flattens `LoanFigures` into the per-account dict field by
  field -- `is_paid_off`, `is_originated`, `monthly_payment`, `current_rate`, `payoff_date`, and
  since X-o `is_retired`. **The field it did not copy was the one the debt-line question needed**,
  and nothing could have failed: a consumer cannot miss a key that was never there, it just asks the
  nearest question the dict can answer. That is the failure mode
  `_types._LoanAccountResult` was written to close ONE LAYER DOWN -- its docstring says the copy
  "silently went stale the moment the seam grew `is_originated`" and that "a bundle that must be
  hand-synchronised with the seam it mirrors is the seam's fence with a hole in it" (the same
  sentence `_loan_figures.py:130-131` states from the seam's side) -- so the ruling already exists
  and this step applies it where the copy actually happens.

  The dict carries `ad["loan_figures"]` (the seam's own value) and the six flat keys go. Consumers to
  re-point, all read at the trace: `_metrics.py` (`:282`, `:362`, `:371`, `:427`, `:428`, `:452`),
  `_horizon.py` (`_debt_line_loans`, the milestone loop), `savings/_cockpit.html` (`:157`, `:182`,
  `:188`, `:189`) and `savings/dashboard.html` (`:223`), plus **14 test sites in two files at
  `d1c218c2`** (`test_savings_dashboard_service.py` and `test_balance_at.py`) and the **two more X-o
  adds** in its firing controls -- 16 to read, never sed. **Jinja is
  where the risk is**, not Python: an undefined attribute renders as empty rather than raising, so
  every template site is checked by a rendered assertion and not by the page merely loading.

- [ ] **X-h** `test(balance): four controls that cannot fail are not controls` -- **NO production
  change, so the baseline provably cannot move.** Closes **B-17**, **N-45**, **N-65**, **N-94**
  (ruling R-AO). One root: Section 7.3 requires every guard carry a negative control shown to fire,
  and these four cannot fire. B-17 asserts `_metrics` behaviour on an `_ad` dict the TEST builds, so
  changing the production builder at `_projections.py:241-243` leaves it green; **N-94**'s per-kind
  injection lock compares every surface against the fixture's ASSERTED value, which no surface has
  returned since plan step X-g2b gave the anchor period its own accrual (ruling R-Y), so it raises
  with the patch and without it; **N-45** passes only because a sibling test class registers a
  synthetic module under a real dotted name and warms astroid's cache; **N-65** is the third
  instance of "the suite's clock is frozen and the DATABASE's is not" (after the loan walk's stamp
  and `create_account`'s opening).

  **Sequenced FIRST of the four new steps, on the X-g2b-0 precedent: the instrument before the
  measurement.** These four are what currently grade the steps after them, and a control that
  cannot fail reads as a green sign-off. There is no cheaper time to fix them than before the work
  they measure.

  **Each fix carries a control for the control, and that is the only evidence that will do here:**
  the repaired test must FAIL against the production defect it claims to catch and PASS without it,
  shown both ways. Fixing a blind control without demonstrating it now bites reproduces the defect
  one level up.

  **N-65's structural half is the one that can grow and it splits if it does.** Patching each
  fixture is what the row already records as done; the structural fix is for the test clock to reach
  the DB default (`db.func.now()` on `paid_at` and `AccountAnchorHistory.created_at`), which is
  shared suite infrastructure and not scoped to this ledger. If it proves larger than the other
  three combined it ships on its own rather than dragging them; the other three do not depend on it.

  **A FIFTH commit, added by ruling R-AQ: the LEDGER-INTEGRITY gate, and it is the reason the other
  four cannot recur.** Section 9's rules say tick the box and re-point the row, and nothing enforces
  them -- so four rows named a resolver that had SHIPPED and nothing noticed for weeks (N-14, N-33,
  N-40, N-56, found by reading the code and not the document). That is Section 8's own lesson
  turned on this file: a safety that is a predicate is not a safety, and this one was not even a
  predicate. **It is prose.** The gate is a test that parses THIS document and fails when:

  * a Section 6 owner names a Section 5 step whose checkbox is **TICKED** -- the exact class above,
    and it would have failed the day X-c shipped;
  * an owner names a step ID that does not exist in Section 5;
  * an owner is outside the vocabulary Section 9 rule 6 fixes -- a live step ID, `operator`, or
    `developer-decision`. **There is no value meaning "someone will get to it."**

  **Three shapes the gate's PARSER has to survive, measured on the ledger as it stands (X-o's
  review, 2026-07-27), because each of them would make a naive implementation fail on correct rows
  and read as a broken gate rather than a broken ledger.** (i) An owner cell is often an ANNOTATED
  id -- `X-i1 (the redundancy)`, `X-g4 (the deletion; not live since 560b3339)`, `X-e (widened
  2026-07-27)` -- so it parses the IDs out of the cell, never the cell as an id. (ii) A cell can name
  TWO owners for two halves of one row (`X-j (display) / X-e (cache)`), and BOTH must be live.
  (iii) A step ID appears in prose all over Section 5 as a historical citation -- the X-g tick line
  names X-g1 / X-g2 / X-g3, which are archived and are deliberately NOT checkboxes here -- so the
  "names an ID that is not a checkbox" arm is scoped to the OWNER COLUMN and to nothing else.

  It is not a new pattern here: `tests/test_integration/test_template_no_money_arithmetic.py` and
  `tests/test_models/test_posting_ref_seed_parity.py` already read repo files by path and assert on
  their text, and this runs in CI on every PR like they do. **Its firing control is free and must be
  shown: point one row at a ticked step and watch it fail.** Written against the ledger AS IT NOW
  STANDS it also catches two more stale owners this triage found by hand -- **N-46** (owner cited
  `X-c2b3`, which is ticked) and **N-73** (cited `X-g2b`, ticked) -- both corrected in this same
  pass, which is the gate earning its place before it ships.

- [ ] **X-i** `refactor(balance): one read pass, one derivation, one clock` -- closes **FU-3**,
  **N-14**, **N-40**, **N-56**, **N-72**'s second half, **N-89**, **N-91**, **N-92**, **N-93**
  (ruling R-AO). **Nine rows, one sentence of root cause:** `BalanceContext` pins the read pass's
  `as_of` and `scenario` and memoizes three LOAN derivations through `_memoize_once`, and every
  OTHER input the replay consumes is loaded ad hoc by whoever needs it, at the wall clock. The arc
  solved this for loans at plan step D-ctx and stopped there.

  **Two symptom families the ledger filed apart, and they are one defect.** RECOMPUTED: the
  pay-period calendar loaded three times per modelled grid render (N-89 + N-93), the contribution
  feed at `~9.3-9.5 ms` an investment account with no cache (N-92), the modelled base built
  **14 times for 4 accounts** on one `/savings` render (N-72's second half -- and that render threads
  ONE context from `_data.py:67` through `_orchestrator.py:95`, which is what makes a context memo
  reach it), `contractual_schedule_from_origination` twice on the property page (N-14).
  UNPINNED: the employer-match gross resolving at `income_service`'s implicit `date.today()` and
  across all scenarios (N-91), `live_amount_overrides` calling `date.today()` inside the pinned fold
  (N-40, re-verified live 2026-07-27 at `loan_payment_service.py:864`), and the standing overpayment
  read off the CURRENT template row whatever date the pass is pinned at (FU-3 --
  `_resolution.py:294` calls `loan_standing_extra_for_account(account.id)`, which resolves through
  `recurring_transfer_query.py:72-76` with no as-of).

  **DECOMPOSED on the arc's own eight-times-proven line, because one half cannot move a cent and the
  other moves money:**

  * [ ] **X-i1 THE MEMO** -- additive, byte-identical on both databases. The context gains the input
    tier the loan derivations already have, through the SAME `_memoize_once` mechanism (`_context.py:256`)
    rather than a second one: the pay-period calendar, the per-account contribution feed, the
    override map, the standing extra, the contractual schedule. Every loader keeps the clock it has
    TODAY, so no figure can move and the harness is the proof.
  * [ ] **X-i2 THE CLOCK** -- the cutover. Each memoized loader takes `ctx.as_of` and `ctx.scenario`,
    and this MOVES MONEY: N-91 measured the gross at `$3,631.74` today against `$3,722.53` at a 2027
    read and `$0` before the first pay period, and FU-3 changes a loan's whole forward trajectory on
    a historical read. It gets its own trace, its own oracle and its own every-figure sign-off.

  **What X-i1 does NOT close, stated so it is not discovered at the sign-off: N-56.** The grid's two
  self-refresh endpoints are two HTTP requests, so each builds its own context and a per-PASS memo
  cannot reach across them. Its fix is the `hx-swap-oob` topology its own row describes, and it
  rides in this step as its own commit because it is the same "one interaction, one derivation"
  question one level up -- not because the memo touches it.

  **Sequenced BEFORE X-j on a MEASURED ground.** X-j moves the dashboard hero, the pulse and the
  calendar onto the modelled view, whose contribution load N-93 measured at `2.7 -> 14.8 ms` per
  render entry for an INVESTMENT account (an APPRECIATING asset `2.7 -> 3.7 ms`), and the pulse
  scans the WHOLE horizon rather than one column. Shipping X-j first ships that regression onto
  three more surfaces and then removes it; shipping X-i1 first means the memo is already there when
  they arrive.

  **Forks for X-i2's trace, recorded here so they are ruled and not decided in a commit:** which
  salary profile a historical read picks when several are `is_active`, whether the deduction feed
  becomes scenario-scoped (`projection_inputs._active_deductions_query:193-202` filters user and
  active only), and whether a historical as-of should reach the contribution feed AT ALL or be
  refused -- today the only non-default `as_of` in `app/` is `tax_report_service.py:373`, which
  reaches `loan_interest_in_year` and no cash producer, so the whole class is inert at HEAD and the
  step is choosing a rule before a caller needs one.

- [ ] **X-j** `feat(balance): one account, one answer -- or a row that explains the difference` --
  closes **N-87**, **N-90** and **N-83**'s DISPLAY half (ruling R-AO); **N-58** is sequenced after
  X-f by its own row's ruling and stays open here.

  **This step is the OWN RULING ruling R-AK deferred, and its title does not presuppose the
  outcome.** R-AK (2026-07-27) ruled the dashboard, the pulse and the analytics calendar STAY on the
  kind-blind cash view *inside X-g3*, corrected the false contract statement in-commit, and said in
  terms that the surviving argument "deserves its own ruling with its own measurement". X-j is that
  ruling. It is NOT a decision to move them, and nothing here overrides R-AK: the two candidate end
  states are "one producer, with the difference rendered" and "two producers that legitimately
  answer different questions, with the NAVIGATION that equates them fixed instead", and the trace
  decides between them.

  **Root: the seam offers two families that answer "what
  is this account worth" for the SAME account, and the CALLER picks.** `cash_balance_map` /
  `cash_balance_at` / `cash_daily_balance_series` against the kind-correct family and
  `grid_balance_view`. Plan step X-g3b proved the resolution on the grid -- give the surface the
  rows that explain the modelled tiers, then let it render the modelled balance, and ruling R-K's
  identity holds for all five kinds. Every surface that has NOT had that treatment is where the
  contradiction now lives.

  **It is live on the developer's own default screens today, which is why it does not wait for
  X-d.** `dashboard_service.py:112` (the hero), `dashboard_pulse_service.py:160` (the runway chart
  and its trough / peak chips) and `calendar_service.py:889` (the analytics calendar) all read the
  CASH family; `/grid` and `/savings` read the modelled one. `resolve_grid_account` returns the
  Empower 401(k) on `shekel`, so the default `/dashboard` hero renders **`$31,070.06`** at the
  current period against the default `/grid`'s **`$31,751.40`** -- **`$681.34`**, same account, same
  period, and the pulse's chips carry `view in grid` links built with no `account_id`, so ONE CLICK
  joins a captioned figure to a different Decimal for the same account (finding N-87, measured
  2026-07-27). At the last projected column the gap is `$21,856.66` on the Property.

  **The trace's FIRST job is the measurement R-AK named, and until it exists neither end state is
  arguable.** The pulse's surviving reason for the cash basis is a runway-SAFETY property: modelled
  growth inflates the "lowest point ahead", so a real future dip below zero could be HIDDEN, which
  is about the question `/dashboard` asks ("will I run out of money") and not the question the grid
  asks. How often does an accrual lift a trough above zero on the real data, and by how much? That
  is measurable on both databases and nothing should be designed before it is.

  **Whichever end state the trace picks, ONE property is not negotiable and is what the step ships:
  no surface renders a figure that contradicts an adjacent surface with nothing to explain it.**
  Two shapes satisfy it, and they cost very differently:

  * **Unify** -- after X-g there is ONE replay, and the cash-flow view is a FILTER on it (omit
    ACCRUAL and CONTRIBUTION). The cash-flow entry then hands back a RECORD carrying the omitted
    tiers, which is exactly what `GridBalanceView` already is, and a surface that cannot render the
    reconciling rows cannot obtain a balance. That converts "keep checking that the screens agree"
    into "a screen that disagrees will not build" -- the substitution this whole arc exists to make,
    and the reason this option is stated first rather than as an equal.
  * **Separate, honestly** -- keep the runway on the cash basis because it is a different question,
    and fix the NAVIGATION that equates them: the pulse's trough / peak chips carry `view in grid`
    links built with no `account_id` (`templates/dashboard/_pulse.html:75-95`), so one click joins a
    captioned figure to a different Decimal for the same account and period. Re-targeting or
    dropping those links is strictly smaller than either option R-AK weighed and touches no balance
    producer at all -- which is the argument FOR it and, on its own, is not enough: it leaves two
    numbers for one account with no row naming the difference, so this option only satisfies the
    property above if the difference is also SAID somewhere the reader is standing.

  **N-90 rides here rather than in its own commit** because it is the same question about the SAME
  identity: R-K holds by construction only in its BOUNDARY form, while the form the screens render
  (`balance[p] - balance[p-1]`) additionally needs the rendered periods contiguous and ordered, and
  nothing enforces that -- caller discipline standing in for a structural property. A step whose
  whole subject is "the rendered identity" is where it belongs.

- [ ] **X-k** `fix(recurring): the recurrence bound is reconciled, not stored and forgotten` --
  closes **N-18**, **N-19**, **N-23**, **N-24**. **Developer ruling R-AP (2026-07-27), taken AGAINST
  the recommendation**, which was to hand these four to the recurring-transfer arc: they stay in
  this ledger and get a step. The ground the recommendation rested on SURVIVES the ruling and is the
  step's scoping rule -- X-k touches the recurrence engine and the transfer write door and NOT the
  seam, so it shares no file with any other remaining step and must not grow into one.

  **Root: `RecurrenceRule.end_date` is a stored derived value that is never reconciled against what
  was actually GENERATED, and the write door's refusals have no consistent batch contract.**
  `recurrence_engine.match_periods` neither backfills nor prunes, so generation that ran between two
  different bounds is never revisited (N-18), and a retired loan's bound admits the current period
  because `period.start_date <= end_date` (N-19). Beside it, one refused loan payment rolls back an
  entire carry-forward batch (N-23) and three generation call sites have no `ValidationError`
  handler at all, so a refused write 500s on pay-period EXTEND and on unarchive (N-24).

  **It is not cosmetic and the money consequence is on a balance screen, which is why the ruling to
  keep it here is defensible on more than filing:** a shadow generated past a bound that later moves
  EARLIER keeps its checking-side expense leg, so the cash projection debits a payment for a loan
  that owes nothing. N-18 has a firing control for that direction (measured: bound 1 `2026-04-01`
  against bound 2 `2026-03-01` on a 1-month $12,000 loan paid manually at $6,100); the opposite
  direction is argued reachable and NO control could be constructed for it across three fixtures,
  so the prune ships with a control and the truncation half does not ship at all until one exists
  (Section 7.3).

  **N-23 is a semantics decision, not a touch-up, and is ruled at the trace:** the guard's DECISION
  to refuse is correct, the blast radius is the defect, and skip-and-report (leave the row in its
  source period, count it in the message) changes carry-forward's batch contract. Do not fold it
  into the prune commit.

- [ ] **X-l** `feat(periods): the pay calendar answers any date` -- closes **N-82** and **N-79**'s
  surviving FAR half. **Root, and it is this arc's own disease on the other axis: the pay calendar
  is a PARTIAL function.** `pay_period_service.get_all_periods` is
  `db.session.query(PayPeriod).filter_by(user_id=...).order_by(period_index).all()` -- the
  MATERIALIZED rows and nothing else (verified 2026-07-27, `pay_period_service.py:207-212`). Past
  the last row every consumer improvises its own answer, and the improvisations disagree, which is
  precisely the shape Section 1 describes for the cash balance and Section 3 deletes with a total
  fold.

  **Two measured improvisations, filed as two findings and they are one:**

  * **N-82** -- the modelled replay's ACCRUAL tier keeps running past the horizon while its
    CONTRIBUTION tier stops, because a contribution is dated on a real payday and there are none out
    there. Measured at 2029-01-01, six months past a horizon ending 2028-07-12: Empower
    **`+$2,501.92`**, Roth `+$1,754.08`, Property `+$5,427.07`, Money Market `+$272.24`. A HALF
    model, and nothing on screen says so.
  * **N-79's far half** -- `growth_engine._project_one_period` looks a `ContributionRecord` up by
    `period.start_date` (`:399`, verified: the lookup IS by date, not by period identity), so past
    the real calendar no record can match and every period falls back to the flat
    `periodic_contribution`. Ruling R-AF closed the NEAR half for free by landing the synthetic axis
    on the real pay boundaries; the far half survives because there is nothing out there to land on.

  **THIS STEP SUPERSEDES RULING R-AG's record-not-fix half (developer ruling R-AQ, 2026-07-27).**
  R-AG rejected "extending the payday cadence past the calendar so both tiers stop together" as
  "correct in principle, invents a calendar the app does not have, and is materially larger than
  this step" -- three grounds, of which the first is a concession and the other two are cost. The
  developer has ruled that cost is not a reason to leave a defect in place. R-AG's OTHER half stands
  unchanged and is what makes this step safe: the fold stays TOTAL and is never clamped.

  **The design, and it is the fold's own shape:** a pay period is generated by a CADENCE (an anchor
  payday plus a frequency), and the materialized rows are that cadence's persisted prefix, not its
  definition. So the calendar gains a total accessor -- "what pay period contains date T, for any
  T" -- that reads a materialized row where one exists and derives from the cadence where none
  does, with the derived ones marked as derived so no writer can persist one by accident.

  **Three things to settle at the trace, before any code.** (a) Whether a derived period may carry
  an `id` at all, given `PayPeriod.id` is a foreign key target in five tables -- the answer is
  almost certainly no, and every consumer keyed on `period.id` past the horizon has to be found
  with an AST scan and not a grep (Section 8). (b) Whether this changes what the ROLLING WINDOW
  materializes, which is `pay_period_admin`'s concern and must not be widened silently.
  (c) Whether a derived payday participates in ruling R-Z's strictly-after-the-assertion boundary
  the same way a real one does -- it should, but it is a money-moving rule and gets its own worked
  example.

- [ ] **X-m** `refactor(growth): the projection engine takes its axis, not its boundaries` -- closes
  **N-86**. **Root: `growth_engine.project_balance` takes a derived boundary as an ARGUMENT its
  caller must compute to match the window the caller also passes.** Verified 2026-07-27:
  `project_balance(..., periods, ..., ytd_contributions_start=ZERO, ...)` (`growth_engine.py:457-464`)
  receives BOTH the axis and a YTD boundary that must hold exactly the periods that axis EXCLUDES.
  Nothing checks that they agree. That is Section 8's rule verbatim -- **an argument a caller can
  get wrong is a defect, not a contract** -- and it is the same argument ruling R-AF made when it
  deleted the chart's seed junction rather than captioning it.

  **What the divergence costs when a caller gets it wrong, measured:** `$1,000.00` of annual-limit
  room per period of divergence, compounded over the horizon, on a `$23,500` limit at `$1,000` a
  period with today in the year's 15th period. Today both live callers are correct and pinned in
  both directions by `TestTheAnnualLimitSeedFollowsTheWindow` -- but the app now carries TWO correct
  YTD boundaries whose difference is invisible in the rendered figures (`_compute_limit_info`
  renders the through-current total; the projection needs the window's exclusion), and a THIRD
  projection surface would have to KNOW the rule to get it right. Knowing a rule is what this arc
  replaces with structure.

  **The fix is a signature change, so trace every caller before touching it (rule 7).** The engine
  derives the boundary from the axis it was handed; `ytd_contributions_start` leaves the signature
  rather than gaining a validator, because a validator is a second statement of the same rule and
  Section 8 already ruled that shape. Its five real call sites -- `retirement_projection.py:593`,
  `retirement_readiness.py:631`, `investment_dashboard_service` `:367` / `:972`,
  `savings_dashboard_service/_horizon.py:413` -- are the what-if surfaces ruling R-U deliberately
  keeps, so this step touches the engine and its callers and NO balance producer. It cannot move a
  balance, which is what makes its sign-off the harness plus the five surfaces' own figures.

- [ ] **X-n** `fix(loan): a redistributed payment carries its REAL installment` -- closes **N-36**.
  **Root: `_redistribute_to_distinct_months` OVERWRITES the fact instead of carrying it.** Its own
  docstring is the evidence (`loan_payment_service.py:340-359`, verified 2026-07-27): it shifts a
  colliding payment's DUE date to the next month so the monthly engine does not sum two into one,
  and it says "Only the DUE date shifts. `payment_date` (the pay period the cash actually moved in)
  is a FACT and is carried through untouched." **Both are facts.** The real installment a payment
  satisfies is as much a fact as the period its cash moved in, and the redistribution destroys it.

  **What that costs downstream, and why the ledger recorded it as deliberate rather than fixing it:**
  archived ruling D5 re-keyed every split input onto contract time -- ordering, rate and escrow key
  on the DUE date, so out-of-order or late settlement can never re-split an installment -- but
  `rate_period_engine._replay_from_anchor` (`:893`) was left on `payment.period_start`, because
  keying its rate on the REDISTRIBUTED due date would let a schedule-alignment artifact move a
  replayed balance. So the codebase holds one question with two keys, on purpose, and states it at
  the site so it cannot be rediscovered as an accident.

  **It is contained TODAY and the containment is not a fix.** The replay's rows and balance are
  DISCARDED whenever a `confirmed_view` is supplied (`_build_forward_inputs` keeps only
  `next_pay_date` / `remaining_months_as_of`), which is every production read since E1d-b -- so the
  two keys can differ only on the unseeded what-if path and never inside one rendered figure. That
  is a property of which surfaces exist, not of the design: the day a caller renders the replay's
  balance, a schedule-alignment artifact becomes a rendered number. **The step exists so that day
  cannot arrive**, which is the whole reason the developer ruled no finding is left to a wake
  condition.

  **The fix carries both dates and re-keys the replay onto the real one**, after which
  `rate_period_engine` states ONE rule with ruling D5 rather than a documented exception to it.
  `PaymentRecord` gains the real installment alongside the redistributed one; the collision handler
  keeps writing the shifted date for the monthly engine that needs it. Trace both consumers before
  changing the record (rule 7): the monthly engine wants the shifted date and the replay wants the
  real one, and today one field is serving both.

- [ ] **X-d** `fix(cash): the posted account ledger is a checked projection` -- E1a's shape for
  cash. The posting writer consumes X-a's walk instead of its own, and the per-visible-date assert
  (`sum(postings) == fold(ACTUAL events)`) makes a stale posting a detectable, repairable cache
  inconsistency. Ship-gated on a prod-data sweep for walk-invisible legacy rows, exactly as E1a
  was; any found row is an F1-class human decision, never a silent exclusion.
- [ ] **X-e** (old **X4**) `refactor(accounts): current_anchor_balance is a reconciled cache or it
  is nothing` -- today `cash_ledger.resolve_anchor` detects the divergence from the history table
  and only LOGS it (`EVT_ANCHOR_CACHE_RECONCILED`), never repairs it. Decide the column's fate once
  the fold reads history directly (cash D4).

  **WIDENED 2026-07-27 (ruling R-AO), because the one-liner is not the step the code describes.**
  This entry read as ONE question -- keep the column or drop it -- and an AST-free `grep` over
  `app/` on 2026-07-27 finds **23 files** touching `current_anchor_balance` /
  `current_anchor_period_id`, in four distinct roles. It owns findings **cash D4**, **N-4**,
  **N-5**, **N-73**, **N-83**'s CACHE half and **X5**, and the trace decides the decomposition:

  * **The WRITERS, and two of them are kind-blind** -- `anchor_service.py:233-234` (the mutation),
    `account_service.py:204-205` (the create factory, which writes an origination cash anchor for
    EVERY kind -- **N-5**), `routes/accounts/crud.py:364-369`, `pay_period_admin.py:521` (the
    pay-period reset, which re-anchors every kind -- **N-4**), and
    `account_validation.py:428` comparing against it at the write door. N-4 and N-5 are the residue
    of the archived B-15: the mechanism that RENDERED a wrong anchor closed at A1, the writers that
    CREATE one did not.
  * **The rendered VALUE reads**, which is the half the one-liner did not have at all:
    `home_equity_service.py:137` (the property hero -- **N-83**, a primary read and not a fallback),
    `property_equity_chart.py`, `retirement_projection.py:602` / `:729`,
    `savings_dashboard_service/_projections.py:92`, `_data.py:184` (inactive accounts),
    `retirement_dashboard_service.py:448`, `investment_dashboard_service/_context.py:188` / `:261`,
    `dashboard_service.py:116` and `routes/grid.py:288` (the grid HEADER's starting figure, on the
    surface X-g3b just unified). Most are degraded-state fallbacks; N-83's is not, which is why that
    row's display half goes to X-j and its cache half stays here.
  * **The residual GUARDS** -- `_kernel.py:320` / `:432`, `_inputs.py:286` and `_investment.py:139`
    (the last dies at X-g4) branch on `is None` for two `nullable=False` columns carrying a check
    constraint and **0 NULLs across all 19 account rows in both databases** (**N-73**). Deleting
    them changes `balance_map`'s `| None` contract, which every net-worth consumer handles, so they
    are this step's work and not X-g4's.
  * **The DETECTOR** -- `cash_ledger/_facts.py:188-189`, which finds the divergence and logs it
    (**cash D4**).

  **X5 is DECIDED here rather than left as "own arc, if ever"** (ruling R-AO): whether an
  `AccountAnchorHistory` row gains an `effective_date` -- separating "when this was true" from "when
  it was typed", which is what a backdated statement assertion needs -- is a question about the SAME
  table this step is deciding the shape of, and answering the column's fate without it decides half
  a design. It may still be declined; it may not be left unasked.
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

- [ ] **X-p** `fix(analytics): the calendar's chips and its balance line are on one clock` -- closes
  **N-58**. **Sequenced AFTER X-f by that finding's own recorded ruling** (developer, 2026-07-26),
  and the reason is not caution: X-f shrinks the date noise at its SOURCE, so ruling before it would
  decide the question against numbers X-f then changes. It is scheduled, not deferred -- the
  distinction the R-AQ ruling turns on.

  **Root: one day cell renders two facts on two clocks with no row explaining the gap.** A cell
  shows its flow CHIPS and its end-of-day BALANCE. The chips are placed on the BUDGET attribution
  date (`calendar_service._get_display_day`: `due_date` clamped into the period, falling back to the
  period start); the balance line is the fold, which steps on the day the money MOVED -- `paid_at`'s
  UTC civil day for a settled row, `max(attribution, as_of + 1)` for a still-projected one (ruling
  R-G). They agreed by construction until plan step X-c2b2, because the retired ramp distributed the
  same still-projected rows over the same attribution days.

  **Measured (finding N-42, same data):** `paid_at - due_date` is median **2 days**, p75 **6**, max
  **25** across 130 settled Checking rows, so on production essentially every past chip is displaced
  from its own balance step. The step's own fixture demonstrates it --
  `test_flow_strip_low_trough_warning_cells` renders a `$600` chip on Jan 2 with `$1,000.00` under
  it and the `$400.00` drop on Jan 5.

  **This is the split the GRID met, and ruling R-K answered it there with the "Timing & true-ups"
  row.** The calendar has no such row, which is the whole finding: the same defect, on the surface
  that never got the treatment. Its option space is recorded and is ruled at this step's trace, with
  X-f's post-fix numbers in hand: (a) place the chip on the cash clock, which changes which MONTH a
  row appears in; (b) give the calendar R-O's treatment, a reconciling figure per cell or per month;
  (c) rule the divergence acceptable and label it -- weakest, and Section 8 already rules a label
  weaker than a predicate, which is itself not a safety.

  **It shares a surface with X-j and must be re-verified against it.** X-j decides which producer
  the calendar's balance line reads at all; this step decides which CLOCK its chips sit on. Whichever
  ships second re-verifies the first, exactly as N-83's two halves do.

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

  **A FIFTH question, added 2026-07-27 (ruling R-AO): finding N-33's 13 private-NAME crossings.**
  That row's stated home was "D3-adjacent", and D3 has SHIPPED, so it names a resolver that is gone.
  It belongs here rather than in a commit of its own, because it is the same question this trace
  already asks one level down: `app/routes/accounts/{anchor,crud,detail,types}.py` and
  `app/routes/loan/params.py` import 13 private names from `app.utils.account_validation`, and the
  names LIE about their visibility -- routes are their consumers, so they are cross-package API. The
  honest fix is a rename to public, after which extending W9910 to private NAMES (owner = the
  defining module's package) is a zero-exception tightening, every other private-name import in the
  tree being intra-package. Deciding a re-export surface for the balance cluster while a second
  package's cross-package API is spelled private is deciding the same rule twice.
- [ ] **E2-n** the move itself, and the registry deletion. Decide the decomposition from E2-0, not
  here. The deletion of `_FENCED_MODULE_RULINGS` is the LAST commit, never the first: prove the
  boundary holds before removing the gate that currently compensates for its absence (the C3b3
  prove-the-successor-first precedent, which this arc has now applied eight times).


## 6. The findings ledger

**Only UNRESOLVED findings are here, and every one of them has an OWNER** (Section 9 rule 6). The
CLOSED registers are in the two as-built records: the 14 that Phase X's shipped steps closed are in
`archive/cash_arc_as_built_2026-07-27.md` Section 3, and the 75 the LOAN arc closed are in
`archive/loan_arc_as_built_2026-07-26.md` Section 7. IDs keep their names, so a reference to any
finding resolves in whichever file holds it. Unfinished work stays HERE whichever half of the arc it
came from: a loan-side question that is still open is still open.

**Three rows were RE-VERIFIED against the code at the 2026-07-26 trim and two of them were wrong.**
B-7 / B-10 were closed by F2 (`3aecceb0`) and the row had never been updated -- archived. B-16 and
B-17 both named steps that have since shipped as their resolvers, and both are still LIVE; their
rows below are rewritten with what the code actually does now. The rest are carried forward
UNCHANGED and were NOT re-measured at the trim -- their figures and citations were true on their
own write date, per Section 7.6.

**EVERY row was TRIAGED 2026-07-27 (ruling R-AO), and the count is why.** 51 rows, 10 CLOSED, 41
open. Of the 41, one is the **E2** pointer row and one is an operator question (**FU-1**); **10
named a live step that owned them -- cash D4, N-4, N-5, N-42, N-43, N-46, N-72, N-73, N-78, N-85 --
and 29 did not.** Four of those 29 named a resolver that had SHIPPED: **N-14** ("Phase D"),
**N-33** ("D3-adjacent"), **N-40** ("X-c"), **N-56** ("X-c2b2") -- the B-16 / B-17 class recurring,
and the measured proof that an unowned row does not wait, it rots. Grouped by ROOT rather than by
symptom the 29 collapsed to five clusters, four of which became named Section 5 steps (**X-h**,
**X-i**, **X-j**, **X-k**) with **X-e** and **E2-0** widened to absorb the rest.

**Ruling R-AQ then closed the last six the same day, and RETIRED this column's old vocabulary.**
R-AO had left six as "residue", four with wake conditions; the developer ruled that a finding
waiting on a condition is a time bomb with a note attached and that cost is not a ground for
deferral. Those six became **X-o** (B-16), **X-l** (N-82 + N-79's far half), **X-m** (N-86), **X-n**
(N-36), **X-p** (N-58) and ONE dated `developer-decision` (N-25's class). **"Own commit", "own
step", "own arc", "if ever", "recorded, deferred", "residue" and every wake condition are no longer
values in the last column** -- see Section 9 rule 6 for the closed vocabulary and the gate that
enforces it. **Twenty-nine unowned rows became zero.** Two more stale owners were found by hand
while applying it -- **N-46** (cited the ticked `X-c2b3`) and **N-73** (cited the ticked `X-g2b`) --
which is the gate earning its place before it ships. **Six were left as residue and ruling R-AQ overturned that the same day** -- B-16 to X-o, N-36 to
X-n, N-79's far half and N-82 to X-l, N-86 to X-m, and N-25's class to a dated
`developer-decision`.

**Three rows were re-verified against the CODE during that triage and two were wrong.** **N-43**
said OPEN and the defect is no longer REACHABLE (zero live callers pass through
`_merge_balance_sources` since X-g2b; it is dead code awaiting X-g4). **N-40** said its resolver had
shipped and it is still LIVE. **FU-3** was a nine-word row with no citation and is confirmed real,
in the X-i class. One NEW finding came out of the same pass: **N-95**, the seam's own front-door
docstring stating the contract X-g2b retired.

**X-o's trace re-read the six rows X-g4b owned (2026-07-27) and ALL SIX were stale, which is
the same class one commit later.** X-g4b shipped and none of its rows was re-pointed, so every one of
them named a TICKED owner. **N-43** ("OPEN"), **N-46** ("half closed"), **N-78** and **N-95**
("recorded, NOT fixed") each described a defect whose code was already deleted; all four are verified closed by `17c57cde` and moved to the archive's
register. **N-72** keeps only its redundancy half (owner **X-i1**); the merge half went with the
module. **N-85** named the ticked `X-g4` AND is the same defect as **N-96** -- one callerless public
seam entry, found twice by two reviews neither of which read the other's row -- so it is re-pointed
to N-96's owner and de-duplicated in place. That is three separate hand-passes in two days finding
stale owners; the gate at plan step **X-h** is what stops the fourth. **Four NEW rows came out of
the same trace and its adversarial review**: **N-98** (the debt-free date's second producer),
**N-99** (revolving debt is invisible to "Debt-free"), **N-100** (the Horizon publishes three things
nothing reads) and **N-101** (the projection dict re-flattens `LoanFigures`, which is B-16's root).

| id | finding (one line) | worst measured | status | closed by |
|---|---|---|---|---|
| B-16 | **The /savings horizon asks the debt-line question with the CONGRATULATION predicate.** `LoanFigures` states the contract at `_loan_figures.py:176-177` -- "Use `is_retired` to decide whether a loan has a debt line; use `is_paid_off` to decide whether to CONGRATULATE the user" -- and `_horizon.py:144` (which loans are ACTIVE for the debt-free date) and `:561` (which payoff milestones to plot) both ask the debt-line question with `is_paid_off`. `is_paid_off` is strictly narrower: it also requires at least one CONFIRMED payment, a badging guard against a degenerate `$0` opening anchor. So a loan retired by a lump-sum balance TRUE-UP with no payment rows reads `is_retired=True, is_paid_off=False`, stays in the horizon's ACTIVE set, and -- being retired -- has `payoff_date is None`, which fires the "an ACTIVE loan with no payoff never clears" branch: **no debt-free date at all, and the user reported not loan-free, on a loan that owes nothing.** The same misconfiguration class produced the `$197,049.32` equity-chart defect `_loan_figures.py:178-184` cites | the debt-free milestone suppressed entirely; not yet measured in dollars | **OPEN -- RE-VERIFIED against the code 2026-07-26** (the trim). Its old status ("latent -- collapses at C3b/C4") was WRONG: both steps shipped and both call sites are live. Not fixed here: it is a loan-surface predicate swap with its own control to write **TRIAGED 2026-07-27 (ruling R-AO): genuine RESIDUE.** A single independent surface -- a loan-figures predicate swap with its own control -- sharing a root with no other row, so 'own commit' here means triaged and independent rather than unread. **RE-TRIAGED 2026-07-27 (ruling R-AQ): there is no deferred category, and the R-AO pass under-triaged this as residue). It is a LIVE defect on a rendered screen -- re-verified at `_horizon.py:144` and `:563` on this date -- with a stated contract, a one-line fix and an obvious control, so it is DONE FIRST, at plan step X-o.** | X-o |
| B-17 | **The debt-track `is_originated` guard proves where the value comes FROM and never that production puts it there -- the N-63 / N-67 class, third instance.** `test_balance_at.py:3583-3592` builds its OWN `_ad` dict with `"is_originated": figures.terms.is_originated` and asserts `_metrics` behaviour on it. Production builds that dict at `_projections.py:241-243`, with the same expression PLUS a `loan_result is None` fallback of `True` the test's dict has no branch for. Change the production key to `False`, to `is_retired`, or drop it, and the test stays GREEN -- it never executes the builder. The value's source is proven; the WIRING is not | the debt track counting an unclosed mortgage as 100% paid (the defect the flag was added to stop) | **OPEN -- RE-VERIFIED 2026-07-26** (the trim). Its old status ("flag deleted at C3b") was WRONG: `is_originated` is live on `LoanFigures.terms`, read through that dict at `_metrics.py:362`. The fix is the Section 8 rule N-63 wrote: assert the CALL, or assert behaviour through the real builder **TRIAGED 2026-07-27 (ruling R-AO): to X-h**, with the three other controls that cannot fire. | X-h |
| FU-1 / F1 | **The Van Loan's one unexplained true-up STEP -- an operator question, not a code fix.** RE-SCOPED 2026-07-25 on a fresh PROD clone: the duplicate same-day anchors the finding named are DEV-CLONE pollution (created 2026-07-07 during arc development), not production. Prod's account 8 carries exactly THREE anchors (origination 2023-02-14 `$32,402.45`, user_trueup 2026-05-22 `$17,020.47`, user_trueup 2026-06-23 `$15,663.59`) and an audit trail of 6 INSERTs / 0 UPDATE / 0 DELETE -- the shape was never there, so it was not silently repaired either. What DOES remain: the 2026-06-23 true-up moves the balance `$905.33` beyond what the recorded payment explains (after the 06-22 installment's `$451.55` principal the walk stands at `$16,568.92`; the anchor asserts `$15,663.59`). That is a user ASSERTION, which the architecture treats as authoritative by design, not a defect -- the Mortgage's own 2026-05-22 true-up reconciles to the cent (`$177,829.83` == the walk after two payments), so the machinery is not suspect | `$905.33` against the servicer's statement | **OPEN -- awaiting the OPERATOR.** Whether the `$905.33` matches the servicer's statement is a question only you can answer; it blocks nothing, and the ledger is self-consistent under E1a's assert either way. Converted from a Phase F step to a finding at the 2026-07-26 trim, because it is a question and not a commit | operator (unchanged by the R-AO triage) |
| FU-3 | Standing overpayment resolves at today for any as-of | -- | latent **RE-VERIFIED 2026-07-27** and it is the X-i class, not a C-phase note: `_resolution.py:294` calls `loan_standing_extra_for_account(account.id)`, which resolves through `recurring_transfer_query.py:72-76` off the CURRENT template row with no as-of, inside a resolution the context pins an `as_of` for. **TRIAGED 2026-07-27 (ruling R-AO): to X-i2.** | X-i2 |
| N-96 | **`balance_at.interest_by_period_for_account` is a public seam entry with ZERO `app/` callers.** AST-verified 2026-07-27 during X-g4b's review: the account-detail route reads `interest_projection_for_account`, and nothing reads this. `__init__.py` states as fact that it and `debt_schedule_rows` are "the two non-balance seam entries the out-of-cluster consumers (the account-detail route, the savings orchestrator) read" -- true of the second, FALSE of the first. Same class as the `calculate_interest` orphan X-g4b deleted, but it pre-dates that step rather than being created by it, so it was reported and not swept | a public seam entry no screen can reach, described as one two screens read | **OPEN -- found 2026-07-27** by X-g4b's adversarial review, AST-verified, deliberately NOT fixed in that commit (out of its scope, CLAUDE.md rule 6) | X-e |
| N-97 | **`app/utils/dates.py:314` cites `balance_resolver.daily_cash_balance_series` as a live consumer of `attribution_date`.** That producer was deleted at plan step X-c2b3 (the calendar's per-day line is the fold sampled at every day). The rule the sentence states -- one attribution rule shared by the calendar's day cells and the balance line's steps, so a flow's cell and its step land on the same day -- is STILL TRUE and load-bearing; only its named example is gone. Found 2026-07-27 in X-g4b's sweep, outside that step's 26-site scope | a present-tense claim naming a producer deleted a month earlier, in the docstring of the rule two surfaces share | **OPEN -- found 2026-07-27**, reported not fixed (pre-dates X-g4b) | X-p |
| N-98 (X-o trace) | **The debt-free date has TWO producers over ONE `account_data`, with different membership rules, rendered on ONE page.** `_metrics._compute_debt_summary` feeds the cockpit's `Debt-free <month>` caption (`_cockpit.html:259`) and the dashboard debt track (`_tracks.html:91`), selecting loans with `_loan_ad_current_principal` (`_metrics.py:282`: skip when `current_balance <= 0`); `_horizon._resolve_horizon_domain` feeds the chart's `Debt-free` flag and its x-axis, selecting with the debt-line predicate. They part on a NOT-YET-ORIGINATED loan -- it owes `$0.00` today, so the balance rule drops a mortgage whose whole 30-year line is ahead of it. This is finding B-16's THIRD call site; B-16's own row cites only the two in `_horizon`, which is why closing it does not close this | caption **`2029-02-22`** against chart **`2048-12-01`** (19 years) on the developer's own Mortgage rewritten into the not-yet-closed state; **`2028-03-01`** against **`2056-06-01`** (28 years) on an independent two-loan fixture | **OPEN -- found 2026-07-27** by X-o's trace and independently by its adversarial review, measured on both databases' shape, deliberately NOT fixed in X-o (that commit moves no figure anywhere; this one moves a rendered caption) | X-q1 |
| N-99 (X-o trace review) | **"Debt-free" ignores revolving debt.** Both debt-free producers select accounts carrying `loan_params`, so a Credit Card -- which the seam holds FLAT because it has no forward model, and which the Horizon's own `_liability_band` sums into the chart -- cannot affect the date. A user carrying a card balance is flagged Debt-free on the date their last LOAN clears, on a chart whose liability band never reaches zero. A design fork, not a bug to fix silently: including revolving debt means nobody carrying a card ever gets a date | not measured in dollars; the developer carries no card balance today | **OPEN -- found 2026-07-27** by X-o's adversarial review, which caught the new `_debt_line_loans` helper over-claiming its scope in prose. The helper's docstring now states the gap; the CHOICE is X-q1's fork and goes to the developer before any code | X-q1 |
| N-100 (X-o trace) | **The Horizon producer publishes three things nothing reads.** `build_horizon` returns `is_loan_free` and `horizon_end`, and `_serialize_horizon` (`routes/savings.py:113-132`) emits neither -- it maps `labels` / `net` / `composition` / `milestones` / `current_index` and drops the rest; no template or JS names either. `savings_dashboard_service.compute_net_worth_horizon` is a PUBLIC export with ZERO `app/` callers, alive on 10 test call sites in one file (AST-counted, not grepped). Same class as N-85 / N-96 in the seam: dead surface kept honest by its own tests. It matters here because B-16's entry claimed the user was "told they are not loan-free", and the flag that would tell them reaches no screen | -- | **OPEN -- found 2026-07-27** by X-o's trace, which needed the claim checked before repeating it | X-q2 |
| N-101 (X-o trace) | **The per-account projection dict re-flattens `LoanFigures` field by field, and B-16 is what a dropped field looks like.** `_projections._project_one_account` copies `is_paid_off`, `is_originated`, `monthly_payment`, `current_rate` and `payoff_date` out of the seam's value object; `is_retired` was never copied, so the Horizon asked the nearest question the dict could answer. Nothing can fail on a key that was never there. `_types._LoanAccountResult` already composes `LoanFigures` for exactly this reason ONE LAYER DOWN -- "the copy silently went stale the moment the seam grew `is_originated`" -- so the rule is decided and only its application is missing | the B-16 defect itself; X-o adds a sixth copy rather than removing the pattern | **OPEN -- found 2026-07-27** by X-o's trace. X-o publishes `is_retired` as a sixth flat key deliberately (ruling R-AW): the live defect does not wait behind a refactor that touches two Jinja templates | X-r |
| cash D4 | Anchor column vs history table: divergence detected, only logged | latent | latent | X-e (widened 2026-07-27) |
| N-4 (A1) | Pay-period reset re-anchors EVERY kind, refreshing loan cash-anchor rows (balance-preserving `stage_anchor_true_up` inside the reset's deferred-FK transaction; same-value, not user-supplied) | -- | **OPEN** -- residue of the archived B-15 (a kind-blind true-up wrote a CASH anchor onto a LOAN; both real loans carried such rows), whose mechanism closed at A1 while these two writers did not | X-e (widened 2026-07-27; see also N-73) |
| N-5 (A1) | Account-create factory writes an origination cash anchor for every kind -- a loan created with a balance seeds the column at birth (entangled with loan onboarding) | -- | **OPEN** -- residue of the archived B-15, as above: the mechanism that RENDERED the wrong anchor closed at A1, the writers that create one did not | X-e (widened 2026-07-27; see also N-73) |
| N-18 (C8d) | **The recurrence bound and what was GENERATED can disagree, in both directions.** `create_payment_transfer` syncs the bound, generates, then re-syncs (C8d added the second call, because the payoff folds the forward PLAN and the first call cannot see the payments it is about to generate). But `RecurrenceRule.end_date` only gates FUTURE generation (`recurrence_engine.match_periods`) -- it neither backfills nor prunes -- so generation ran between two different bounds and is never revisited. Measured on a 1-month $12,000 loan originated 2026-03-01, read 2026-03-20, paid manually at $6,100: bound 1 (folded with no payment records) is `2026-04-01`, bound 2 (with the generated shadows) is **`2026-03-01`** -- EARLIER, and a PAST date, because the generated shadows include overdue slots that clamp forward to `as_of + 1d` and pay the loan down, and the clearing installment's DUE date is past (the edge `plan_payoff_date`'s docstring names). So the stored bound can sit BEFORE shadows that already exist. The opposite direction (a manual amount below contract truncating generation) is argued reachable but I could NOT construct a firing control for it across three fixtures. **A re-generation after the second sync was written and then REVERTED**: it addresses only the truncation direction, and shipping a write-path change whose control never fires violates Section 7.3. The over-generation direction needs a PRUNE, which is a pre-existing gap shared with `update_payment_settings`. **The concrete cost of deferring, stated so it is not mistaken for cosmetic:** a shadow generated past a bound that later moves earlier keeps its CHECKING-side expense leg, so the cash projection debits a payment for a loan already at zero -- money on a screen, not just a stale column. Unlike the truncation direction, this one HAS a firing control (measured above), so the prune is testable when it is built | bound 1 `2026-04-01` vs bound 2 `2026-03-01` (measured) | recorded, deferred **TRIAGED 2026-07-27 (ruling R-AO), and against the recommendation (R-AP): to X-k**, which keeps this cluster in this arc rather than handing it to the recurring-transfer arc. | X-k |
| N-19 (C8d) | **A RETIRED loan's recurrence bound does not exclude the CURRENT pay period.** `recurrence_end_date` returns `ctx.as_of` for a retired loan (developer ruling), and `recurrence_engine.match_periods` admits a period when `period.start_date <= end_date` -- so the current period, which started before today, still matches, and only `should_skip_period` (an existing row) stops another payment generating into a loan that owes nothing. Pre-C8d this varied rather than being reliably better: a retired loan WITH history got its last payment date (same wart), one WITHOUT got `origination_date` (which did exclude everything). Excluding it properly means bounding at the current period's `start_date - 1 day`, a different rule than the one ruled. Second-order: a retired loan mutated across days rewrites `end_date` to each new day and emits a BUSINESS audit event, so the write is idempotent only within a day | -- | recorded, deferred **TRIAGED 2026-07-27 (ruling R-AO) / R-AP: to X-k.** | X-k |
| N-23 (C9b) | **A refused loan payment now fails an entire carry-forward batch.** Carry-forward moves transfers via `update_transfer(pay_period_id=...)`, which since C9b runs the archived ruling R-C guard (the transfer write boundary REJECTS a loan payment dated at or before the loan's origination), and `routes/transactions/carry_forward.py` rolls the whole batch back on `ValidationError` -- so one un-movable loan payment costs the user every other carried item. The guard's DECISION is correct there (the moved payment would still be erased); the blast radius is the defect. Reachable on a row the C9a purge deliberately leaves: an ad-hoc (template-less) or `is_override` pre-origination payment on a future-originating loan. Worked: loan originates 2026-08-01 payment_day 1, current period 2026-07-10..07-23, no due_date -> installment 2026-08-01 `<=` origination -> refused -> 400, nothing carries. Fixing it means skip-and-report (leave the row in the source period, count it in the message), which is a change to carry-forward's batch semantics rather than to the guard -- a developer call, not a touch-up. Both stale docstrings that claimed the old raise conditions are corrected in-commit | whole batch lost | recorded, deferred **TRIAGED 2026-07-27 (ruling R-AO) / R-AP: to X-k**, where its batch-semantics decision is ruled at the trace and NOT folded into the prune commit. | X-k |
| N-24 (C9b) | **Three generation call sites have no `ValidationError` handler, so a refused write 500s.** `create_transfer` can now raise the archived ruling R-C refusal -- a loan payment dated at or before origination -- (as it already could raise `_reject_transfer_out_of_loan`), and the recurrence engine fans out through it. `transfers/templates.py:690` wraps generation correctly and C9b added the same wrap to `create_payment_transfer`; `period_population.py:86` (pay-period EXTEND / regenerate -- one bad loan template breaks the whole extension) and `transfers/templates.py:457` (unarchive) do not. Largely closed in practice by C9a: every loan-payment rule now carries a `start_date` (migration-backfilled + synced + bound at creation), and `first_installment_date` is strictly `>` origination for every input, so a bounded rule cannot generate a refused installment. This is the residual exposure for an unbounded rule, and it is partly PRE-EXISTING (the out-of-loan guard has the same reach) -- C9b widens an existing hole rather than inventing one | 500 on extend / unarchive | recorded, deferred **TRIAGED 2026-07-27 (ruling R-AO) / R-AP: to X-k.** | X-k |
| N-25 (D0a) | **A real runtime import cycle in the balance cluster was invisible to `cyclic-import`, because a TYPE-ONLY import of the same module excluded the edge.** pylint's `_add_imported_module` drops an edge into `_excluded_edges` when `in_type_checking_block(node)`, keyed by the `(importer, imported)` MODULE pair -- so one type-checking import silences the check for EVERY import of that module, including a runtime one elsewhere in the file. `resolution_context.py` had exactly that: a `TYPE_CHECKING` `PlannedPayment` import (line 73) masking the lazy runtime `loan_plan` import inside the method (line 305), which closed a genuine cycle with `balance_at._plan`. Measured both directions on this repo: neutralise the type-only edge on the PRE-D0a code and pylint reports `R0401 (app.services.balance_at._plan -> app.services.resolution_context)`; neutralise it on the D0a code and it reports nothing. Reproduced from scratch on a 3-file probe (8.75/10 -> 10.00/10 by adding a type-only import and nothing else). **The instance is fixed; the CLASS is not** -- the masking still applies anywhere a module imports another both for types and at runtime. Residual risk is bounded by two accidents rather than a gate: a top-level re-import would `ImportError` at load (`_plan` imports `BalanceContext` at module scope), and a function-level one now trips stock `import-outside-toplevel` since D0a deleted the scoped disable. The remaining path is re-adding the lazy import WITH a rationale comment -- which is what the pre-D0a code was, and it passed every gate | a cycle + an inverted dependency, gate-green | **instance closed (`8285fcad`)**; class recorded **TRIAGED 2026-07-27 (ruling R-AO): the CLASS is genuine RESIDUE.** It is a pylint/astroid limitation with no shared root here; the honest options are a custom checker (this repo has the framework and this is what it is for) or an explicit accepted-with-rationale ruling. 'If ever' is not one of them. **RE-TRIAGED 2026-07-27 (ruling R-AQ): there is no deferred category; this row is the one `developer-decision`.** The CLASS is the developer's own fork, taken for its own session: write the custom checker (`tools/pylint/shekel_checkers/` holds eight already, and a type-only import masking a runtime cycle is exactly the AST pattern that framework exists for) or rule it declined with the rationale written down. It is owned by a person with a name, on a dated decision -- not by 'if ever'. | developer-decision (dated 2026-07-27) |
| N-33 (D-gate) | **13 cross-package private-NAME imports -- the measured residual OUTSIDE D-gate's ruled scope.** The zero-exception scan for D-gate (AST over `app/` + `scripts/` + `shekel_checkers/`, confirmed by the shipped checker) found ZERO private-module crossings but 13 private NAMES imported across a package boundary from PUBLIC modules -- all one shape: `app/routes/accounts/{anchor,crud,detail,types}.py` and `app/routes/loan/params.py` import `_anchor_schema` / `_create_schema` / `_validate_update_account` / `_account_type_is_visible` / `_visible_account_types` / `_appreciation_params_schema` / `_interest_params_schema` / `_crosses_posting_boundary` / `_owned_account_type` / `_type_create_schema` / `_type_update_schema` / `_validate_account_type_boundary_edit` / `_validate_collateral_link` from `app.utils.account_validation`. The names lie about their visibility: routes ARE their consumers, so they are cross-package API. The honest fix is a RENAME to public (not a checker extension carrying an allowlist); once renamed, extending W9910 to private NAMES (owner = the defining module's package) would be a zero-exception tightening -- every other private-name import in the tree is intra-package (the `_helpers` convention). Not a live defect: a guard-scope observation | -- | recorded, deferred **Its stated home 'D3-adjacent' is STALE -- D3 has SHIPPED.** **TRIAGED 2026-07-27 (ruling R-AO): to E2-0**, which asks the same who-owns-this-name question one level down and would otherwise decide it twice. | E2-0 / E2-n (R-AO) |
| N-35 (E1e) | **The statement tier `app.services.ledger_report_service` is not W9909-scoped, so a public balance-at-T born there is unguarded.** E1e's rationale for deleting W9906 whole rests on "no public single-account balance-at-T producer exists outside the seam" -- true, and the claim was NARROWED to that wording in review, because `compute_balance_sheet(user_id, as_of)` does fold every posted source attributed on or before a date into per-account cumulative positions. It is the ruled exception (a whole-chart statement whose sections articulate only because the trial balance ties; pulling ONE line out to answer "what is this account worth on date T" is the named misuse), and it never sat on W9906's producer list, so the deletion cedes nothing. The GAP is the completeness half: the package holds every ingredient of a posted balance-at-T -- `dated_account_nets`, the chart load, the class-id sectioning -- OUTSIDE W9910's protection, exactly the shape that put `cash_ledger`, `loan_ledger`, `loan_resolver` and `account_projection` on the registry. **Measured on this tree:** a public `account_balance_on(user_id, ledger_account_id, as_of)` folding `dated_account_nets` inside the package rates **10.00/10** under the full `--fail-on` set. Scoping it is its own step because every public name in the package must then be classified (2 report entries + 7 attribution names).  **E2's ratification (2026-07-26) gives this a SECOND and cheaper resolution, and the two are alternatives rather than a sequence:** if `ledger_report_service` is a MEMBER of the super-package, the gap closes structurally and NOTHING has to be classified -- the same trade E2 makes for the other seven.  Whether it is a member is an E2-0 question: it is the STATEMENT tier over the postings, not the read seam, the write cluster or a shared leaf, so it is outside E2's stated membership and inside its own rationale.  Do not scope it under W9909 in the meantime without deciding that first, or the classification work is done and then thrown away | a balance-at-T on a screen outside the seam, every gate green (the archived N-28 / N-31 class: a public balance producer born in a module W9909 does not scope is unclassified, and the scope is keyed by module identity in BOTH directions -- moving a name out un-scopes it, and moving a module IN un-scopes what it holds) | **recorded, NOT fixed** -- the false absolute claim was corrected in-commit (checker header, the `loan_posting_service` ruling, the package's own docstring); the scope entry is deferred **TRIAGED 2026-07-27 (ruling R-AO): to E2-0**, which is where its membership question is already asked. Do not scope it under W9909 before that answer. | E2-0 |
| N-36 (C2b) | **The resolver's money-blind replay keys its rate on the PAY-PERIOD START, where the genesis walk now keys on the DUE date -- one question, two rules, deliberately.**  C2b re-keyed every split input onto contract time (archived ruling D5: the split inputs -- ordering, rate and escrow -- key on the DUE date, so out-of-order or late settlement can never re-split an installment), but `rate_period_engine._replay_from_anchor` (`:893`) was left on `payment.period_start`.  The reason is measured, not a preference: it consumes payments that have been through `loan_payment_service._redistribute_to_distinct_months`, which INVENTS a due date for a payment colliding on an already-allocated schedule month, so keying its rate on that date would let a schedule-alignment artifact move a replayed balance -- trading the archived N-34's defect (the split's rate and escrow keyed on the pay-period START rather than the DUE date, fixed at C2b) for a subtler one.  Containment, verified: the replay's rows and balance are DISCARDED whenever a ``confirmed_view`` is supplied (`_build_forward_inputs` keeps only `next_pay_date` / `remaining_months_as_of`), which is every production read since E1d-b, so the two keys can differ only on the unseeded what-if path and never inside one rendered figure.  The honest fix is to carry a payment's REAL installment alongside the redistributed one so the replay can key on the fact rather than the artifact -- which is a schedule-alignment change, not a split change | none measured (the divergent surface is discarded on every production read) | **recorded, deliberate, NOT fixed** -- stated at the site in `rate_period_engine`, so it cannot be rediscovered as an accident **TRIAGED 2026-07-27 (ruling R-AO): genuine RESIDUE.** Contained (the divergent surface is discarded on every production read) and its fix is a schedule-alignment change sharing a root with nothing else here. **RE-TRIAGED 2026-07-27 (ruling R-AQ): there is no deferred category: to X-n.** Its containment -- the divergent surface is discarded on every production read -- is a property of WHICH SURFACES EXIST, not of the design, so it is exactly the shape R-AQ refuses to leave standing. X-n carries the real installment alongside the redistributed one so `rate_period_engine` states ONE rule with archived ruling D5 rather than a documented exception to it. | X-n |
| N-42 (X-c trace) | **Nothing in the app records WHEN money moved.** `Transaction.paid_at` is stamped `db.func.now()` inside the status seam (`status_seam.py:105`) and the API refuses any other value (`schemas/validation/transactions.py:62` is `dump_only`); the only entry-creation door posts a HIDDEN `entry_date` fixed to today (`_transaction_entries.html:179`). So the balance engine's ACTUAL clock is a data-entry click. Measured on the real Checking account: `paid_at - due_date` is median **2 days**, p75 **6 days**, max **25 days**, and **81 of 130** settled rows were marked in same-minute batches (one batch of 6 spanning due dates 04-09..04-23). The corrections this produces swing `+/-$1,000` a month against a true four-month net of **`-$159.73`** -- the amounts are right and the dates are guesses. Not introduced by the fold (the posted ledger dates cash the same way); made VISIBLE by it, since the Reconciliation row is where the noise lands | `$36,323.99` gross swing vs a `-$159.73` true error; headroom measured at `$4,643.94` from entry-dating alone | **RULED 2026-07-25 (R-N)**: cut over first; X-f records the real dates after X-d | X-f |
| N-40 (X-b) | **`live_amount_overrides` reads the wall clock, so a fold given an explicit `as_of` is not fully as-of-pure.** `loan_payment_service.live_loan_transfer_amounts` calls `date.today()` (after two early-outs, so only for a derive-mode loan-payment transfer shadow) to resolve the loan's current P&I + escrow. The cash fold takes a pinned `as_of` and threads that map into its PLANNED tier, so a historical read values such a shadow at TODAY's loan state rather than the state at `as_of`. Bounded: the planned tier only contributes to dates after `as_of`, and this is inherited UNCHANGED from all three shipping producers (every one of them builds the same map), so X-b introduces nothing -- but the fold is the first cash producer to carry an explicit as-of at all, which is what makes the impurity visible and worth naming before X-c makes it a rendered number | latent; scoped to derive-mode loan-payment transfer shadows on a historical read | recorded **Its stated home 'X-c' is STALE -- X-c has SHIPPED, and the finding was RE-VERIFIED LIVE 2026-07-27**: `loan_payment_service.py:864` calls `date.today()` and `_cash_fold.py:482` feeds that map into a fold holding a pinned `as_of`. **TRIAGED 2026-07-27 (ruling R-AO): to X-i2**, whose whole subject is a loader consulting a clock the pass did not give it. | X-i2 |
| N-45 (X-c1) | **A checker unit test is green only because a DIFFERENT test class in the same file warms astroid's module cache.** `TestShekelPackagePrivacyChecker::test_allows_seam_submodules_importing_each_other` parses `from app.services.balance_at._context import _memoize_once` inside a synthetic `app.services.balance_at._plan` and asserts NO message. The checker's `_names_a_module` resolves the base through astroid, and under pytest's `tools/pylint/tests` rootdir the real `app` package is NOT importable -- so a cold cache raises `AstroidBuildingError`, the checker fail-CLOSES (correctly, by design), and the assertion fires. It passes only because `TestShekelBalanceSeamChecker` runs first in the same file and `astroid.parse(module_name="app.services.balance_at._context")` REGISTERS its synthetic module under that real dotted name, so the later resolve hits the cache. **Reproduced deterministically at HEAD, independent of this step:** the class alone fails 3/3 (both `./scripts/test.sh` and serial `-c /dev/null`), the whole file serially passes 5/5, and the whole file under `pytest.ini`'s `-n 12 --dist=loadgroup` fails ~2/3 depending on which worker gets the class. **The merge gate is NOT at risk** -- CI and pre-commit both run `pytest tools/pylint/tests -c /dev/null -q`, serial and whole-file (`ci.yml:186`, `.pre-commit-config.yaml` `shekel-checker-tests`), and `pytest.ini`'s `testpaths = tests` excludes the directory from the default suite. The honest fix is for the test to stop depending on a cross-class cache side effect (give the synthetic importer a real `path=` so `_importer_file_inside` decides it, as the checker's own file-arm tests already do). Out of scope here: a test-isolation defect in a file this step does not touch | a gate's own suite green by accident; ~2/3 flake under xdist | recorded, deferred **TRIAGED 2026-07-27 (ruling R-AO): to X-h.** | X-h |
| N-56 (X-c2b1) | **The desktop grid's two self-refresh endpoints now compute the SAME per-period view, twice per `balanceChanged`.**  ``#grid-summary`` (the sticky ``<tfoot>``) fires ``/grid/balance-row`` and ``#grid-subtotals-income`` fires ``/grid/subtotal-rows``, and since X-c2b1 both read one ``grid_balance_view`` -- which is what makes ruling R-K's identity survive the live swap, but means the browser pays for the projection twice.  Measured on the prod-shape clone 2026-07-26 (real Checking, 60 periods, 5 runs): per endpoint ``272.3 -> 165.4 ms`` (balance row) and ``87.9 -> 165.6 ms`` (subtotal rows), so the PAIR is ``360.2 -> 331.0 ms`` -- no aggregate regression, because the balance row stopped building a second override map, but the duplication is now visible and avoidable.  The fix is the pattern ``subtotal_rows`` already uses for its own two ``<tbody>`` blocks: let the balance-row response carry the two subtotal sections as ``hx-swap-oob`` fragments, so ONE GET refreshes the whole reconciling block and the rows are one response as well as one row set.  Not done here because it changes the refresh topology (a user-visible behaviour change in a commit whose contract is "the rendered grid is unchanged") and it has to clear the ``<template>`` parser constraint ``_balance_row.html`` documents | ``165.6 ms`` of duplicate producer work per refresh | recorded, deferred **Its stated home 'or X-c2b2' is STALE -- X-c2b2 has SHIPPED.** **TRIAGED 2026-07-27 (ruling R-AO): to X-i**, as its own commit and NOT closed by X-i1's memo -- these are two HTTP requests, so each builds its own context and a per-pass memo cannot reach across them. **Owner reworded 2026-07-27 (R-AQ): the parenthetical carried a retired vocabulary word.** It is X-i's work and X-i1's memo does NOT close it -- two HTTP requests, two contexts, so a per-pass memo cannot reach across them; its fix is the `hx-swap-oob` topology this row describes, shipped as X-i's own commit. | X-i |
| N-58 (X-c2b2 review) | **The analytics calendar renders a flow on one day and the balance step for it on another, with no row to explain the gap.**  A day cell shows BOTH its flow chips and its end-of-day balance.  The chips are placed on the BUDGET attribution date (`calendar_service._get_display_day`: `due_date` clamped into the period, falling back to the period start); the balance line is the fold, which steps on the day the money MOVED -- `paid_at`'s UTC civil day for a settled row, `max(attribution, as_of + 1)` for a still-projected one (ruling R-G).  They agreed by construction until X-c2b2, because the retired ramp distributed the same still-projected rows over the same attribution days.  **Measured (finding N-42, same data):** `paid_at - due_date` is median **2 days**, p75 **6**, max **25** across 130 settled Checking rows, so on production essentially every past chip is now displaced from its own balance step.  This is the split the GRID met and ruling R-K answered with the "Timing & true-ups" row; the calendar has no such row.  The step's own fixture demonstrates it: `test_flow_strip_low_trough_warning_cells` renders a `$600` chip on Jan 2 with `$1,000.00` under it and the `$400.00` drop on Jan 5.  The false docstring that still claimed the two share one clock is corrected at the site | chip and step up to 25 days apart; median 2 | **recorded, NOT fixed (developer ruling 2026-07-26: record, rule at its own step).**  The option space: (a) place the chip on the cash clock, which changes which MONTH a row appears in; (b) give the calendar R-O's treatment (a reconciling figure); (c) rule the divergence acceptable and label it.  Plan step X-f shrinks the date noise at its source, so ruling after it may change the answer **TRIAGED 2026-07-27 (ruling R-AO): to X-j**, and it KEEPS its own row's sequencing -- it is ruled after X-f, because X-f shrinks the date noise that defines the answer. X-j closes the rest of the cluster without it. **RE-TRIAGED 2026-07-27 (ruling R-AQ): there is no deferred category: to X-p, its own step, SEQUENCED after X-f and not deferred behind it.** X-f shrinks the date noise at its source, so ruling before it decides the question against numbers X-f then changes -- a schedule with a stated reason, which is what a deferral is not. X-j decides which PRODUCER this surface reads; X-p decides which CLOCK its chips sit on, and whichever ships second re-verifies the first. | X-p |
| N-65 (X-c2b2) | **The suite's frozen clock does not reach the DATABASE clock, so a fixture's settle lands months after the read that is supposed to see it.**  `tests/test_services` freezes today to 2026-03-20, but `status_seam` stamps `paid_at` with `db.func.now()` and `AccountAnchorHistory.created_at` server-defaults the same way -- both the real wall clock.  Every producer before the cutover read the LATEST anchor row and ignored its date, so nothing noticed; the fold dates every event, so an unpinned settle or assertion lands outside the seeded period range entirely and contributes to nothing.  This is the archived N-8 / X-c2a shape a THIRD time -- a fixture's stored instant coming from the real wall clock while the test's own clock is frozen elsewhere (the loan walk's stamp, then `create_account`'s opening, now `paid_at`), and the lesson is the same one: a fixture whose clock disagrees with its own data builds a state production cannot reach.  Mitigated rather than closed: `tests/_test_helpers.override_anchor` and `conftest._pin_opening_to` stamp assertions inside their own period, and the suites that needed a dated settle pass `paid_at` explicitly | a fixture asserting against a state production cannot reach | recorded; mitigated per-fixture.  The structural fix is for the test clock to patch the DB default too, which is its own change **TRIAGED 2026-07-27 (ruling R-AO): to X-h**, whose entry records that this row's STRUCTURAL half (the test clock reaching the DB default) splits out if it proves larger than the other three combined. | X-h |
| N-14 (C6b) | **`contractual_schedule_from_origination` is computed twice per pass on the property page** -- once inside the (now-memoized) `ctx.loan_plan` and once in the equity chart's `_back_projection_by_month` (both call it for the same loan). Deferred (developer ruling): pure-CPU (no query), only 2x, property-page only, and a full dedup via a fourth context memo must FIRST prove the two call sites' rate-change inputs are identical (`load_rate_changes(id)` vs `resolved.context.rate_changes`) -- a correctness check better done in its own focused change | -- | recorded, deferred **Its stated home 'or Phase D' is STALE -- Phase D is COMPLETE and archived.** **TRIAGED 2026-07-27 (ruling R-AO): to X-i1**, where the fourth per-pass derivation joins the other three on one context memo. Its own condition still holds and is X-i1's work: prove the two call sites' rate-change inputs identical BEFORE deduplicating them. | X-i1 |
| E2 | **RATIFIED 2026-07-26 -- promoted OUT of this ledger and back into Section 5 as a committed step; see "Phase E2" there for the scan, the three open questions and the sequencing.** The row stays so the id resolves. Original text: **The super-package boundary: the option that would dissolve the last name-keyed gate.** Move the read seam, the write cluster and the shared leaves (`loan_ledger` / `cash_ledger`) under ONE package whose shared internals are private to it, so the W9909 classification registry -- the last name-keyed surface -- dissolves structurally the way the W9906 call allowlist already did. Large reorganization with its own arrow risks (the D0b class, where scoping the step showed it would ADD four fence entries); W9910's per-boundary membership would need extending | -- | **OPEN, recorded, NOT committed to** (developer ruling 2026-07-24). The registry's residue is small, fail-closed and self-attest-pinned, so the reorg must earn its churn on its own merits. Recorded so the option cannot be forgotten. Converted from a Phase E step to a finding at the 2026-07-26 trim, because it is an option and not a commit. **CLOSED as a finding and RE-PROMOTED to a step the same day**: the developer ruled the fences are to become structurally unnecessary, which is the one argument the 2026-07-24 ruling did not weigh -- it asked whether the reorg earned its churn on correctness grounds, and it does not; it earns it on the standing goal. Sequenced LAST because every structural step ahead of it deletes code it would otherwise move and then delete (measured at the step) | Section 5, Phase E2 |
| N-72 (X-c2c trace) | **A modeled asset's balance is three producers merged by a preference order; the window that would have compensated for it is CANCELLED and the merge is DELETED instead.** `_merge_balance_sources` (`_investment.py:395-424`) picks forward projection, else the cash base, else reverse projection, per period. Finding N-43 is a bug in that preference order -- the fold, being TOTAL, always has the period, so it always wins and silently replaces two RULED pre-anchor models. Two fixes exist: keep the base out of the merge's way (the window), or have no merge (one replay). **X-c2c3 was to ship the window; ruling R-V (2026-07-26) CANCELLED it and X-g ships the replay instead** -- so this row records a band-aid that was recorded, priced and then NOT paid for, which is the outcome this ledger exists to make possible. Also measured here, and NOT introduced by X-c2c: one `/savings` render builds the modeled base **14 times for 4 accounts** (3x per IRA from two general `build_maps` passes plus retirement's own, 1x more from `investment_seed_map`, 2x for the Home) and `/investment` **4 times for one account** -- a pre-existing redundancy whose cause is upstream (consumers not sharing a read pass), which the developer ruled recorded, not fixed, at X-c2c | `-$6,315.57` of net-worth history is what N-43 measured the preference order silently rewriting | **recorded; NO compensator ships (ruling R-V), and the merge is DELETED at X-g.** The `/savings` and `/investment` redundancy half of this row is UNAFFECTED by R-V and stays open **TRIAGED 2026-07-27 (ruling R-AO): the merge half is X-g4's deletion; the `/savings` + `/investment` REDUNDANCY half is X-i1's** -- one `/savings` render threads ONE context (`_data.py:67` -> `_orchestrator.py:95`), which is what makes a per-pass memo reach all 14 builds. **The MERGE half CLOSED at plan step X-g4b (`17c57cde`)** -- `_merge_balance_sources` was deleted with its module. What is left of this row is the REDUNDANCY half alone: a per-pass memo's work. **Its `14 builds for 4 accounts` figure is a PRE-X-g2b measurement and is not the count today** -- one of the 14 was `investment_seed_map`, which X-g2b deleted outright, and the producer the rest were counted through (`_investment.build_investment_balance_map`) went at X-g4b. X-i1 re-measures before it designs a memo around it. | X-i1 (the redundancy) |
| X5 | **Anchor `effective_date`: an optional feature, not a step.** An `AccountAnchorHistory` row is dated by its `created_at` -- the instant it was ASSERTED -- so a user cannot enter a balance they read off last month's statement and have it land on last month. Adding an `effective_date` column would separate "when this was true" from "when it was typed", which is what a backdated statement assertion needs. Nothing depends on it: every shipped step and every remaining one (X-c2c .. X-g) works on the assertion instant | -- | **OPEN, optional, NOT committed to.** Converted from a step to a finding at the 2026-07-26 trim, on the same ground as E2: an optional feature nothing sequences against is a recorded option, not a commit -- and as the last numeric ID in a letter-suffixed scheme it read as a step whose position in the order was ambiguous. Its old text also said "NOT a prerequisite for X-a .. X-e", which had gone stale twice over (X-f and X-g did not exist when it was written) **TRIAGED 2026-07-27 (ruling R-AO): to X-e**, which was widened to decide it. It may still be declined; it may not be left unasked, because it is a question about the same table X-e is deciding the shape of. | X-e (widened 2026-07-27) |
| N-79 (X-g2 trace) | **The investment chart's projection axis and its contribution timeline are on two different calendars, so the chart answers differently depending on the day it is opened.** `_assemble_chart_context` projects over SYNTHETIC periods starting at `date.today()` (`growth_engine.generate_projection_periods`, `investment_dashboard_service.py:721-723`), while `ctx.contributions` is `build_contribution_timeline(..., periods=all_periods)` -- REAL pay periods, each `ContributionRecord` dated on its period's `start_date` (`investment_projection.py:639`). `growth_engine._project_one_period` looks a record up by the projection period's own `start_date` (`:399`), so the two align only when today IS a pay-period start: on a payday every synthetic period inside the real horizon matches its record and the chart applies the RECORDED amounts, and on the other thirteen days none matches and every period falls back to the flat `periodic_contribution`. Same account, same inputs, two answers | not yet measured in dollars; the gap is `recorded amount - periodic average` per period over the real horizon, and it is `$0.00` on today's data because no account has a contribution feed at all (ruling R-R) | **recorded, NOT fixed.** It is inside the forward WHAT-IF engine ruling R-U deliberately KEEPS, not the balance path, so X-g touches neither half -- but it is the same two-clocks-in-one-figure shape as plan step C6c-ii's double count and the plan's Section 8 lesson names it ("when a rule says period, ask if it means instant"). The honest fix is for the synthetic axis to carry the recorded feed by DATE rather than by period identity. **Its NEAR half closes at X-g2b as a side effect of ruling R-AF**: an axis opening the day after the current period's end lands on the real pay-period boundaries exactly (verified `2026-07-30..2026-08-12` against real period 9's own dates on both databases), so every synthetic period inside the real horizon matches its record whatever day the page is opened. The FAR half survives -- past the user's last pay period there is no record to match, which is where the flat fallback is the honest answer anyway **TRIAGED 2026-07-27 (ruling R-AO): the surviving FAR half is genuine RESIDUE.** Its near half closed at X-g2b via ruling R-AF; what is left is one chart's synthetic axis past the real pay calendar, sharing a root with nothing else here. **RE-TRIAGED 2026-07-27 (ruling R-AQ): there is no deferred category: the surviving FAR half goes to X-l**, which shares its root with N-82 -- past the materialized pay calendar the app improvises, and `_project_one_period`'s lookup IS by date (`:399`), so there is simply nothing out there to match. Its near half closed at X-g2b via ruling R-AF. | X-l |
| N-73 (X-c2c trace) | **Five balance sites guard against a NULL anchor on two `nullable=False` columns.** `Account.current_anchor_balance` and `current_anchor_period_id` are both `nullable=False` (`app/models/account.py:91`, `:100`) with a `current_anchor_balance IS NOT NULL` check constraint (`:55`), and there are **0 NULLs across all 19 account rows in both databases**. Yet `_kernel.build_account_balance_map:508`, `_kernel.base_account_balance_map:341`, `_kernel.interest_projection_for_account:395`, `_kernel.interest_by_period_for_account:440` and `_investment.get_anchor_period_index:127` each branch on `is None`, and two of them return a `None` / empty map that every caller must then handle -- so a state the schema refuses is propagating optionality through the seam's signatures (line numbers re-verified 2026-07-26; the originals drifted by two at `c649b322`) | -- | **recorded, NOT fixed** (out of X-c2c's scope, rule 6). It is not merely dead: X-g removes the anchor PERIOD from the balance paths entirely, at which point four of the five guards have nothing left to test. **Confirmed at X-g2's trace and re-scoped**: the replay reads no anchor period at all, so from X-g2b the guards test a state the schema refuses AND that the producer beneath them no longer consults -- but deleting them changes `balance_map`'s `\| None` contract, which every net-worth consumer handles, so they stay for X-e. **Corrected at X-g2b's trace: FIVE becomes FOUR**, because `base_account_balance_map` deletes with the ladder (ruling R-AD) and its guard at `:341` goes with the function rather than surviving as one of the five **Owner CORRECTED 2026-07-27: it cited `X-g2b`, which is TICKED** -- the second row X-h's ledger gate would have failed on. The NEED closed there; the GUARDS are X-e's. | X-e (widened 2026-07-27) |
| N-82 (X-g2b trace) | **Past the pay-period horizon the replay's ACCRUAL keeps running while its CONTRIBUTION tier stops.** A contribution event is dated on a real payday (`_asset_contributions._dated_events` walks `pay_period_service.get_all_periods`), and there are no paydays past the user's last pay period; the accrual window's end is the caller's own furthest requested date. So a date beyond the calendar reads growth-only. Today's producer has the opposite wart -- `_kind_correct.balance_at` resolves the date to a period and `find_period_containing_date` falls back to the latest period that ENDED earlier, so a past-horizon date clamps to the final column. Measured at 2029-01-01, six months past a horizon ending 2028-07-12: Empower **`+$2,501.92`**, Roth `+$1,754.08`, Property `+$5,427.07`, Money Market `+$272.24` | `+$5,427.07` at six months past the horizon; `$0.00` on any rendered surface | **RULED at R-AG (2026-07-27): let the fold answer and record this.** Not live -- the only non-loan caller of that scalar in production is the `/investment` hero cell, at today (the other four call sites are loan-gated), so nothing reads a past-horizon modelled date. The honest fix is a payday cadence that outlives the calendar, which is materially larger than X-g2b and belongs with whatever step extends the pay-period horizon **TRIAGED 2026-07-27 (ruling R-AO): genuine RESIDUE.** Already RULED (R-AG) as record-not-fix, and its honest fix -- a payday cadence outliving the calendar -- belongs to whatever step extends the pay-period horizon, which no step here is. **RE-TRIAGED 2026-07-27 (ruling R-AQ): there is no deferred category: to X-l, and R-AG's record-not-fix half is SUPERSEDED.** R-AG's three grounds were one concession ('correct in principle') and two costs ('invents a calendar the app does not have', 'materially larger than this step'); cost is no longer a ground. R-AG's OTHER half STANDS and is what keeps X-l safe -- the fold stays TOTAL and is never clamped at a horizon. The honest fix R-AG itself named, a payday cadence that outlives the calendar, IS X-l. | X-l |
| N-83 (X-g2b trace) | **A Property's value is answered two ways on adjacent screens, and X-g2b widens the gap.** `/savings` net worth and the grid read the modelled map (which appreciates from the latest assertion's own day, ruling R-Y); the property detail page's equity HERO (`home_equity_service.resolve_home_equity:137`) and its equity CHART (`property_equity_chart._value_series`) BOTH read `Account.current_anchor_balance` -- the denormalized CACHE column -- and deliberately flat-carry it through today ("the last honest value"). Measured on `shekel`: the modelled current-period value is `$350,794.53` today against `$350,000.00` flat on the property page, a **`$794.53`** gap that X-g2b widens to **`$965.03`**. It is ruling R-W's shape on a different pair of surfaces (one account, two producers, no row explaining the difference), and it is also cash D4's cache column reaching a screen | **`$965.03`** after X-g2b, growing with the time since the last assertion | **recorded, NOT fixed -- and X-g2b TRIED and reverted, which is the row's most useful content.** Pointing `resolve_home_equity` at the seam inside the cutover produced two new defects its adversarial review caught: the equity CHART then double-counted the appreciation between today and the current period's end (its value became as-of the period end while `_value_series` still compounded from `today`), and the hero netted a period-end value against a today-dated debt leg. The second could not be fixed without moving the loan tile's own date convention as well, which is a third surface. So this stays PRE-EXISTING -- X-g2b changes its size, not its existence -- and the cross-page tests now assert the gap EXPLICITLY from both sides, so it cannot drift and this finding's own commit must update them. Its resolution is R-W's: one producer, with the difference rendered rather than implied, and ONE date for the whole property page. Note both readers take the CACHE column, so plan step X-e touches them too. **X-g3b ADDED a THIRD surface to the disagreement without widening it** (shipped 2026-07-27): the grid's current column for the Property is now `$350,965.03` on `shekel`, which is `/savings`' own figure to the cent (900 of 900 pairs across both databases), against the property page's flat `$350,000.00` -- so this row's resolver now has three readers to reconcile, not two **TRIAGED 2026-07-27 (ruling R-AO): the row SPLITS.** Its DISPLAY half -- one account answered two ways on adjacent screens with no row explaining it -- is X-j's, alongside N-87, because it is the same defect on a third surface pair. Its CACHE half -- both property readers taking `Account.current_anchor_balance` -- is X-e's. Neither half closes without the other, so whichever ships second must re-verify the first. | X-j (display) / X-e (cache) |
| N-85 (X-g2b) | **`interest_by_period_for_account` has no production caller and survives on its own tests.** The account-detail page reads `interest_projection_for_account` for BOTH halves of its projection (the balance chart and the "Interest, next 12 mo" chip) since finding N-64 collapsed the two seam calls into one; this sibling entry -- which returns the interest map alone -- is exported from the seam, exercised by 5 test call sites in 3 files, and called by nothing in `app/`. That is the dead-code-alive-for-its-own-tests shape plan steps C3b4 / D2a / F2 / E1e each deleted. It was PORTED to the replay at X-g2b rather than deleted, deliberately: that step's contract is to move producers onto one event stream, not to prune the seam's public surface (rule 6), and deleting a public entry with its tests is a different commit's work | -- | **recorded, NOT fixed.** Delete it with the rest of the incumbent at plan step X-g4, which is already reading this list; a two-line port is the cheap way to keep it honest until then **Owner CORRECTED 2026-07-27 (it cited `X-g4`, which is now TICKED) and the row is DE-DUPLICATED: this and N-96 are ONE defect**, found twice -- N-96 by X-g4b's adversarial review, which did not notice this row. X-g2b (`560b3339`) PORTED the entry to the replay and X-g4b did not delete it, so the name is still exported and still callerless -- AST-verified on 2026-07-27: zero `app/` calls, 5 test call sites in 3 files. N-96 carries the extra fact (the `__init__` sentence that names it as a surface two screens read) and both now point at the same owner; whichever ships closes both. | X-e |
| N-87 (X-g3 trace) | **The dashboard pulse justifies its cash basis by agreement with a grid that stopped agreeing at PR #47.** `dashboard_pulse_service.compute_pulse_section` (`:75`) reads the seam's kind-blind `cash_balance_map` (`:146`) and its comment gives three reasons (`:123-133`); the middle one is that the kind-correct map would "accrue interest into an HYSA's chart, amortize a loan, or compound an investment, **diverging from the grid that deliberately keeps the SAME account on the cash-flow view**". The grid has rendered the INTEREST-accrued balance since PR #47, so that clause has been false for ONE of the three kinds it names (interest) since PR #47 (`87cfdc5e`, 2026-06-28).  The review corrected an earlier "two of the kinds": INVESTMENT is still on the cash basis on BOTH surfaces today, which is X-g3's whole premise, and an AMORTIZING loan is refused by `account_resolver.is_cash_flow_account:41` so it reaches neither. **The two surfaces read at DIFFERENT dates and are measured apart** (corrected from the review, which caught the first draft conflating them).  The HERO reads `cash_balance_at(account, ctx, current_period.end_date)` -- the CURRENT period -- and diverges from the grid's current column by **`$55.88`** on the Fidelity Savings and `$17.79` / `$5.95` on the Money Market (`shekel` / `shekel_f3_final`).  The PULSE's forward trough/peak scan runs the whole horizon, where at the last projected period the cash view answers `$5,363.56` against the grid's `$5,779.68` (**`$416.12`**, identical on both databases) and `$16,644.27` against `$17,348.99` (**`$704.72`** on `shekel`; `$16,159.51` against `$16,819.41`, `$659.90`, on `shekel_f3_final`).  Neither surface carries a row explaining the difference.  **It is not hypothetical on the developer's own data: `resolve_grid_account` returns the Empower 401(k) on `shekel` today**, so after plan step X-g3b the DEFAULT `/dashboard` hero reads `$31,070.06` while the DEFAULT `/grid`'s current column reads `$31,751.40` -- the same account, the same period, two pages. The same two producers (`dashboard_service.py:97` `cash_balance_at`, `dashboard_pulse_service.py:146` `cash_balance_map`) feed `/dashboard`'s hero and its runway chart, and `calendar_service.py:689` / `:889` feed the analytics calendar the same way; all three are reachable for a modelled account, because `resolve_grid_account` and `resolve_analytics_account` admit every non-amortizing kind (`account_resolver.is_cash_flow_account:41`). Plan step X-g3 extends the same gap to `$6,263.60` / `$2,662.70` / `$17,776.85` on the three investments and `$21,856.66` on the Property. **The adversarial review found it is worse than "two adjacent screens", and this is the row's sharpest evidence:** the pulse's "Lowest point ahead" and "Highest point ahead" chips each carry a `view in grid` LINK (`templates/dashboard/_pulse.html:75-95`) built from `url_for('grid.index', offset=...)` with **no `account_id`**, so the grid re-resolves through the very same `resolve_grid_account` the dashboard used. One click joins a captioned figure to a different Decimal for the same account and the same period -- and the pulse chart's own `aria-label` says "Projected end balance for the next six months" (`:104`) against the grid row literally titled "Projected End Balance" (`_balance_row.html:88`) | **`$704.72`** live today; **`$21,856.66`** after X-g3, at the last projected column | **RULED at R-AK (2026-07-27); BOTH false clauses are now deleted -- the pulse's at X-g3a `320a4641`, and `dashboard_service`'s at X-g3b, which the X-g3a review MISSED even though this row named both producers by line.** The corrections shipped with their measured figures in the comments, so neither surface justifies its basis by an agreement that ended at PR #47. **The divergence is now LIVE on the developer's own default screens:** `resolve_grid_account` returns the Empower 401(k) on `shekel` (the saved `default_grid_account_id`), so the default `/dashboard` hero renders `$31,070.06` at the current period against the default `/grid`'s `$31,751.40` -- **`$681.34`**, same account, same period, one click apart. The pulse's OTHER argument is untouched by ruling R-W and was never weighed against it -- modelled growth inflates the "lowest point ahead", so a real future dip below zero could be hidden, which is a RUNWAY-safety property of the question `/dashboard` asks and not of the question the grid asks. Deciding it needs its own measurement (how often does an HYSA's accrual lift a trough above zero on real data?) and its own ruling, which is a different commit's work. It is finding N-76's shape on a second pair of surfaces -- one account, two producers, no row explaining the difference; CLOSED at X-g3b, register in `archive/cash_arc_as_built_2026-07-27.md` -- and the calendar half sits beside finding N-58. **One option R-AK's rejected list did not contain, surfaced by the review and left for this row's own commit:** if the two SERVICES legitimately answer different questions, the defect is the NAVIGATION that equates them -- so re-targeting or dropping the trough / peak `view in grid` links when the dashboard account models a return is strictly smaller than either option R-AK weighed, and does not touch a balance producer at all **TRIAGED 2026-07-27 (ruling R-AO): to X-j**, which it anchors -- it is the largest live contradiction the arc has left, and X-j does NOT pre-empt the runway-safety fork this row records: that is ruled at X-j's trace, before any code, with its own measurement. | X-j |
| N-89 (X-g3 trace review) | **The modelled contribution tier re-queries the whole pay-period calendar that its caller already loaded.** `_asset_contributions.contribution_events` ends in `pay_period_service.get_all_periods(account.user_id)` (`:250`), and that function is UNMEMOIZED -- a fresh `SELECT ... ORDER BY period_index` on every call (`pay_period_service.py:207-212`). Every grid entry has already loaded exactly that list and passed it in (`routes/grid.py:188`, `:740`, `:820`, `:877`), as has every `/savings` and `/investment` reader, so an INVESTMENT account pays for the calendar twice per read -- and one `balanceChanged` fires two grid endpoints that each rebuild the whole view (finding N-56), so 4 calendar loads of which 2 are redundant per interaction. **It is not fixable by taking the periods as an argument, and that is the point:** the tier LOADS rather than TAKES precisely because Section 8's "an argument a caller can get wrong is a defect, not a contract" was paid for here -- the cash fold once took the period list its visibility rule needed and a caller passed a WINDOW, moving a balance by `$150,000.00`. The honest fix is a memo on the read pass's `BalanceContext`, the shape `ctx.loan_plan` already has, so the list is loaded once per request and cannot be a caller's choice | 1 redundant calendar query per modelled read; 4 per grid `balanceChanged` | **recorded, NOT fixed.** Out of X-g3's scope (rule 6) and shared with `/savings`, so it belongs with whatever step memoizes the calendar on the context -- the same place finding N-14's second `contractual_schedule_from_origination` call is waiting **TRIAGED 2026-07-27 (ruling R-AO): to X-i1.** | X-i1 |
| N-90 (X-g3 trace review) | **Ruling R-K's identity is a property of the construction only in its BOUNDARY form; the form the screens render needs contiguous ordered periods and is unverifiable at the leftmost column.** The producers state the boundary form and it is airtight: `AssetPeriodFigures` (`_asset_fold.py:285-292`) and `CashPeriodFigures` (`_cash_fold.py:539`) both value each period over its OWN span `(start - 1 day, end]`, which is why `_asset_fold.asset_period_view`'s docstring can promise the periods "need be neither contiguous nor ordered" (`:716`).  `cash_period_view`'s own promise is narrower still -- "need not be contiguous and need not start at the account's anchor" (`_cash_fold.py:665-666`), with no order clause at all, which strengthens this finding rather than weakening it (the review corrected the attribution). What R-K, R-W, R-AH, the templates and `_assert_grid_view_reconciles` (`test_balance_at.py:2311-2323`) all actually use is the COLUMN-TO-COLUMN form `balance[p] - balance[p-1]`, which additionally requires `balance[p-1] == balance(p.start - 1 day)` -- true only when the rendered set is contiguous and in order. Nothing enforces it: the templates iterate whatever `periods` they were handed, and the test zips adjacent entries and assumes it. It holds today because all four grid entries pass `all_periods`, so this is caller discipline standing in for a structural property, which is the exact substitution this arc exists to remove. Second half: the leftmost rendered column has no predecessor, so its identity is unverifiable ON SCREEN in every window -- including `?periods=1`, the mobile This Period arrow-nav state, where it is the ONLY column | none measured (every production caller passes the full contiguous set) | **recorded, NOT fixed.** Not introduced by X-g3 -- it has been R-K's shape since X-c2b1 -- and closing it means either rendering the boundary form or having the view state its own contiguity, both of which are changes to what the grid displays rather than to what it computes **TRIAGED 2026-07-27 (ruling R-AO): to X-j**, whose whole subject is the RENDERED identity rather than the computed one. | X-j |
| N-91 (X-g3b-0 review) | **The modelled-contribution feed is measured against a clock nobody pinned, and the seam owns the handle it does not pass.** `_inputs._contribution_inputs_for_accounts` calls `income_service.get_current_gross_biweekly(user_id)` with neither of that function's two keywords (`income_service.py:54-58`), so the employer-match basis resolves against the helper's own implicit `date.today()` (`:120`) and against the user's first `is_active` salary profile ACROSS ALL SCENARIOS (`:110-116`), rather than against the read pass's pinned `ctx.as_of` / `ctx.scenario`. The deduction half is scenario-blind for the same reason (`projection_inputs._active_deductions_query:193-202` filters user and active only). It is the unnamed-clock shape `_kind_correct.balance_at` describes in its own "two dates, deliberately distinct" note (`:236-239`) and that `BalanceContext.build`'s `as_of` parameter exists to remove (`_context.py:180-186`) -- so a HISTORICAL read (`BalanceContext.build(user_id, as_of=<past>)`) models an account's contributions at TODAY's gross. **Measured on both databases: the gross is `$3,631.74` at today, `$0` at 2026-01-15 (before the first pay period) and `$3,722.53` at 2027-06-30** (the post-raise figure), so the value genuinely moves with the date it is not given; the scenario half is latent (one active profile, same answer either way). PRE-EXISTING and not a regression -- the retired `_AssembledInputs` took a `ctx` and never threaded it either -- and inert at HEAD, because the only non-default `as_of` in `app/` (`tax_report_service.py:373`) reaches `loan_interest_in_year` and never the contribution feed | `$90.79` per period of gross basis between today and a 2027 read; `$3,631.74` against `$0` for a pre-horizon one. `$0.00` live today (no historical-`as_of` caller reaches the feed) | **recorded, NOT fixed.** Threading `ctx` would CHANGE which profile is picked and which period the gross is measured at, which moves money -- so it needs its own measurement and its own ruling, not a slot inside a refactor that proved itself byte-identical. The parameter was NOT kept against that future fix: an argument nothing reads is one a caller can get wrong (Section 8), and `_contribution_inputs_for_accounts`' docstring now states this cost rather than claiming the feed is context-free -- which is what the review corrected **TRIAGED 2026-07-27 (ruling R-AO): to X-i2**, the money-moving half, which is exactly the trace-and-rule this row asks for rather than a slot inside a refactor. | X-i2 |
| N-92 (X-g3b-0 review) | **The contribution feed is the seam's one un-memoized per-pass derivation, and it is the most expensive loader in the set.** `_contribution_inputs_for_account` issues an investment-params query, a deductions query and a FULL paycheck-engine run (`get_current_gross_biweekly` -> `load_tax_configs` + `get_all_periods` + `calculate_paycheck`) on every call, with no cache. **Measured, best of five on both databases: `9.3-9.5 ms` for an INVESTMENT account and `0.0 ms` for a PLAIN one** (the loader skips the engine when no account in the set has params, so the cost is investment-only). `retirement_projection._resolve_seed_balances` (`:567-571`) loops `balance_at.balance_at` over every account in its context and every one of them is a retirement account, so that is one engine run per account -- on top of the one `build_maps` already did at `:498`. The seam memoizes loan resolutions, plans and payoffs on the context precisely to end this shape (`_context.py:136-164`; `resolved_loan`'s "ELEVEN times for two loans" note at `_resolution.py:136-140`); the feed is what is left. PRE-EXISTING: `_assemble_inputs([account], ctx)` cost exactly the same | `~9.4 ms` per investment account per seam read; `~28 ms` per pass on the developer's three real investments | **recorded, NOT fixed.** Out of X-g3b-0's scope (rule 6), and it shares a fix with finding N-91 rather than competing with it: a `ctx.contribution_feeds` memo through `_memoize_once` is the same shape `ctx.loan_plan` already has, and the commit that adds it is the natural place to decide what clock the feed is loaded at. Note it also shares a home with N-89 (the calendar re-query) and N-14 (the second `contractual_schedule_from_origination`) -- three per-pass derivations waiting on one context memo **TRIAGED 2026-07-27 (ruling R-AO): to X-i1.** | X-i1 |
| N-93 (X-g3b review) | **Every grid render entry now pays the modelled contribution load, including the one that reads none of it.** Plan step X-g3b made `grid_balance_view` load the account's real `ContributionInputs`, which for an INVESTMENT costs an investment-params query, a deductions query and a full paycheck-engine run: measured best-of-five on both databases at `2.7 -> 14.8 ms` (an APPRECIATING asset `2.7 -> 3.7 ms`; PLAIN and INTEREST inside run-to-run noise). The grid has FOUR render entries and each pays it. **The sharpest sub-case is `subtotal_rows`**, which resolves the whole modelled fold to render three rows that read only `income` / `expense` / `net` -- and plan step X-g3a's own entry declined to hand that endpoint an `accrual_label` because "handing it a label would add a context variable nothing reads", which is the same argument one level cheaper. It also compounds finding N-89: `income_service.get_current_gross_biweekly` loads the pay-period calendar (`income_service.py:127`) on top of the route's own load and `contribution_events`', so a modelled grid render loads it THREE times where N-89 recorded two, and one `balanceChanged` firing `balance_row` + `subtotal_rows` costs ~6 loads and 2 engine runs | `~12 ms` per modelled grid render entry; 1 extra calendar load per modelled read on top of N-89's | **recorded, NOT fixed.** The load itself is not waste -- it is what the INVESTMENT kind's return is modelled FROM (ruling R-AJ (a)), so it cannot be gated away without reinstating the defect X-g3b removed. What is addressable is the per-pass repetition, and that is findings N-89 / N-92's shared fix (a context memo), plus a narrower view record for `subtotal_rows` if that endpoint's cost is ever measured to matter **TRIAGED 2026-07-27 (ruling R-AO): to X-i1.** | X-i1 |
| N-94 (X-g3b review) | **A per-kind cross-page control fires whether or not its injection lands.** `TestPerKindSeamInjectionLock.test_injected_divergence_is_caught` (`tests/test_integration/test_cross_page_balance_equality.py`) patches ONE reader to a wrong Decimal and asserts `_assert_surfaces_equal` raises naming that surface -- but it compares every surface against `ctx["V"]`, the fixture's ASSERTED value, and no surface has returned `V` since plan step X-g2b gave the anchor period its own accrual (ruling R-Y). So the assertion raises with the patch and without it, and the name/value checks then match against the "All surface values: {...}" dump rather than against the lock actually biting. It is the shape Section 7.3 exists to prevent: a negative control that cannot distinguish the state it is controlling for | none measured (the sibling `test_all_surfaces_equal` cases do the real work) | **recorded, NOT fixed.** PRE-EXISTING -- X-g2b moved the expected figure and left the control's comparand behind -- and untouched by X-g3b, which only added `grid` to the two reader dicts (verified: the lock still passes, and still for the wrong reason). The fix is to compare against `_modelled_current_balance(ctx)` the way the sibling equality tests do, at which point the unpatched run passes and the patched one fails **TRIAGED 2026-07-27 (ruling R-AO): to X-h.** | X-h |
| N-86 (X-g2b review) | **The `/investment` limit CARD and the projection beside it read two different YTD boundaries, and only one of them is a function of the window.** `_compute_limit_info` renders `ytd_contributions` (the total THROUGH the current period -- what the user has actually put in this year), while `growth_engine.project_balance`'s `ytd_contributions_start` must hold exactly the periods the projection's window EXCLUDES. Those coincide only when the window opens past the current period, which is the case on this surface after ruling R-AF and is NOT the case on either of `retirement_projection`'s axes. X-g2b resolves the projection's half once, beside the window it depends on (`_context._projection_ytd`), so the two call sites cannot disagree; what is recorded is that the app now carries TWO correct YTD boundaries whose difference is invisible in the rendered figures. The durable version is for the engine to take the axis and derive the boundary itself, which is the same "one derivation" argument ruling R-AF made for the seed | `$1,000.00` of annual-limit room per period of divergence, compounded over the horizon -- the defect this finding's fix closed, measured on a `$23,500` limit at `$1,000`/period with today in the year's 15th period. `$0.00` on today's data: no real investment account has a contribution feed | **recorded, NOT fixed** -- the boundary is correct on both surfaces today and pinned in both directions by `TestTheAnnualLimitSeedFollowsTheWindow`. It is recorded because a THIRD projection surface would have to know the rule to get it right, and knowing a rule is what this arc replaces with structure **TRIAGED 2026-07-27 (ruling R-AO): genuine RESIDUE.** Correct on both surfaces today and pinned in both directions; its durable fix is inside the growth ENGINE's axis, which no remaining step touches. **RE-TRIAGED 2026-07-27 (ruling R-AQ): there is no deferred category: to X-m.** 'Correct on both surfaces today' is a statement about how many surfaces exist; the row's own text says a THIRD would have to KNOW the rule, and knowing a rule is what this arc replaces with structure. X-m has the engine derive the boundary from the axis it is handed, and `ytd_contributions_start` leaves the signature. | X-m |

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
   behind that (164 citations in 49 files, plus the commit messages). **A step that ships must also
   re-point every Section 6 row that named it** -- rule 6's gate fails the build otherwise, which is
   the only reason this instruction is now reliable.
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

6. **EVERY finding has an OWNER, and the owner vocabulary is closed** (developer ruling R-AQ,
   2026-07-27). Section 6's last column is one of exactly three things:
   * **a live (unticked) Section 5 step ID** -- the normal case;
   * **`operator`** -- a question only the developer can answer from outside the code, with the
     question stated (FU-1 is the only one);
   * **`developer-decision`** -- the developer has taken a fork for their own session, dated, with
     the options named (N-25's class is the only one).

   **Retired as values: "own commit", "own step", "own arc", "if ever", "recorded, deferred",
   "residue", and any wake condition.** They all mean the same thing -- nobody -- and the count is
   why: 29 of 41 open rows carried one, four of them naming a step that had already shipped. **A
   finding is BORN with an owner**: the review or trace that records it assigns one in the SAME
   commit, and a finding with no owner is not recorded, it is unfinished.

   **An owner must be TICKABLE.** Every step ID cited as an owner is a CHECKBOX in Section 5, never
   a plain bullet -- otherwise "did its owner ship?" is unanswerable and the gate below has nothing
   to read. Applying this on 2026-07-27 found three IDs owning eight findings between them that had
   no checkbox at all (`X-g4`, and the new `X-i1` / `X-i2`); they were converted, and the two
   DECOMPOSED headers beside them (`X-g2`, `X-g3`) were ticked to match, the leaf convention
   `* [x] **X-g1**` having already been the page's practice everywhere else.

   **This rule is a GATE, not a discipline.** Plan step X-h ships a test that parses this document
   and fails on an owner that names a ticked step, an owner that names no step, an owner naming an
   ID that is not a checkbox, or an owner outside the vocabulary. Prose does not enforce itself --
   this file's own Section 8 says a safety that is a predicate is not a safety, and a rule with no
   gate is weaker than a predicate.

7. **A finding is not deferred for cost** (developer ruling R-AQ, 2026-07-27). "Materially larger
   than this step" is a reason to give something its OWN step, never a reason to leave it open. A
   finding that costs `$0.00` on today's data is not resolved; it is a defect waiting for the data
   to change, and the data changes without asking. Where a fix must follow another step to be
   decided correctly it is SEQUENCED behind it with the reason stated (X-p behind X-f) -- which is
   a schedule, and is what a deferral is not.
