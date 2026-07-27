# The cash balance arc: as-built record (Phase X, X-a .. X-g3b, 2026-07-25 .. 2026-07-27)

**Read-only history. Nothing here governs work.** The live document is `../README.md`, the plan of
record for the balance arc. This file is the as-built record of the Phase X commits that have
SHIPPED -- what each one did, what it measured, what its firing controls were, and which findings it
closed. Read it for how a shipped cash behaviour was decided and which commit shipped it; do NOT
read it as instructions.

**Extracted 2026-07-27 on the developer's instruction**, at 2,982 lines, so the plan of record
carries the work that REMAINS rather than the log of work that is done. It is the same operation the
loan half had at `loan_arc_as_built_2026-07-26.md`, with ONE difference that must be stated because
Section 9 rule 5 of the live document does not cover it: **the loan half was archived because it was
COMPLETE and in production. The cash half is NOT complete.** Phase X is in flight -- X-g4, X-c2c4
and eleven more steps remain, and nothing here has shipped to production. So this is a
shipped-so-far record inside a live phase, not the closing of a half, and the live document's Phase
X header stays where it is.

**What stayed in the live document, and why each had to:**

* **Every RULING (its Section 4).** A ruling made during a shipped step still governs the steps that
  have not shipped -- R-G's clamp, R-I's back-projection, R-K's identity, R-Z's contribution
  boundary. Moving them would put a live rule in a read-only file.
* **Every UNRESOLVED finding.** Unfinished work belongs where work is planned. Only the ten findings
  the shipped steps CLOSED are here, in Section 3 below.
* **The target shape and the loan regression baseline** (its Sections 2 and 3), because the
  remaining steps are held to both.
* **Every unshipped step**, including `X-g4` and `X-c2c4`, whose parent entries' shipped siblings
  are in Section 2 here.

**Two standing warnings, the same ones the loan record carries.** Dollar figures and line numbers
were true on their write date only -- several were already stale within days, because the Checking
figures moved with every re-anchor. And a step's own narrative is what its author believed when it
shipped: where a later step disproved a claim, the live document carries the correction and this
file does not.

---

## 1. The running state record, as it stood at 2026-07-27

This is the live document's entire preamble -- everything above its Section 1 -- extracted verbatim.
It was a chronological narrative of every shipped step, grown one paragraph at a time, and it is the
single largest thing this archive exists to remove: a reader opening the plan of record was met with
the log before the plan. Its only LIVE content -- what the document is, where the arc stands, and
what is next -- is restated compactly at the top of `../README.md`.

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

