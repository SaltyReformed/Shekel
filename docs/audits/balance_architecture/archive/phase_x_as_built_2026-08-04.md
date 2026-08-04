# Phase X as built: X-c2c4 through X-f1b

**Read-only history. Nothing here governs work.** The live document is `../README.md`. This record
was extracted 2026-08-04, when that document stood at 6,688 lines and Section 9 rule 4 was rewritten
into a 1,000-line cap with a gate behind it.

**Every line here is one step, its commit, and what it closed. The commit message is the step's own
account of itself and every hash below was verified against `git log` at extraction; the narrative,
the measurements and the review residue that used to sit in the plan document are NOT reproduced.**
That is deliberate. This arc repeatedly carried a claim into an as-built record that the code later
contradicted -- an invented provenance line, a count that drifted, a citation to a deleted producer
-- and prose nobody re-verifies is worse than a hash anyone can check. **When you need the detail,
read the commit.**

Two sibling records hold the earlier spans: `loan_arc_as_built_2026-07-26.md` (Phases A-F, PR #64)
and `cash_arc_as_built_2026-07-27.md` (X-a .. X-g3b, PR #65). Open questions from any span live in
`../README.md` Section 6, never here.

## 1. The steps

All of X-c2c4 .. X-z shipped to production in **PR #65** (merge `69a527cd`). X-ae, X-af and X-aj1
shipped in their own PRs, noted per row. X-f1b is committed on `feat/xf1-settle-day` and is not yet
merged.

| step | commit(s) | what the commit says it did | findings closed | note |
|---|---|---|---|---|
| **X-c2c4** | `17c57cde` | refactor(balance): the modelled and cash producers delete | -- | The last cash producer. Shipped INSIDE X-g4b by ruling R-AR; no commit of its own |
| **X-g1** | `17ead4c5` | feat(balance): a modelled asset is an event stream | -- | Additive and unwired: the modelled replay beside the incumbent |
| **X-g2** | `560b3339` | refactor(balance): a modelled asset is one event stream | N-71, N-74, N-75 | The cutover. One event replay for all five kinds |
| **X-g3** | `920366a9` | feat(balance): the grid renders the modelled balance | -- | The grid renders the modelled balance; R-K's identity holds for all five kinds |
| **X-g4a** | `2ee817b4` | test(balance): the drift oracle walks 52 periods of the fold | -- | The drift oracle walks 52 periods. ADDITIVE: `git diff app/` empty |
| **X-g4b** | `17c57cde` | refactor(balance): the modelled and cash producers delete | N-43, N-46, N-78, N-95 | 1,347 production and 4,937 test lines removed; baseline byte-identical on both databases |
| **X-o** | `68c22fa0` | fix(savings): the debt-line question uses the debt-line predicate | B-16 |  |
| **X-q1** | `3b7823e1` | fix(savings): one debt-free date, one derivation | N-98 | One `LoanPayoffOutlook`, both surfaces read it |
| **X-q3** | `bad97e6a` | feat(savings): the payoff caption says what it measures | N-99 |  |
| **X-q2** | `be6cfae6` | refactor(savings): the horizon publishes what the page reads | N-100, N-102 | Rendered payload BYTE-IDENTICAL on both databases |
| **X-r** | `1204a99e` | refactor(savings): the projection dict carries the seam's figures | N-101 | No figure moved |
| **X-h** | `6337606e` + `7d61c67f` + `8e739298` + `86c38e28` + `6b1373ab` + `cd002872` | test(balance): the debt-track guard asserts through the builder | B-17, N-45, N-65, N-94 | No production change. The last two commits are the plan gate; `cd002872` moved it from `tests/` to `tools/` so editing the ledger is what runs it |
| **X-s** | `bbdfc2c0` | refactor(savings): the payload and the debt summary publish what is read | N-104, N-105, N-106 | Three leaves, one commit: the production files split cleanly and their tests did not |
| **X-t** | `db1e45a4` + `b3ff3343` + `709cda23` + `21893ec5` + `d4e0d4e7` | refactor(savings): the per-account projection is a value object | N-107, N-108, N-110, N-111 | No figure moves, on a harness built for this step because the seam one is blind above the seam |
| **X-u** | `70c5cf39` + `e2cdc589` | docs(balance): rule X-u's four forks, open N-115, gate the ledger's own count | N-109 | Opened N-115 |
| **X-v** | `7d4e4986` + `dbf154c7` | docs(balance): X-v's rulings -- one named exception answers a state the app cannot produce | N-112, N-113 | Opened N-116, N-117. 18 display-tier guards deleted |
| **X-w** | `03272174` + `38f8d879` | docs(balance): X-w's rulings -- the second per-account container, and the six others | N-114 | Seven commits; the two listed are the rulings and the review residue. Opened N-118 |
| **X-aa** | `5c2ba585` + `c10d5d12` | docs(balance): R-CO -- the two records X-w reported and gave to nobody | N-119 | Opened by the developer's own question |
| **X-z** | `b6b1446e` + `5e77d0db` | docs(balance): R-CP..R-CS -- X-z's four forks, all as recommended | N-118, N-120 | Ten commits; the two listed are the rulings and the review residue. Classifier calls per render ~488 -> 8. Opened N-121, N-122 |
| **X-ae** | `cbca7eed` | fix(app): a submitted digit string is parsed, not predicated | N-136, N-140, N-141, N-143 | PR #79, merge `a778703f`. Shipped WIDER than scoped: two reviews proved its central claim false. Opened N-139, N-142 |
| **X-af** | `209e8b6c` | test(periods): the fixtures build their window on the USER's clock | N-137 (merge-gate half) | PR #77, merge `dbee3812`. Test-only. Opened N-138 |
| **X-aj1** | `1688f508` + `63514efc` + `1e75d0ce` | refactor(transfers): restore validates before it mutates | N-146 | PR #80, merge `dde107f6`. `transfer_service.py` 999 -> 987. Opened N-149..N-152 |
| **X-f1b** | `51679384` | feat(transactions): a settle carries the day the money moved | N-179, N-182, N-183 | Figure-neutral by construction. Opened N-180, N-181, N-184, N-185 |
## 2. Findings closed outside a Phase X step

| finding | closed by | note |
|---|---|---|
| **N-130** | S1-a + S1-b, PR #67 (merge `fd0ddfab`), in production 2026-07-31 | The anchor/settle partition was decided by CLICK ORDER and cost `$4,001.42` on production: the grid rendered `-$4,021.37` against a hand-computed `-$19.95`. Ruling R-DH. Verified on a fresh clone: current period `-$19.95`, all nine past period ends land on an asserted balance |
| **N-131** | `92879e86`, its own PR | The cross-page locks were a month-end TIME BOMB, red on `main` at an unmodified commit and firing ~12 days a year |
| **N-132** | closed at the step, by day-offset conversion with the reason recorded at each site | Fixtures separated by calendar arithmetic stopped testing the case they name |
| **N-133** | `fix/n133-review-residue`, shipped in PR #68 | **The one row in this arc that a MEASUREMENT contradicted a ruling on.** R-DH (a)'s opening amendment was made mid-build on a hypothetical and never re-scored; it was 3.2x worse on the net plug than the rule the ruling's own table scores. Ruled: revert the amendment and date the opening. This is where Section 8's "score the rule you shipped" lesson was paid for |
| **N-137** (merge-gate half) | X-af, PR #77 | The app half is still open and is **N-138**'s, in the live ledger |
| **N-146** | X-aj1 commit `63514efc` | A notes-only save on a PAID transfer moved its money forward to today, through the ordinary UI path. Reproduced at `HEAD` before it was written down |

## 3. Rulings

**One line each: the RULE.** These governed work that has shipped, so a live sentence in
`../README.md` no longer depends on any of them (rule 5). Rulings that still govern remaining work
stayed in that document's Section 4. Where a headline reads thin, the ruling's full deliberation is
in the plan document's git history at the commit that recorded it.

| ruling | date | what was ruled |
|---|---|---|
| **R-B** | -- | The cash projection counts a settled transaction iff `COALESCE(paid_at, period start)` is after the latest anchor's `created_at` -- SHARED with the posting walk's existing rule, never copied |
| **R-F** | -- | Phase X ships FOLD-FIRST, not partition-patch-first |
| **R-H** | -- | ONE walk, designed for both consumers from the start |
| **R-J** | 2026-07-25 | A loan is refused at the SOURCE; the cash producers stay TOTAL and kind-blind |
| **R-P** | 2026-07-26 | Every surface that renders the subtotal figures renders ruling R-O's row, on R-O's own non-zero rule |
| **R-R** | 2026-07-26 | A contribution is partitioned by SOURCE, so the two feeds are disjoint BY CONSTRUCTION and there is no de-dup rule to get wrong |
| **R-V** | 2026-07-26 | Plan step X-c2c3 is CANCELLED; X-g replaces the modeled bases outright, and no compensator ships in the meantime |
| **R-W** | 2026-07-26 | The grid renders the MODELED balance, with a "Growth" row that is the accrual producer's own answer -- ruling R-K's identity then holds for all FIVE kinds |
| **R-AA** | 2026-07-26 | X-g2 ships in TWO commits: X-g2a the SHAPE, byte-identical, then X-g2b THE cutover |
| **R-AB** | 2026-07-26 | The forward-projection SEED becomes a DATE read at `window_start - 1 day`, and both `current_period_transfer_contribution` subtractions DELETE |
| **R-AC** | 2026-07-26 | The growth chip becomes a sum over the replay's ACCRUAL and CONTRIBUTION tiers, and hides ONLY with no investment params or no current period |
| **R-AD** | 2026-07-26 | The kernel's per-kind ladder COLLAPSES at X-g2b; the producers it replaces stay dead until X-g4 |
| **R-AE** | 2026-07-27 | The forward-projection SEED is the modelled balance at the day before the window, with NOTHING filtered out -- `asset_seed_at` DELETES and `investment_seed_map` leaves with no successor at all |
| **R-AH** | 2026-07-27 | The grid renders the two modelled tiers as TWO conditional rows, on ruling R-O's own non-zero rule and on both form factors (R-P) |
| **R-AI** | 2026-07-27 | The accrual row's label is PER KIND, resolved in the ROUTE from `classify_account`; the contribution row is unconditionally "Contributions" |
| **R-AJ** | 2026-07-27 | `grid_balance_view` assembles the account's REAL `ContributionInputs`, its kind GATE deletes, and `GridColumn |
| **R-AL** | 2026-07-27 | The accrual row's CSS marker renames kind-neutral: `interest-row` -> `modelled-accrual-row`, and the new row is `modelled-contribution-row` |
| **R-AM** | 2026-07-27 | The accrual figure's sign treatment is THREE-WAY -- `> 0` green with an explicit `+`, `< 0` the danger token, `== 0` neither -- and NOT `/investment`'s `>= 0` |
| **R-AN** | 2026-07-27 | The two MODELLED rows render CENTS; the four rows around them stay whole-dollar |
| **R-AR** | 2026-07-27 | X-g4 and X-c2c4 ship as TWO commits: X-g4a the PORT, then X-g4b the WHOLE deletion |
| **R-AS** | 2026-07-27 | `_interest |
| **R-AT** | 2026-07-27 | The ported 52-period drift oracle exercises ALL THREE of the fold's tiers, not the one the original had |
| **R-AU** | 2026-07-27 | `test_asset_fold_parallel |
| **R-AV** | 2026-07-27 | X-o ships the predicate alone; the second debt-free producer gets its OWN step, X-q, and both are built now |
| **R-AW** | 2026-07-27 | The per-account projection dict carries the seam's `LoanFigures`, and the six flat copies go -- in its OWN commit, AFTER the defect is fixed |
| **R-AX** | 2026-07-27 | "Debt-free" stays LOAN-ONLY, and the surfaces say so |
| **R-AY** | 2026-07-27 | A payoff date that is already PAST is reported, and the chart falls back |
| **R-BA** | 2026-07-27 | `compute_net_worth_horizon` DELETES, and its tests read the horizon where the route reads it |
| **R-BB** | 2026-07-27 | NO "Paid Off" badge on the archived drawer, permanently, and the drawer's WRONG NUMBER is recorded as N-103 with X-e as its owner |
| **R-BC** | 2026-07-28 | The milestone's `kind` is deleted at BOTH ends, so X-q2's mutation guard extends one level down and the payload contract stays STRUCTURAL |
| **R-BD** | 2026-07-28 | The debt summary becomes a frozen value object that CARRIES the outlook, the DTI trio collapses to ONE nullable field, and the dashboard track COMPOSES the summary instead of copying it |
| **R-BE** | 2026-07-28 | A borrower whose loans are ALL retired earns the caption "All loans paid off" on the /savings Liabilities footer, and nowhere else |
| **R-BF** | 2026-07-28 | `_project_one_account` states each of its two conditions ONCE, and the missing-baseline guard is HOISTED to cover both arms |
| **R-BG** | 2026-07-28 | `compute_net_worth_series` stops publishing `assets` and `liabilities` |
| **R-BH** | 2026-07-28 | `DtiMetrics` stores ONE fact: the ratio |
| **R-BI** | 2026-07-28 | The debt track's DOUBLE projection is recorded as finding N-109 with its own step, not folded into X-s3 |
| **R-BJ** | 2026-07-28 | The two residues X-s does not reach get ONE new step, and the `ad` dict rides with them |
| **R-BK** | 2026-07-28 | The projection becomes a frozen `AccountProjection` with a NESTED `LoanDetail \| None` |
| **R-BL** | 2026-07-28 | One predicate on the CONTEXT, plus a hoist inside the region -- not the plan's "hoist all four to the build entry" |
| **R-BM** | 2026-07-28 | Derive what can be derived, gate the rest, and delete two homes doing it |
| **R-BN** | 2026-07-28 | A milestone's LABEL is its identity, and a duplicate is a display outcome rather than a defect |
| **R-BO** | 2026-07-28 | `compute_property_equity` owns its no-baseline state, and the "two doors" claim is corrected in every docstring that made it |
| **R-BP** | 2026-07-28 | The Horizon's band literals are DERIVED, and the gate asserts the partition |
| **R-BQ** | 2026-07-28 | The gate is comment-stripped, and it carries controls on ITSELF |
| **R-BR** | 2026-07-28 | The fabricated no-baseline HERO is recorded, not changed inside a fix commit |
| **R-BS** | 2026-07-28 | `principal_paid_fraction` becomes a `DebtSummary` FIELD, set at that value object's one construction site, and the second debt producer is deleted |
| **R-BT** | 2026-07-28 | `_project_debt_accounts` is INLINED into `compute_debt_summary` and deleted |
| **R-BV** | 2026-07-28 | `/savings` does not render the new field, and X-u adds no pixel |
| **R-BW** | 2026-07-28 | The no-baseline state gets ONE answer, and it is a NAMED exception with ONE handler -- not nineteen invented values |
| **R-BX** | 2026-07-28 | `BalanceContext |
| **R-BY** | 2026-07-28 | TWO guards keep their own explicit handling, and the reason is written at each |
| **R-BZ** | 2026-07-28 | X-t2's `/savings` no-baseline degradation is REVERSED, and this row is where that is findable |
| **R-CA** | 2026-07-28 | N-113 is closed by DELETION, not by inventing a display vocabulary for a state no page can now reach |
| **R-CB** | 2026-07-28 | The census instrument becomes a permanent GATE, because that is the only part of this step that cannot go stale |
| **R-CD** | 2026-07-29 | 204 answers a POLL, never a BUTTON |
| **R-CE** | 2026-07-29 | The exception carries the user it was RESOLVED for, and the event logs both ids |
| **R-CF** | 2026-07-29 | The sweep's coverage claim becomes a PINNED LIST, and it grades the fragment answer too |
| **R-CG** | 2026-07-30 | ONE per-account record per render, and the dense period map rides on it |
| **R-CI** | 2026-07-30 | Every RECORD container crossing a module boundary on this path is a value object; the four that are not records stay dicts, with the reason written |
| **R-CJ** | 2026-07-30 | A map that is TOTAL over its input is INDEXED, not defaulted |
| **R-CK** | 2026-07-30 | A total map is INDEXED at every reader, and indexing is not a policy -- it is the absence of one |
| **R-CL** | 2026-07-30 | `TrendPoint |
| **R-CM** | 2026-07-30 | The one-record design STANDS and the INSTRUMENT is what changes |
| **R-CN** | 2026-07-30 | A type that crosses this package's boundary is re-exported, and a region's type lives with its own fields |
| **R-CO** | 2026-07-30 | Both records are typed at their producer, and the unreachable nullable X-w4 wrote goes with them |
| **R-CP** | 2026-07-30 | ONE classifier answers the CATEGORY, and both existing questions BUILD ON it |
| **R-CQ** | 2026-07-30 | The classifier's module is RENAMED to `app/services/account_category |
| **R-CR** | 2026-07-30 | ONE category map per render, and the Jinja spelling of the liability key is GATED |
| **R-CS** | 2026-07-30 | The three coverage units are each quantized ONCE, from the RAW ratio -- and the two inputs that cannot be `None` stop being nullable |
| **R-CT** | 2026-07-30 | The category rides on `AccountProjection`, and the per-account map X-z2 threaded is DELETED |
| **R-CV** | 2026-07-30 | Every citation, count and control the two reviews found, plus an O(1) category reverse lookup in `ref_cache` |
| **R-CW** | 2026-07-30 | The raw-ratio rule STANDS, and the shape it can render is recorded rather than discovered later |
| **R-DF** | 2026-07-31 | The hole's reconciliation defect is finding N-128 with plan step X-l as its owner, and R-CX's false sentence is corrected in the same commit |
| **R-DG** | 2026-07-31 | The whole residue is fixed and RE-REVIEWED before anything is committed; nothing ships piecemeal |
| **R-DI** | 2026-08-02 | The residue arm is DELETED, and a posting the source rows cannot explain becomes a LOUD refusal instead of a silent absorption |
| **R-DJ** | 2026-08-02 | Two distinct civil-day TYPES, and `ReconciledThrough |
| **R-DL** | 2026-08-02 | The anchor reconcile resolves its two ledger accounts ONCE per account rather than once per correction, and the fix rides INSIDE X-d |
| **R-DX** | 2026-08-03 | The IDENTITY invariants go to the DATABASE tier where they CAN go |
| **R-DZ** | 2026-08-03 | R2 for anchor corrections is a KEY-SHAPE change with NO migration in it, and it ships FIRST, alone, as plan step X-ai-r |
## 4. What this record deliberately does not carry

* **The running-state narrative.** ~1,000 lines of "X-n is DONE" entries with their measurements,
  branch topology and deploy notes. Superseded by the commits and by `git log`.
* **Per-step measurement tables and review residue.** Each step's adversarial reviews, their
  findings, the corrections they forced and the figures behind them are in that step's commits and
  in the commit that recorded its rulings.
* **Anything already contradicted by the code.** Where the plan document's prose and the tree
  disagreed at extraction, nothing was carried forward on the prose's authority.
