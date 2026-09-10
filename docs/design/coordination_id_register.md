# Coordinator id register, 2026-09-06/07

> **HOLDING PLACE, NOT A PLAN OF RECORD.** This file exists because the findings and developer
> rulings of 2026-09-06/07 were made while the registry lane was occupied, and lived only in
> untracked files and session transcripts. It governs NOTHING. Each arc's registry pass moves its
> content into `docs/plans/` (`ledger.md`, `steps.md`, `rulings.md`), and
> **this file is deleted once consumed**. Where it disagrees with `docs/plans/` or with the code as
> committed, they win.

## Coordinator ID register (session shekel-7a)

Authority: the developer confirmed 2026-09-06 that this session holds authority over COMMITS,
PUSHES, PRs, MERGES and REGISTRY IDS. Lanes request; this file is the record.

Base: origin/dev `2bce6a90`. Last updated 2026-09-06 evening.

Convention: a NEW ruling id CARRIES ITS ARC'S PREFIX (developer 2026-09-05). The shared LETTER run
(R-JA..R-KD) is CLOSED; nothing new joins it. TRAP: a naive grep over rulings.md reads its own
PROSE. `R-BAL130` is a counterexample in the convention text at `rulings.md:17`, NOT a minted id --
only R-BAL1..R-BAL5 existed. Ceilings below exclude it. TRAP: never grep sibling worktrees for a
free id. A worktree created after you look is invisible.

### Next free (ask before taking any of these)

| arc | ruling | ledger | step |
|---|---|---|---|
| balance | R-BAL15 | BAL-479 | `X-cc` -- **ADVANCED 2026-09-09 EVENING, AND THE ROW'S OWN MECHANISM IS WHAT FAILED.** `R-BAL10` WAS MINTED TWICE on 2026-09-09: `refactor/balance-x-au-f` took it (plus `R-BAL11`, `R-BAL12`) for *a recurring loan payment's amount lives on the parent transfer*, and `docs/transfer-invariants-endpoint` took it for *a transfer's two legs are not rows in `budget.transactions`*. **The two causes are NOT the same and an earlier draft of this cell blurred them, which the loan-payment session itself corrected:** it picked `R-BAL10` by GREPPING `rulings.md` for used ids -- the move `CLAUDE.md` forbids -- and never opened this register at all, landing on the offered number by coincidence; the endpoint session read this row and minted against it without reserving. So one cause is a SKIPPED step and the other is that nothing makes claiming an id atomic. Both are the R-GW / R-EX class one layer up, and the plan gate's unique-key arm reads ONE tree, so it stays silent until the merge. Resolved by the lead coordinator (developer, 2026-09-09: *in the event of a conflict shekel-5c session will take the lead on the coordinator role*): the pushed branch KEEPS `R-BAL10` because conventions rule 10's basis is that a rename orphans citations in IMMUTABLE places and it is cited in three commit messages (`ce8bf485`, `a1fe229e`, `16f83aa0`), a steps row, a README bullet and an archive record; the uncommitted branch moved to `R-BAL13`, cited in nothing immutable. Mechanical, not seniority. **`BAL-475` is taken** (the transfer-invariant endpoint clause) and `BAL-476` by the loan-payment branch. **`BAL-477` and `X-cc` were OFFERED and deliberately DECLINED, not merely unused**: that session drafted both for an `extra_principal` finding and backed them out before committing, because rule 12 wants a README specification and the balance README stands at exactly 1260 of its 1280 cap -- its 20-line headroom floor -- with no completed span left to archive. Rule 4 makes a binding cap the DEVELOPER's question, so the finding was raised with him rather than parked under a wrong owner. If he frees the lines it consumes `BAL-477` and a step id; if he attaches it elsewhere, neither is consumed. Ceiling advanced past BOTH sittings; the X-b run is EXHAUSTED at X-bz, `X-ca` and `X-cb` are taken, `X-cc` is free **`BAL-477` was CONSUMED 2026-09-09** by the `X-au-f` lane, owned by `X-bm`: `Transaction.pay_period` is read by no amount rule after leaf 2's re-split, kept rather than dropped because five batch loaders apply that option set and their other reads are uncensused. Ceiling advanced to `BAL-478`. It was taken off this row's published ceiling rather than reserved through the coordinator, which is the same read-not-reserve shape one entry up -- harmless here only because no second session wanted that number. **`R-BAL14` GRANTED 2026-09-10 to the `X-au-f` lane THROUGH the coordinator** -- the first id this evening reserved rather than taken off the ceiling, which is the correction the two entries above exist to force. It records the developer's ruling that the parent-transfer cutover is ONE leaf over all 169 rows: *the parent gains its producer* and *the parent's column empties* are two halves of one act, so a boundary between them leaves a commit where every reader asks a home that is still full. **It supersedes nothing** -- `R-BAL10` stands, and that is the point worth recording: the DESIGN was right and the leaf BOUNDARY was wrong, which are different errors with different remedies. **`X-au-f-3` is a CONSUMED step id**: absorbed into `X-au-f-2` rather than withdrawn, its three ledger rows (`N-450`, `N-451`, `BAL-476`) re-pointed, and it will never ship under its own name. |
| bank_import | R-BI3 | BI-485 | X-gr |
| recurrence | R-R63 | REC-517 | -- |
| pay_calendar | R-PC63 | PC-507 | -- |
| recurrence | R-R63 | REC-516 | -- |
| salary | R-SAL19 | -- | -- |
| credit_card | R-CC13 | -- | -- |
| shared | -- | N-550 | -- |
| shared | -- | F-22 | -- |