**X-g3's TRACE (2026-07-27) -- no code, four rulings, four findings, and the step DECOMPOSES.** Its
three recorded forks were RULED along with a fourth the trace itself found: **R-AH**, **R-AI**,
**R-AJ**, **R-AK** in Section 4, all as recommended. **The recorded third fork INVERTED from a cost
into a prerequisite**: `grid_balance_view` passes `ContributionInputs.absent()` today, and with that
token an INVESTMENT account models NO return at all -- `_asset_fold._modelled_return` reads the
CALLER's `investment_params` on that one arm (`_asset_fold.py:394-397`) where INTEREST and
APPRECIATING read the account's own rows -- so without the real assembly the step would move `$0.00`
for the entire kind and leave N-76 open (`$17,776.85` on the Empower today; the `$17,642.13` that
row was written with is its pre-X-g2b figure). Two more forks the entry did not have: the
kind gate can DELETE (the replay already decides it; measured `$0.00` on 60 of 60 PLAIN columns on
both databases), and `GridColumn.interest` can no longer BE `None`. R-W's identity as written was
re-measured from the grid's own producer and breaks on **53 of 59** period pairs, worst **`$181.59`**
a column; the four-term form BREAKS on **0 of 59** -- it holds on all 59 -- for every non-loan account on
both databases, and the
proposed grid balance equals `balance_map` on **900 of 900** (account, period) pairs -- 480 on
`shekel` and 420 on `shekel_f3_final` -- N-76 closing
byte-exactly rather than approximately. **Its adversarial reviews then found nine defects in the
step and three more findings**, the sharpest being that the control X-g3a proposed for its new row
CANNOT FIRE in that commit and that the accrual row is hard-coded success green on a tier the two
new kinds can drive NEGATIVE. Four findings recorded: **N-87** (a contract statement false since
PR #47), **N-88** (the green loss, fixed at X-g3a), **N-89** (a redundant calendar load) and
**N-90** (ruling R-K's rendered identity is by-construction only in its boundary form).

**X-g3a SHIPPED `320a4641` (2026-07-27) -- the SHAPE, and the baseline did not move.** The kind
gate STAYS, so only an INTEREST account resolves the modelled arm; what changed is the record the
templates read and the rows they render. `GridColumn.interest` became a non-optional `accrual`
beside a new `contribution`, `GridRowFlags` renamed and gained its third flag in the order the rows
RENDER, the three templates gained the Contributions row and the per-kind label on both form
factors, and the gate's other arm is now NAMED (`_cash_only_columns`) so X-g3b is a deletion rather
than a rewrite. **Byte-identical on both databases: ZERO changed shared keys across 17,148
(`shekel`) and 15,008 (`shekel_f3_final`) leaf figures**, the only deltas being the three the step
predicted -- `interest` -> `accrual` carrying all 120 real values unchanged on each, 360 / 300
`null` -> `"0.00"`, and 480 / 420 new `contribution` keys all `"0.00"`. Both loans UNMOVED at the
standing gate. **18 firing controls, every one shown to fire.** Finding **N-88** closed with it.
**THREE developer rulings were taken during the build and are recorded at the step: R-AL** (the
row's marker renames kind-neutral), **R-AM** (the sign treatment is three-way, NOT
`/investment`'s `>= 0` -- this document's own pin said "verbatim" and was wrong), and **R-AN** (the
two modelled rows render CENTS where the four rows around them render whole dollars). **Both
adversarial reviews found real defects**; the step's entry carries all seven.

**X-g3b-0 SHIPPED (2026-07-27) -- a prerequisite the plan did not have, and it moved nothing.**
X-g3b's entry told its implementer to build a FOUR-field `_AssembledInputs` and slice THREE fields
out of it; the developer read that as the shape it is and asked what a from-scratch design would do
instead. The measured answer: the fourth field's VALUE was read nowhere in the app -- its only use
was a membership test whose consumer re-derived the same resolution itself -- and that test was the
seam's SECOND spelling of a predicate the scalar wrote out longhand and the liability band split
into two guard clauses, with the equivalence recorded in a docstring rather than enforced. So the
bundle DELETED, a per-account `ContributionInputs` loader replaced it, and all three surfaces now
ask one `_resolution.configured_loan`. **Byte-identical harness on both databases**, suite 7659,
pylint 10.00. Two adversarial reviews confirmed the behaviour on every axis they attacked and found
three FALSE claims in the new docstrings, one of them load-bearing; findings **N-91** (the feed is
measured against an unpinned clock -- `$3,631.74` today against `$3,722.53` at a 2027 read) and
**N-92** (the feed is the seam's last un-memoized per-pass derivation, `~9.4 ms` an investment
account) are recorded, not fixed.

**X-g3b SHIPPED (2026-07-27) -- the grid renders the modelled balance, and finding N-76 is
CLOSED.** The kind gate and `_cash_only_columns` deleted; every kind reaches the replay and the
replay decides what it models. Every figure in the step's sign-off table landed to the cent on both
databases, and the blast radius is bounded structurally rather than visually: ONLY the `columns`
family moved (483 / 369 figures), and inside a column only `balance` / `accrual` / `contribution`
-- `income`, `expense`, `net`, `reconciliation`, `kind_correct_map`, `daily_series` and every
scalar moved ZERO, both loans included. **N-76 closes byte-exactly on 900 of 900 (account, period)
pairs.** Live-rendered: "Growth `+$121.37`" and "Contributions `$181.59`" on the Empower,
"Appreciation `+$397.69`" on the Property, "Interest `+$6.16`" on the Money Market, neither row on
Checking. The adversarial review found no Critical and no High on the cutover itself, and NINE
surviving statements of the retired contract -- including the SECOND false N-87 clause, in
`dashboard_service`, which X-g3a's review missed. New findings **N-93** (every render entry pays
the contribution load, `subtotal_rows` included) and **N-94** (a per-kind injection control that
fires either way).

**NEXT: X-g4** (the
deletion, which now also carries finding **N-95**'s doc sweep), then **X-c2c4** (the deletion
X-g's cutover is what makes possible, and which carries the 52-period drift-oracle PORT as a
prerequisite -- see the step), then **X-h / X-i / X-j / X-k**, then X-d / X-e / X-f, then E2.

**THE FINDINGS LEDGER WAS TRIAGED 2026-07-27 and Section 5 grew four steps (rulings R-AO and
R-AP).** The developer asked which of Section 6's rows the remaining steps actually close and which
need a plan of their own. Counted and then measured against the CODE: 51 rows, 10 CLOSED, 41 open;
of the 41, one is the E2 pointer and one an operator question, **10 named a live step that owned
them, and 29 did not** -- four of those 29 naming a resolver that had already SHIPPED (N-14 "Phase
D", N-33 "D3-adjacent", N-40 "X-c", N-56 "X-c2b2"), the B-16 / B-17 class recurring and the measured
proof that an unowned row does not wait, it rots. Grouped by ROOT the 29 collapse to five, and four
become steps: **X-h** four controls
that cannot fire, **X-i** the read pass that pins a clock it does not hand to its loaders (nine
rows, one cause), **X-j** the surfaces that still pick their own producer -- **`$681.34` live today
between the default `/dashboard` hero and the default `/grid`, same account, same period** -- and
**X-k** the recurrence bound. **X-e** and **E2-0** widened to absorb the remainder, and one NEW
finding came out of the pass: **N-95**, the seam's own front-door docstring still stating the
contract X-g2b retired.

**THEN RULING R-AQ WENT FURTHER, AND IT IS THE ONE THAT MATTERS (developer, 2026-07-27).** R-AO
left six rows as "residue", four of them with stated wake conditions. **The developer ruled that a
finding waiting on a condition is a time bomb with a note attached, and that cost is never a ground
for leaving a defect in place.** So the six became five more owned steps and one dated developer
decision: **X-o** (B-16 -- a LIVE defect the R-AO pass under-triaged), **X-l** (the pay calendar is
PARTIAL, which is this arc's own disease on the other axis -- N-82 + N-79's far half), **X-m** (the
projection engine takes a boundary its caller must derive -- N-86), **X-n** (a redistributed loan
payment destroys its real installment date -- N-36), **X-p** (the calendar's chips and its balance
line are on two clocks -- N-58, sequenced behind X-f with the reason stated, which is a schedule and
not a deferral). R-AQ **supersedes the record-not-fix half of ruling R-AG** and **retires the words
"residue", "own commit", "deferred" and every wake condition from Section 6's owner column**.
**ZERO findings are now unowned.** Section 9 gains rules 6 and 7: a closed owner vocabulary -- a
live step ID, `operator`, or `developer-decision` -- and a GATE that enforces it, shipping as plan
step X-h's fifth commit, because prose does not enforce itself.

**Order: X-g4 -> X-c2c4 -> X-o -> X-h -> X-i -> X-j -> X-k -> X-l -> X-m -> X-n -> X-d -> X-e ->
X-f -> X-p -> E2**, with X-i before X-j on a measured ground rather than a preference (see R-AO).
**Twenty-nine unowned rows became zero.**

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

---

## 2. Phase X as built: X-a .. X-c2c3, and X-g1 .. X-g3b

The shipped step entries, extracted verbatim from the live document's Section 5. Two unshipped
steps were left behind in the live document and are NOT here: **X-c2c4** (the last cash producer
deletes -- it sat under the `X-c2c` decomposition header below) and **X-g4** (the deletion -- it sat
under the `X-g` entry). Both are live work and both cite the entries below for their preconditions.

### 2.1 X-a through X-c2c3

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

**Deletion-list corrections, carried from the live document's `X-c2c` block.** All three are
historical -- the live `X-c2c4` entry restates the only part still owed (correction (b)'s
`load_balance_transactions`, its own item (iv)).

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

### 2.2 X-g1 through X-g3b (the modelled half)

These sat under the live document's `X-g` entry, whose header, target shape and remaining child
(`X-g4`) stayed there. The design they implement is the live document's Section 3.2.

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
  * [x] **X-g2 (DECOMPOSED, 2026-07-26, ruling R-AA)** -- THE cutover, split on the arc's own
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
      (**Superseded at X-g3b-0**, recorded here per Section 7.6 because this is a SHIPPED step's
      history and the name it turns on no longer exists: `_AssembledInputs` was deleted whole, so
      the duck-typing this sub-point removed can no longer recur in any form. What X-g2b moved
      beside a bundle, X-g3b-0 moved beside nothing -- the loader answers the narrow type directly.)

      **Recorded, not fixed:** **N-85** (`interest_by_period_for_account` has no production caller
      and survives on its own tests -- the dead-code-alive-for-its-own-tests shape, deleted with the
      module at X-g4) and **N-86** (the `/investment` limit CARD and the projection beside it read
      two different YTD boundaries by design; the card is always through-current, and only the
      projection's depends on its axis).
  * [x] **X-g3 (DECOMPOSED, 2026-07-27, from its own trace)** -- ruling R-W's grid. `grid_balance_view`
    renders the modelled balance, and the conditional Interest row becomes TWO rows -- a per-kind
    accrual row and a Contributions row -- on R-O's own non-zero rule and on BOTH form factors
    (R-P). Its four forks are **R-AH, R-AI, R-AJ and R-AK in Section 4, and all four are ANSWERED**
    (developer ruling 2026-07-27, all as recommended).

    **"Render plumbing only" is WRONG and this entry said so before the step started.** The grid's
    rendered balance MOVES: it is the kind-blind cash-flow figure today and becomes the modelled
    one, which is finding N-76's whole point. So this is a money-moving render step and needs the
    every-figure sign-off, not a plumbing one.

    **THE TRACE IS DONE (2026-07-27) and all four forks are RULED.** No code was written for it.
    What it changed, so the build starts from the corrected picture rather than the one this entry
    carried before:

    * **The third recorded fork INVERTED, from a cost into a prerequisite** (ruling R-AJ (a)). The
      entry filed "it must assemble the real per-account inputs ... and is where the step's cost
      actually is". It is where the INVESTMENT KIND is: `_asset_fold._modelled_return` reads the
      CALLER's `investment_params` on that one arm (`_asset_fold.py:394-397`), where INTEREST reads
      `_interest.accrual_params(account)` and APPRECIATING reads the row's own relationship -- so
      under today's `ContributionInputs.absent()` an INVESTMENT models no return at all. Measured:
      the last column stays byte-identical to today's grid figure on all three investments on both
      databases. Without the assembly this step moves `$0.00` for the whole kind.
    * **Ruling R-W's missing term was re-measured from the grid's own producer.** `net +
      reconciliation + accrual` breaks on **53 of 59** period pairs on the real Empower 401(k),
      worst **`$181.59`** a column (the employer's flat 5% of `$3,631.74`). The four-term form breaks on
      **0 of 59** -- it holds on all 59 -- for every non-loan account on both databases.
    * **TWO more forks existed and neither was in R-W** (rulings R-AJ (b) and (c)): the kind gate
      `_grid.py:346` can DELETE, because the replay already decides what it decides (measured
      `$0.00` on 60 of 60 columns for all three PLAIN accounts on both databases); and
      `GridColumn.interest` can no longer BE `None`, which leaves four template `is not none` guards
      testing an impossible shape.
    * **A FOURTH fork the trace found, and it is a live finding rather than something this step
      introduces** (ruling R-AK, finding **N-87**): `dashboard_pulse_service.py:123-133` justifies
      reading the cash view by agreement with a grid that has not been on the cash view for an
      INTEREST account since PR #47 -- measured `$416.12` and `$704.72` of divergence TODAY. The
      three surfaces stay; the false statement is corrected in-commit.
    * **N-76 closes byte-exactly, not approximately.** The proposed grid balance equals
      `balance_map` on **900 of 900** (account, period) pairs -- 480 on `shekel`, 420 on
      `shekel_f3_final` -- which is the
      unification's proof, and the reason the cross-page readers can take the grid as a surface for
      the modelled kinds instead of asserting a locked gap.
    * **One of this document's own citations had drifted and is corrected here** (Section 7.6's
      rule, and X-g1's precedent): ruling R-W's row and finding N-76's both cited the grid's
      INTEREST-only layering at `_grid.py:271-277`, which X-g2b moved -- it is `:345-362` with its
      gate at `:346`. Re-read at the new lines, not arithmetic.

    **What the step moves, measured 2026-07-27 on BOTH databases** (the sign-off list; figures are
    the CURRENT column and the LAST projected column, the primary value from `shekel` and the
    PARENTHETICAL from `shekel_f3_final` where the two differ):

    | account | kind | current column | last column | columns moving |
    |---|---|---|---|---|
    | Checking / S8 Checking | PLAIN | `$0.00` | `$0.00` | **0 of 60** |
    | Fidelity Savings | INTEREST | `$0.00` | `$0.00` | **0 of 60** |
    | Money Market | INTEREST | `$0.00` | `$0.00` | **0 of 60** |
    | Fidelity Roth IRA | INVESTMENT | `$28,000.00 -> $28,184.43` (`$27,432.35 -> $27,537.61`) | `-> $34,263.60` (`-> $33,477.26`) | 53 of 60 (52) |
    | Fidelity Traditional IRA | INVESTMENT | `$11,675.48 -> $11,794.25` (`$11,714.31 -> $11,759.26`) | `-> $14,338.18` (`-> $14,295.64`) | 54 of 60 (52) |
    | Empower 401(k) | INVESTMENT | `$31,070.06 -> $31,751.40` | `-> $48,846.91` | 54 of 60 |
    | 203 Chalmers Dr (`shekel`) | APPRECIATING | `$350,000.00 -> $350,965.03` | `-> $371,856.66` | 54 of 60 |
    | Home (`shekel_f3_final`, rate `0.00000`) | APPRECIATING | `$0.00` | `$0.00` | **0 of 60** |
    | Mortgage / Van Loan | AMORTIZING | **UNMOVED** -- the standing regression gate | | |

    **What the grid renders for those accounts TODAY is a STEP FUNCTION, not a flat line**
    (corrected from the review, which caught the first draft of this entry claiming otherwise, and
    caught its mechanism too). The cash fold replays every `AccountAnchorHistory` row as a RESET
    (rulings R-I / R-S), so a modelled account's grid column set steps at each recorded assertion
    and holds flat between them: measured on `shekel`, the Roth shows **5** distinct values across
    its 60 columns (`$23,851.08` .. `$28,000.00`), the Traditional IRA **4** (`$10,175.49` ..
    `$11,675.48`) and the Empower **3** (`$26,912.56` .. `$31,070.06`); only the Property is
    genuinely flat, at one value. "Because they hold zero transaction rows" was the wrong reason --
    it is their ASSERTIONS that move those columns, which is the same fact finding N-74's own
    closure records. The step replaces the staircase with the modelled curve through it.
    `$31,751.40` is the figure X-g2b already signed off for the Empower's `/investment` headline, so
    the grid is joining a shipped number rather than inventing one. The Property's `$350,965.03` is
    finding **N-83**'s gap gaining a THIRD surface (the grid renders exactly `/savings`' figure; the
    property detail page stays on the cache column), which widens nothing and is recorded there.

    **Ruling R-W's claim about a typed row was exercised on real data** (rolled-back transaction, the
    real Empower): a `$1,000.00` projected income row typed into a future period moves that column
    by `$1,003.84` and the horizon by **`$1,211.04`**, the difference being the accrual ON the new
    money -- and the modelled CONTRIBUTION tier is UNMOVED, because the employer feed is flat
    percentage and a typed row is not a transfer shadow. So "a typed grid row IS an event in the same
    stream" holds, and ruling R-R's partition holds with it.

    **The decomposition, DECIDED FROM THE TRACE.** Two commits, on the arc's own
    additive-then-cutover line (X-c2b1/b2/b3, X-g2a/b):

    * [x] **X-g3a** `refactor(grid): the accrual row is one of two modelled rows`
      -- **SHIPPED `320a4641` (2026-07-27).** The SHAPE,
      baseline-identical because the kind gate STAYS, so no account's balance moves.
      `GridColumn.interest` becomes `accrual: Decimal` with `contribution: Decimal` beside it (both
      non-optional, ruling R-AJ (c)); `GridRowFlags` gains its third flag on R-O's one rule; the
      THREE templates that render the conditional rows (the desktop `<tfoot>`
      `_balance_row.html`, the mobile This Period card `_mobile_tp_summary.html`, the mobile Plan
      recap `_mobile_plan.html`) gain the second row and the sign-aware styling finding **N-88**
      names; the THREE route render entries that feed those templates (`index`, `balance_row`,
      `mobile_this_period_summary`) supply the per-kind label (ruling R-AI);
      `verify_balance_baseline.py` captures both new fields, so X-g3b is DIFFED
      rather than argued (the X-g2b-0 precedent -- the instrument before the measurement); and
      `dashboard_pulse_service.py:123-133`'s false statement is corrected with N-87's figures
      (ruling R-AK).

      **Four things pinned so the build starts cold rather than re-deriving them.**
      (1) **`GridRowFlags.interest` renames to `accrual` with it**, so the seam has ONE vocabulary
      for the tier rather than a field called `accrual` behind a flag called `interest`; the third
      flag is `contribution`. (2) **The label helper lives in `app/routes/grid.py`**, module-level,
      beside `_build_grid_view` -- the three render entries already call into that module and it is
      the presentation layer ruling R-AI puts it in; it takes `Account | None` and returns `str`,
      total over `AccountProjectionKind`'s five values. (3) **The harness site is
      `verify_balance_baseline.py::_grid_columns` (`:132-143`)**, the dict literal that already
      emits `"interest"`. (4) **N-88's styling puts the SIGN in the treatment on all three
      surfaces** -- the row takes a positive token on a gain, a negative one on a loss, and renders
      an explicit `+` on the gain, so the SIGN carries the meaning and colour is never the only
      signal; the desktop `<tfoot>` and the Plan recap adopt the same rule rather than staying
      colourless, or the two form factors disagree (ruling R-P).
      **This pin said "follows `investment/dashboard.html:93-100` VERBATIM" and was WRONG; ruling
      R-AM (2026-07-27) supersedes it.** Verbatim means the chip's `>= 0` boundary, and that
      boundary imports a state the chip never faces: ruling R-O renders `$0` in EVERY column of a
      window the row is on for, so `>= 0` paints each of them a gain and prints `+$0` in success
      green. The shipped rule is three-way (`> 0` / `< 0` / `== 0`), and it uses the GRID's own
      tokens rather than the chip's, because the mobile card's Net Cash Flow bar three rows up
      already uses exactly that pair. Ruling **R-AN** goes with it: the two modelled rows render
      CENTS, so a sub-50-cent accrual cannot round to `$0` and leave the row's own visibility rule
      unverifiable on screen.

      **THREE route entries, not four** (corrected from the review): `subtotal_rows` renders
      `_subtotal_rows.html`, which has no conditional rows, takes no `row_flags` and reads only
      `income` / `expense` / `net` -- handing it a label would add a context variable nothing
      reads, which is the "argument a caller can get wrong" shape inverted.

      **How the Contributions row is graded here, because the obvious answer is IMPOSSIBLE**
      (corrected from the review; the entry previously named a fixture that cannot reach the row).
      With the gate still in place only an INTEREST account resolves the modelled arm
      (`_interest.accrual_params` returns `None` for every other kind), and
      `_asset_contributions.contribution_events` returns `[]` for every kind but INVESTMENT -- so in
      this commit `contribution` is `0.00` in every column of every account for EVERY POSSIBLE
      FIXTURE, and `row_flags.contribution` is permanently `False`. No producer-level control can
      fire. The row is therefore graded exactly the way "Timing & true-ups" was graded at X-c2b1,
      whose test class says why in its own words: "A row whose template nobody ever executed would
      arrive at the cutover unproven, and the cutover is the commit where money moves"
      (`tests/test_routes/test_grid.py:7059-7062`). So X-g3a renders it through HAND-BUILT
      `GridColumn`s (`TestTimingAndTrueUpsRow._columns`, `:7068-7081`, which already constructs the
      dataclass directly) and X-g3b supplies the producer-side control on a `_401k` fixture with a
      feed. Say which commit proves which half; do not claim a control this commit cannot run.

      **Its "baseline-identical" claim is about FIGURES, not tests** (corrected from the review).
      Making the field non-optional breaks every `column.interest is None` assertion in this commit,
      not at X-g3b: `test_balance_at.py:2348, 2378, 2476, 2511, 2564`,
      `test_cross_page_balance_equality.py:598`, `test_grid.py:4704`, and the
      `interest is not None` branch inside `_assert_grid_view_reconciles`
      (`test_balance_at.py:2311-2315`). Those are field-contract updates; the two BEHAVIOURAL
      inversions belong to X-g3b.

      **The harness diff should show exactly THREE things** (corrected from the review; the entry
      said two and its own instrumentation was the third): the `interest` key renaming to `accrual`,
      `null` becoming `"0.00"` on every account that models nothing, and a NEW `contribution` key
      reading `"0.00"` on every column of every non-loan account. Anything else is a defect.

      **AS BUILT (`320a4641`) -- it showed exactly those three and nothing else, on BOTH
      databases.** Structurally compared rather than eyeballed: **ZERO changed shared keys** across
      **17,148** leaf figures on `shekel` and **15,008** on `shekel_f3_final`, with `interest` ->
      `accrual` carrying all **120** real values unchanged on each, **360 / 300** `null` ->
      `"0.00"`, and **480 / 420** new `contribution` keys every one `"0.00"`. Both loans UNMOVED at
      the standing gate (Mortgage `$177,277.97`; Van Loan `$15,663.59` / `$15,205.63`), period maps
      byte-identical. **18 firing controls, every one shown to fire.** Live-rendered against the dev
      clone: the Money Market reads "Interest" `+$6.16` in the success token on all three surfaces,
      while Checking, the Empower, the Property and the Mortgage are unchanged.

      **Its two adversarial reviews found SEVEN defects, and three of them changed the step.** Both
      reviews were run before the commit, on the standing instruction.
      (a) **The four-term identity shipped with NO firing control, and one was constructible in
      this very commit** -- the sharpest finding, and it inverts what the entry above says. The
      entry reasoned that no PRODUCER can put a figure in `contribution` at this step, which is
      true, and concluded the term was unprovable here, which is false:
      `_assert_grid_view_reconciles` takes a **view**, and this file already owns a hand-built
      column builder and a hand-built view builder. A two-column view whose balance delta contains a
      contribution grades the oracle with no fixture and no producer.
      `TestTheReconciliationOracleSeesAllFourTerms` now does exactly that, and reverting the oracle
      to its three-term form FIRES it. A commit whose whole job is to seat the four-term identity
      must not seat it unguarded.
      (b) **`pytest.raises(match="contribution")` was matching the wrong string.** Pytest's
      assertion rewriting appends the `GridColumn` repr, which contains the literal
      `contribution=` for every column -- so the pattern matched even when the failure message
      never named the term. Pinned to `\+ contribution ` (a space, no equals), which appears only in
      the oracle's own sentence, and a control now proves it.
      (c) **FOUR surviving statements of the retired three-term identity in `app/`**, corrected
      in-commit: `balance_at/__init__.py:75` (the seam package's OWN entry docstring -- the worst
      possible one to miss), `routes/grid.py`'s `_build_grid_view` docstring and its
      `subtotal_rows` comment, `_asset_fold.py:293` (which still promised ruling R-W's *singular*
      "Growth" row), and `_interest.py:180`.
      (d) **A liability's accrual was labelled after an asset's growth.** `AccountProjectionKind.AMORTIZING`
      mapped to the unreachable placeholder "Growth"; a loan's accrual is interest CHARGED, and the
      map's own rationale forbids a knowingly-wrong entry. It reads "Interest", with a control.
      (e) The intermediary `_mobile_this_period.html` was the one link in the include chain whose
      contract was never updated -- it still documented "two conditional bars" and never mentioned
      `accrual_label`, which the summary partial had just declared REQUIRED.
      (f) The unreachable placeholder was bound to the SAME constant as INVESTMENT's ruled word, so
      editing the placeholder would silently rename the row ruling R-AI named. Split.
      (g) A spliced sentence in the new macro's comment, and the macro file's header still claiming
      it held only matching-driven renderers.

      **Two corrections to this entry's own text, as built.** (1) The fourth term went into
      `_assert_grid_view_reconciles` HERE, not at X-g3b as the entry below says -- the entry's own
      list requires that branch to change in this commit anyway (it dereferences `column.interest`),
      and finding (a) is why having the oracle in final form BEFORE the cutover matters. X-g3b still
      adds the producer-side control on a `_401k` fixture with a real feed. (2) **"Baseline-identical"
      is about FIGURES, and the RENDER does change**: every accrual cell gains a leading `+`, the
      desktop `<tfoot>` gains a colour token where it was colourless, and the two modelled rows now
      carry cents (ruling R-AN). The harness captures figures, not HTML, so nothing in the
      verification standard sees that -- which is why the live render above is part of the sign-off
      rather than an extra.
    * [x] **X-g3b-0** `refactor(balance): the seam loads a contribution feed, not a bundle`
      -- **SHIPPED (2026-07-27).** A PREREQUISITE the plan did not have, raised by the developer
      when X-g3b's entry was read as instructions: the sentence below told the implementer to
      assemble a FOUR-field bundle and slice THREE fields out of it, which is a shape rather than a
      call, and X-g3b would have been its fifth caller. It moves ZERO money and its proof is that
      claim: the harness diffs **byte-identical on BOTH databases** (480 and 420 grid cells, 6720
      and 5880 daily points, every scalar, period map and growth decomposition), pylint `app/`
      10.00/10, full suite **7659**.

      **What was actually wrong, measured before the change.** `_AssembledInputs` welded two
      concerns: three fields that ARE a `ContributionInputs`, plus a `debt_schedules` map **whose
      VALUE was read nowhere in the app**. Its only use was the membership test `account.id in
      inputs.debt_schedules` -- so a map of fully resolved amortizations was built on every seam
      read to answer one boolean, and `positions_period_map`, the consumer that test gated,
      re-derived the identical resolution itself (`_positions.py:131`, `:195`). Worse, that boolean
      was the seam's SECOND spelling of one predicate: `_kind_correct.balance_at` wrote it out
      longhand and `_liability` decomposed it into two guard clauses, and the equivalence between
      the three was RECORDED in a docstring rather than enforced. Section 8's "a DRY refactor of a
      PREDICATE can move money" cuts both ways -- a predicate stated three times moves money when
      one statement is edited and the others are not.

      **As built:** `_AssembledInputs` / `_assemble_inputs` DELETED; `_contribution_inputs_for_accounts`
      (batch, TOTAL over its accounts) and `_contribution_inputs_for_account` (single, the batch
      over a one-element list) replace them, and take no `BalanceContext` because none of their
      three loads reads one. `_resolution.configured_loan(account, ctx)` is the one predicate, and
      all three surfaces call it. `__init__`'s private re-exports drop to the single name a test
      actually reaches -- correcting a comment that claimed `test_balance_at.py` reached three of
      them, when that file reached none. New `TestTheLoanGateIsOneQuestion` grades the gate on the
      shapes that discriminate it (a configured loan; a paid-off loan whose schedule is EMPTY, which
      is what a careless reimplementation drops to cash; a Mortgage with no `LoanParams`; a plain
      account), with a monkeypatched control shown to FIRE.

      **Two adversarial reviews ran; both confirmed the behaviour and both found the same class of
      defect in what I had WRITTEN.** Every refutation attempt against the gate equivalence,
      totality, cross-user scoping, laziness and dead surface failed -- the code was right. Three
      new docstring claims were not: (a) it named `grid_balance_view` as a caller of the new loader,
      which is X-g3b's end state stated as present fact; (b) it justified dropping the
      `BalanceContext` with "a property of the concern", which the loader's own callee refutes
      (finding **N-91**); (c) it claimed a caller-independence the code did not establish -- the
      gross was gated on whether ANY account in the SET had params, so one account's feed differed
      by who it was loaded beside. (c) was fixed STRUCTURALLY rather than by correcting the prose
      (the gross now reaches only the accounts that can consume one), which is what makes the
      Returns contract true by construction. Findings **N-91** and **N-92** are the two the reviews
      surfaced and this step deliberately does not fix.

    * [x] **X-g3b** `feat(balance): the grid renders the modelled balance` -- **SHIPPED (2026-07-27).**
      THE cutover, and the only commit here that moves a figure. The kind gate deletes and `grid_balance_view` loads
      the account's real `ContributionInputs` through the single call
      `_inputs._contribution_inputs_for_account(account)` (ruling R-AJ (a), (b)) -- the same entry
      `_kind_correct._modelled_scalar` and `investment_growth_since_anchor` already call, so the app
      keeps ONE definition of the contribution feed. **Corrected at X-g3b-0**: this entry used to
      say "through `_inputs._assemble_inputs([account], ctx)` sliced by `_contribution_inputs`", and
      both of those functions were deleted by that step -- see its entry above, and ruling R-AJ (a),
      whose own text carries the same retired pair.
      Every figure in the table above signed off on both databases; ruling R-K's
      four-term identity re-verified on 0 breaks of 59 for every non-loan account; both loans
      UNMOVED. Two tests INVERT BEHAVIOURALLY here (the field-contract updates were X-g3a's)
      and the developer's ruling R-W is the authority that says the
      expected behaviour changed: `test_investment_stays_cash_flow_no_accrual` and
      `test_property_stays_cash_flow_no_accrual` (`tests/test_services/test_balance_at.py:2462`,
      `:2500`) each assert the grid is strictly BELOW the modelled map. And the cross-page lock
      gains the grid as a surface for the modelled
      kinds -- reading `grid_balance_view`, NOT `cash_balance_map` the way `_grid_value`
      (`test_cross_page_balance_equality.py:215`) does, because after this step those two producers
      answer a modelled account differently BY DESIGN and a reader on the wrong one would prove
      nothing about the grid.

      **START HERE: five things X-g3a changed under this entry, re-verified 2026-07-27 against
      `320a4641`.** X-g3a edited the very files this step edits, so read at these lines, not the
      ones above them (Section 7.6's rule, and the X-g2b precedent).
      (1) **The gate MOVED and its predicate INVERTED.** It is no longer
      `if _interest.accrual_params(account) is not None:` at `_grid.py:346` guarding the modelled
      arm; it is `if _interest.accrual_params(account) is None:` at **`_grid.py:435`**, whose
      CASH arm calls `_cash_only_columns`. Deleting the gate therefore means deleting that helper
      and its arm and calling `_asset_fold.period_columns(_asset_fold.resolve(...))`
      unconditionally -- `_cash_only_columns` exists only to be deleted here, and its docstring says
      so.
      (2) **`_assemble_columns` already takes the modelled map** and reads the balance and both
      tiers off it, so the cutover changes what is PASSED, not how a column is built.
      (3) **`_assert_grid_view_reconciles` already carries the fourth term** -- moved to X-g3a
      because that branch had to change there anyway, and because the review found it was about to
      ship unguarded. What X-g3b still owes it is the PRODUCER-side half:
      `TestTheReconciliationOracleSeesAllFourTerms` grades it on hand-built views today, and this
      step must drive a non-zero `contribution` through a real `_401k` fixture with a deduction
      feed, which is the first commit in which any producer can.
      (4) **`TestTheContributionsRow`'s hand-built render tests survive**; they grade the template,
      not the producer. The producer-side control joins them rather than replacing them.
      (5) **The render is already final** -- the two rows, the per-kind label, the three-way sign
      treatment and the cents (rulings R-AL / R-AM / R-AN) all shipped at X-g3a, so nothing on this
      step is presentation work. If a figure looks wrong here it is a fold slip, which is exactly
      what the split bought.

      **The sign-off table above is still the pre-cutover expectation.** X-g3a moved no figure --
      ZERO changed shared keys across 17,148 and 15,008 harness leaves on the two databases -- so
      the CURRENT-column and LAST-column values it lists are still what the grid renders today, and
      the deltas it predicts are still the deltas to sign off. Re-capture a fresh HEAD baseline
      anyway: this is a money-moving cutover, and the table is an expectation, never the evidence.

      **AS BUILT -- every figure in the sign-off table above signed off on both databases, to the
      cent.** The harness moved ONLY the `columns` family: **483 of 3,360** column figures on
      `shekel`, **369** on `shekel_f3_final`. ZERO moved in `kind_correct_map`, `daily_series`,
      `cash_scalar_today`, `cash_scalar_dates`, `scalar_today`, `scalar_dates`, `amount_overrides`
      or `investment_growth_since_anchor` -- and INSIDE a column only `balance` / `accrual` /
      `contribution` moved, with `income`, `expense`, `net` and `reconciliation` at ZERO on every
      account. Per-account counts matched the table exactly (PLAIN 0/60, both INTEREST 0/60, Roth 53
      / 52, Trad IRA 54 / 52, Empower 54 with 53 contribution columns, the Property 54, `Home` at
      rate `0.00000` 0, both loans 0). **N-76 closed byte-exactly: 480 of 480 and 420 of 420
      (account, period) pairs equal `balance_map`.**

      **Live-rendered against the dev clone**, because the harness captures figures and not HTML:
      the Empower reads "Growth `+$121.37`" and "Contributions `$181.59`", the Money Market
      "Interest `+$6.16`", the Property "Appreciation `+$397.69`" -- ruling R-AI's per-kind label on
      real data -- Checking renders NEITHER row, and a loan cannot be selected at all (ruling D4's
      refusal, live).

      **The adversarial review found no Critical and no High on the CUTOVER**, and independently
      re-derived the `$400.00` employer arithmetic and the byte-identical-by-construction argument
      for why a PLAIN column and a loan column cannot move (`_merged_day_deltas` collapses per day,
      `sample_cumulative` rounds only at the sample). What it did find was **nine surviving
      statements of the retired contract**, two of them High: **`dashboard_service.py:74-81` still
      justified the hero's cash basis by agreement with the grid** -- the SAME clause ruling R-AK
      ordered corrected, which X-g3a fixed on the pulse only, even though finding N-87 named both
      producers by line -- and **`GridBalanceView`'s own class docstring** still said "for every kind
      EXCEPT interest-bearing its balances are identical to `cash_balance_map`", the contract of the
      object all three grid templates read. The other seven: both self-refresh partial comments in
      `routes/grid.py`, `_inputs.py`'s claim that the grid still passes `absent()` (written one
      commit earlier, at X-g3b-0), `ContributionInputs.absent`'s three-site audience (now one),
      a self-contradiction this step introduced in `_interest.py`, `_cash_flow.py`'s consumer list
      AND its now-overturned rationale, `__init__.py`'s surface list and intra-package dependency
      line, `account_resolver.is_cash_flow_account`'s characterisation, and two sibling test guards
      still replaying `cash_balance_map` under the comment "what does the grid show". All nine
      corrected in-commit.

      **Two of my own claims were WRONG and are corrected rather than carried.** (1) The module
      docstring explained a `$134.72` gap between ruling R-W's `$48,712.19` and the shipped
      `$48,846.91` as "that day's accrual" -- the review checked the order of magnitude and one day
      of that account's accrual is nearer `$10`. The cause is now stated as NOT measured, per this
      document's own rule about figures it must not carry. (2) A test asserted
      `get_current_gross_biweekly` as the employer match's basis; the arithmetic actually consumes
      the DEDUCTION-derived gross (`round_money(annual / pay_periods_per_year)`), which agrees here
      only because the fixture's salary makes both `$4,000.00`. Findings **N-93** (the cost every
      render entry now pays, including `subtotal_rows`, which reads none of it) and **N-94** (a
      per-kind injection control that fires whether or not its injection lands) are recorded, not
      fixed.

    Do NOT collapse X-g3a and X-g3b: the split exists because mixing render plumbing with a
    money-moving change makes a plumbing slip read exactly like a fold slip, and a revert would
    throw away the render work with the cutover.

---

## 3. Closed findings register

The ten findings the shipped Phase X steps CLOSED, extracted from the live document's Section 6
so that ledger carries only unresolved work. **IDs keep their names**, so a reference to any of them
resolves here or there. The 75 findings the LOAN arc closed are in
`loan_arc_as_built_2026-07-26.md` Section 7.

| id | finding (one line) | worst measured | status | closed by |
|---|---|---|---|---|
| N-29 (D1b) | **The balance seam's NON-loan branch now reaches into a loan-named package for a generic calendar primitive.** D1b moved `find_period_containing_date` to `loan_ledger/_visible.py` -- correct on cohesion (it is chronology, it sat in a kind CLASSIFIER, and `_visible` had to import that classifier to reach its own primitive) and correct on the fence (`loan_ledger` is W9909-scoped WHOLE, so the ruling travelled with the name -- the archived N-28: relocating a public name OUT of a W9909-scoped module silently un-scopes it). But its seam call site is `_kind_correct.py:278`, the INTEREST / INVESTMENT / APPRECIATING fallthrough -- HYSAs, brokerages, properties -- which now imports from the loan fold leaf, two lines below an existing `pay_period_service.get_all_periods` call. `pay_period_service` is the neutral home that owns the calendar and carries no loan semantics; the reason D1b did NOT use it is that it is W9909-UNSCOPED, so relocating a classified public name there would drop its classification -- the archived N-28's hole exactly (a module's W9909 scope is its module IDENTITY, so moving a name across a module boundary moves it out of scope silently). So the honest fork is: leave it in the loan leaf (a naming wart), or move it to `pay_period_service` AND scope that module for W9909 (a registry entry on a module holding the T of balance-at-T but no money). Not a correctness defect either way -- the function is pure and its two callers are proven -- and worth deciding before D-fold locks the leaf's surface | -- | **CLOSED at X-g2b (`560b3339`).** The fork dissolved rather than being decided: the seam's non-loan branch stopped needing a calendar primitive at all when the scalar became a fold over dated events, so the import into the loan-named leaf is gone and there was nothing left to relocate or scope | X-g2b (`560b3339`) |
| N-71 (X-c2c trace) | **Three account kinds still answer a DATE with a PERIOD, and it is documented as intended.** `_kind_correct.py:195-197`: "INTEREST / INVESTMENT / APPRECIATING are period-granular: they answer 'what is the balance at the end of the period containing *as_of*?'" So the whole of a period's modeled growth is credited on the period's FIRST day. **Measured 2026-07-26 on the prod-shape clone, period 30 (2027-05-20..06-02): the scalar returns the IDENTICAL value on the first and last day of the period** -- Empower 401(k) `$38,617.11` against `$328.50` of growth in that period, Money Market `$9,090.81` against `$261.24`, Roth IRA `$29,843.76` against `$114.07`. This is finding cash D2's exact shape ('the scalar is period-flat; it contradicts a date-precise read'), closed for PLAIN and AMORTIZING at plan step X-c2b2 and still live for the other three | a whole period's growth on the wrong day: `$328.50` measured, unbounded in principle | **CLOSED at X-g2b (`560b3339`).** The scalar is date-precise for all five kinds; `find_period_containing_date` and the pre-horizon anchor fallback left `_kind_correct` with it.  Its pre-closure record, kept because it is the measured case: **RE-VERIFIED to the cent 2026-07-26** (all three figures reproduced at period 30 by the X-g trace, plus Trad IRA `$12,744.04` / `$48.71` and Fidelity Savings `$5,571.99` / `$7.03`); the fix was structural, not a patch -- a period-flat answer is what a period-keyed MAP can give, and a date-precise one needs the event replay -- and **ruling R-T set the grain that closed it**, daily, `+0.5 ms` per account per read, measured | X-g2b (`560b3339`) |
| N-74 (X-g trace) | **A modeled account's map DISCARDS the user's own recorded balance assertions and renders a model over them.** The three modeled accounts carry **15 `AccountAnchorHistory` rows** between them (Roth IRA 6, Traditional IRA 6, Empower 401(k) 3, spanning 2026-03-31 .. 2026-07-16). `_investment.build_investment_balance_map` reads only the LATEST -- `_cash_engine.balances_for` starts at `resolve_anchor`'s single anchor and `_calculator.calculate_balances:117-118` skips every pre-anchor period -- and `_reverse_project_periods` then re-derives every earlier period from a growth curve, which `_merge_balance_sources` prefers because the base has no entry there. Measured on `shekel_f3_final` at the period ending 2026-04-08: Roth renders `$26,604.63` against the 2026-04-06 assertion of `$23,851.08`; Trad IRA `$11,360.85` against `$10,175.49`; Empower `$29,289.22` against its earliest record `$26,912.56` (2026-04-09). The cash fold reproduces all three assertions to the cent. It renders on `/savings`' net-worth history (`_net_worth.py:151` -> `build_maps`) | **`$6,315.57`** of net-worth history contradicting the user's own bank facts, at ONE period | **CLOSED at X-g2b (`560b3339`).** Ruling R-S shipped: an ASSERTION always wins and there is no backward model.  Verified on the prod-shape clone -- the period ending 2026-04-08 now renders the Roth at its own `$23,851.08` assertion (was `$26,604.63`), the Trad IRA at `$10,175.49` and the Empower at `$26,912.56` | X-g2b (`560b3339`) |
| N-75 (X-g trace) | **Entering a FUTURE contribution rewrites a PAST balance.** `_reverse_project_periods` passes the FORWARD `periodic_contribution` into `growth_engine.reverse_project_balance`, which un-contributes it walking backward -- so the pre-anchor half of the map is a function of the plan for the future. Measured by creating six `$500.00` projected Checking -> Roth transfers inside a ROLLED-BACK transaction: the Roth's period-7 (past) balance moved `$27,327.49 -> $26,829.40` while the user's recorded assertion for that period is `$27,332.33`. Both numbers are wrong and the second is `$502.93` wrong. Not reachable on today's data only because no investment account has a contribution feed at all (ruling R-R's measurement) | `-$498.09` on one past period from six future rows; unbounded in the contribution amount | **CLOSED at X-g2b (`560b3339`).** It died with the reverse projection, which left the balance path entirely: a replay has no backward direction to pass a forward contribution into | X-g2b (`560b3339`) |
| N-76 (X-g trace) | **The grid and `/savings` answer one modeled account two ways, with no row explaining the gap.** `_grid.grid_balance_view` layers an accrual for INTEREST only (the gate is `_grid.py:435`, `accrual_params(account) is None` on its CASH arm since X-g3a `320a4641`, which both MOVED it and INVERTED it; this row's earlier citations `:271-277` and `:345-362` / `:346` drifted at X-g2b and X-g3a respectively and are corrected here per Section 7.6), so an INVESTMENT or APPRECIATING account's grid balance is the kind-blind cash-flow fold while `_kind_correct.balance_map` returns the modeled map -- and both surfaces are reachable for those kinds (`account_resolver.is_cash_flow_account:41` admits every non-amortizing kind). Measured at the last projected period: Empower 401(k) grid `$31,070.06` vs `/savings` `$48,712.19`; Roth `$5,916.95` apart; Trad IRA `$2,526.68`; and on `shekel` the Property `$21,675.99`. The grid's interest column is `None` for every one of them. INTEREST accounts are byte-identical on both surfaces, which is what shows the unification works | **`$17,642.13`** on one account, growing with the horizon | **CLOSED at X-g3b (2026-07-27).** The grid renders the modeled balance with an accrual row and (ruling R-AH) a Contributions row beside it, so ruling R-K's identity holds for all five kinds. Verified before the build: the proposed grid balance equals `balance_map` on **900 of 900** (account, period) pairs (480 on `shekel`, 420 on `shekel_f3_final`), so the unification is byte-exact rather than approximate; the gap this row measures was `$6,263.60` (Roth) / `$2,662.70` (Trad IRA) / `$17,776.85` (Empower) / `$21,856.66` (the Property) on `shekel` at the last projected period, and is now `$0.00` on every one of them. **As shipped:** the kind gate and `_cash_only_columns` deleted, the grid loading the account's real `ContributionInputs`; harness-verified with ONLY the `columns` family moving (483 figures on `shekel`, 369 on `shekel_f3_final`) and, inside a column, only `balance` / `accrual` / `contribution` -- `income`, `expense`, `net` and `reconciliation` moved ZERO on every account, and `kind_correct_map` / `daily_series` / every scalar moved ZERO. Both loans unmoved at the standing gate | **X-g3b (SHIPPED)** |
| N-77 (X-g1) | **Two shared account factories leave their opening assertion on the WALL CLOCK, which plan step X-g2 makes load-bearing.** `create_hysa_account` pins its opening to the anchor period's first day and says why (the N-8 / N-65 shape, ruled at plan step X-c2a); `make_investment_account` and `make_appreciating_account` do not, so `account_service.create_account` stamps `AccountAnchorHistory.created_at` with the real clock while `tests/test_services` freezes today to 2026-03-20. Nothing fails TODAY because no shipped producer reads an INVESTMENT's or a Property's assertion DATE -- `_investment` pivots on the `current_anchor_period_id` cache column instead. **X-g1's own parallel run hit it immediately:** an unpinned Property opening dated 2026-07-27 is the LATEST assertion, lands past the seeded horizon, and the account then accrues NOTHING anywhere -- a state production cannot reach, and one in which a test asserting "the anchor period accrues $113.44" reads $0.00. Pinned per-fixture in X-g1's two files rather than in the shared helpers, because that is the X-c2a precedent exactly: `create_hysa_account` was pinned by the step that made the date load-bearing, with that step's full-suite run as the evidence | a fixture asserting against a state production cannot reach; every INVESTMENT / APPRECIATING fixture in the suite | **CLOSED at X-g2a.** Both helpers now pin the opening to the anchor period's first day and say why, and X-g1's own per-fixture restamp for the Property was deleted with the compensator it was. Its firing control: reverting the helper's restamp fails `test_pre_anchor_periods_agree_and_the_anchor_period_accrues`, the one test whose Property fixture does not restamp for itself | X-g2a |
| N-80 (X-g2b trace) | **Two rulings correct ONE overlap twice, and stacking them starts the `/investment` chart's projection line below its own history line.** Ruling R-U made the forward-projection seed "the replay with ACCRUAL filtered out"; ruling R-AB then moved that read to `window_start - 1 day`. The filter's whole purpose is to stop the growth engine re-growing a period the seed already grew -- and R-AB's date makes that impossible, because `growth_engine.project_balance` grows only the periods it is handed and every one of them starts at or after `window_start`. So the filter subtracts growth the window never touches: the accrual since the account's latest assertion, measured 2026-07-27 as Roth **`$161.31`**, Trad IRA `$109.10`, Empower `$292.11` on `shekel` and `$82.67` / `$35.30` / `$292.11` on `shekel_f3_final`. The result is a projection line starting from a balance the history line beside it has not rendered since the assertion date. NOT data-dependent: all three accounts hold ZERO transaction rows in both databases, which is what this document predicted would make the junction `$0.00` | the projection line up to **`$292.11`** below its own history line at the junction; the junction itself `-$76.99` / `-$73.97` / `-$15.96` on `shekel` under R-AB as written | **CLOSED at X-g2b (`560b3339`).** The filter went, `asset_seed_at` and `AssetPeriodFigures.balance_without_accrual` deleted unwired, and the seed is the ordinary date-precise scalar.  Found because this document required the junction be MEASURED before the display fork was recommended, on a step whose own additive commit had already shipped the filtered entry -- which is the argument for additive-then-cutover, from the other end | X-g2b (`560b3339`) |
| N-81 (X-g2b trace) | **The `/investment` balance hero and the cell that RESTORES it read two different producers.** `investment_dashboard_service.compute_balance_hero_cell` -- the anchor editor's Cancel / Escape / 409 revert target, whose own docstring says it returns "the model-from-anchor balance the headline shows" -- reads the seam SCALAR (`balance_at.balance_at(account, ctx, ctx.as_of)`, `:684`), while `compute_dashboard_data`'s headline reads `balance_map[current_period]` via `_resolve_current_balance` (`:221`). They agree today only by accident: the kind-correct scalar for an INVESTMENT is period-granular, so it IS the map read at the containing period. X-g2b makes the scalar date-precise and they separate by the accrual from today to the period's end -- measured `$22.59` / `$9.65` / `$26.05` on `shekel_f3_final` and `$23.12` / `$9.67` / `$26.05` on `shekel`. Cancelling the anchor editor would then restore a figure the page was not showing | up to **`$26.05`** between a rendered balance and the value its own revert restores | **CLOSED at X-g2b (`560b3339`)**: one cell, one producer. The broader question it sits on -- whether a "current balance" tile should be a DATE or a period END -- is NOT opened here: `/savings` has shown the period-end figure for every kind since X-c2b2 (Checking `$2,824.26` at today against `$2,683.63` in its current column), so the modelled kinds are joining a shipped convention, not setting one | X-g2b (`560b3339`) |
| N-84 (X-g2b trace) | **The `/investment` chart's de-dup compensator subtracts a contribution the engine never re-applies on that axis.** `current_period_transfer_contribution` is subtracted from the chart's seed (`investment_dashboard_service.py:318`) so the growth engine does not double-count a recorded current-period contribution. But the chart's axis is SYNTHETIC and opens at `date.today()`, while `_project_one_period` looks a `ContributionRecord` up by the projection period's own `start_date` and every record is dated on a REAL pay-period start (finding N-79) -- so on thirteen days in fourteen no synthetic period matches the current period's record and the engine applies the flat fallback instead. The subtraction is then a pure UNDER-count on that surface, not a de-dup. It IS load-bearing on `retirement_projection`'s real-period fallback axis (`:379-382`), where the dates do match, which is why ruling R-AB had to establish the date before the compensator could delete | `$0.00` today (no account has a recorded contribution feed -- ruling R-R's measurement); the current period's recorded contribution, per open of the chart, when one exists | **CLOSED at X-g2b (`560b3339`)** -- it deleted with the overlap ruling R-AB removed. Kept as a row because it is the measured reason the compensator was never a de-dup on one of its two surfaces -- a compensator that was wrong in both directions at once, which is the shape this ledger exists to make visible before it is quietly ported | X-g2b (`560b3339`) |
| N-88 (X-g3 trace review) | **The grid's accrual row hard-codes success green, and the two kinds X-g3 adds to it can accrue NEGATIVE.** `templates/grid/_mobile_tp_summary.html:106` renders the row's figure inside `<span class="font-mono fw-bold text-success">`. That is safe only while INTEREST is the sole kind reaching the row, because `interest_params` bounds the rate `apy >= 0 AND apy <= 1` (`:33-35`); the two kinds ruling R-W adds are bounded only `> -1` (`investment_params.py:48-51`, `asset_appreciation_params.py:43-45`), and the latter's own comment says a negative rate exists on purpose ("A negative rate is permitted so a future depreciating asset (e.g. Vehicle) reuses this table unchanged"). Ruling R-AH's own evidence is a measured `-10.5%` run producing `-$142.11` in a single column. So a depreciating Vehicle, or a 401(k) in a down market, renders a LOSS in success green on the mobile card -- while the desktop `<tfoot>` (`_balance_row.html:74-84`) and the Plan recap (`_mobile_plan.html:128-131`) render it colourless, so the app would also disagree with itself across form factors, which is the shape ruling R-P exists to prevent. There is no `.accrual-row` / `.interest-row` rule anywhere in `app/static/`, so the inline class IS the whole treatment | a rendered market loss shown as a gain; `-$142.11` measured in one column at a `-10.5%` rate | **CLOSED `320a4641` (X-g3a).** Fixed one commit BEFORE any non-INTEREST figure can reach the row, which is the X-g2b-0 ordering (the instrument before the measurement) and not what this row first said. `accrual_class` / `accrual_money` (`templates/grid/_grid_row_macros.html`) state the rule ONCE for all three surfaces, so the form factors cannot diverge again: a gain takes the success token with an explicit `+`, a loss the danger token with the `-` the money macro renders, and a column that earned nothing takes neither. It follows `/investment`'s stated principle -- "the rendered sign carries the meaning so color is never the only signal" -- on a THREE-WAY boundary rather than that page's `>= 0` (ruling R-AM: `>= 0` would paint every `$0` column of an accruing window as a gain, a state a one-off chip never reaches). Two firing controls: restoring the hard-coded success token, and moving the `+` back to `>= 0` | X-g3a `320a4641` |