`N-` is a SHARED run across arcs. A lane never self-assigns one. `N-548` is HELD for the
migration-lint coverage finding, owner `developer-decision`. Unminted.

### Issued 2026-09-06

| id | arc | for |
|---|---|---|
| R-SAL14 | salary | developer's per-payday-function revision of S3-d (shipped, PR #283) |
| N-547 | salary | /retirement prices the current period twice; owner C12 (shipped, PR #283) |
| R-SAL15 | salary | the feed prices a payday ON DEMAND rather than carrying a window |
| R-SAL16 | salary | S3-e splits at the money line (S3-e-1 / S3-e-2) |
| R-SAL17 | salary | `models_employee` deleted for `is_payroll_linked` |
| R-BI2 | bank_import | the X-gi-2a fork: X-go -> X-gp -> X-gi-2a; supersedes R-IV |
| BI-481 | bank_import | the R-IV docstring names X-gn; the step that re-arms it is X-gi-2a |
| BI-482 | bank_import | five further X-gi-2 orphans: ReviewBounds.calendar_opens, MatchProposal.bank_amount/.app_amount/.confirms/.redate_gap |
| BI-483 | bank_import | sweep guard staging regressed from three withholding arms to one |
| X-go | bank_import | delete the schema's member cap |
| X-gp | bank_import | merge the attribution select and the consent box into ONE control |
| X-gq | bank_import | SPLIT `_variance.py` (999 of the 1000 ceiling) BEFORE X-gp writes in it -- pure move, own PR, own review; N-365's shape is splitting a module while changing a money gate in one commit |
| N-549 | shared (bank_import files it) | **A HARDCODED FUTURE DATE DECAYS INTO THE PAST**, and nothing re-checks it because nothing fails until the day it does. **POPULATION OF THE SHAPE THAT FIRED: ONE, already fixed** (`test_templates.py:1863`, PR #291) -- the row must SAY that, or "there may be siblings" reads as an open hazard forever. The MIRROR shape (a calendar-DERIVED value asserted equal to a literal) is 15 assertions across 7 files, 2 verified safe, **13 needing per-site reading**. Scope: the UNFROZEN trees only -- `tests/test_routes/`, `test_models/`, `test_integration/`, `test_scripts/`; `tests/test_services/` is OUT (its conftest pins today to 2026-03-20). **REMEDY IS A SIMULATION, NOT A GREP**: run the unfrozen trees under a time machine with the clock advanced six and twelve months. A static sweep only finds shapes someone thought to describe. No owner yet. |
| X-ca | balance | Split `__table_args__` out of `app/models/transaction.py` (999 of the 1000 ceiling). **Developer-approved 2026-09-07 as its OWN step and PR, a pure move** -- rejected: folding it into the CHECK leaf, and raising the pylint ceiling. Measured 1011 -> 662 with a 384-line sibling, both 10.00/10, constraint inventory diffed IDENTICAL. GATES the X-bv CHECK leaf. |
| REC-516 | recurrence | **ISSUED BUT HELD UNFILED.** After X-bz merges no code can compute `occurs_on` for an existing row; both automated prod backups on disk are pre-stamp, so restoring one yields a permanently un-backfillable DB whose next maintain pass HARD DELETES (`_maintain` -> `db.session.delete`; `transfer_recurrence` -> `delete_transfer(soft=False)`). Also: `recurrence:R-R46` becomes FALSE at merge. **CANNOT BE FILED YET -- it has NO OWNER and the plan gate refuses that** (`_registry.py:234`): `developer-decision` needs the DATE the fork was taken (`:259`), `operator` needs the QUESTION stated (`:253`). It is a REAL design fork -- remedies span from folding the occurrence walk into the maintain pass (no backfill ever needed) to making a stale restore unrepresentable. **QUEUED FOR THE DEVELOPER.** |
| BI-484 | bank_import | `_variance.py`'s module docstring carries an argument that partly describes code in `_landing.py` after X-gq's split. **A DELIBERATE ACCEPTANCE, not an oversight**: the argument is genuinely ONE argument (the module's own first line says so), and cutting it to serve a line count would fragment a design record -- N-365 pointing the other way. **THIS ROW EXISTS NOWHERE BUT HERE** until bank_import's registry pass files it; every other bank_import id is durable in a pushed commit message. |
| X-bz | balance | RETIRE `scripts/stamp_occurrences.py`, its `entrypoint.sh:288-296` block and sentinel, and its tests. Its own STEP, not an X-bv leaf -- its subject is retiring scaffolding whose window has closed, and X-bv is invalidated BY it rather than composed of it. Measured: `X-by` already exists, so `X-bz` was the next free single-letter id. |
| BAL-471 | balance | `test_stamp_occurrences.py:259` hand-builds `due_date=None` instead of calling the producer, so the control for the PROVENANCE FILTER stays green while grading a shape the app can no longer produce. Filed separately from X-bz because the CLASS outlives the script. |
| R-SAL18 | salary | `DerivedPeriod.is_projected` -- a NAMED ACCESSOR over `period_id is None`, so the timeline asks a DOMAIN question rather than reading a primary key. Deletes `saved_through`, both call sites' wiring and the arch census; PRESERVES `pay_calendar:C2-f2c` because the ORM `PayPeriod` grows the same property returning False. Binds S3-e-2 |
| R-BAL6 | balance | reserved for X-bv's re-specification (unminted, blocked on developer) |
| BAL-470 | balance | reserved for X-bv's new row (unminted, blocked on developer) |

pay_calendar asked that `R-PC63` and `PC-507` stay UNMINTED and unreserved: its one new gap is a
property of a PROPOSED design, not of shipped code, and a finding about a planning artifact is a row
nowhere. It lives as fork 7.5 of `docs/design/pay_calendar_from_scratch.md`.

### THE REGISTRY LANE -- one PR touching docs/plans/ at a time

Order is by READINESS, not by arc. Code-only PRs flow around it freely and in parallel.

| # | holder | what | state |
|---|---|---|---|
| 1 | **salary** | R-SAL15/16/17, S3-e split into S3-e-1 / S3-e-2 | HOLDING NOW; iterating on 4 gate failures |
| 2 | bank_import | tick X-gi-3, close BI-479/480, BI-481/482/483, R-BI2, mint X-go + X-gp | queued; drafting outside docs/plans/ |
| 3 | coordinator | tick C14-e-3 | queued behind the code merge |
| 4 | balance | re-specify + decompose X-bv, R-BAL6, BAL-470 | not ready; blocked on developer |

**Why serialization is not tidiness.** Salary splits S3-e; bank_import ranks X-go and X-gp ahead of
X-gi-2a. BOTH insert rows into the DENSE order column. Two branches doing that merge with perfectly
clean TEXT and a broken rank SEQUENCE, and the plan gate is the only reader in this repo that sees
that column as a sequence. Nothing else catches it.

**The owning lane re-applies its own patch ON THE MERGED TREE**, taking every derived number from
the plan gate rather than its own counting. Numbers computed against a lane's own base merge with no
conflict and go silently stale.

### Developer answers my register must carry forward

**`implementation_plan_salary.md`'s cap: LEAVE IT** (developer, 2026-09-06). Nothing is blocked
today, so no raise and no archive. **Rule 5's escape stays UNSPENT.** The NEXT lane to open that
file hits the 300-line floor immediately and should
**ARCHIVE the shipped R14 span -- R14 / R14-a / R14-b -- rather than ask him again.** Hand this to
whoever opens it next; the question is answered. Note for them: rule 5 archiving frees BODIES ONLY,
since every indexed step keeps a checkbox entry in both directions.

**balance's two leaves ship in this ORDER (coordinator call, 2026-09-06):** `X-bz` (the retirement)
merges FIRST, then `X-bv` (the door leaf). Merging the door leaf first opens a window in which
leftover rows CARRY a due date while `stamp_occurrences.py` still reads `due_date IS NOT NULL` as
proof of carry-forward provenance -- so it can stamp a leftover with an occurrence it does not
answer, WRITING FINANCIAL ATTRIBUTION. The developer ruled they ship as separate leaves; he did not
rule an order, and two PRs merged retirement-first satisfies that decomposition exactly.

Premise verified against PRODUCTION (`shekel-prod-db`, read-only, 2026-09-06), not the clone: 626
template-linked rows, 620 carrying `occurs_on`, 6 NULL -- matching the residue the script's own
docstring says it deliberately leaves. 0 template-linked rows with a NULL due date.

**bank_import's run, ruled with the lane 2026-09-06:** registry pass -> `X-gq` (the split) -> `X-gp`
(MOVES MONEY, own PR) -> `X-gi-2a`. X-gq ranks AHEAD of X-gp.

### CORRECTION to an attribution I got wrong

The **index-versus-worktree** finding on `X-bz` was the neutral REVIEW's, NOT the balance lane's --
the lane had written that exact trap into its own handoff hours before walking into it. I credited
the lane in #289's PR body and here. The finding's own claim is the transferable part: **a green
pylint, a green shellcheck, 306 targeted tests, 28 deploy tests and a 13162-passed suite can ALL
grade the WORKTREE while the INDEX ships something else, and NO GATE IN THIS REPO LOOKS AT THE
INDEX.** After a `git rm`, check `git status` for unstaged siblings and grade with
`git show :<path>`, never the file on disk.

### Open PRs

| # | head -> base | what | state |
|---|---|---|---|
| 286 | `feat/pay-calendar-c14e-3` -> `dev` | C14-e-3, MOVES MONEY, code-only | CI running. **Its base was silently retargeted to `main` when dev was deleted; set back by hand.** |
| 287 | `feat/x-gi-2` -> `dev` | X-go, code-only, 0 `docs/plans/` files | CI running |

STANDING CONDITION: if salary stalls on the `implementation_plan_salary.md` cap question (311 of
320) waiting on the developer, the lane FLIPS to bank_import rather than blocking it behind a
question it cannot answer.

### MY OWN registry pass (C14-e-3's tick) -- its blocker, RESOLVED 2026-09-07

**The defect** (found by pay_calendar, not by me): closing `N-398` removes its `ledger.md` row while
`app/` still cites the id, and `tests/test_services/test_paycheck_calculator.py`'s
`test_the_plan_identifiers_this_step_cites_actually_exist` pins
`("ledger.md", "| pay_calendar | N-398 ")`.
**It fires exactly once, on the commit AFTER the code half** -- PR #286 alone leaves `ledger.md`
untouched so the gate passes; the registry tick removes the row and it fails. Individually green,
jointly red.

**THE DISPOSITION, settled by PRECEDENT in the gate's own docstring, verified not believed:**
`N-390` LEFT that parametrize list at `balance:X-bh-2`, which closed it. Measured on dev:
`grep -c "N-390" docs/plans/ledger.md` = **0**; absent from the list; and
**STILL CITED FROM `app/` in FIVE places** (`pay_schedule.py:54`, `:233`, `payroll_basis.py:41`,
`paycheck_calculator.py:42`, `:44`). The gate does not care. Its docstring says why no general arm
can exist: it would fire on 88 live citations, because an archived ruling's text keeps its
citations. `ledger.md`'s preamble agrees -- a row leaves when its fix SHIPS, and closed rows move to
their arc's as-built record under the same id.

**So the pass does exactly three things, and touches NO `app/` docstring:**

1. Move N-398's row to pay_calendar's as-built record under the same id.
2. Delete `("ledger.md", "| pay_calendar | N-398 ")` from that parametrize list.
3. Update the gate docstring's narrative to record N-398 leaving as N-390 did.

**TWO CORRECTIONS to the report that raised it.** `_derive.py` carries ZERO N-398 citations on
either tree -- the citing files are `pay_calendar/_rhythm.py`, `services/paycheck_calculator.py` and
`utils/business_days.py`. And **this is NOT a C14-e-3 problem**: `business_days.py` is already on
dev, dev already carries FOUR citations, and C14-e-3 only raises `_rhythm.py` from one to three. The
gate would fail on dev's own citations with or without that branch.

### Watcher scripts: TWO of mine gave FALSE readings in one session

Recorded because I nearly merged five PRs on the first one.

1. `out=$(gh pr checks "$n" 2>&1) || true` then `rc=$?` -- **the `|| true` RESETS `$?` to 0**, so
   the pending sentinel (8) was never seen, every PR read as settled on the first pass, and "no line
   says fail" reported GREEN. All five were still running.
2. `for n in $remaining` -- **this shell is zsh, which does NOT word-split an unquoted parameter**,
   so all five numbers went to `gh` as one branch name and it reported one settled PR that does not
   exist.
Both were caught only because the result contradicted what I expected. Use `set -- a b c` +
`for n in "$@"`, and capture `rc=$?` on the line immediately after the command with nothing between.
