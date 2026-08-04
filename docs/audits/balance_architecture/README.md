# The cash balance architecture: the plan of record

**This is the ONLY live document for the balance arc, and it carries the work that REMAINS.**
Amendments are edits HERE, a shipped step gets its checkbox ticked with its commit hash HERE, and no
new planning documents get written for this arc. The rules are Section 9; read rule 6 first if you
are recording a finding.

**Two as-built records hold what is already done. Neither governs anything.**

| record | what it holds |
|---|---|
| `archive/cash_arc_as_built_2026-07-27.md` | Phase X as built: the running state narrative, every shipped step from **X-a** to **X-g3b** with its measurements and firing controls, and the 10 findings they closed. **In production since 2026-07-28 (PR #65, merge `69a527cd`)** |
| `archive/loan_arc_as_built_2026-07-26.md` | The LOAN half, complete and in production (PR #64, merge `88c79857`). Phases A-F, rulings D1-D5 / R-A / R-C / R-D / R-E, and the 75 findings that arc closed |

Everything else that ever governed this work is in `archive/`, indexed by `archive/README.md`.

**How this document works changed on 2026-07-27 (rulings R-AO and R-AQ).** The findings ledger was
triaged against the CODE: of 41 open rows, 10 named a live step that owned them and 29 did not, four
of those naming a resolver that had already SHIPPED. Nine new steps now carry them, **no finding is
unowned**, and there is no deferred category at all -- **Section 9 rule 6 fixes a closed owner
vocabulary and rule 7 rules that cost is never a ground for deferral.** Rule 6 is a GATE, shipping
as plan step X-h's fifth commit. Read those two rules before recording a finding.

## Where the arc stands

**BOTH HALVES ARE NOW IN PRODUCTION.** The loan half shipped at PR #64 (merge `88c79857`); the
CASH half shipped 2026-07-28 at **PR #65** (merge `69a527cd`, image `sha256:5cb8ec33`) -- 89
commits, **zero migrations**, CI green on its first sight of any of them, prod healthy in 10s with
no rollback.

**Branch topology, re-measured 2026-08-01 (evening):** everything is MERGED. `origin/main` carries
PR #66, #67, **#68 (the N-133 residue)**, and three test-infrastructure PRs (#69, #70, #71). The
residue shipped the F1 ruling (the OPENING exception deleted from both walks), step 2's opening half
(`account_anchor_history.observed_on`, **migration `c4a19e7b2d80`**), F2's remainder, and F4, F5,
F6, F7, F8, F9, F12 -- plus the eight items its own second adversarial review found
(`anchor_settle_partition.md` Section 9).

> **PRODUCTION RUNS `e5f27154` and `main` == `e5f27154`, so PROD IS CURRENT WITH `main`.
> X-ai-r IS IN PRODUCTION (PR #81, merged and deployed 2026-08-03T18:28Z).**
> Re-measured from the container itself, not from a paragraph: `docker inspect
> shekel-prod-app` reports image `sha256:8a51f9059187` carrying
> `org.opencontainers.image.revision = e5f271544561b7d465dc46d47e2d1a620aa67467`, **0
> restarts**, `health=healthy`, healthy **15s** after `shekel-deploy` recreated it
> (`5cd2c22d13fc -> 8a51f9059187`, cosign-verified, no rollback).  So step 3 (PR #76),
> X-af (PR #77), X-ae (PR #79), X-aj1 (PR #80) AND X-ai-r (PR #81) are all live.
>
> **X-ai-r changed production DATA with no migration, and the post-deploy census matched
> the prediction exactly.**  The deploy hook runs
> `backfill_all_account_anchor_postings_after_migration` unconditionally, so it wrote **+2
> journal entries / +4 postings** (318 -> 320, 643 -> 647) at deploy.  Verified against a
> full 389-cell snapshot taken BEFORE the deploy: **exactly 4 cells moved, all the
> 2026-06-03 Checking pair** -- period 5 `+$3,054.36 -> +$200.00`, period 6 `-$2,854.36 ->
> $0.00` -- trial balance held at `$0.00`, migration head unchanged, anchor history
> unchanged at 77 rows, and the count of periods carrying an unbalanced ledger account was
> **10 before and 10 after** (the finer key locked no new period).  The two entries are
> `319` (period 5, the correction) and `320` (period 6, the R2 REVERSAL -- it lands in the
> period of the postings it undoes and carries their date, which is why it is one of the
> six entries whose `entry_date` sits outside its own period; its pair nets to `$0.00`).
> Migration head is unchanged at `d7c1f4a9e603` on every one of these --

> **none of these ships carried a migration**, so each was a pure image swap and a rollback
> is a pure digest revert.  Step 3 was measured to move **0 of 16,536 seam leaves**, so no
> rendered figure should have changed and that is what a post-deploy check looks for.
>
> *An earlier version of this note read "PRODUCTION RUNS `6f4b4cbf`", and a draft of THIS
> correction accused it of having been false when written.  That accusation was itself
> false and is withdrawn.*  Measured: the note was written in `7c2ba9ff` at
> **2026-08-02 18:42Z** and PR #79 merged at **23:12Z**, so it was TRUE for 4h30m and went
> stale when X-ae deployed at 23:28Z -- which the paragraph below it then recorded
> correctly.  A neutral review caught the fabrication, in the very paragraph congratulating
> this document on citation hygiene.  Kept visible: an invented provenance claim is the
> same defect class as an invented line number, and it is easier to commit while writing a
> correction.  **The rule that survives is the operational one: re-measure this note from
> `docker inspect` at each edit rather than carrying a sentence forward.**
>
> *State the merge prod carries, never the word "current": merging builds and signs an
> image but `shekel-deploy` is a separate manual run, so "current" is false from the next
> merge until that is run.*

**The test suite grew two clock gates on 2026-08-01** and they matter to this arc specifically,
because three of the five defects behind them were fixture-clock bugs this arc's own work created
(N-131, N-132, R8). CI now runs with `TZ: Pacific/Kiritimati` so a `date.today()` /
`display_today()` mix fails there, and a weekly calendar sweep runs the suite at a leap day, both
sides of a year boundary, a month end and the first of a month. **Read `docs/test-suite-clocks.md`
before writing a fixture that touches an anchor, an assertion instant or a due date** -- this arc
writes more of those than anything else in the codebase.

**F11 is MEASURED and CLOSED (2026-08-01, `anchor_settle_partition.md` Sections 10 and 11):
R-DH (d) STANDS AS RULED.** Measured on a fresh production clone, it moves the current period's
projected end balance from `-$19.95` to `+$514.13` (838 of 15,682 seam leaves, all Checking, from
the current period forward; today's own balance does not move) -- and F11's conclusion from that
does not survive audit. The settle side already carries **`$3,142.61`** of the identical "recorded
hours after the assertion" exposure against the entry side's `$623.70`, accepted by explicit ruling
in R-DH (a); outside one outlier day the alternative's residual is twice as large (`$177.43`
against `$89.62`); and the evidence that the ruling could never win was an artifact of a hidden
form field (0 of 74 audited entry UPDATEs ever touched `entry_date`). **Two recommendations were
withdrawn on measurement in the course of closing it**, one of them a from-scratch redesign
(per-movement reconciliation coverage) that an adversarial review showed re-opens `-$4,001.42` by a
different route -- recorded as REJECTED in Section 11 rather than deleted.

**S1-c IS COMPLETE AND GREEN on branch `feat/entry-posting-date` (`b305b7b5`, 2026-08-01), and
ruling R-M was RE-RULED in the course of it** (`anchor_settle_partition.md` Section 12). Building
R-DH (d) as Section 10.6 recommended surfaced the defect underneath the fork: **one column,
`transaction_entries.entry_date`, was carrying two facts** -- R-M defined it as the day the
purchase happened and R-DH (e) as the day the money hit the account, and every reconciliation rule
built on it inherited the ambiguity. It SPLITS into `purchased_on` (R-M's guard, unchanged, on the
column it was always about) and a nullable `settled_on` (the day the bank was SEEN to take it).
**NULL means "not observed to have posted" and is NOT reconciled, so the engine never guesses a
posting day** -- which is what Section 11.1's surviving premise demanded and what R-DH (d) as
written could not deliver. A reconcile step at the true-up is how an observation gets recorded.

**Verified before the PR** (`anchor_settle_partition.md` Section 13): the suite is **7,724 passed /
0 failed** under both `America/New_York` and CI's `TZ=Pacific/Kiritimati`; `pylint app/` and
`scripts/` 10.00/10; on a fresh production clone **no figure moves** (0 of 15,682 seam leaves over
9 accounts, 427 grid cells and 5,978 daily points), no date is invented (`settled_on` starts NULL
on all 82 rows), the migration runs both directions with the downgrade REFUSING once any row
carries an observed posting day, and R-DH (f)'s split lands exactly on the hand-computed
`-$427.22` / `-$160.05` halves with `period_timing` netting to `$0.00` across history. The 157-test
conversion, the five tests Section 13.2 owed, and the three holes a neutral adversarial review
found in it are all recorded in Section 13. **The whole step is ONE commit** -- the migration and
the tests written against its schema cannot revert separately without leaving a tree that fails.

**Step 3 IS MERGED TO `main` and it did NOT ship the checker this document specified**
(commit `d3e3d82a`, 2026-08-01; **PR #76**, merge `6f4b4cbf`, 2026-08-02;
`anchor_settle_partition.md` Section 14). The developer ruled the fence must be structural rather
than a detector. **An earlier version of this paragraph justified that with an AST census showing
the checker would have been BLIND at `account_posting_service/_sync.py`'s bare-local
`earliest <= latest` -- and step 3's own adversarial review REFUTED that, so 14.1 withdrew it**:
adding those names to the vocabulary matches them, and resolving a `Name` to its `Assign` is one
astroid hop. What survives is the ruling itself, which was never a claim about lint, plus 14.5's
addition that a type and a checker fence COMPLEMENTARY holes rather than substituting for each
other. The correction is kept visible because the withdrawn claim was cited again on 2026-08-02, in
plan step X-ag's first draft. What shipped instead is
`cash_ledger.ReconciledThrough`: a type carrying one day and one method, `covers`, with **no
ordering defined against a civil day**, so a restatement of the rule is a `TypeError` rather than a
lint finding. The read replay's stable SORT and the posting walk's `<=` LOOP -- the same algorithm
in two spellings, which nothing had named -- are now the same loop over the same call, and
`merge_anchor_and_cash_events` is deleted. Measured on a production clone: **0 of 16,536 seam
leaves move**, and the new posting walk reconciles the ledger the OLD walk wrote to `(0, 0)`
changed with the trial balance at `$0.00`.

**X-ae IS IN PRODUCTION** (2026-08-02; **PR #79**, merge `a778703f`), **and it SHIPPED WIDER
THAN IT WAS SCOPED.**  CI green on its first sight of it, including the `TZ=Pacific/Kiritimati`
clock gate.  **Deployed 2026-08-02**: image `sha256:4e6adc8dd3da`, cosign-verified, container
healthy in **15s** with **0 restarts** and no rollback -- verified from `docker inspect`, and
the digest confirmed against the `a778703f` build run's own log rather than assumed from the
`:latest` tag.  **The ship carried NO migration** (zero files under `migrations/` between
`6f4b4cbf` and `a778703f`), so it was a pure image swap and a rollback is a pure digest
revert -- the caveat about a rollback leaving old code on a new schema does not apply. As ruled it closed **N-136** -- a reachable unhandled 500 at four `str.isdigit()` doors, one
of them the LOGIN path -- and every one of the four raises was REPRODUCED against a real request
before anything was written. Then two neutral adversarial reviews refuted its central claim
independently: it called itself *"the ONE answer to what row does this string name"* while two
larger surfaces were still lax, and the developer ruled both into the same step.

**What the reviews found is more serious than what the step was written for.** Werkzeug's `<int:>`
converter has a `r"\d+"` regex compiled without `re.ASCII` and a bare `int()`, so across **123 path
parameters** `/accounts/١/details` answered byte-identically to `/accounts/1/details`, and a long
enough path segment raised `ValueError` inside `url_adapter.match()` -- ahead of the view, ahead of
`@login_required`, ahead of any session. That is an **UNAUTHENTICATED unhandled 500** (**N-140**),
worse than any of N-136's four doors, and reachable in production because `gunicorn.conf.py` raises
`limit_request_line` to 8190. One converter registration closes all 123. Separately, `fields.Integer`
read seven spellings of an id and **73 `*_id` declarations** used it (**N-141**); all 73 now use a
`RowId` field consuming the same rule, and the suite passed 7,768 / 0 immediately after the
conversion, so nothing depended on the laxness.

**Four claims of mine were wrong and are recorded as corrections rather than quietly fixed**: the
`int4`-overflow theory (measured false at all three doors), "attempt the parse" as sufficient
(`entry_ids='١٠٦'` really stamped entry 106), ASCII as sufficient for one-spelling (`"007"` still
named row 7 until the parse was made to round-trip), and the collateral door, which kept a `.strip()`
and so still cleared a real link on a forged non-breaking space -- verbatim the behaviour the step
had just added a docstring saying was closed. **N-139 was refuted TWICE and is reframed**: the
method-name checker it proposed reports clean over a bare `try: int(raw)`, which is the exact form
the original ruling specified and measurement rejected. The query-string surface is deliberately NOT
here and is **N-142**'s, because those ~30 call sites are mixed row ids and non-ids (`offset=0` is
meaningful) and need a per-site ruling.

**N-145 IS ANSWERED, and the developer refused all four options it offered** (2026-08-02, rulings
**R-DN**..**R-DQ**): *"I want to make the fences structurally unnecessary."* That is step 3's own
ruling -- structural, not a detector -- applied to `shekel-transaction-status-bypass` (**W9907**),
and the trace it forced found that the 1000-line ceiling was a symptom. `transfer_service` carries a
**second implementation of the transaction status seam**, which `status_seam.py:11-13` already says
in its own words, and that duplication is also why the fence's allowlist has two entries. Merging
them frees the room X-d needs -- but **NOT mostly from the merge, and an earlier draft of this
paragraph said it did.**  Measured by AST extents: the seam merge is **-13** lines, `update_transfer`
**+5**, and **-54** come from extracting `restore_transfer`'s four preconditions into
`_transfer_validation`.  **As built the module lands at 987** -- the review fixes (R-DS's pair
instant and the routed repair) added 50 back -- so the headroom over the 1000-line ceiling is **13
lines** against X-d's ~9, not the comfortable margin a mid-build draft of this paragraph claimed.
It fits, and it is thin: `transfer_service` is still a four-verb module and the NEXT change to it
after X-d will hit the gate again.  Recorded as finding **N-152** rather than shaved a fifth time.

**And the trace found a LIVE production defect that all four options would have shipped past
(N-146).** On a finalised transfer the full-edit form disables the money fields, so a notes-only save
submits `{version_id, status_id (identity), notes}` -- the template's own comment states that
mechanism -- and the finalised lock therefore sees no locked field and lets it through. The transfer
path then stamps `paid_at = now()` on the identity re-submit where the transaction seam would have
PRESERVED it, and since E1a that day IS the posted `entry_date`. **Reproduced at `HEAD` in an
isolated worktree with the exact form payload**: a transfer settled 7 days earlier moved from one
ledger entry dated `2026-07-26` to three -- the original, a reversal, and a fresh posting at
`2026-08-02`. **Editing the notes on a paid transfer moves its money forward to today**, by however
long ago it really settled.

**X-aj1 IS BUILT AND COMMITTED on branch `feat/one-status-seam`** (5 commits: `99794cf7` rulings,
`1688f508` extraction, `63514efc` seam merge, `1e75d0ce` refusal, `d3c68cd5` as-built corrections).
**Not yet PR'd or merged.** Verified standalone: 7,798 passed / 0 failed, again under CI's
`TZ=Pacific/Kiritimati`; `pylint app/` and `scripts/` 10.00/10; the real-data baseline
byte-identical over 9 accounts, 427 grid cells and 5,978 daily points, with that diff shown to catch
a planted one-cent move among 10,539 figures; and three mutants planted, all three killed.

**X-aj1 SHIPPED AS PR #80, MERGED 2026-08-03** (merge `dde107f6`; `lint-and-test` and
`polyglot-lint` both SUCCESS on their first sight of it, no conflicts).  It carried the five X-aj1
commits plus the two docs-only commits that were sitting on `dev`.  **It is MERGED but NOT
DEPLOYED**: production still runs `a778703f` (see the deploy note above), so `main` is one merge
ahead of the running image.

**X-d IS PARKED on `feat/xd-checked-projection`, RED at one test on purpose, NOT FOR MERGE.**  Six
commits past `dde107f6` -- `fb8efb9f` rulings, `15773163` code, `78d476de` the as-built record,
`0d539fcf` the review residue, `3f4aa643` the parking record, `2b11aaed` the hand-off to X-ai -- and
its head-but-one says so in its own subject line.  **An earlier version of this paragraph read "X-d
IS BUILT AND GREEN ... three commits ... 7,796 passed / 0 failed"; it was written before the park and
was left standing directly above the paragraph announcing the park**, so the document contradicted
itself in adjacent sentences until a neutral review found it.  Corrected rather than deleted, because
"a stale sentence beside a rewritten one" is the class this arc keeps paying for.  Its entry below
carries the as-built record and the resume list.

**X-ai-0 HAS REPORTED (2026-08-03), and TWO NEUTRAL ADVERSARIAL REVIEWS THEN REFUTED THREE OF ITS
OWN CLAIMS.**  What survives is the conclusion and one number: **a whole-account cash re-derive
issues 8 SQL statements when its reads are batched and 696 when it is assembled by looping today's
per-source reconcile** (`7.74 ms` against `406.01 ms` on the real Checking account, 139 settled rows
and 55 assertions, steady state).  Every statement count reproduced EXACTLY under an independent
re-run.  **X-ai-a must build a batched reconcile and must not loop the per-source one**, and that
ratio carries it alone.  What was WITHDRAWN is the claim that a BUILT verb's cost has been measured:
the `7.74 ms` is detection only, and the shipped loan verb it was compared against carries 28
payments and 1 anchor against Checking's 139 and 55, so "lands in the loan verb's class" was an
extrapolation, not a measurement.  A commit-boundary hook scoped by the sync-populated registry would
grade **11,917 of the suite's 16,169 commits (73.7%)** over **12,459 pairs**, never more than **4**
on any one commit -- corrected DOWN from an earlier draft because the probe leaked its registry
across dead sessions, which a review measured before the fix.  **Session inspection is both
over-inclusive** (1,861 commits carry a source row no writer posted) **and under-inclusive** (3,431
commits ran a writer while the inspector saw nothing), so R-DU holds on sharper ground than it was
taken on.

**The most valuable output is not a number: the probes' BLIND SPOTS re-ruled the step.**  A
walk-driven re-derive cannot see a source that LEFT the settled set (**N-162**, reproduced twice --
the row's legs stay posted forever); the anchor-correction reconcile violates the R2 attribution rule
the source reconcile obeys, filing a reversal in a period it did not come from while the same module
implements R2 correctly in the branch that never runs (**N-161**, re-rooted); and a whole-account
verb would have computed a transfer's entry from two different rules at its two endpoints, turning a
latent disagreement into a write OSCILLATION on the largest movements in the ledger (**N-164**).

**X-ai IS RE-RULED INTO THE FROM-SCRATCH MODEL (2026-08-03, rulings R-DV..R-DZ), and a THIRD
adversarial review then found five ship-blockers in those rulings, all corrected here.**  R-DU's
direction stands -- one verb, one trigger, both ledgers -- and its DECOMPOSITION does not, because
R-DU named the re-derive's SCOPE and never named what OWNS a journal entry.  **A journal entry is the
projection of exactly ONE source event, and the EVENT owns it; an account is the SCOPE of a re-derive
and never an owner.**  The relation is MANY entries to ONE event (21 transactions carry 3 entries
each under one key -- that is what reconcile-to-target IS), and the full key is `(source_kind_id,
owning event, scenario_id, pay_period_id, entry_date)`, both of the last two load-bearing.  Measured,
and it is why: **four of the seven posting source kinds carry no owning FK at all -- 129 of 318
entries, 40% of the ledger, cannot name the event they are a projection of.**

**THE MOST IMPORTANT CORRECTION, ruling R-DZ: N-161's fix needs NO MIGRATION and ships FIRST.**  The
R2 violation is one omission -- `posted_correction_legs` drops `pay_period_id` from its `GROUP BY`
and `_anchors.py:187` re-supplies it from the source row's CURRENT period, which is verbatim what R2
forbids.  Adding the period to the key delivers R2 in two files.  **A draft of this plan scheduled
that behind a migration on a misattributed root cause** -- the exact deferral the developer objected
to, committed inside the plan written to stop it.  The step is now **seven sub-steps opening with
X-ai-r**, and the migration (X-ai-s) buys the separate thing it actually buys: per-ASSERTION
attribution.

**Four more ship-blockers, each refuted by execution or measurement rather than argument**: an
exactly-one CHECK on the source arc **breaks every source hard delete** (`ON DELETE SET NULL` is an
UPDATE, and an UPDATE is CHECK-validated -- reproduced), so it is at-most-one; the backfill gate was
unmeetable (**10 of the 129 entries have no candidate event**, three groups are ambiguous and one is
a triple, and the mapping is not 1:1 in either direction); the anchor-period invariant **cannot be a
CHECK** and live code produces the violation by design, so it is finding **N-168** and not a
migration; and X-ai-a's one-pass fixpoint **requires X-d's walk swap as a precondition**, so the swap
moves into X-ai-a.  Three requirements were missing entirely and are now findings owned by X-ai-a:
cross-account closure (**N-165**), concurrency (**N-166**) and reversal linkage (**N-167**).

**And the fences got a measured inventory and a schedule** (developer instruction: *"I want to
eliminate every fence, checker, and allowlist I possibly can"*).  `app/` carries **8 custom checkers
with 16 hand-maintained module sets holding 36 names**; **the achievable end state is 2 checkers and
0 allowlists**, both survivors allowlist-free.  Phase G now runs **INSIDE E2** rather than after it
(the structural replacement for both surviving allowlists IS a module move, so E2 cuts that boundary
once), and the three checkers that had no scheduled replacement anywhere -- W9901, W9902, W9904 --
get the new step **G2**.

**X-ai-r IS BUILT AND GREEN, not yet committed** (2026-08-03).  It shipped the posted-side half of
R-DZ verbatim and REFUSED its target-side half: **ruling R-EA** replaces "read the row's stored
period" with "derive it from the assertion's day, through the one function both ledgers share".  The
rule that decided it was already in the tree and neither R-DZ nor the first build had read it --
`account_service.resolve_anchor_period_id` states ruling R-DH verbatim, *"the period is DERIVED from
the day, not chosen beside it"*, and describes the exact broken state both rejected shapes produce.
**Measured against the grid's own "Book vs bank" row over 61 periods of a PRODUCTION clone: the
shipped rule agrees on 61, R-DZ's on 59, the stored column on 59.**  Suite **7,804 / 0** under both
`America/New_York` and CI's `TZ=Pacific/Kiritimati`; `pylint app/` and `scripts/` 10.00/10; 4 of 389
ledger cells move and **0 of 12,636 rendered figures**; five firing controls, all five RED at `HEAD`;
positive control -- the same re-derive at `HEAD` on the same clone emits 0 entries.  It carries **no
migration** and still ships a data change: the deploy hook re-derives unconditionally, so prod gains
**+2 entries / +4 postings**.  **A THIRD adversarial review then found nine things, and seven were
false CLAIMS this step's own docstrings had added** -- a loan-side paragraph still describing the
rejected shape as shipped, a shared primitive documenting a split the ruling makes impossible, an
"agrees on 61 of 61" whose TRUE-UP scope went unstated, and a truncate mechanism misattributed.  All
corrected in place.  **Two were substantive**: the ledger/grid agreement the ruling was decided on
had nothing pinning it (now `TestLedgerAgreesWithTheGridOnAssertionPeriods`), and one rewritten
truncate test was silently dependent on the directory clock freeze in a way that would have graded
the OPPOSITE case on a later clock (now derived from `display_today()`).  New findings **N-169** and
**N-170** -- the latter a divergence this step itself introduced: deriving the period split the
app into TWO day-to-period rules that disagree for a day past the schedule (writer index 0, ledger
index 60 on the clone's calendar), unreachable on today's data and owned by X-ak with N-168.

> ### X-f1c's COLD-RESUME RECORD (2026-08-04, second session) -- read this before touching the tree
>
> **The previous session's record opened with a CRITICAL finding and a red tree.  BOTH ARE
> CLOSED, and the CRITICAL one closed WIDER than it was written.**  What follows replaces that
> record; its two blocks are kept below as history because the second one's lesson is the
> expensive one.
>
> ---
>
> **C1 IS CLOSED, AND THE DEFECT WAS LARGER THAN THE REVIEW FOUND -- finding N-190.**  R-EN
> deleted the C-17 lock on the ground that *"an assertion history is APPEND-ONLY, so a second tab
> overwrites nothing."*  That is a property of ONE table in a transaction that mutates three:
> `apply_anchor_true_up` also runs a RECONCILE-TO-TARGET, a read-modify-write against
> `budget.journal_entries` / `budget.account_postings` with no unique index behind it, and the
> deleted `version_id` UPDATE had been serialising it only by accident (it autoflushed and took a
> row lock before the walk).
>
> **Reproduced INDEPENDENTLY this session** with the interleave forced at the reconcile's read
> (probe since removed), on an account reconciled at `$4,000.00`:
>
> ```
> WARM resolved=4000.00 total=4000.00
> STATUS a=200 b=200
> RESOLVED 2000.00   LEDGER TOTAL 1000.00
> ```
>
> The arithmetic, which the previous record did not have: both tabs read the same posted
> `-$1,000.00`; the `$2,000` tab computes it owes `-$3,000` and writes `-$2,000`, the `$3,000` tab
> computes it owes `-$2,000` and writes `-$1,000`; `5000 - 1000 - 2000 - 1000 = $1,000` against a
> resolved `$2,000`.  Trial balance still `$0.00`, because the anchor-equity leg carries the
> mirror-image error -- so nothing fails loudly, ever.
>
> **THE LOAN HALF HAS THE SAME RACE AND HAS HAD IT SINCE COMMIT 16.**  `sync_loan_postings` uses
> the identical `posted_correction_legs` / `emit_correction_deltas` pair, and
> `apply_loan_anchor_true_up` appends to an append-only event table and UPDATEs no row -- so it
> never had even the accident the cash half lost.  **R-EN cited that append-only contract as its
> precedent**, which is how a defect became a rationale.
>
> **The fix is one per-USER advisory lock, and the developer ruled the scope** (*"which option is
> what I should do if I were building everything from scratch"*).  It is NOT the reviewer's
> recommendation, which was a per-account lock at the top of `apply_anchor_true_up`:
>
> * **Taken INSIDE the reconcile**, at all four entry points (cash per-scenario + all-scenarios,
>   loan per-scenario + all-scenarios), because the true-up is one of FOUR callers reaching that
>   window -- the settle self-heal, the direct anchor edit and the pay-period resync reach it too,
>   and a door-level lock covers none of them.
> * **Per USER on a correctness argument, not a convenience one.**  The reconcile does not only
>   read the account's ledger: it derives each correction's period from the OWNER'S calendar
>   (`resolve_anchor_pay_period`), and `journal_entries.pay_period_id` is `ON DELETE CASCADE`, so a
>   concurrent truncate can delete the period it is filing under.  The consistency boundary is the
>   ledger AND the calendar.  **It does NOT make deadlock impossible, and a first version of this
>   record claimed it did** -- see N-193: an adversarial review reproduced an advisory-vs-ROW-lock
>   cycle, because a settle takes its row locks before reaching this lock while a truncate takes
>   this lock before its DELETE cascade.
> * **It is the SAME lock the pay-period mutations take.**  `pay_schedule_service.lock_schedule`
>   MOVED to `app/services/user_write_lock.py` as `lock_user_writes`, **namespace value unchanged**
>   (`0x53484B4C`) so a rolling deploy cannot leave old and new code taking different keys for the
>   schedule lock.
> * **The two deploy-wide backfills pre-take every key ascending by user id**
>   (`lock_every_user_writes`), because they are the only multi-owner transactions and their
>   account-id enumeration visits owners in no order.  Acquired in PYTHON, one statement per user:
>   `SELECT pg_advisory_xact_lock(ns, id) FROM users ORDER BY id` orders the RESULT, not the
>   evaluation.
>
> **Sufficiency was verified, not assumed:** READ COMMITTED measured as the default on dev, test
> AND production with no override anywhere (`SHOW default_transaction_isolation` on all three), so
> the waiting transaction takes a fresh snapshot after acquiring, sees the winner's postings, and
> reconciles to the true merged target.  Under REPEATABLE READ the lock alone would NOT have been
> enough.
>
> **C2 is closed with it.**  `test_concurrent_true_up` gained invariant 4 -- the linked-ledger
> posting sum must equal `resolve_anchor(account).balance` -- which is the one assertion that fails
> in the broken state while 1, 2 and 3 all pass.  Its own deterministic suite is
> `tests/test_services/test_user_write_lock.py` (8 tests): the lock is emitted, emitted BEFORE the
> first ledger read, genuinely blocks a second transaction holding the key (with a non-vacuity
> control that the same call completes when the key is free), is re-entrant, and is ONE key for two
> accounts of one owner.  **Three mutants planted, all three killed** -- lock removed (4 tests),
> lock moved after the read (2 tests, and ONLY the ordering ones, which is why presence and
> ordering are graded separately), loan lock removed (1 test).
>
> ---
>
> **THE RED TREE IS FIXED.**  The five `pay_period_service.generate_pay_periods(...)` calls are
> restored in `tests/test_routes/test_accounts.py` (14 = HEAD's 14), as bare CALLS with a comment
> saying the call is the setup, rather than as dead bindings.  A census of the same deletion class
> across the whole `tests/` diff came back clean: every other dropped helper call
> (`_create_investment_account`, `create_hysa_account`, `_make_future_periods`, `_future_periods`,
> `_count_periods`, and every `db.session.commit` / `flush` / `add`) accompanies a deleted `def
> test_`, and the three files repaired last session match HEAD exactly.
>
> *The lesson stands and the developer said it first: this step should have been decomposed across
> sessions from the start.*
>
> **BRANCH STATE, re-measured from `git status -sb` / `git ls-remote` / `gh pr list`:**
>
> | fact | value |
> |---|---|
> | branch | `feat/xf1-settle-day` |
> | local HEAD | `44986f88` |
> | `origin/feat/xf1-settle-day` | `e7f782d6` -- **4 commits BEHIND local; nothing since `e7f782d6` is pushed** |
> | PR | none |
> | working tree | ~95 changed files incl. the UNTRACKED migration `c81f0a5b3e27` |
> | production | `d7c1f4a9e603` -- **none of this cluster is deployed** |
>
> | commit | step | suite at that commit |
> |---|---|---|
> | `6fc17ce6` | rulings R-EM..R-EP + Section 5 re-scope + 10 owner re-points | docs only |
> | `c16bdb3b` | **X-f1c3a** the asserted balance has one resolver | 7,815 passed |
> | `379ed1af` | **X-f1c3b** an assertion is a day and a balance | 7,813 passed, both clocks |
> | `44986f88` | the previous session's cold-resume record | docs only |
>
> **X-f1c3c IS BUILT AND UNCOMMITTED.**  It drops both `accounts.current_anchor_*` columns and,
> because they existed only for those columns, `ck_accounts_anchor_balance_present`, the deferrable
> `NO ACTION` FK, `_DEFER_ANCHOR_FK_SQL`, `_reanchor_accounts`,
> `PeriodLockReason.ACCOUNT_ANCHOR` (+ its query and its settings chip),
> `account_service.resolve_anchor_period_id` (**closing N-170** -- one day-to-period rule survives),
> `AccountSpec.anchor_period_id`, and -- ruling **R-EN** -- `AnchorTrueUpOutcome.STALE_CONFLICT`,
> `_anchor_conflict_response`, the 409 conflict cell, the anchor form's hidden `version_id` and
> `AnchorUpdateSchema.version_id`.  `update_account`'s `if current_period:` fork goes with them,
> **closing N-134 at X-f1c3c rather than X-f1c4**.  Migration `c81f0a5b3e27`.  **It now also carries
> N-190's lock, H1's severity fix and the three reviews' residue.**
>
> **VERIFICATION, ALL RE-RUN THIS SESSION on the repaired tree -- do not carry the previous
> record's figures forward:**
>
> | gate | result |
> |---|---|
> | full suite, `America/New_York` | **7,811 passed / 0 failed** |
> | full suite, `TZ=Pacific/Kiritimati` (CI's clock gate) | **7,811 passed / 0 failed** |
> | `pylint app/` + `scripts/`, full `--fail-on` set | **10.00/10** |
> | `tools/plan_gate` (NOT in `tests/`; run it by hand) | **17 passed** |
> | seam baseline, production clone, HEAD vs working tree | **byte-identical** -- 9 accounts, 427 grid cells, 5,978 daily points |
> | seam baseline positive control (one cent on one assertion) | **3,262 diff lines** |
> | anchor-SURFACE probe, HEAD vs working tree | **72 diff lines, 72 removals, 0 additions, 72 naming a dropped column**; with those two keys stripped from both sides, **byte-identical** |
> | anchor-surface positive control (one cent) | **618 diff lines** |
> | migration `c81f0a5b3e27` both directions on a throwaway | **round-trips**; 8 of 9 accounts restore identically, account 8's period restores as 5 rather than the stored 1 -- **exactly the N-168 repair the docstring documents** |
>
> **THE SURFACE PROBE IS NOW IN THE REPOSITORY** -- `tests/manual/verify_anchor_surfaces.py`,
> beside the seam harness.  It was a scratchpad script every anchor-touching step re-wrote or
> skipped, which is the N-181 lesson repeating as a process defect.  It covers the seven surfaces
> `verify_balance_baseline.py` is structurally blind to: the grid header's starting figure and its
> "as of" caption, the reconcile panel, the dashboard balance section, the pulse hero, the savings
> dashboard including the ARCHIVED drawer, Property market value / home equity, and the retirement
> table's seeds.  A producer that raises is RECORDED rather than fatal -- a probe that dies on
> account 3 has silently stopped covering 4 through 9.  One recorded error is expected on this
> clone: user 2 has no baseline scenario.
>
> **REVIEW RESIDUE: all three reviews' open items are worked.**  Closed this session -- H1
> (`integrity_check`'s BA-01 was stamped `warning` by a FAMILY constant, so an account
> `resolve_anchor` raises for exited 2 instead of 1; severity is per CHECK now, the shape
> `check_data_consistency` already used, with tests in both directions); the migration downgrade's
> untested SQL (`_CURRENT_ASSERTION_SQL` is now ONE standalone statement both backfill UPDATEs
> interpolate, EXECUTED against real rows by `tests/test_models/test_anchor_cache_downgrade.py` and
> proven by three more mutants -- ordering flipped, tie-break flipped, gate skipped); M8 (the stale
> form now really SENDS a stale `version_id`, which also pins the `unknown = EXCLUDE`
> backward-compatibility the deletion rests on); M9 (the reset-openings assertion now names the
> EXACT expected period with its arithmetic, killed a mutant that filed in the wrong one, where the
> old "is one of the six live periods" could not); M10 (the fourth conflict-cell deletion and the
> whole `test_anchor_fk_deferrable.py` deletion both have records now); the trivially-true line
> (it graded the PRE-reset snapshot); the unasserted precondition in
> `test_true_up_records_without_a_current_pay_period`; L5's corrupted historical sentence and its
> misnamed local; every remaining 409 / C-17 / `_reanchor_accounts` / `ACCOUNT_ANCHOR` /
> `updated_at` / `DUPLICATE_SAME_DAY` stale docstring; `_add_or_reuse`'s over-claim (the fourth
> get-or-create is NOT converted, and the reason is sound -- its key carries a brand-new account id
> no other transaction can hold); two resolvers logging "Created" for a reused row; the three
> imports this branch orphaned; and the blank-line residue.  The stale citations in re-pointed
> findings rows are marked HISTORICAL in place with the two dead files named, rather than
> re-pointed line by line -- a census rewritten to today's addresses stops being a record of what
> was measured.
>
> **DRY improvements taken along the way**: `_capture_statements` / `_took_advisory_lock` moved out
> of `test_pay_period_topup.py` into `tests/_test_helpers.py` (with a new `advisory_lock_precedes`
> and `linked_ledger_total`), and the migration's duplicated `DISTINCT ON` subquery collapsed to
> one constant -- two copies could have restored the balance from one assertion and the period from
> another.
>
> **THREE NEW FINDINGS**, all in Section 6: **N-190** (above), **N-191** (the app's civil day rests
> on a compose variable at **78** `date.today()` call sites; `$0.00` today because production pins
> `TZ`, and two sites found in this pass -- `top_up_rolling_window` and `classify_periods_bulk` --
> decide against the user's CALENDAR and look like the wrong side of the line; owner **X-ak**), and
> **N-192** (the owner-has-no-pay-periods `PostingError` lost its FK and is now held out of reach by
> code alone; the raise STAYS and its comment is corrected; owner **X-ak**).
>
> **Environment, so a resume does not debug a stale fixture:** the test template
> (`shekel_test_template`, `shekel-dev-test-db`, port 5433) is REBUILT to `c81f0a5b3e27` and no
> longer has the dropped columns -- **it does NOT match `origin`, so a session that checks out the
> pushed tree must re-run `python scripts/build_test_template.py`** with `TEST_DATABASE_URL` /
> `TEST_ADMIN_DATABASE_URL` exported from `.env` (the script's default admin URL is a unix socket
> that does not exist here).  This session's clones are `shekel_x3c_before` (HEAD's schema,
> `b6d1e94c07af`), `shekel_x3c_after` (`c81f0a5b3e27`) and `shekel_x3c_mig` (the round-trip
> throwaway), all on `shekel-dev-db` (port 5432) and all derived from `shekel_prodbase`.  A `git
> worktree` at HEAD may be left under the session scratchpad; remove it with `git worktree prune`.
>
> **THREE NEUTRAL ADVERSARIAL REVIEWS RAN BEFORE THE COMMIT AND ALL THREE FOUND REAL DEFECTS.**
> Their whole residue is worked; what they found is worth stating, because two of the three found
> things the step's own verification could not have:
>
> * **Concurrency review.**  One CRITICAL: the settle self-heal's SKIP PREDICATE reads *before* the
>   lock, and a skip is permanent -- a `$70.00` settle dated after the latest assertion correctly
>   skips while a concurrent true-up walks a ledger that cannot see it, leaving the account
>   `$70.00` under its own assertion forever.  **The lock closed the double-true-up case and left
>   the commonest pair of user actions open.**  Fixed by locking at the top of
>   `self_heal_anchor_corrections`.  Also: a THIRD multi-owner deploy transaction the docstring had
>   missed (`resync_all_cash_postings`, the first of the three to run); three exported loan
>   reconcile doors with no lock; a pre-delete reversal that read posted legs unlocked; a
>   fail-loud raise this step had silently turned into a no-op; and a wrong decimal comment on the
>   namespace constant carried over from its old home.  **And it REFUTED the deadlock claim** --
>   see N-193.
> * **Test-integrity review.**  THREE assertions proven vacuous by planting the broken state:
>   `test_true_up_records_without_a_current_pay_period` asserted the balance the FIXTURE already
>   had, so a true-up that wrote nothing passed; the downgrade suite's ordering test could not
>   exercise `created_at` at all, because `server_default=now()` is `transaction_timestamp()` and
>   is CONSTANT across a test transaction, so mutants that flipped or deleted that tie-break both
>   passed; and the one-key test compared statement TEXT while SQLAlchemy binds the key, so it
>   passed unchanged against a per-ACCOUNT lock.  All three now fail against their mutants.  It
>   also found the DDL source checks graded call names and not arguments (four simultaneous
>   mutations passed), and that `_DOWNGRADE_PERIOD`'s owner-scoping had no executing reader at all.
> * **Claims audit.**  Two of my claims REFUTED: the `date.today()` census was a grep line count
>   (113) where the AST count is **78**, and an attribution of where a deleted module's work went
>   was asserted without being checked -- inside the note written to correct stale citations.
>
> **NEXT, in order:** (1) commit X-f1c3c; (3) **X-f1c4** -- now only the
> statement-date FIELD and its render, because X-f1c3b did its period half and X-f1c3c closed
> N-134; (4) **X-f1d**, the `anchor_settle_partition.md` archive move (N-175); (5) **push** --
> nothing is on the remote yet; (6) tick X-f1c's leaves + re-point every row naming them in ONE
> pass (rule 2); (7) the PR.  **X-an follows X-f1's ship**, not this branch.
>
> **DO NOT TICK X-f1c3a / X-f1c3b / X-f1c3c YET** -- a gate constraint, not an oversight.  Fourteen
> Section 6 rows name X-f1c's leaves as their owner (**N-4**, **N-5**, **N-73**, **N-83**,
> **N-103**, **N-134**, **N-168**, **N-169**, **N-170**, **cash D4**, **N-181**, **N-184**,
> **N-186**, **N-189**) and `tools/plan_gate` FAILS on a row whose owner is ticked.  **That gate is
> not in `tests/`**, so `./scripts/test.sh` never runs it: run
> `python -m pytest tools/plan_gate -c /dev/null -q` by hand after editing the ledger.
>
> **The ship carries THREE MIGRATIONS**, so a prod rollback is NOT a pure digest revert.  Chain:
> `d7c1f4a9e603` (prod) -> `a3f7c8e21b64` (X-f1b, drops `transactions.paid_at`, downgrade refuses
> UNCONDITIONALLY) -> `b6d1e94c07af` (X-f1c3b) -> `c81f0a5b3e27` (X-f1c3c).  The last two have
> WORKING downgrades, and **each restores a CORRECTED value rather than the byte-for-byte
> original** -- both repair finding N-168's mis-filed rows on the way back.  Stated in both
> migration docstrings, because anyone diffing a pre-drop dump against a post-downgrade dump will
> see those rows differ.  **Measured again this session: exactly one of nine accounts differs, and
> it is the row N-168 names.**
>
> **Staging lesson, still current:** `git add -p`, never `git add -A` -- mutation-planting reviews
> have run in this worktree (Section 8), and SIX mutants were planted and reverted this session.
> `grep -rn "MUTANT" app/ tests/ tools/ scripts/ migrations/` was clean at session end.  And a bulk
> mechanical edit over `tests/` needs the same per-change scrutiny as app code: an AST pass that
> deletes "unused" assignments will happily delete a call whose RETURN was unused but whose SIDE
> EFFECT was the fixture.

**THE ORDER CHANGED 2026-08-03 on ruling R-EB, and this paragraph is the orientation to trust.**
The anchor half is redesigned from scratch -- the ledger becomes sum-of-postings and an assertion
becomes a RECONCILIATION rather than a reset -- so the steps that served the OLD model are re-ranked
against it rather than run first.

**NEXT, in order: X-f1, X-an, X-f2, X-f3 (the cutover -- MOVES MONEY, own PR), X-f4, X-f5, then
X-f6** (bank import, the ruled follow-on).  **X-an is new (ruling R-EK, 2026-08-04)** and sits
immediately behind X-f1 because X-f1 is what gives the app the stored day it keys on: the loan
resolver still decides "has this payment happened yet?" from the PAY PERIOD while the posted ledger
uses the day the money moved, so an installment paid before its pay period begins renders twice on
the loan detail page (**N-187**, `$1,003.87` in the reproduction, `$0.00` on today's production data
and one click away).  **X-f1b is GREEN and COMMITTED on branch
`feat/xf1-settle-day`.**  *The branch state is deliberately NOT restated here: it went stale twice
by being carried forward in prose, and the COLD-RESUME RECORD above is the one place that carries
it, re-measured at every edit.*  A `[WIP -- RED, NOT FOR MERGE]` commit is in this branch's
published history (`70ba87f6`); it is true of that commit and of nothing since, and the
squash-or-merge choice at the PR should account for it.  X-f1b's step entry in Section 5 carries
the **"X-f1b as BUILT"** record: what the 102 parked failures actually were, the three findings it closed, the four
things its second pair of reviews found (one of them a defect the step itself introduced), and what
the 22 deleted migration tests really cost.  **X-f1c and X-f1d remain, and X-f1 does not SHIP without
them**: ruling N-175 binds the `anchor_settle_partition.md` archive move to this step so the
superseded plan and the plan that supersedes it land together, which is Section 9 rule 1 (one live
planning document).  X-f1b alone would also leave finding **N-181**'s eight fabricated timeliness
days in production with no door to correct them.

**X-f1c WAS RE-SCOPED INTO FOUR LEAVES on 2026-08-03 (rulings R-EF..R-EI), and the re-scope is the
finding.**  Its one-sentence plan entry -- *"`settled_on` on the full-edit form... and the true-up
form's statement date"* -- was measured wrong in three ways by the build trace.  **N-181's eight rows
are ALL transfer shadows** (transactions 1457/1458/1823/1824/1826/1827/2161/2162, four pairs), and a
shadow's full-edit popover is the TRANSFER form, so the transaction door corrects **0 of 8** and
"X-f1c IS that door" was false as written (R-EF adds the transfer door).  The field as specified
would have **400'd the documented unlock path** on every settled row, because both forms re-submit
the row's own settle day when the user sets Status to Projected to unlock the amounts, and the seam
refuses that pair (R-EG drops it at the door instead, while the SERVICE guard still fails loud).  And
the statement date makes a **back-dated assertion** reachable, which stales the
`accounts.current_anchor_*` cache that the grid header, the dashboard card, the cockpit cards, the
Property market value and the retirement seeds all render from -- measured: Checking carries 55
assertions from 2026-03-27 to 2026-08-03 against a schedule floor of 2026-03-26, so almost any
back-date lands before the latest one.  **R-EH deletes those two columns rather than guarding the
write**, and that leaf runs BEFORE the statement date so back-dating never meets a cache.

**That leaf then RE-SCOPED ITSELF into three, on 2026-08-04 (rulings R-EM..R-EP), and the trace
under it is why.**  R-EH was sized at "two columns and ~20 read sites"; the census is 65 references
over 24 `app/` modules and 5 templates plus 312 across ~50 test files, and three of its four forks
were opened by MEASUREMENT rather than by the plan.  The largest: `account_anchor_history` carries a
`pay_period_id` whose own docstring says **no reader survives in `app/`**, which is already WRONG on
2 of 78 production rows, and whose `ON DELETE CASCADE` is what makes a pay-period reset destroy 69
real balance observations and fabricate 9 replacements.  **R-EO deletes it**, which takes
`_reanchor_accounts`, the deferrable-FK apparatus, `PeriodLockReason.ACCOUNT_ANCHOR` and
`resolve_anchor_period_id` with it -- and closes N-134, N-168, N-169's cash half and N-170
structurally.  **X-ai-s (the migration) is HELD pending X-f3**: it buys
per-ASSERTION attribution for the correction family X-f3 deletes, and running it first would ship a
migration and a backfill for something about to be removed.  **X-ai-r is DONE** (`c518d2e4` /
`8281e82c`, PR #81) and its LOAN half survives untouched.  After X-f: **X-ai-a, X-ai-b, X-ai-c,
X-ai-g** (re-scoped -- the cash half shrinks to the source-posting verb once anchor corrections stop
existing), then **re-land the REST of X-d**, then **X-aj2**, **X-ak**, with **X-ag** (the gate N-139
needs, instrument undecided), **X-ah** (N-142) and **X-al** (N-154) unscheduled behind them, and
**Phase G now INSIDE E2**.  **S2-b is absorbed into X-f1** and is no longer a separate step.

**X-f1b WAS BUILT AND NOT SHIPPABLE, and three neutral adversarial reviews were why.  It is
SHIPPABLE now** -- the paragraphs below record the state it was parked in, because the reason it was
parked is the finding: the app half measured clean while the test half had lost the coverage of two
rules, and a step that grades its own app code with a harness that cannot see the loss will report
success.  The app half was MEASURED: `pylint app/` and `scripts/` 10.00/10, the checker meta-suite 144/144, the
migration runs on a production clone with the settled-iff-dated invariant EXACT (156 settled all
dated, 843 non-settled all NULL), `verify_balance_baseline.py` **byte-identical** over 9 accounts /
427 grid cells / 5,978 daily points with a positive control firing (one settle day moved 30 days ->
16-line diff), the downgrade refusing, and its recovery SQL EXERCISED in a rolled-back transaction
(156/156 rows round-trip to the same civil day).  **The tests were not done: 102 failed**, and the
reviews found the remaining work was not the mechanical part.  It is done now -- see the as-built
record for what those 102 turned out to be.

**THREE OF THE STEP'S OWN CLAIMS WERE REFUTED, and all three corrections are in place.**  *"No
figure moves"* was FALSE -- the backfill moves a payment-timeliness metric onto a day nothing
observed, and the balance harness structurally cannot see it because it is not a balance
(**N-181**, still OPEN, owned by X-f1c).  *"Enforced structurally at the seam"* was FALSE AS STATED
-- `update_transfer` wrote `settled_on` unfenced and could either date an unsettled transfer or 500 a
settled one (**N-183**, CLOSED at X-f1b: both writes route through the seam now, and the seam itself
refuses a day for a non-settled status).  *"The three deleted helpers are callerless"* was FALSE -- eight live
references survived, and one of them made this repo's OWN checker meta-test RED
(`balance_seam.py` still ruled on `settled_civil_day`), a gate the step never ran.

**The most valuable finding is a coverage loss, not a bug (N-182).**  The conversion computed every
derived day CORRECTLY -- all nine genuine UTC/Eastern day differences verified arithmetically, and
no hand-computed money figure was edited -- and still destroyed the coverage of two rules, because
it treated "recompute the day at each site" as the whole job when seven of those sites were the only
tests HOLDING a rule.  **The display-timezone settle rule had zero tests: swapping
`display_today()` for `date.today()` in the seam shipped a green suite**, which is the exact L9 /
R-DH (b) / F3 defect three rulings closed.  And the three "a re-settle must not re-date the money"
pins could not fail, because both calls yielded the same `display_today()` -- that is N-146
and N-178's own class losing its transaction-side guard in the very step that fixed a new instance
of it.  The discriminating pattern exists in this step's own diff (the N-178 transfer test back-dates
BEFORE replaying) and was applied to one side only.

**One defect the reviews found was closed immediately, structurally: N-179 -- and the fix below was
later found to be HALF of it, because it refused only what the seam was handed.  The rule moved onto
the COLUMN at X-f1b; see the as-built record.**  A `datetime` handed to
`settled_on` was silently truncated on the UTC session clock, so an evening-Eastern settle stored one
day late -- the very split R-DH (b) exists to delete, one layer down and with no error.  Sixteen test
sites did it and eight stayed GREEN; one run wrote a journal entry whose `DATE` column held
`2026-03-20T13:00:00+00:00`.  `apply_status_change` now REFUSES a `datetime` before it touches the
row.  That is the one write door refusing a wrong type rather than a checker hunting call sites --
and every OTHER write path now refuses it too, via a `@validates` hook on the column.

**X-f1 IS SCOPED AND ITS THREE FORKS ARE RULED (2026-08-03, R-EC..R-EE), and the first one DELETES
A COLUMN.**  `transactions.settled_on` REPLACES `paid_at` rather than joining it, on the developer's
*"which option is what I should do if I were building everything from scratch"* framing.  Measured
before the ruling: **14 sites turn a `paid_at` instant into a civil day -- 11 call sites across 8
modules** over 3 helper layers, while **zero templates, zero JavaScript files and zero serialized
payloads read the column**, and **nothing anywhere orders or compares two instants**.  So the
instant has no consumer, and keeping it beside a stored day would be two columns for one fact --
this arc's own root cause 1, one step after S1-c removed its mirror.  The replacement also deletes
`to_display_civil_date` outright, makes `days_paid_before_due` exact, removes one of the FOUR
database-clock reaches N-65 had to contain, and drops a query whose only job was materialising
`db.func.now()`.  **Honest costs, measured rather than assumed: it is a destructive migration, and
42 of the 148 live instants are lost for good** -- `system.audit_log` reaches back only to
2026-05-06 and holds a `paid_at` for 106 distinct transactions, so "the audit trail has it" is FALSE
for 28% of the rows.

**And the developer's own question opened N-177 (owner: the new step X-am).**  Asked how a row ever
receives the `Settled` status, the answer measured out to: it does not.  **0 of 897 transactions and
0 of 120 transfers carry it, and 0 of 1,110 transaction + transfer audit rows have ever written
it** in the audit table's whole retention window.  Nothing assigns it; its only door is a
`<select>`; the balance engine cannot tell it from `Paid` because every consumer reads the SET
`settled_status_ids()` and never the member.  It still CONSTRAINS X-f1: `Paid -> Settled` is a
re-entry into the settled band, and the seam preserves the settle stamp on re-entry precisely so
archiving does not re-date the money (**N-146**'s class), so the new column must inherit that rule
verbatim and X-f1 pins it with a test.  That is the third time in this arc a developer QUESTION has
opened a finding no review did.

**X-ai carries N-144, N-153, N-155, N-157, N-158, N-160, N-162, N-163, N-164, N-165, N-166 and
N-167**; **N-168** and **N-170** are X-ak's; **N-161** and **N-169** are now expected to close AT
X-f4, which deletes the module they are properties of.  **X-f carries N-171..N-176.**  Ruling
**R-DU** set the posting direction (one verb, one trigger, both ledgers); **R-DV..R-DZ** set its
shape; **R-EA** corrects where an anchor correction's PERIOD comes from; **R-EB** rules the anchor
model itself -- Option 4 (sum-of-postings + reconciliation), then Option 6 (bank import).

> **X-ai-s inherits a premise this step FALSIFIED.**  Its backfill rule (below) attributes 129
> legacy entries to "the LAST history row of its merged key", justified by *"`_account_anchor_correction_targets`
> already files a merged target at the latest row's period"*.  After X-ai-r it does not -- it files
> by the period containing the day -- and after the deploy re-derive the 2026-06-03 group holds one
> entry in period 5 rather than two spanning 5 and 6.  **Re-measure that group before writing the
> migration**; do not carry the sentence forward.

**X-d** is PULLED FORWARD past nine steps because it is the
structural resolver for the last duplication step 3 could not remove -- **two representations of
the same events**, which is ruling R-H's own words (*"the posting writer consumes the SAME walk, so
the projection and the posted ledger cannot drift by construction rather than by a test keeping two
implementations in step"*). It takes `_attribution.py`'s duplicate loaders with it. None of the
nine steps it passes is stated as a prerequisite, and **its ship gate is already MEASURED AND
CLEAN**: step 3 ran the production sweep for walk-invisible legacy rows in both directions and both
return 0 entries, positive-controlled (`anchor_settle_partition.md` 14.6), so no F1-class human
decision is waiting for it. It also carries **N-135** as an explicit obligation. Then **S2-b** (the
TRANSACTION half of step 2, `transactions.settled_on`, plus the true-up form's own date field,
which is what closes N-133's write-once residue and the loan/cash index asymmetry). **N-134** is
open and unscheduled.

**Both cash cutovers are DONE** -- the cash one at X-c2b2 (`d3489728`) and the modelled one at X-g2b
(`560b3339`), after which no remaining step can move a figure except by fixing a defect. The grid
cutover X-g3b then landed on 2026-07-27, closing finding N-76 byte-exactly on 900 of 900 (account,
period) pairs. Every non-loan account's balance is ONE event replay, date-precise for all five
kinds; the three-source merge, the reverse growth projection, the per-period interest layer and the
kernel's per-kind ladder are deleted rather than merely unwired.

**SHIP AT THE SEAMS FROM HERE, not at the end of the arc.** PR #65 reached 89 commits because the
plan was to open it when the cash half finished, and CI does not run on `dev` pushes -- so nothing
in it had been graded until the PR. The remaining steps have their own natural cut points: after
**X-w**, and after **X-i1** (byte-identical) but BEFORE **X-i2**, which MOVES MONEY and on whose
line X-i is already decomposed. **X-i2, X-k, X-d, X-e and X-f must never ride with a backlog** --
they move money or change writers, and each wants its own PR so a rollback is precise.

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

**With X-g4 the whole of X-g is DONE**, and its header is ticked with the last of the ten commits
its four decomposed steps took (rule 6's convention for a decomposed step). The ledger tail that step left is closed too: N-43,
N-46, N-78 and N-95 were resolved BY `17c57cde` while still reading "OPEN" against a ticked owner,
and they are now in the archive's closed register -- the exact class plan step X-h's gate exists to
make impossible, found by hand one more time.

**X-o, X-q1 and X-q3 are DONE**, shipped 2026-07-27 (`68c22fa0`, `3b7823e1`, `bad97e6a`). X-o's
trace opened the other two steps (rulings R-AV / R-AW) and its own review opened a third finding:
the debt-free date had a SECOND producer the predicate fix does not merge (measured **19 years**
apart on the developer's own data and **28** on an independent fixture), the caption claimed more
than the derivation covers, and B-16's ROOT is that the per-account projection dict re-flattened the
seam's `LoanFigures` field by field and dropped the one field the debt-line question needed.
`/savings` now derives "when is this user debt-free" ONCE, and says it covers loans.

**X-r is DONE too** (`1204a99e`): the projection dict carries the seam's `LoanFigures` whole, so
the copy that dropped `is_retired` cannot recur -- a field the seam grows now arrives at every
consumer by construction.

**X-q2 is DONE** (`be6cfae6`), and with it the whole of X-q. The Horizon producer's two unread
keys and the callerless `compute_net_worth_horizon` are gone, the payload is BYTE-IDENTICAL on
both databases, and "publish only what is read" is now a GATE at both ends of the boundary rather
than a property someone checked once: the producer's key set is pinned, and the route removes each
key in turn and requires the serializer to raise on it. N-102's fork is decided -- **no "Paid Off"
badge on the archived drawer** -- on the ground that `is_paid_off` is the seam's own
CONGRATULATION predicate and the moment to congratulate is the live list, not a drawer opened to
unarchive or delete.

**Its two adversarial reviews earned their cost a SIXTH time, and both found the same two things
independently.** A docstring of mine justified deleting `is_loan_free` by naming a consumer that
does not exist (the cockpit footer renders the same distinction from the two fields `_metrics`
copies OUT of the outlook, never from the property), and the step cited a finding ID that did not
yet exist. Both fixed in the commit. They also opened **N-104**: N-100's root survives one level
INSIDE the dict X-q2 just certified -- the serialized milestones carry a `date` and a `kind` the
client's flag plugin never reads -- and one package over, where the debt-summary dict flattens the
outlook field by field, which is ruling R-AW's pattern again. Plan step **X-s** owns it.

**X-h is DONE** (2026-07-28), in its five commits: `6337606e`, `7d61c67f`, `8e739298`, `86c38e28`
and the gate. No production change, so the baseline provably did not move. The four controls that
could not fail now fail on demand, each shown biting on a planted defect and blind without it -- and
**four of the five commits corrected something X-h's own entry had wrong**, including a proposed fix
that was measured and does not work (N-45's `path=`) and a citation X-r had already made stale
(B-17). **N-65 is not the shape anyone thought it was**: the database is reached three ways, the
first draft covered two, and the full suite came back with **41 failures, every one a bulk
`query.update(...)`** no session listener can see. Its verification arm was itself a control that
could not fire -- a `\b` that can never assert after `now()` -- caught only because it was made to
fire rather than reasoned about.

**Its clock then exposed two latent defects it did not cause.** Six loan-posting tests pass only
because the real calendar is before 2026-12-31 and **would have gone red by themselves on
2027-01-01**; and a retirement test still asserted the pre-R-Y contract that a 401(k)'s modelled
tile equals its cash basis, holding only because its anchor was stamped four months past its own
window. Both repaired with the developer's ruling, no hand-computed figure moved.

**The ledger gate is live**, and on its first run against the real ledger it found one violation
(the **E2** pointer row's out-of-vocabulary owner). Rule 6 is a predicate now, not prose. One
finding opened: **N-105**, owner **X-s**.

**X-s is DONE** (2026-07-28, `bbdfc2c0`). Three dicts published fields nobody read and now none do:
the chart payload carries `{label, x}` per milestone against a client that reads exactly those, the
debt summary is a frozen value object carrying the seam's `LoanPayoffOutlook` WHOLE, and
`_project_one_account` asks each of its two questions once. **`is_loan_free` has its first `app/`
reader since it was written** -- a borrower whose every loan is retired is now told so. No figure
moves, on either database, on both harnesses.

**Its two adversarial reviews earned their cost a SEVENTH time, and the sharpest finding was inside
the FIX.** X-s2's first docstring claimed the no-baseline rule was "stated HERE and nowhere else"
(it is stated in FOUR places), and the correction then cited **two function names that do not
exist** -- the invented-citation class, committed while repairing an overclaim, caught only because
a reviewer walked the AST instead of reading the sentence. The reviews also found a dead producer
surface X-s1 had just CREATED (rulings R-BG / R-BH deleted it and a dead field in X-s3's own new
value object), two holes in X-s1's own guard, and four line citations this commit's own edits had
shifted. **Five findings opened, all born with owners**: N-107, N-108, N-110, N-111 -> the new step
**X-t**; N-109 -> the new step **X-u**.

**One process lesson, paid here and recorded in Section 8**: the two reviews ran CONCURRENTLY with
fixes landing in the tree they were reading, so the correctness reviewer's gate results graded a
tree that no longer existed. Everything was re-run on the final tree. Review a frozen tree.

**X-t is DONE** (2026-07-28), in five commits: `db1e45a4`, `b3ff3343`, `709cda23`, `21893ec5` and
the review residue `d4e0d4e7`. The per-account projection is a frozen value object whose loan half
is ONE field; the seam's no-baseline precondition is one property that `require_scenario` itself
raises on; the net-worth band vocabulary loses two of its homes and the rest are gated across four
languages; and a milestone flag is identified by its `(label, date)` pair. **No figure moves on
either database** -- and proving that took a SECOND instrument, because
`verify_balance_baseline.py` reads the seam directly and is byte-identical for any step whose whole
surface is above it. The new one dumps every per-account projection field, both narrow debt
producers, the tracks section and the serialized `data-chart` payload, and it was shown firing on a
planted one-cent defect before it was trusted.

**Its two adversarial reviews earned their cost an EIGHTH time, and the sharpest finding was
again inside the fix.** X-t2's docstrings claimed this package had "TWO seam doors, and they are
the only two"; it has three, and the third (`compute_property_equity` -> `home_equity_service` ->
`loan_figures`) raised `ValueError` on `/savings` for a borrower with a Property securing a
mortgage. Both reviewers found it independently, one by EXECUTING it. The end-to-end guard X-t2
shipped could not catch it -- its fixture was loan-only -- and writing the Property fixture then
exposed a second dead control of mine: `secured_by_account_id` is not a field, and SQLAlchemy
accepts the assignment in silence. The reviews also found `_horizon` holding two more band
literals the new gate could not see, three of five gate arms scanning RAW source (a band dropped
behind a `//` comment satisfied the arm written to catch it), and nine stale or invented citations
-- including `_net_worth._sum_net_worth_totals`, which has never existed in any commit. Rulings
**R-BK..R-BR**; findings **N-112**, **N-113** and **N-114** opened, owned by the new steps **X-v**
and **X-w**.

**X-u is DONE** (2026-07-28), in two commits: `70c5cf39` (the rulings, first) and `e2cdc589` (the
merge). One dashboard render ran the debt pipeline twice; `principal_paid_fraction` is a
`DebtSummary` field now, and the second producer, the `DebtTrack` wrapper and the shared
`_project_debt_accounts` helper went together. **Measured on both databases**: projections per
render 2 -> 1, seam batches 3 -> 2, SQL 92 -> 83 and 84 -> 75. No figure moves on either harness.
**The membership question ruling R-BI was careful about was answered by measurement, not argument**
-- the two rules are reducers over ONE list, so the merge moved neither predicate, and the
three-loan control that would catch one was shown firing all three ways it could go wrong.

**Its two adversarial reviews earned their cost a NINTH time, and both found the same top defect
independently: six citations of a ruling that did not exist.** The code cited `R-BS` for the
developer's four forks while Section 4 still ended at R-BR -- X-q2's exact class, committed in a
step being careful about everything else. It is fixed structurally rather than by renaming: the
rulings now land in their OWN commit BEFORE the code that cites them, which is a practice this arc
did not have (X-s cited R-BD one commit early; X-t's code cited no ruling at all). The reviews also
found the step's own safety sentence false of exactly one reducer -- `debt_without_payoff_model`
takes `account_data`, not `loan_ads`, because its job is the liabilities that are NOT loans, so
there are **four** membership rules and not three -- a producer count of four where three remain,
and five stale sentences in files the step had not opened. **One finding opened: N-115**, owner
**X-i1**, whose input tier is widened to cover it.

**Two lessons paid here and recorded in Section 8**: a ruling id is a citation like any other, so
the ruling ships first; and the ledger's own row count is now a GATE arm, whose first draft was a
control that could not fire -- it required `rows**` where the document writes `rows.**`, matched
the live file nowhere, and passed a planted defect until it was exercised against the real
document.

**X-v is DONE** (2026-07-29), in two commits: `7d4e4986` (the rulings, first) and `dbf154c7` (the
code, all three decomposed parts). A user with no baseline scenario now gets ONE answer everywhere:
the repair card for a page, `204` for a safe-method fragment, an ERROR event either way. Eighteen
caller pre-checks, eight `or ZERO` reducers and two fabricated figures are gone with it, and
`AccountProjection.current_balance` stopped being nullable. **No figure moves** -- both harnesses
byte-identical on both databases, live-verified.

**The developer's own question is what turned the step**: "why not enforce a default scenario and
backfill?" There was nothing to backfill. Registration writes a baseline for every owner, nothing
deletes or un-baselines one, no path promotes a companion, and `integrity_check` DC-08 already
asserts it -- so nineteen degraded values, two fabricated figures and three 500 doors were all
defending a state the application cannot produce, and disagreeing with each other while they did it.

**Its two adversarial reviews earned their cost a TENTH time, and both found the same top defect
independently: the step's central claim was false.** "No caller pre-checks left" held for the
balance SEAM and not for the application -- seventeen surfaces resolve the baseline DIRECTLY, and
the worst of them, `compute_balance_sheet`, reported assets, liabilities and equity of `$0.00`
**and `tie_out.in_balance = True`** for a ledger it cannot read. The app asserting a user's books
balance is finding N-113's fabrication one screen over, and R-CA's "closed by DELETION" was untrue
while it stood. The reviews also measured a mutating htmx request answered with silence, an ERROR
event logging the wrong user id in the one case it exists to diagnose, and a gate assertion that
could not fail. Rulings **R-CC..R-CF**; finding **N-117** opened, owner the new step **X-y**.

**The lesson this step paid for, recorded in Section 8: an AST census is a grep with better manners
unless it follows the data.** N-112 named the exact site its grep could not see; X-v built the AST
pass, found a 13th site the grep had missed, and STILL missed the named one -- because that
predicate arrives as a function PARAMETER. And its two instruments shared a blind spot, so they
confirmed each other: a surface that resolves the baseline itself and answers with a plausible 200
is invisible to a census keyed on `BalanceContext` and to a sweep that fails only on 5xx.

**X-w is DONE** (2026-07-30), in seven commits: the rulings `03272174`, then `f3d75fe4` /
`fcc8cd36` / `c70acee5` / `88240253` / `740a005d`, then the residue rulings `5c078076` and the
residue `38f8d879`. **The trace turned the step before a line was written**: `_net_worth` took TWO
shapes for ONE account set on ONE render -- `compute_net_worth_today(list[AccountProjection])`
beside `compute_net_worth_series(list[dict])` -- so finding N-114's stored liability flag was what
that asymmetry produced, not the defect. The second per-account container is DELETED rather than
typed: the dense period map rides on the projection for every kind including loans, and there is no
longer anywhere to store a duplicate rule. Eight record containers on this path are value objects,
three published fields with no reader are gone, and a series that was mutated after its producer
returned is built once. **No figure moves**, and that was proved by ONE harness across two trees on
both databases, at 1,377 keyed lines.

**A measured side effect worth its own sentence**: one `/savings` render built **17 per-account
dense maps for 8 accounts**; it builds **11**. SQL `276 -> 211` on `shekel` and `252 -> 195` on
`shekel_f3_final`. That is half of N-72's redundancy half, closed as a CONSEQUENCE of deleting a
container rather than as a fix -- X-i1 still owns the rest.

**Its two adversarial reviews earned their cost an ELEVENTH time, and for the first time in this
arc they opened NOTHING.** Everything they found was the step's own residue, and X-w6 shipped all
of it. Both found the same two defects independently: the new cockpit-hero guard could not fire on
the regression it named (seven occurrences of the figure, at least three from outside the hero), and
the arc's flagship hero-vs-trend assertion carried a failure MESSAGE that raised `TypeError` at the
one moment it exists for. **NINE of the step's own citations were wrong** -- a twelfth key against an
eleven-key dict, "every producer here takes ONE shape" (false for two), a stated proof that two
loaders "issue the same filter" (they do not; the conclusion holds on other grounds), a
construction guarantee this step's own test violates, R-CJ's census of its own survivors, and the
harness contradicting itself four times about who deletes its shims.

**And the step's PREMISE was false.** It said typing a producer does not protect its template
because Jinja renders a missing attribute as an empty string. A bare `{{ value }}` does -- but the
`money` macro opens with `{% if value < 0 %}` and `Undefined.__lt__` RAISES, so a renamed money
field 500s and the pre-existing `status_code == 200` assertion already covered that class. The
guards still earn their place, against a producer returning the WRONG figure with a 200, which no
status check can see; the reason written beside them did not.

**One lesson paid here and recorded in Section 8: widening an instrument is a shape change, and it
needs the same normalization the code does.** Ruling R-CM added the dense map to the above-seam
harness to close a blind spot both reviews named -- the narrow producers carry a map nothing reads
and NEITHER instrument dumped it. The first attempt made every account's map read as a diff on the
pre-X-w tree, because the projection had no such field there. Normalizing it to the seam's own map
turned that into the strongest evidence the step has: the cross-tree diff now proves ruling R-CG's
map-equality DIRECTLY rather than leaving it inferred from the figures downstream.

**X-aa is DONE** (2026-07-30), in two commits: the ruling `5c2ba585` and the code `c10d5d12`.
**The developer's QUESTION is what opened it** -- "why didn't you take action on
`calculate_trajectory` and `calculate_savings_metrics`?" -- and X-w's answer had been three
reasons of which one survived: the scope argument was circular (R-CI's enumeration came from X-w's
own census, whose target list never included that module), the 64 test assertions were cost, which
rule 7 forbids as a ground, and the real failure was leaving both records REPORTED IN PROSE and
owned by nobody. Section 6 is the owner, not a summary.

**And the question found a defect.** `calculate_trajectory` has three returns and every one is a
full four-field answer, so X-w4's `GoalProgress.trajectory: dict | None` was a nullable that cannot
be null -- ruling R-CA's defect written by the step that cites R-CA -- and the goal card's
`{% if gd.trajectory %}` was a truthiness test on an always-four-key value: a guard that could never
be false, in the package whose thesis is that such a guard is not one. Both gone, with the two
producers typed at their producer and 69 assertions moved onto attributes.

**Its adversarial review found the step's own new control was THEATRE**, which is the eleventh time
this arc has paid for a guard nobody exercised and the sharpest instance yet, because the step
existed to delete exactly that shape. The test looped `assert hasattr(traj, field)` over four names
-- structurally unfalsifiable, since a frozen dataclass with four required fields hands all four to
anything that passes `isinstance`. **Seven mutants were run and FIVE survived**, including the one
the docstring itself named ("omits a field") and a `required_monthly` inflated tenfold. It compares
`dataclasses.astuple` per branch now and kills all of them. The review also measured **three of the
step's own claims false** -- the `required_monthly` rule (an actionable target inside the current
month returns `None`, so a goal targeted later this month says "behind" with no remedy line), the
`months_to_goal` precedence, and both "one production consumer" line citations -- and opened
**N-120**, the emergency-fund footer's double-rounded derived units, measured at `1.5` paychecks
rendered where the raw ratio gives `1.6`.

**The lesson, recorded in Section 8: `hasattr` on a dataclass is not a test.**

**X-z is DONE** (2026-07-31), in ten commits: the rulings `b6b1446e`, the code `8c8d19f6` /
`d80e06fe` / `9e1187c3` / `8cc0656c` / `bffb18cc`, then the review rulings `2fcecdd2` and the
residue `7c453074` / `e8bccf4f` / `5e77d0db`. **The trace turned the step before a line was
written**: finding N-118 names two Python spellings of the liability rule and the 2-year trend as
the surface at risk; there is a THIRD, in Jinja, and the worst surface is the HORIZON. Its three
band producers must cover each account exactly once and they selected with BOTH spellings, so a
divergence counts an account twice with opposite signs -- net worth wrong by double its balance --
or drops it, silently. The rule is one resolved member on the projection now, read by both
questions, and the two bare `category_name == 'liability'` comparisons that drive the danger
subtotal and the WHOLE debt-summary footer are gated in the language the band gate exists for.

**N-120 closed with it, and it moved exactly ONE figure**: `paychecks_covered` `1.5 -> 1.6` on
`shekel_f3_final`, proven three ways -- one line in 61,977 across both harnesses, and a cross-tree
HTML render of five pages on both databases where `shekel` is byte-identical and
`shekel_f3_final` differs in that figure alone.

**Its two adversarial reviews earned their cost a TWELFTH time, and both found the same three
things independently.** The step's CENTRAL claim was false -- `account_category` is not "the ONLY
place a `category_id` meets a cached id", and the survivor that matters is
`ledger_class_id_for_category`, the same asset-vs-liability question on the WRITE path deciding
which ledger class an account's postings book against. A finding was CITED in shipped code and
never filed (`N-121`, in a sentence claiming compliance with rule 6). And the new Jinja gate arm
could not fire on three of the four defects it names -- deleting the debt-footer guard leaves one
liability comparison, which satisfied every assertion it made. **The step's one quoted measurement
also pointed the wrong way**: `48 -> 8` was real, but the new classifier scanned four members with
four cache calls where the old predicate made one (2.3x-4.5x, measured), and `is_liability` is read
~480 times per render. Rulings **R-CT..R-CW**; findings **N-121** and **N-122** filed with owners,
the new steps **X-ac** and **X-ab**.

**What the residue bought, beyond the corrections**: the classifier is now FASTER than the code it
replaced (`0.108` us flat against `0.136-0.139`), the category is resolved **8 times for 8
accounts** rather than ~488, and the second per-account container X-z2 had introduced -- ruling
R-CG's own defect, re-created one commit after the step that deleted it -- is gone.

**X-x's TRACE is DONE (2026-07-31) and it turned the step before a line was written**, in the
pattern this arc has now paid for a dozen times. Finding N-116 counted 63 branches on one question;
an AST census that follows the DATA measures **96 in 49 files** on **five** questions, resolving to
about **50 distinct answers**. **The state 96 branches defend is unreachable for an owner** --
registration writes a bootstrap period, the truncate schema floors its index, and both `DELETE`s
regenerate inside their own transaction -- **and the state beside it, which nobody guards, corrupts
money.** Measured on the prod-shape clone with a 5-day calendar hole: `/savings` net worth
`$233,096.49` -> **`$236,325.04`**, liquid `$4,076.92` -> **`$8,591.92`**, the Checking tile
rendering **`$2,932.41`** -- `Account.current_anchor_balance`, the derived cache, and the exact
figure Section 5's own table names as the pre-X-c2b2 scalar's fabrication -- while `/grid` renders
the "generate your pay periods" card at the same instant and the net-worth trend collapses to zero
points carrying `current_index = 0`. The hole is PERMANENT: the rolling window counts periods ending
on or after today, sees 52, and never fires.

**The trace also found the WRITER, and the developer ruled the root rather than the recommendation.**
On a form that says "Enter your next (or first) payday", **`today+1` through `today+13` are
REFUSED**, `today` and `today+14` are clean accepts, and everything later leaves a permanent
hole -- because registration's bootstrap period is in the way. (An earlier draft said "13 of 14
refused, `today+14` the only clean accept"; X-x's design review measured `today+0` accepted
too, because `generate_pay_periods` removes already-existing starts before
`_reject_overlapping_batch` sees them.) Rulings **R-CX..R-DD**; findings **N-123** and
**N-124** opened, owned by the new step **X-ad**, which is sequenced immediately after X-x because
it WRITES the calendar where X-x only reads it.

**NEXT: X-ad**, then **X-x**, then the steps after them in the order Section 5 lists.

**X-x is BUILT AND HELD** (ruling R-DE, 2026-07-31). Its first two leaves are written, green and
measured -- `pylint app/` 10.00/10, 7,686 tests, both real-data harnesses byte-identical on both
databases, and the `$3,228.55` fabrication provably gone on the gapped clone -- and its two
adversarial reviews then found that **the repair its refusal points at does not work for the
interior hole** (N-127). A refusal whose repair fails is worse than the wrong number it replaced,
so the calendar WRITER goes first and X-x refuses into a calendar the user can actually fix. The
code is PARKED ON BRANCH `wip/x-x-held` (one throwaway commit, `697fcefc`), not in the working
tree: an unrelated production bug in the checking projected end balance needed a clean `dev`, and
that fix branches off `origin/main` so its prod diff is only itself -- `dev` is 27 commits ahead of
`origin/main` and a dev-based hotfix would ship all of them. Nothing on `wip/x-x-held` is for merge
as-is; ruling R-DG's residue work rewrites it. Resume with `git merge dev` onto that branch -- it
carries NO changes to this document, so the merge is conflict-free here by construction.

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

   **This row's own assessment was WRONG, and production paid for it (2026-07-31, ruling R-DH,
   finding N-130).** Ruling R-N had recorded the cost as "the reconciliation row's size, not its
   correctness". It is a correctness defect in the PROJECTED END BALANCE: because the fold partitions
   two data-entry clocks at SECOND granularity, three rows ticked off in the nine seconds after an
   anchor were subtracted from a bank balance that already contained them, and the grid rendered
   **-$4,021.37** against a true **-$19.95**. 47% of the real Checking account's settled rows
   (`$19,602.13` gross) are classified by click order. X-f is no longer a follow-up that shrinks a
   row; it is the fix, and the day partition that unbreaks production today is the seam it fills.
   Trace, measurements, rulings and build: **`anchor_settle_partition.md`**.
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

7. ~~**A producer publishes what nothing reads, and a template reads what nothing publishes.**~~
   **CLOSED 2026-07-27 at X-q2 (`be6cfae6`)**, its larger half -- the debt-free date derived twice,
   19 years apart -- having closed at X-q1 the same day. The Horizon's `is_loan_free` and
   `horizon_end` and the callerless `compute_net_worth_horizon` are deleted (N-100), and the
   archived list's unrenderable "Paid Off" badge is a decided NO rather than an open question
   (N-102). **The root itself is not closed**: X-q2's own reviews found it one level inside the
   dict and one package over (**N-104**, plan step **X-s**), which is why this row is struck
   through rather than removed -- the disease has a smaller surface, not none.

**Nine more steps carry what the seven roots above do not name**, each owning findings the
2026-07-27 triage grouped by root: X-h, X-i, X-j, X-k, X-l, X-m, X-n, X-p, plus **X-s** (root 7's
surviving residue, opened by X-q2's reviews) and E2's two. (Root 7 itself is CLOSED; X-o, X-q1,
X-q2, X-q3 and X-r have all SHIPPED.) Section 5 has them in execution order; Section 6 records who
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
| **R-M** (N-39; answered 2026-07-25, **AMENDED 2026-08-01 -- see `anchor_settle_partition.md` Section 12.3**) | **AMENDMENT: the column SPLITS rather than the guard bending.**  R-M and ruling R-DH (e) had been defining ONE column two ways -- "the day the purchase happened, never in the future" and "the day the money hit the account, one to two days later for a debit card" -- and both are right about their own fact.  `transaction_entries.entry_date` becomes **`purchased_on`**, which keeps this guard verbatim, plus a nullable **`settled_on`** carrying the posting day (`CHECK settled_on >= purchased_on`; no upper bound, because any "at most N days ahead" ceiling is an unjustifiable constant).  The as-of window stays deleted on X-c2c1's own sharper ground -- *"a purchase that happened belongs in the reservation whatever date the reader is asking from"* -- not on this guard, which is what made the split reachable; the load-bearing claim in Section 10.6 was stale.  *Rejected:* widening this bound on one column (the field would then mean the purchase day on some rows and the posting day on others, with nothing in the schema recording which, while `remaining` and the out-of-period warning both read it as the purchase day).  Migration `d7c1f4a9e603`.  ORIGINAL RULING: **A future `entry_date` is REFUSED at the write door, and the reservation's `as_of` window then DELETES.**  The fork is closed rather than ruled.  An entry is "an individual purchase recorded against a parent transaction" (`transaction_entry.py:15`) -- something that HAPPENED; a purchase not yet made is what the envelope's remaining budget already models.  Traced: the add form cannot create one (`_transaction_entries.html:179` posts a HIDDEN `entry_date` fixed to today), the edit form can (`:79`, an unbounded `<input type="date">`, and `EntryUpdateSchema.entry_date` carries no bound), and ZERO such rows exist in either database (newest entry anywhere 2026-07-24).  Worked on the live Groceries envelope #2280 (`$780.00` budgeted, 4 credit + 2 debit entries, hold-back `max(780.00 - 226.42 - 493.03, 0.00) = $60.55`): one `$150.00` entry dated 2026-08-05 moves the projected balance **`-$89.45`** as a debit (the `max()` floor takes over: `$150.00` held back) or **`+$60.55`** as a CC entry (`$0.00` held back) -- so it moves money in EITHER direction for a purchase that has not happened.  With the guard in place the window drops nothing: every entry is then dated at or before today, and the only production reader that pins a non-default `as_of` is `tax_report_service.py:373`, which reaches `loan_interest_in_year` and no cash producer -- so the parameter is dead and deletes, which is what ends the shipping divergence (the calendar windows, the grid and the daily ramp do not) rather than picking a winner.  Backdating stays fully allowed and is used (the real 05-21 Groceries row carries entries from 05-18).  **Both halves are SHIPPED**: the guard at X-c0 `5b3764a7`, the deletion at X-c2c1 `b42dda42`, whose as-built entry records the sharper reason -- a purchase that happened belongs in the reservation whatever date the reader asks from, so the parameter was not merely dead but wrong to keep. | X-c0 `5b3764a7` (guard), X-c2c1 `b42dda42` (deletion) |
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

### Answered (developer ruling, 2026-07-27: X-q's two forks, both as recommended)

**Both were put to the developer BEFORE any X-q code was written**, which is what the X-q entry's
own text required. Each carries what the trace measured, not what the shape of the problem
suggested.

| # | ruling | consumed by |
|---|---|---|
| **R-AX** (answered 2026-07-27, as recommended) | **"Debt-free" stays LOAN-ONLY, and the surfaces say so.**  The derivation covers accounts with a payoff MODEL -- amortizing loans -- while the Horizon's own liability band sums a revolving Credit Card the seam holds FLAT at its owed magnitude.  A flat balance never reaches zero, so a card can carry no payoff date, and including it under today's model would mean nobody carrying a card balance ever gets a date at all: honest and useless.  So the figure is unchanged and the CAPTION narrows to what it measures -- "Loans paid off <mon>", "All loans paid off" on the chart flag, plus "excludes `$X` revolving" when a liability with no payoff model carries a balance.  A card that can carry a REAL payoff date is the credit-card arc's work (`docs/plans/implementation_plan_credit_card.md`), and `_debt_line` is where it would be admitted.  Rejected: including revolving debt as it is modelled today (poisons the date for the state most users are in), and building the card paydown model inside X-q (a feature with its own UI and its own trace, overlapping an arc that already exists) | X-q3 |
| **R-AY** (answered 2026-07-27, as recommended) | **A payoff date that is already PAST is reported, and the chart falls back.**  `plan_payoff_date` returns the DUE date the balance first folds to zero, and an overdue-but-still-projected installment that clears the loan folds behind today; `_metrics` counted it in its `max()` while `_horizon` filtered it out -- one question, two rules, which is the defect X-q exists to end.  The outlook reports what the fold says, because it is a fact about the loan's plan; the Horizon then falls back to its fixed window for AXIS SIZING only, since `_milestone_axis_x` clamps a past target to index `0.0` and would plant the flag on "Today".  The user is NOT loan-free in that state, and the rule this replaced said they were.  Rejected: future-dates-only (the caption stops reporting a date the fold can state, and "no date" then covers two different situations) and a third rendered state ("Debt-free Feb 2026 (payment overdue)": most informative, but a new value-object state and a new string in two templates for a case that needs an overdue unpaid installment to reach) | X-q1 |

### Answered (developer ruling, 2026-07-27: X-q2's three forks, all as recommended)

**The trace ran first and no code was written for it**, and it measured every figure below on both
databases. The third fork was put to the developer TWICE: the first framing was rejected as unclear
and re-asked in plain language -- what the drawer is, what the badge was, and what the number beside
it actually comes from -- which is what surfaced the measurement that reframed it.

| # | ruling | consumed by |
|---|---|---|
| **R-AZ** (N-100; answered 2026-07-27, as recommended) | **The Horizon publishes only what the presentation boundary reads: `horizon_end` and `is_loan_free` DELETE, and `_resolve_horizon_domain` returns the two things the axis needs.**  `horizon_end` is `dates[-1]` -- every branch of the resolver returns a December 31 strictly after `today`, so `_build_sample_dates` always ends on it (verified by brute force over **4,034,784 inputs**, zero mismatches), making it one fact under two keys.  `is_loan_free` is :attr:`LoanPayoffOutlook.is_loan_free`, a derived property of a value object this producer does not own -- ruling **R-AW's** copy one layer up from where X-r deleted it.  Neither key is named by any serializer, template, script or JS file, so the rendered payload is byte-identical.  **What the deletion COSTS, stated because it is the only real cost:** the X-q1 firing control `test_a_user_whose_only_loan_is_retired_is_loan_free` had `is_loan_free` as its ONLY discriminating value, and it moves to `loan_payoff_outlook(...).is_loan_free` -- where the discrimination now happens -- verified by mutation to fire on that exact assertion.  Rejected: **publishing them to the UI** (the cockpit footer beside the chart already renders the same three-state distinction, so a second rendered statement is a second place to disagree -- which is what X-q1 spent a commit ending), and **deleting `horizon_end` only** (`is_loan_free`'s ground is the stronger of the two, and keeping it leaves the exact shape this step exists to remove) | X-q2 |
| **R-BA** (N-100; answered 2026-07-27, as recommended) | **`compute_net_worth_horizon` DELETES, and its tests read the horizon where the route reads it.**  A PUBLIC export with zero `app/` callers -- an AST census (never a grep) found 10 call sites, all in one test file -- while `compute_debt_summary`, `compute_debt_principal_progress`, `compute_goal_progress` and `compute_account_balance_cell` each have a live one.  "A narrow producer has a narrow consumer" goes from a pattern with one exception to an invariant with none.  Two tests delete with it: the narrow-vs-full agreement test, whose entire content is "two producers agree", and the standalone resolve-once test, whose property the full-build sibling pins (proved by mutation, not by argument).  Rejected: **keeping it and giving it a caller** (an HTMX range endpoint is a FEATURE with its own trace, and the client already receives BOTH ranges in one `data-chart` payload -- an endpoint would add a second full build per page, not remove one), and **keeping it documented as a public API** (verbatim N-85 / N-96 / N-100's own words: dead surface kept honest by its own tests) | X-q2 |
| **R-BB** (N-102; answered 2026-07-27, as recommended, and re-asked in plain language first) | **NO "Paid Off" badge on the archived drawer, permanently, and the drawer's WRONG NUMBER is recorded as N-103 with X-e as its owner.**  The ground is a reason and not a cost: `is_paid_off` is the seam's CONGRATULATION predicate in its own words, and the moment to congratulate is the live list when the loan clears -- the archived drawer is opened to unarchive or permanently delete.  So `_load_archived_accounts` stays two keys and runs no engine or seam call.  **What pricing the badge found, and it reframed the fork:** the drawer's "Last Balance" is the `current_anchor_balance` COLUMN, which for an amortizing loan is not a balance at all -- `anchor_service.AmortizingAccountAnchorError` says so in terms and the cash true-up door REFUSES the kind, so nothing keeps it true; measured at `$178,103.41` against `$177,277.97` owed (Mortgage) and **`$0.00` against `$15,663.59`** (Van Loan).  That is archived finding B-15's shape on the one surface B-15's fix did not reach.  Zero archived loans exist on either database, so nothing is on screen today.  Rejected: **badging it** (accurate and stable -- `is_retired` folds RECORDED events, so an archived loan still owing never self-badges -- but it seats a green badge beside that wrong number), **badging AND fixing the balance** (decides a third of X-e's question early, on a surface X-e's entry already lists, and puts a per-kind switch inside a presentation loader -- the "surface picks its own producer" defect X-j exists to remove), and **handing the whole question to X-e** (leaves X-q2 having decided nothing).  **X-q2's adversarial design review argued for OVERTURNING this split**, on the ground that the deferred half falsifies the decided half -- the drawer congratulates anyway, falsely, through the number.  The argument is recorded at N-103 with its proposed fix (suppress the line for ledger-derived balances) rather than acted on, because the developer took this fork with the measurements in front of them | X-q2, N-103 |

### Answered (developer ruling, 2026-07-28: X-s's four forks, two as recommended and two WIDER)

**The trace ran first and no code was written for it**, and it re-verified both halves of N-104 by
AST rather than by grep (Section 8's rule): `LoanPayoffOutlook.is_loan_free` has exactly ONE
occurrence in `app/`, its own `def` at `_debt_line.py:105`, and the two fields `_metrics` copies out
of the outlook appear only as the string literals at `_metrics.py:529` and `:536`. The client side
is as the finding states: the flag plugin reads `milestone.x` and `milestone.label` and nothing else
(`net_worth_cockpit.js:393`, `:407`, `:419`), `selectRange` never touches `data.assets` /
`data.liabilities` (`:168-186`), `parseData` inspects only `data.net` (`:157`), and
`_serialize_horizon` is the ONLY `app/` consumer of `horizon["milestones"]` (`routes/savings.py:138`).
**Two of the four forks the developer took are WIDER than the recommendation, and both widen for one
reason: they convert a property that would need a guard into one that cannot be violated.**

| # | ruling | consumed by |
|---|---|---|
| **R-BC** (N-104a; answered 2026-07-28, WIDER than recommended) | **The milestone's `kind` is deleted at BOTH ends, so X-q2's mutation guard extends one level down and the payload contract stays STRUCTURAL.**  The serialized milestone becomes `{label, x}` and the top-level payload drops `assets` / `liabilities`; that half was never in doubt.  The fork was what keeps them deleted, because X-q2's guard removes TOP-LEVEL keys and a dead key here rides inside a live one.  Under this ruling the producer's milestone dict is `{date, label}`, every key of it is subscripted by the serializer (`date` for `_milestone_axis_x`, `label` for the chip), and the SAME remove-a-key-and-require-a-crash guard therefore reaches the nested dicts with no new mechanism.  **The recommendation was a typed `Milestone` value object plus a test that reads `net_worth_cockpit.js` with comments stripped**; the developer declined it, and the ground is that a JS-text check proves a NAME appears, never that a value is used -- Section 8's "a static guard that greps for a NAME cannot tell code from prose", measured three times in this codebase.  **The price, stated so it is not discovered later:** the 5 test sites that identify a flag by `kind` re-key on its `label`, and the two structural labels are now module constants (`_DEBT_FREE_MILESTONE_LABEL`, `_PAID_OFF_MILESTONE_SUFFIX`) so a test cannot drift from the string production emits -- which is what X-q3's rename of that very label would otherwise have broken silently.  Rejected: keeping `kind` in the producer with the payload's key set merely PINNED (the client half becomes a comment, and N-100's history is that a pinned list passes while a mutation fails).  **One correction the step's own design review earned and this ruling now records:** the recommendation BUNDLED the typed value object with the JS-text check, and the ground given for declining it is the JS half's weakness -- the two are separable, and the value object did not depend on the check.  The chosen design's payload contract also ends in a pin (`test_maps_decimals_dates_and_milestones`'s key-set assertion), so both options pinned something; what the ruling actually buys is that the PRODUCER contract stays a mutation with no new mechanism | X-s1 |
| **R-BD** (N-104b; answered 2026-07-28, WIDER than recommended) | **The debt summary becomes a frozen value object that CARRIES the outlook, the DTI trio collapses to ONE nullable field, and the dashboard track COMPOSES the summary instead of copying it.**  R-AW applied at the boundary where the copy happens, plus the two defects the trace found in the container itself.  (1) `_apply_dti_metrics` MUTATES the dict in place with three parallel nullables that are always all-set or all-`None` (`_metrics.py:582-596`), and three predicates across two templates read that one state -- `_cockpit.html:278` on `dti_ratio`, `_tracks.html:73` on `dti_ratio` and `:76` on `dti_label`.  One `DtiMetrics \| None` field makes the state unrepresentable wrong and leaves ONE predicate.  (2) The dict is extended by two more layers after it is built -- `dashboard_pulse_service.py:829-834` copies it and adds `principal_paid_fraction`, then `routes/dashboard.py:96-99` mutates THAT to add `principal_paid_pct` -- so the object a template reads is assembled across four modules and no one of them states its shape.  **The recommendation was the outlook only, with the container's conversion given its own step**; the developer ruled both into X-s on the ground that the outlook lands in the container either way, and re-typing the container twice costs two passes over the same ~124 test references.  There is NO figure change in any of it: every field keeps its value and its rounding | X-s3 |
| **R-BE** (answered 2026-07-28, as recommended) | **A borrower whose loans are ALL retired earns the caption "All loans paid off" on the /savings Liabilities footer, and nowhere else.**  The state is `LoanPayoffOutlook.is_loan_free` -- the third of the three the value object exists to tell apart -- and today the footer's `{% if %}` / `{% elif %}` chain falls through it in silence (`_cockpit.html:267-274`), the chart plants no flag (there is no future date to flag), and the property has no reader at all.  The string is the one ruling R-AX already chose for the chart's flag, so the app says one thing one way.  **It is reachable and bounded:** `_compute_debt_summary` returns `None` when the user has NO loan accounts, so the footer is absent entirely there and this caption cannot claim "paid off" for someone who never borrowed.  The dashboard debt track is deliberately left alone -- it already renders `100% paid` on its rail and `$0.00 to $0` beside it for this exact state (`_tracks.html:83`, `:88`), so it is not silent and a second string would be a second place to disagree.  A revolving balance still qualifies the caption through the `revolving_debt` line X-q3 shipped beside it.  Rejected: captioning both surfaces (above), and shipping no caption (leaves the third state of a three-state value object unrendered on every surface, which is the reason N-104b is a finding rather than a tidy-up) | X-s3 |
| **R-BF** (N-105; answered 2026-07-28, as recommended) | **`_project_one_account` states each of its two conditions ONCE, and the missing-baseline guard is HOISTED to cover both arms.**  Two halves of one shape.  The doubled predicate: the function branches on `loan_result is not None` (`_projections.py:210`) and then re-tests it as `if acct_loan_params:` (`:247`) before dereferencing `loan_result.figures` (`:266`); the two cannot diverge today -- both resolve to a `LoanParams` row for the same `account_id`, `_data._load_loan_params_and_escrow` merely filtering a SUBSET on `has_amortization` where `loan_loaders.load_loan_params` does not -- but the invariant lives in two other modules, neither of which knows this dereference depends on it.  The loan arm already HAS the answer, so it states it.  The asymmetry: the non-loan arm owns the no-baseline state explicitly (`_account_balance_maps:59-60`) while the loan arm four lines later reaches `require_scenario`, which RAISES (`balance_at/_context.py:327-332`), so `/savings` 500s for a user the other four kinds degrade for.  The seam's own contract names the pattern -- "callers that legitimately handle the no-baseline case keep their own guard BEFORE calling the seam; this is the defensive backstop" -- so the fix is to state that guard once, above both arms, and let the backstop stay a backstop.  Unreachable in production (`auth_service.py:707` writes a baseline at sign-up and no route deletes or un-baselines one, verified by an `is_baseline` census over `app/`), so no figure moves.  Rejected: failing loud for every kind (defensible, and the arc's stance elsewhere -- but it deletes a guard four kinds rely on, which is a behaviour change to every non-loan tile and belongs to whoever re-opens that ruling, not to a step closing a doubled predicate), and fixing only the predicate (leaves one rule stated in two places, which Section 8 says is what moves money when one statement is edited and the other is not) | X-s2 |

### Answered (developer ruling, 2026-07-28: X-s's review residue, four forks)

**Two adversarial reviews ran against X-s before it was committed and both earned
their cost.** Between them they found the step's headline claim overstated, a
dead surface X-s1 CREATED, a dead field X-s3's own new value object carried, and
-- sharpest -- that the docstring correcting a false invariant cited **two
function names that do not exist**. Every claim below was re-verified by AST or
by measurement before it was ruled on; the two that were wrong are recorded as
wrong rather than quietly fixed.

| # | ruling | consumed by |
|---|---|---|
| **R-BG** (answered 2026-07-28, as recommended) | **`compute_net_worth_series` stops publishing `assets` and `liabilities`.**  X-s1 CREATED this dead surface: once the chart payload stopped copying the two totals across, the producer keys had ZERO `app/` readers (AST census -- no Python, no template, no JS), leaving four test files as their only consumers.  They are one fact under two keys, exactly R-AZ's ground: the split and the totals come from ONE per-period sum (`_sum_composition_at_period`), so `assets[i]` IS the sum of the asset-side bands and `liabilities[i]` IS the liability band.  The oracle that reads them gains by the move -- `test_cross_page_balance_equality` now compares the other pages against the bands the chart actually draws, rather than against a parallel copy.  **What made this a ruling rather than a tidy-up:** the docstring justifying the keys named the cross-page equality TEST as their consumer, two lines below its own sentence "a justification that names a consumer which does not read the value is the shape this arc keeps finding" -- R-BA's "dead surface kept honest by its own tests", written by the step that was committing it.  Rejected: recording it for a later step (the surface is newly dead, and rule 7 gives cost no standing), and keeping it behind an honest caption (states the truth and still leaves what two rulings have already deleted twice) | X-s1 |
| **R-BH** (answered 2026-07-28, as recommended) | **`DtiMetrics` stores ONE fact: the ratio.**  The same census applied to X-s3's own new value object found `gross_monthly_income` with zero `app/` readers and no template rendering it -- an input already spent computing the ratio, which is byte-for-byte why X-s1 refused to carry a milestone's `date` into the payload.  The band `label` likewise stops being stored and becomes a PROPERTY over the ratio, which is `LoanPayoffOutlook.is_loan_free`'s own pattern ("derived rather than stored so it cannot contradict the other two") and R-AZ's ground for deleting the Horizon's copy of it.  Two stored fields that must agree can disagree; one cannot.  **The price, stated because it is the only one:** five tests pinned the engine-derived denominator by reading it back (MED-06 / F-032's raise-aware gross, `$8,926.67` against the off-engine `$8,666.67`).  They now pin it by DIVIDING by it -- `ratio == total_monthly_payments / gross * 100` with the numerator asserted separately -- which the trace judged the stronger pin, since the off-engine figure fails the identity.  Rejected: keeping it as the audited denominator (a plausible future caption is not a consumer), and deleting it while adding a caption that renders it (a UI change beyond what this step scoped) | X-s3 |
| **R-BI** (answered 2026-07-28, as recommended) | **The debt track's DOUBLE projection is recorded as finding N-109 with its own step, not folded into X-s3.**  Measured on the developer's own data: ONE `compute_tracks_section` call runs `_project_debt_accounts` **twice** and the seam-batch builder **three times**, because `compute_debt_summary` and `compute_debt_principal_progress` each run the full load -> params -> project pipeline over the same loan set.  X-s3 rewrote exactly those lines and kept the shape.  The review's proposed fix -- make `principal_paid_fraction` a `DebtSummary` field, deleting `DebtTrack`, the second narrow producer and the duplicate pass -- is recorded WITH the finding.  It is declined for X-s3 on one ground: the two producers answer over different membership rules (owed-today for the money, all-loans-ever for the marker), and merging them means re-deciding which loan set each figure is entitled to, which is the question ruling X-q had to settle once already at a cost of 19 years of contradiction.  Rejected: folding it in now, and keeping the narrow producer alive beside a merged field (a public function with no caller, which R-BA deletes) | X-u |
| **R-BJ** (answered 2026-07-28, as recommended) | **The two residues X-s does not reach get ONE new step, and the `ad` dict rides with them.**  (a) The chart payload's `composition` map is passed through wholesale by both serializers while `net_worth_cockpit.js` hardcodes its four asset-band names and silently drops anything else, so ADDING a band ships a dead float series per period with no gate firing -- the mutation guard descends into the milestone dicts and not into this one, which is the bigger of the two (finding N-108).  (b) The no-baseline rule is stated in FOUR places, not one: X-s2's first docstring claimed "stated HERE and nowhere else" and was wrong, and its correction then cited two functions that do not exist (finding N-107).  (c) The per-account projection dict `ad` is still untyped with optional keys used as type discriminators, read by five modules and by Jinja -- and where the debt-summary dict X-s3 converted cost `$0.00`, this one's measured cost is B-16 and N-98, a 19-year contradiction (finding N-111).  Plan step **X-t** owns all three: they are one question -- "which of this package's shapes are guaranteed and which are merely tested" -- and answering it per-finding across three steps is how the answers drift apart | X-t |

### Answered (developer ruling, 2026-07-28: X-t's four forks, all as recommended)

**The forks were priced before any code was written, each with its option space and a
recommendation, and the trace corrected the plan on three of the four** -- the no-baseline rule was
stated 18 times and not four, six modules read the projection dict and not five, and a stray
composition band costs more than the "dead float series" the finding named. What the developer
ruled is below; what the two adversarial reviews then found is the row after it.

| # | ruling | consumed by |
|---|---|---|
| **R-BK** (answered 2026-07-28, as recommended) | **The projection becomes a frozen `AccountProjection` with a NESTED `LoanDetail \| None`.**  Loan-ness is then ONE structural question -- the field's existence -- so the figures and the contract row cannot arrive apart, which is the two-predicates-for-one-condition shape X-s2 unpicked one layer down (N-105).  Rejected: four flat optional fields (re-opens exactly that, since `loan_figures is None` and `loan_params is None` become two askable predicates), and a `TypedDict` (types the dict without changing it: key MEMBERSHIP stays the discriminator and Jinja keeps degrading silently, which is the half of N-111 with a measured cost).  **`is_liability` became a derived property in the same pass**, on the ground the arc has already ruled twice (`DtiMetrics.label`, `LoanPayoffOutlook.is_loan_free`): the page asked that one rule two ways over one set of balances | X-t1 |
| **R-BL** (answered 2026-07-28, as recommended) | **One predicate on the CONTEXT, plus a hoist inside the region -- not the plan's "hoist all four to the build entry".**  The plan's own text could not be executed as written: `dashboard_pulse_service` is another page with another degraded contract, and the savings build entry cannot hoist because the degraded page still renders its account grid OUT of the projection.  What ships instead: `BalanceContext.has_baseline` states the rule once and `require_scenario` raises on it, and the two copies that sat under ONE caller collapse into one guard there.  Rejected: renaming the predicate everywhere and deleting nothing (the two copies that disagreed would still disagree), sequencing the whole finding behind X-i (X-i merges the two `build_maps` passes, which kills one guard by construction -- but a finding is not deferred for cost, rule 7), and hand-writing a degraded 12-key context at the entry (a second copy of the page's shape that nothing keeps in step) | X-t2 |
| **R-BM** (answered 2026-07-28, as recommended) | **Derive what can be derived, gate the rest, and delete two homes doing it.**  `_ASSET_BANDS` becomes the display categories minus the liability key, and the template's band-order copy is deleted by iterating the producer's own composition map.  The three homes that CANNOT import a Python tuple -- a script served to a browser, CSS custom properties, display microcopy -- get a static gate in the tier `test_template_no_money_arithmetic.py` established.  Rejected: gate-only (nothing deleted), and publishing the band vocabulary in the payload for the client to iterate (moves display microcopy into the service, still needs a per-band CSS token, so the gate survives anyway -- recorded as the option it is, with `liability_band` as the one key that would genuinely earn its place) | X-t3 |
| **R-BN** (answered 2026-07-28, as recommended) | **A milestone's LABEL is its identity, and a duplicate is a display outcome rather than a defect.**  Two flags at two dates are two true statements; the producer must never DROP one to keep labels unique, and priced by planting, the dedupe kept a small loan's flag and dropped the debt-free one.  What a collision breaks is a CONSUMER that identifies a flag by the string alone, so a flag is identified by its `(label, date)` pair -- unique by construction, since a per-loan flag fires strictly before the debt-free date.  Rejected: re-adding a machine `kind` server-side only (its one consumer would be a test, the exact ground R-BH deleted `DtiMetrics.gross_monthly_income` on), and re-adding it at both ends with a client that styles the two kinds differently (a UI change, and it re-opens R-BC six days after it was taken) | X-t4 |

### Answered (developer ruling, 2026-07-28: X-t's review residue -- what shipped in X-t5)

**Two adversarial reviews ran against the FROZEN four-commit tree and both, independently, found
the same top defect -- and it was inside the fix.** The rulings below were taken on the reviews'
evidence; each was re-verified in the tree before it was acted on, and the two the reviews got
slightly wrong are recorded as re-measured rather than repeated.

| # | ruling | consumed by |
|---|---|---|
| **R-BO** (answered 2026-07-28) | **`compute_property_equity` owns its no-baseline state, and the "two doors" claim is corrected in every docstring that made it.**  The package has THREE seam doors, not two: the equity producer reaches `balance_at.loan_figures` through `home_equity_service` for every secured loan, outside X-t2's hoisted guard, so a borrower with a Property securing a mortgage and no baseline got a `ValueError` -> 500 where every other tile degraded.  It pre-dates X-t (the same probe raises at `33cb3e8f`), which is precisely why it is ruled here: the step's subject is how many places state this rule, and its census counted CALL SITES instead of walking the call graph.  Rejected: hoisting to `compute_dashboard_data` (the three doors have three different degraded values -- a blank tile, an empty region, an empty card list -- and one guard cannot produce all three without re-writing the page's shape) | X-t5 |
| **R-BP** (answered 2026-07-28) | **The Horizon's band literals are DERIVED, and the gate asserts the partition.**  `_ENGINE_BANDS` plus a hand-written `{"asset", "other"}` had to keep summing to the vocabulary X-t3 gated across four languages -- so a sixth category would pass all five gate arms and still be published as a permanent ZERO series here, while the `2 years` range beside it reported the real money and this module's own documented invariant (index 0 equals the hero) broke with nothing failing.  `_PARAM_GROWTH_BANDS` is `_ASSET_BANDS` minus the engine's, and the gate asserts the three producers PARTITION the composition.  This is N-108's root in the one language where it could be deleted rather than gated | X-t5 |
| **R-BQ** (answered 2026-07-28) | **The gate is comment-stripped, and it carries controls on ITSELF.**  Three of five arms scanned raw source, so a band dropped behind a `// "other" dropped` comment satisfied the arm written to catch exactly that -- proven by exercising the helper, not by reading it.  `TestTheGateItself` now plants that source, a Jinja comment, a nested literal and a moved file.  The docstring also states what the arms do NOT prove: that a declaration is USED.  Rejected: trusting the "declaration, not a mention" claim as written (it held for two arms of five) | X-t5 |
| **R-BR** (answered 2026-07-28) | **The fabricated no-baseline HERO is recorded, not changed inside a fix commit.**  Every balance in that state is `None`, so the `$0.00` hero is as fabricated as the flat chart X-t2 deleted -- the review is right.  Whether it should read `--` is a DISPLAY ruling with its own surface, and X-t5's job was the review residue; it becomes finding N-113 with step X-v, and the test that pins today's answer says so at the assertion rather than reading as endorsement | X-v |

### Answered (developer ruling, 2026-07-28: X-u's four forks, all as recommended)

**The trace ran FIRST -- it is what the X-u entry named as the step's own first action -- and it
answered ruling R-BI's objection with a measurement rather than an argument.** R-BI declined this
merge inside X-s3 on the ground that the two producers "answer over different membership rules, and
merging them means re-deciding which loan set each figure is entitled to". The trace measured that
the rules are REDUCERS OVER ONE LIST, not two loan sets: `_loan_ad_current_principal` and
`_compute_principal_paid_fraction` are both handed the same `loan_ads`, so neither predicate has to
move for the merge, and the fraction is byte-identical over the narrow and the full projection on
BOTH databases (`0.1768790812553367082980574648` on `shekel`,
`0.1788328151006954065539843973` on `shekel_f3_final`). R-BS is therefore the ruling that REVERSES
R-BI on evidence R-BI did not have, and it is recorded here BEFORE the code that cites it -- because
X-s's code commit cited R-BD one commit before the ledger recorded it, and both of X-u's adversarial
reviewers independently flagged the same shape here as an unresolvable citation.

| # | ruling | consumed by |
|---|---|---|
| **R-BS** (answered 2026-07-28, as recommended) | **`principal_paid_fraction` becomes a `DebtSummary` FIELD, set at that value object's one construction site, and the second debt producer is deleted.**  It reduces over the same `loan_ads` the money figures do and applies its own all-loans-ever rule inside itself, so the rules stay distinct while the loan SET stays one -- the agreement the two producers' docstrings PROMISED becomes structural.  `DebtTrack`, `compute_debt_principal_progress` and the duplicate pass go together.  Rejected: a new `DebtPosition` value object pairing summary + fraction (it is `DebtTrack` relocated one package over -- a second container whose only job is to pair a value object with one scalar, which is the class finding N-114 opens next), and memoizing the shared pipeline behind both producers (caches a duplicate instead of deleting it, keeps two producers that must keep agreeing, and a memo keyed on a mutable context is a new hazard).  **The R-BG / R-BH objection was priced and does not bite**: those deleted surfaces with ZERO `app/` readers, and this field has a live one -- and `DebtSummary` was ALREADY a two-consumer union (four fields read only by `/savings`, and the dashboard track reads a strict subset), so the merge makes that union symmetric rather than adding a new class of thing | X-u |
| **R-BT** (answered 2026-07-28, as recommended) | **`_project_debt_accounts` is INLINED into `compute_debt_summary` and deleted.**  Its entire stated rationale was that two producers must not drift onto different loan sets; with one producer left that rationale is gone, and the other three narrow producers in the module already inline their own load -> params -> filter -> project.  The helper was the ASYMMETRY, not the DRY -- verified by hand, since pylint's cross-module `duplicate-code` cannot see same-module duplication, and nothing it single-sourced is duplicated after the inline.  Rejected: keeping it as a one-caller private helper (a docstring justifying itself by a caller that no longer exists, and the debt producer shaped unlike its three siblings) | X-u |
| **R-BU** (answered 2026-07-28, as recommended) | **The residual double load stays for the input-tier memo and is SEQUENCED, not deferred** (rule 7).  Threading a pre-loaded core through the narrow producers' signatures is a second sharing channel beside `BalanceContext`, which plan step X-i1 exists to make unnecessary.  **What the ruling assumed was smaller than what X-u's design review then measured**, so the sequencing survives and the RECORD does not: the residue is finding **N-115**, its owner's input tier is widened to name it, and it is a Section 6 row rather than a sentence in a docstring.  Rejected: collapsing it inside X-u | N-115 -> X-i1 |
| **R-BV** (answered 2026-07-28, as recommended) | **`/savings` does not render the new field, and X-u adds no pixel.**  The cockpit's Liabilities footer has no rail and no slot for a progress marker; adding one is microcopy plus a caption that must not read as contradicting the owed-today total beside it -- two figures over two different loan sets -- which is a DISPLAY decision with its own surface, and is the change R-BH itself refused to make inside a cleanup step.  The step stays a refactor whose proof is that both real-data harnesses come back byte-identical.  Rejected: adding the caption here | X-u |

### Answered (developer ruling, 2026-07-28: X-v's five forks -- and the developer's own question was the root)

**The trace ran FIRST, as this arc's entries require, and it inverted the step's premise twice.**
X-v was entered as "convert the 12 remaining spellings of the no-baseline rule, reading each
degraded VALUE rather than renaming the predicate". Two instruments changed what the step is:

* an **AST census** of `app/` -- the entry's own stated requirement, since the count was "a floor
  until an AST pass replaces the grep" -- found **13**, not 12. `tax_report_service.py:374-375`
  writes the rule through a local alias (`scenario = balance_ctx.scenario` then `if scenario is
  None`), which the spelling-shaped census could not see.
* a **route sweep** -- every GET rule in `url_map`, requested by an owner holding every account
  kind with the baseline removed, 173 requests with and without HTMX headers -- found **8 endpoints
  across 3 doors that 500, and not one of them spells the rule anywhere**.
  `_load_route_context` (`routes/loan/_helpers.py:323` -> `_require_figures:239` ->
  `loan_figures` -> `memoized_payoff`) 500s the loan dashboard, its anchor form, its balance hero
  and both calculators; `resolve_home_equity` (`home_equity_service.py:140`) 500s
  `/accounts/<id>/property`; `_load_debt_accounts` (`debt_strategy.py:140`) 500s `/debt-strategy`
  and its calculate POST. **This is X-t5's lesson a second time**: a grep answers "who writes this
  line"; the question was "who reaches that raise".

**Then the developer asked the question the step had been circling -- "why not just enforce a
default scenario per user and backfill?" -- and the answer is that there is nothing to backfill,
because the invariant already holds.** Measured, not assumed:

* `auth_service.py:707` writes a baseline for every OWNER at registration, and `register_user` is
  the ONLY owner-creating path (`scripts/seed_user.py` calls it; `scripts/seed_companion.py` and
  `routes/settings.py:529` create COMPANIONS).
* Nothing in `app/` or `scripts/` ever sets `is_baseline = False`, deletes a `Scenario` row, or
  PROMOTES a companion to owner -- the single role write in the tree is
  `scripts/seed_companion.py:94`, which demotes.
* `scripts/integrity_check.py:495-511` already declares the invariant as a **critical** check
  (**DC-08**, "Users without a baseline scenario"), excluding companions on the stated ground that
  a companion "views the linked owner's data and owns no budget rows of their own ... by design".
* The one baseline-less row on EITHER real database is that companion (`klgrubb@pm.me`, 0
  scenarios, 0 accounts, 0 pay periods). A route sweep run AS her returns
  `{"200": 7, "302": 4, "404": 60}` with **zero 5xx**: `require_owner` 404s her off every balance
  surface, and no companion service imports `BalanceContext` or `balance_at` at all.

**So nineteen degraded values, two fabricated figures and three 500 doors are defending a state the
application cannot produce -- and they disagree with each other.** The state is reachable only by
`psql`. N-112's and N-113's cost columns said "unreachable in production" for the WRONG reason
(they said no such user exists; one does, and what keeps him out is the role check, not the
invariant), which is Section 7.6's rule earning its keep one more time.

| # | ruling | consumed by |
|---|---|---|
| **R-BW** (answered 2026-07-28, developer's own reframing) | **The no-baseline state gets ONE answer, and it is a NAMED exception with ONE handler -- not nineteen invented values.**  `require_scenario` raises `BaselineMissingError` (`app/exceptions.py`, subclassing both `ShekelError` and `ValueError` so the 20 existing `pytest.raises(ValueError)` seam assertions stay honest rather than being rewritten), and one app-level handler beside the 400/403/404/429/500 ones answers it: the existing "Setup Incomplete" card with its **Create Baseline Scenario** button for a page request, `204 No Content` for an HTMX request (the grid partials' own shipped contract, so a live DOM is never replaced by a setup card), and an ERROR `log_event` either way so a genuine caller bug is loud in the logs even though it is quiet on screen.  **The handler is what keeps the REPAIR reachable**: `POST /grid/create-baseline` is today linked from exactly one template rendered by exactly one route, so deleting that route's guard with nothing in its place would leave an owner in this state with a fully 500'd app and no way back.  Rejected: a `@require_baseline` decorator on ~30 routes (same screens, but opt-in -- a route added later escapes silently, and this file's own Section 8 has already paid for a fail-closed gate scoped by a hand-written list); and keeping the seven per-surface answers while guarding only the three crash doors (that is the rename N-112's row rules out, and it leaves both fabrications live) | X-v1 |
| **R-BX** (answered 2026-07-28, as recommended) | **`BalanceContext.scenario_id` becomes the RAISING accessor (`-> int`), so the nullable cannot escape the context.**  Five sites dereference `ctx.scenario.id` to SCOPE A QUERY rather than to read a balance -- the grid's transaction load, both calendar entries, the cash detail's anchor resolve, the investment anchor caption, the tax report's profile load -- and with the guards deleted each would raise `AttributeError` on `None`, which is a 500 of the WRONG TYPE that the handler cannot answer.  One accessor makes every dereference, seam read or query scope, fail the same named way at its first use.  `has_baseline` is DELETED with the guards that read it (X-t2 added it for exactly those callers, and after X-v2 it has none).  **What survives is the one reader that should**: `_liability.liability_owed_at_dates:185` keeps reading the nullable `ctx.scenario` directly, because a missing baseline there is the degenerate case of its own rule (no loan is resolvable, so every liability holds flat) and not an error -- the seam's documented single exception, now its ONLY one.  Rejected: making `scenario` itself non-nullable and raising in `BalanceContext.build` (it reads cleanest and it would undo plan step C8e, which split `LoanTerms` off precisely so escrow and rate editing work without a scenario they never needed; `_loan_terms_now` builds a context and must not raise) | X-v2 |
| **R-BY** (answered 2026-07-28, as recommended) | **TWO guards keep their own explicit handling, and the reason is written at each.**  (a) `loan_recurrence_sync.py:267` is a WRITER running mid-mutation: under the handler a raise would roll back the user's just-flushed loan-params write and render a setup card, losing the edit, where today it writes the contract-derived START bound and skips only the scenario-scoped END bound -- C8e's rule ("a loan's contract terms are not scenario-scoped") applied to a write.  (b) `liability_owed_at_dates` per R-BX.  Everything else -- the grid's page and its three partials, both calendar entries, both dashboard sections, all five investment sites, the cash detail, the tax report, and X-t2's three `/savings` guards -- is DELETED | X-v2 |
| **R-BZ** (answered 2026-07-28, developer confirmed under CLAUDE.md rule 5) | **X-t2's `/savings` no-baseline degradation is REVERSED, and this row is where that is findable.**  X-t2 ruled the honest region was "the today figures over an empty series and no Horizon"; the today figures are `compute_net_worth_today` reducing `current_balance or ZERO` over balances that are ALL `None`, so the page states a net worth, a total-assets and a total-liabilities figure for a user whose every balance it cannot answer.  Under R-BW the route answers with the repair card and there is no hero to fabricate.  **The developer confirmed the expected behaviour has changed**, which rule 5 requires before a test may move: the three tests in `TestNoBaselineDegradesEveryKindTheSameWay` are rewritten to assert the raise and the route's answer, and two grid tests that assert `204` on a NON-HTMX request are given the `HX-Request` header the browser actually sends.  Same standing as R-BS reversing R-BI: recorded as a reversal so the reader who looks it up finds the evidence, not a contradiction | X-v2 |
| **R-CA** (answered 2026-07-28, as recommended) | **N-113 is closed by DELETION, not by inventing a display vocabulary for a state no page can now reach.**  `current_balance or ZERO` and the investment dashboard's `current_anchor_balance` fallback (`_context.py:189` / `:259`, which presents the raw cache column as a *current balance* -- finding N-103's complaint one screen over) both go with the guards that reach them.  **Measured before ruling it**: `AccountProjection.current_balance is None` has exactly ONE cause today.  The other cause its own docstring names ("a cash account whose anchor is after the current period") is stale -- a future-anchored HYSA still carries every period in its map since the X-c2b2 cutover, verified by probe -- and the seam's only other `None` map is `current_anchor_period_id is None`, which the schema forbids (`accounts.current_anchor_period_id` is `NOT NULL`).  Rejected: rendering `--` in the hero, chips and legend (a new display vocabulary for an unreachable state, and it keeps a page whose every tile is blank); and leaving the `$0.00` with its explanatory test | X-v2 |
| **R-CB** (answered 2026-07-28, as recommended) | **The census instrument becomes a permanent GATE, because that is the only part of this step that cannot go stale.**  The route sweep enumerates `url_map`, so a route added in a year is graded without anyone remembering the rule exists -- the property the rejected decorator could not have.  It requires every GET route to answer a baseline-less owner without a 5xx, and the three named doors to answer with the card.  Per Section 7.3 it ships only after being shown to FIRE: the arms are run against the tree with the handler removed.  It does NOT prove the absence of a fabricated figure -- stated in its own docstring, because a gate that reads as proving more than it does is this arc's most-paid-for lesson | X-v1 |

### Applied (X-v's two adversarial reviews, 2026-07-29: the residue, under the developer's standing instruction)

**Both reviews earned their cost a TENTH time, and they found the same top defect independently: the
step's central claim was false.** "There are no caller pre-checks left" was true of the balance
SEAM and false of the application: the census instruments -- an AST pass that followed
`BalanceContext`, and a route sweep that graded only 5xx -- are both blind to a surface that
resolves the baseline DIRECTLY through `get_baseline_scenario`. Seventeen such sites remained, and
the correctness review reached the worst of them by walking the call graph.

| # | ruling | consumed by |
|---|---|---|
| **R-CC** (applied 2026-07-29) | **A financial STATEMENT never reports zeros for a ledger it cannot read**, and `scenario_resolver` gains the raising accessor that makes that structural.  `compute_balance_sheet` and `compute_income_statement` each resolved the nullable, saw `None`, and returned an EMPTY report -- which for the balance sheet meant assets `$0.00`, liabilities `$0.00`, equity `$0.00` **and `tie_out.in_balance = True`**: the application ASSERTING that a user's books balance over a ledger it could not read.  Verified by execution on the new fixture, and pinned green by two tests until this step.  It is finding N-113's fabrication one screen over, and R-CA's "closed by DELETION" was false while it stood.  `require_baseline_scenario` is now the resolver's raising form -- the same split `scenario_id` / `scenario_id_or_none` make one tier up, in the same direction, so the two tiers have ONE shape | X-v3 |
| **R-CD** (applied 2026-07-29) | **204 answers a POLL, never a BUTTON.**  The handler keyed on `HX-Request` alone, so a MUTATING htmx request got `204 No Content` -- measured on `POST /debt-strategy/calculate`: the user presses Calculate and nothing happens, silently and every time, where before X-v1 it at least 500'd.  The branch is now `HX-Request` **and a safe method**; a mutating request gets the card, which swaps into the results target and says why the action did nothing.  **And the 200 status keeps its place on a NEW argument**: the old one was circular (it appealed to the grid guard this step deleted for being one of the wrong answers).  The real reason is htmx -- it swaps only 2xx, so an "honest" 4xx/5xx would make both htmx branches render NOTHING, which is the silence this ruling exists to end.  The failure signal is the ERROR event, which is what an operator alerts on | X-v3 |
| **R-CE** (applied 2026-07-29) | **The exception carries the user it was RESOLVED for, and the event logs both ids.**  The handler's own docstring names "a caller resolving a context for the wrong user" as one of the two reasons it exists -- and it logged `current_user.id`, the REQUESTER, so the event was blind in exactly that case.  `BaselineMissingError` now takes `user_id`; the event carries `user_id` (who asked) beside `context_user_id` (who the raise was for), and they differ only when a caller has the wrong one.  Rejected: rendering a different card for the mismatch (a branch for a state that has never occurred, where a log line is what an operator actually needs) | X-v3 |
| **R-CF** (applied 2026-07-29) | **The sweep's coverage claim becomes a PINNED LIST, and it grades the fragment answer too.**  Its skip-list assertion (`all("<" in rule for rule in skipped)`) was TRUE BY CONSTRUCTION -- the branch that fills the list is the one that tests for `<` -- so it could never fail while its docstring promised "an uncovered route that grows a balance read would pass this suite" was impossible.  That is finding N-63's class committed INSIDE the gate that quotes it.  The 17 unreachable rules are pinned literally, so a new one turns the gate red; and the sweep now asserts no HTMX request receives the full-page card, which is the regression the 204 branch exists to prevent and which a 5xx-only arm could never see | X-v3 |

### Answered (developer ruling, 2026-07-30: X-w's four forks, all as recommended)

**The trace ran first and no code was written for it**, and it moved the step from "type a dict" to
"delete a container". The measurement that turned it: `_net_worth` took TWO shapes for ONE account
set on ONE render -- `compute_net_worth_today(list[AccountProjection])` beside
`compute_net_worth_series(list[dict])` -- so finding N-114's stored flag was a symptom of a second
per-account record existing at all.

| # | ruling | consumed by |
|---|---|---|
| **R-CG** (answered 2026-07-30) | **ONE per-account record per render, and the dense period map rides on it.**  `AccountProjection` gains `balances`; `net_worth_account_data.to_net_worth_account_data` and `_net_worth.build_account_net_worth_maps` are DELETED with the `{account_id, balances, is_liability}` container they built.  The stored liability flag is not fixed, it becomes unrepresentable: there is no second container to store one in.  **The projection's map now covers EVERY kind including loans**, which is what the old batch left out -- a loan tile reads no map, but the net-worth trend, the composition split and the liability band do, and that omission is precisely why a second container had to exist.  Priced before it was chosen: a loan's dense map on a WARM context costs **0.19-0.59 ms and ZERO SQL** (best of five, both databases) against the **20-95 ms** its resolution costs cold, which every caller already pays -- so the two narrow producers pay essentially nothing.  Proved safe before it was built: the hero equals the trend's today point equals the Horizon's index 0 on both databases (`$237,527.61` / `$233,096.49`), and per account the scalar equals the dense map's current-period entry for all eight accounts INCLUDING both loans.  A loan's `current_balance` is still the SCALAR and not a read of the map: they agree by the SEAM's construction (the current column clamps to the pass's `as_of`), not by this module's, so the equality is asserted rather than assumed.  **A measured side effect, and it is a whole finding's worth**: one `/savings` render built **17 per-account dense maps for 8 accounts**; it now builds **11** (SQL `276 -> 211` on `shekel`, `252 -> 195` on `shekel_f3_final`), which is half of N-72's redundancy half closed without waiting for X-i1.  Rejected: a typed `NetWorthAccountRow` with a derived `is_liability` property (fixes the flag and leaves two per-account records keyed on the same account, so the duplication of IDENTITY survives and the double `build_maps` waits for X-i1), and typing now / merging later (two commits, the second re-opening every file the first touched) | X-w1 |
| **R-CH** (answered 2026-07-30) | **The archived drawer's figure is NAMED for what it is.**  `_load_archived_accounts` returns a frozen `ArchivedAccount(account, last_anchor_balance)`.  The rename is the ruling: the key was `current_balance`, which is what :class:`AccountProjection` calls the seam-derived balance today, and this is `Account.current_anchor_balance` -- a different fact, and for an amortizing loan not a balance at all.  The vacuous `or Decimal("0.00")` dies with it: the column is `nullable=False` with a redundant CHECK (`account.py:54-57`, `:91`), so the reducer can only fire on a real `$0.00` and return `$0.00`, and it is the truthiness-on-money shape ruling R-CA deleted eight of.  **Finding N-103's question is NOT taken here** -- whether the line should be shown at all for an account whose balance is ledger-derived is X-e's, which already owns it and its three options.  Re-verified at this trace: zero archived loans on either database (both archived accounts are cash), and the ACTIVE Van Loan carries `current_anchor_balance = $0.00` against `$15,663.59` owed, which is the measurement N-103 rests on | X-w2 |
| **R-CI** (answered 2026-07-30) | **Every RECORD container crossing a module boundary on this path is a value object; the four that are not records stay dicts, with the reason written.**  The AST census found eight; N-114 named two.  Typed: the dense map (R-CG), the archived rows (R-CH), `compute_net_worth_today`, `compute_net_worth_series` -- built ONCE, which kills the `series["current_index"] = ...` MUTATION the orchestrator applied after the producer returned, the "never fully built anywhere" shape ruling R-BD deleted for `DebtSummary` -- `compute_property_equity`, and `_build_goal_datum`'s eleven keys.  **(Corrected 2026-07-30 by X-w's adversarial design review**: this sentence said "three" and then enumerated four, and it listed six typed containers where the code types EIGHT -- the region itself and its per-period `TrendPoint` are the two it omitted.  Both are the "a count in a docstring is a claim" class, in the ruling that exists to close it.)  **Deliberately EXCLUDED**: the render-context kwargs dict (it IS `render_template(**ctx)`), the two serializers' JSON payloads (a payload is a dict by nature), and `build_horizon`'s dict with its milestone dicts -- those are pinned by `TestHorizonSerialization`'s remove-a-key gate, which proves every published key is READ and is therefore a STRONGER contract than a dataclass; typing them would weaken the guard findings N-100 and N-104 bought | X-w3, X-w4 |
| **R-CJ** (answered 2026-07-30) | **A map that is TOTAL over its input is INDEXED, not defaulted** -- ruling R-CA's rule, applied one container over.  `_sum_composition_at_period` read the per-account category band as `category_by_account_id.get(account_id, "other")`, with a comment calling the default "defensive; the producer passes a total map".  Both sides are built over `core.accounts`, so the default is unreachable; what it would do if reached is bank a real account's money in the wrong chart band, in silence, on a page whose bands are asserted to reconcile to the hero.  The memo itself STAYS -- it classifies once per account rather than once per account per trend period.  **Its census of survivors was WRONG and is corrected here** (X-w's adversarial design review, 2026-07-30): it said one `.get` survives, in `_horizon._retirement_investment_bands`; **two** do, and the second is `_horizon._asset_bands`, which iterates `account_data` and is handed the map built from that same list -- exactly the total pair this ruling declares indexable.  It is a membership FILTER there (`if band not in bands: continue`) rather than a band substitute, so it is not a figure hazard, but the ruling applied its own rule inconsistently to one map on one render and said so wrongly.  The engine-fed one at `_retirement_investment_bands` genuinely survives: its accounts come from the /retirement engine's own load (a verified `is_active` retirement/investment subset), and the band test beside it is a separate guard against a mis-typed account, not this map's totality.  **Ruling R-CK extends this rule to the BALANCE maps in the same function** | X-w1, X-w6 |

### Answered (developer ruling, 2026-07-30: X-w's review residue -- two ruled against the standing rubric)

**Its two adversarial reviews earned their cost an ELEVENTH time, and both found the same two things
independently**: the new cockpit-hero guard cannot fire on the regression it names, and the arc's
flagship hero-vs-trend assertion carries a failure MESSAGE that raises. **Nine of the step's own
citations were wrong** -- the class this arc keeps paying for, committed while writing about it.
The correctness review also closed the blind spot the step left open: it materialised `fd8abc05`
with `git archive`, ran the harness against both trees, and then RENDERED all three templates on
both trees against both databases and diffed the HTML, which is the layer neither harness reaches.
Nothing moved.

**Two of these four the developer ruled by applying the standing rubric** ("is this DRY, SOLID,
fully normalized, robust, maintainable for a solo developer, future-proof, financially correct, and
how would you build it from scratch?") rather than by picking an option; the answers below are that
rubric applied, not a preference.

| # | ruling | consumed by |
|---|---|---|
| **R-CK** (answered 2026-07-30) | **A total map is INDEXED at every reader, and indexing is not a policy -- it is the absence of one.**  `_net_worth` ended the step asking "is this dense map total over this window?" THREE ways: `_projections._current_balance_from_map` indexes, `_sum_composition_at_period` wrote `ad.balances.get(period_id, ZERO)`, and `compute_sparklines` wrote `[balances[p.id] for p in window if p.id in balances]`.  **The rubric decides it**: one question answered three ways in one module is not DRY; two of the three fail SILENTLY, which is not robust; and the defaults are financially wrong in opposite directions.  `.get(..., ZERO)` banks a real account's balance at `$0.00` in a chart band, so the bands stop reconciling to the hero with nothing failing (finding N-113's fabrication, one surface over).  The membership filter is WORSE: :func:`app.routes.savings._serialize_sparklines` normalizes on series LENGTH (`x = (index / last) * _SPARK_VIEW_W`), so dropping one point moves EVERY remaining point on that card -- a wrong shape, silently, from a missing key.  Both are unreachable and that was verified rather than assumed: measured on both databases, every account's map covers all 60 periods and both windows (`build_trend_periods`' slice and `_compute_card_sparklines`' forward filter) are subsets of that domain.  Rejected: fixing only the composition one (leaves two spellings and R-CJ applied inconsistently inside one module), and recording a finding (rule 7 gives a finding its own step, but this is three lines in a function the step had already rewritten) | X-w6 |
| **R-CL** (answered 2026-07-30) | **`TrendPoint.period_index` is DELETED: it is finding N-100's defect in the step that deletes `goal_mode_id` for it.**  The review's AST census found ZERO production readers -- :func:`app.routes.savings._serialize_net_worth_chart` formats only `end_date`, the cockpit tests the list for truthiness, and no script names it; the only readers are tests and the manual harness.  This is X-s3's precedent exactly, where that step's own review found `DtiMetrics.gross_monthly_income` had no `app/` reader and the developer deleted a field the same step had just written.  The RECORD survives with one field, because the name is what says the series' x-axis is a DATE per period and both the serializer and the harness read it by that name; a bare `list[date]` would save a line and lose the sentence.  Rejected: keeping it against the chart keying on `period_index` one day -- that is a change to the payload the client reads, it belongs to X-j which owns the chart's contract, and "keep a field for a consumer that has not arrived" is what X-q2 deleted `compute_net_worth_horizon` for | X-w6 |
| **R-CM** (answered 2026-07-30) | **The one-record design STANDS and the INSTRUMENT is what changes.**  The three narrow producers now carry a dense `balances` map none of their consumers reads, and neither harness dumps it -- so a defect confined to that field on those paths is invisible to both, which is Section 8's "a census and a gate can be blind the same way, and then they confirm each other".  **The rubric splits the question**: one record per account is the DRY, normalized, maintainable answer and a conditional map re-creates the branch R-CG deleted, so the DESIGN is right; what is not robust is an instrument that cannot see a field.  From scratch you build one record AND make the regression harness see every field of it.  So `verify_savings_producers.py` dumps the dense map per projection, and the cost of the field itself is written at it.  Measured before it was accepted, and it refutes the objection: the narrow producers pay **ZERO extra SQL and ~1 ms**, because each loan's resolution is already memoized on the shared context (`compute_debt_summary` 58 SQL before and after; `compute_tracks_section` 82 and 82).  Rejected: documenting without widening the harness (leaves the blind spot the review named), and refusing the map to narrow callers (a flag or a second code path, the shape R-CG deleted) | X-w6 |
| **R-CN** (answered 2026-07-30) | **A type that crosses this package's boundary is re-exported, and a region's type lives with its own fields.**  The package's `__init__` states that rule and applies it to `DebtSummary`; the step then shipped `compute_goal_progress -> list[GoalProgress]` consumed by `dashboard_pulse_service`, unexported, and made the ONE type the route and both templates actually read (`_NetWorthRegion`) the only new value object that is underscore-private -- in `_orchestrator`, while its two field types live in `_net_worth`.  That placement is what forced two signatures to LOSE their type hints (`_serialize_net_worth_chart`, `_track_goal_datum`), against `.claude/rules/coding.md`.  `NetWorthRegion` moves beside `NetWorthToday` and `NetWorthSeries`, loses its underscore, and both it and `GoalProgress` are re-exported; the two hints come back.  Rejected: annotating through `if TYPE_CHECKING:` imports of private names, which is the private-module import the package rule exists to prevent, written in a form the W9910 checker cannot see | X-w6 |

### Answered (developer ruling, 2026-07-30: the two records X-w reported and did not take)

**The developer's question is what re-opened it**: "why didn't you take action on
`calculate_trajectory` and `calculate_savings_metrics`?" The answer was three reasons and only one
of them survived contact.

**Sound**: `savings_goal_service` is outside the savings-dashboard package, so typing its returns is
a change to a module with its own consumers and its own test surface, and `CLAUDE.md` rule 6 says
report rather than fix.
**Circular**: "ruling R-CI's enumeration does not cover them" -- that enumeration came from X-w's own
AST census, whose target list never included `savings_goal_service`. The scope was set and then
cited as the reason.
**Forbidden**: the 64 test assertions were quoted as cost, and Section 9 rule 7 says cost is never a
ground for deferral.
**The actual failure**: they were left owned by NOBODY -- reported in prose, absent from Section 6.
Rule 6 exists because 29 unowned rows rotted, four of them naming steps that had already shipped.

| # | ruling | consumed by |
|---|---|---|
| **R-CO** (answered 2026-07-30) | **Both records are typed at their producer, and the unreachable nullable X-w4 wrote goes with them.**  :func:`app.services.savings_goal_service.calculate_trajectory` has THREE return statements and every one returns a full four-key dict -- it can never return ``None`` -- and :func:`~app.services.savings_goal_service.calculate_savings_metrics` has two, both three-key.  So plan step X-w4's ``GoalProgress.trajectory: dict | None`` is a nullable that cannot be null, which is ruling R-CA's defect written by the step that quotes R-CA four commits earlier; and ``savings/dashboard.html``'s ``{% if gd.trajectory %}`` is a truthiness test on a dict that always carries four keys, so it can never be false.  A guard that cannot fail is what this arc deletes.  **The scope question the developer's rubric settles**: one production consumer each, both on this path, and the trajectory is NESTED INSIDE the record X-w4 typed -- so leaving it a dict makes ``GoalProgress.trajectory["pace"]`` a typed outer with a dict inner, the exact inconsistency ruling R-CI exists to remove, inside R-CI's own container.  Rejected: fixing only the nullable and the dead guard and leaving the two producers untyped (closes the defect and keeps the wart, and leaves the step's title untrue), and recording findings without fixing (the process failure this ruling is a correction OF) | X-aa |

### Answered (developer ruling, 2026-07-30: X-z's four forks, all as recommended)

**The trace ran first and no code was written for it.** It confirmed finding N-118's premise
(the two spellings agree on every account on both databases -- 10 on `shekel`, 9 on
`shekel_f3_final`, zero disagreements), and then found two things the row does not say: the
hazard's WORST surface is the Horizon, not the trend, and there is a THIRD spelling of the rule,
in Jinja, where no gate can see it.

**The Horizon is where a divergence would cost the most.** Its composition is assembled by three
band producers that must partition the account set exactly once, and they select with BOTH
spellings: `_horizon._liability_band` takes `ad.is_liability`, while `_horizon._asset_bands` and
`_retirement_investment_bands` key off `category_by_account_id`. An account the two classified
differently is counted TWICE with opposite signs -- net worth wrong by double its balance -- or
ZERO times. `test_net_worth_band_vocabulary.test_the_horizon_projects_every_band_it_publishes`
pins that the BANDS are disjoint and exhaustive; nothing pins that the two ACCOUNT-selection
rules agree.

**The third spelling**: `savings/_cockpit.html:139` and `:269` compare `category_name ==
'liability'` as a bare Jinja literal, driving the liability group's danger subtotal and the WHOLE
debt-summary footer. The X-t3 band gate reads that same file's `category_labels` and
`category_icons` dicts and does not see these, so a renamed key drops the debt footer with
nothing failing.

**And the classifier is asked 48 times for 8 accounts on one render** (measured, both databases):
`_display._group_accounts_by_category` re-classifies every account once per category label (5N)
while `category_key_by_account_id` has already built the same map one function away (N).

| # | ruling | consumed by |
|---|---|---|
| **R-CP** (answered 2026-07-30, as recommended) | **ONE classifier answers the CATEGORY, and both existing questions BUILD ON it** -- Section 8's "a DRY refactor of a PREDICATE can move money; prove two rules answer the same question before merging them, otherwise make one BUILD ON the other".  A new `account_category(account) -> AcctCategoryEnum \| None` is the only place `account_type.category_id` is compared against a cached id; `is_liability_account` becomes `account_category(a) is LIABILITY`, and `_display.account_category_key` becomes a lookup of that answer in the display vocabulary `_CATEGORY_KEYS`.  The equivalence the two spellings have today by READING then holds by CONSTRUCTION: `account_category_key(a) == LIABILITY_KEY` iff `is_liability_account(a)`, given only that `_CATEGORY_KEYS` is injective and `_OTHER_KEY` is not one of its values -- two one-line assertions in the band gate.  `_net_worth._LIABILITY_BAND` is DELETED with the literal it held: the band IS the category key (which is already the band gate's own thesis), so `_display.LIABILITY_KEY` is the one home and `_net_worth` / `_horizon` import it.  `_CATEGORY_ORDER` is written from `_CATEGORY_KEYS` entries in display order, so the module holds one spelling of each key while the ORDER stays an explicit display decision (deriving the order from `AcctCategoryEnum`'s declaration order was rejected: reordering a ref enum would silently reorder the cockpit's cards).  **The reducer keeps `ad.is_liability` for the SIGN and the key for the BAND, deliberately**: after this the two are provably one answer, and money math reading a DOMAIN predicate rather than a display key is the honest structure.  Rejected: merging by having the reducer take its sign from `band == LIABILITY_KEY` (makes the composition's arithmetic depend on a display vocabulary), and leaving the two spellings with a test asserting they agree on the seeded fixtures (a test over a fixture set is not a construction property -- finding N-69's lesson) | X-z1 |
| **R-CQ** (answered 2026-07-30, as recommended) | **The classifier's module is RENAMED to `app/services/account_category.py`, and it stays public at the service layer.**  `net_worth_account_data.py` has been named for a container that no longer exists since plan step X-w deleted `to_net_worth_account_data` (ruling R-CG) -- its own docstring says only the classifier remains, and the module's stated reason for living outside the cockpit package ("its importers are in two of them") describes two modules of ONE package.  The honest reason is the one that survives: the classification RULE is account metadata, not a cockpit display decision (finding N-118's own words), so the next consumer reaches a PUBLIC module instead of importing a private one -- Section 8's "a shared primitive reached through a private import is telling you the package boundary is wrong", which the W9910 checker now enforces.  Rejected: keeping the name (every future reader must read the docstring to learn the name is historical), moving it into `savings_dashboard_service/_category.py` (package-private, and the next non-cockpit consumer must either import a private module or copy the rule), and folding it into `account_projection` beside `classify_account` (that module deliberately imports no `ref_cache` and takes it by PARAMETER to stay cycle-free; a category classifier needs it, so the move would break the stated discipline of the module it joined).  **Two citations in the read-only archive go stale and are left stale**, per rule 5: an archived record was true on its write date | X-z1 |
| **R-CR** (answered 2026-07-30, as recommended) | **ONE category map per render, and the Jinja spelling of the liability key is GATED.**  `compute_dashboard_data` builds `category_key_by_account_id` ONCE and threads it to both the net-worth section and the grid section; `_group_accounts_by_category` takes the map and buckets in one pass, so the classifier is asked N times instead of 6N (measured `48 -> 8` for 8 accounts on both databases).  The map is INDEXED, not defaulted, which is ruling R-CJ's rule at a third reader: it is built from the same `account_data`, so a missing key is a producer defect and says so.  Separately, `test_net_worth_band_vocabulary.py` gains an arm over `savings/_cockpit.html`'s bare `category_name == '<key>'` comparisons -- every such literal must be a composition band, and `LIABILITY_KEY` must be among them -- with its negative control planted in the REAL template rather than a synthetic twin, per Section 8's "a gate's pattern must be exercised against the artifact it grades".  Rejected: handing the template a `LIABILITY_KEY` context variable (the file already reads the producer's keys as microcopy dict keys, and a sixth vocabulary home is what the gate exists to avoid), and leaving the redundancy to X-i1 (it is a consequence of unifying the classifier, not a shared per-pass loader -- X-i1's subject is a memo on `BalanceContext`, and this map is not on it) | X-z2, X-z3 |
| **R-CS** (answered 2026-07-30, as recommended) | **The three coverage units are each quantized ONCE, from the RAW ratio -- and the two inputs that cannot be `None` stop being nullable.**  `calculate_savings_metrics` rounds months to `0.1` FIRST and derives `paychecks_covered` and `years_covered` from that rounded value.  Measured on `shekel_f3_final`: `$4,076.92` over `$5,667.63` is `0.719334` raw months, rendered `0.7 months / 1.5 paychecks`, where converting the raw ratio gives `1.6`.  A sweep over **40,817** (savings, expenses) shapes -- savings `$0`-`$60,000` in `$25` steps against expenses `$1,000`-`$9,000` in `$500` steps -- differs on `paychecks_covered` in **53.5%** and on `years_covered` in **4.2%**, worst gap **0.2 paychecks** (`$250` against `$1,000`/mo renders `0.3 months / 0.7 paychecks` where the raw ratio gives `0.5`, a 40% error on that figure).  The rule is this codebase's own, stated in the two functions beside it: `resolve_goal_target` ("Intermediate results are NOT quantized -- only the final result is rounded ... to avoid penny-level rounding drift") and `amount_to_monthly` ("The result is NOT quantized -- callers are responsible for rounding at their own aggregation boundary").  "How many pay periods would my savings cover" is `savings / (monthly expenses x 12/26)`; converting the ROUNDED months answers a different question -- how many pay periods the DISPLAYED figure corresponds to -- which is a fact about the display, not about the money.  **It MOVES exactly one figure on real data**: `paychecks_covered` `1.5 -> 1.6` on `shekel_f3_final`, nothing on `shekel`.  **And no existing test can tell the two rules apart**: all five `TestCalculateSavingsMetrics` cases use exactly-divisible ratios (`12000/2000`, `24000/3000`, `36000/1000`, `100000/0.01`, `0/2000`), so every current assertion is byte-identical under either -- Section 7.4's fixture-matrix rule, and the fix adds the shape the feature exists for.  Beside it, both parameters stop being `Decimal \| None`: the ONE production caller passes `_sum_liquid_balances(...)` and `_compute_avg_monthly_expenses(...)`, which return a `Decimal` on every path, and NO test anywhere passes `savings_balance=None` -- a branch with zero exercisers, which is ruling R-CA's defect one function over from where X-aa just closed it.  The four `Decimal(str(x))` coercions go with them: they defend against types the signature forbids, and dropping them makes a float caller raise rather than silently succeed.  The `<= 0` guard STAYS -- no expenses is a real state and three zeros is a real answer.  Rejected: keeping the double-rounding for mutual convertibility at the displayed grain (buys a display property at the cost of a figure that is wrong about the user's money, and double-rounds where the two neighbouring functions forbid it), and deleting only `savings_balance`'s nullable (leaves half of one shape) | X-z4, X-z5 |

### Answered (developer ruling, 2026-07-30: X-z's two adversarial reviews, four forks, all as recommended)

**Both reviews found the same three defects independently, and the sharpest is that the step's own
central claim is FALSE.** `account_category`'s docstring says it is "the ONLY place in the
application where an `account_type.category_id` is compared against a cached reference-table id".
It is not, and the survivor that matters is
`ledger_account_service.ledger_class_id_for_category:156` -- the SAME asset-vs-liability question,
on the WRITE path, deciding which ledger class a real account's paired posting account carries.
The docstring that would have told the next reader to look tells them not to.

**The second: a finding cited in shipped code that was never filed.** `_orchestrator.py:596` says a
duplication is "recorded as finding N-121 with an owner"; `grep -rn N-121` returned exactly that one
line. Rule 6's own failure mode, written inside a sentence claiming compliance with rule 6.

**The third: the new Jinja gate arm cannot fire on the defects it names.** It asserts non-empty,
subset and membership -- never the COUNT, though its helper's docstring says the count is the point.
Both reviewers ran mutants against the real template and got the same survivors: deleting the
debt-footer guard leaves `['liability']`, and moving the danger ink to the Assets card leaves
`['asset', 'liability']`. Both pass. The committed control renamed BOTH sites to a non-band, which
only the subset arm catches.

**And the step's one quoted measurement points the wrong way.** `48 -> 8` is real, but
`account_category` scans up to four enum members with four `ref_cache` calls where the old predicate
made one: measured 2.3x-4.5x per call (`0.135 -> 0.307` us for an ASSET, `0.139 -> 0.624` us for an
INVESTMENT, 200k iterations each). `is_liability` is a non-cached property read ~480 times per
render, so the step saved ~`0.019 ms` and added ~`0.125 ms`. Both are noise against a 20-95 ms loan
resolution; the NUMBER is not, because it was quoted in two commit messages and a ruling.

| # | ruling | consumed by |
|---|---|---|
| **R-CT** (answered 2026-07-30, as recommended) | **The category rides on `AccountProjection`, and the per-account map X-z2 threaded is DELETED.**  X-z2 built `{account_id: category_key}` once and handed it to two section helpers -- which is a parallel per-account container keyed by account id, beside the record that already exists, and therefore the shape ruling R-CG deleted at plan step X-w with `{account_id, balances, is_liability}`.  Neither R-CP nor R-CR considered the alternative and both should have.  `AccountProjection` gains `category: AcctCategoryEnum \| None`, set ONCE in `_project_one_account`; `_display.category_key_by_account_id` goes, both new parameters go, `_sum_composition_at_period`'s third argument goes, and `is_liability` becomes `self.category is LIABILITY` -- a field comparison with NO `ref_cache` call at all.  **Total classifier calls per render ~488 -> 8**, which is the measurement R-CR should have taken: it counted the 40 calls it removed and not the ~480 it left.  It also removes the locals pressure that forced X-z2's `_compute_emergency_fund_section` extraction, and that extraction STAYS -- it is right on its own terms (the module's other two sections are helpers) and reverting it would be churn.  Rejected: keeping the threaded map (a second per-account container survives and the step's own measurement stays net-negative), and making `is_liability` a stored field too (re-introduces exactly what plan step X-t1 deleted at finding N-111 -- a STORED liability flag beside the rule that derives it; the property is what makes a second path underivable) | X-z7 |
| **R-CU** (answered 2026-07-30, as recommended) | **The ledger-class rule is finding N-122 with its OWN step, and the false claim is corrected NOW.**  `ledger_class_id_for_category` compares the same column against the same cached LIABILITY id and answers Liability-class / Asset-class; `_ledger_class_id_for_account` applies it to a real account, `create_ledger_account_for_account` pairs the posting account with it, and `account_validation:192-193` decides a type change flips it.  It agrees with `is_liability_account` by READING, which is finding N-118's condition surviving on the write path.  **It is not fixed inside a residue commit**: it is the POSTING path, and Section 8's "a DRY refactor of a PREDICATE can move money" is exactly the case -- so rule 7's answer applies, its own step with its own trace and its own sign-off, not a deferral.  The exclusivity claim is narrowed in the same commit that records it, in all three places it appears plus ruling R-CP.  Rejected: fixing it in X-z's residue (mixes a write-path predicate into a commit whose contract is "nothing moves", which is the mixing this arc splits commits to avoid), and correcting only the claim (leaves the second rule unowned -- the state 29 rotted rows bought rule 6) | X-z8, N-122 -> X-ab |
| **R-CV** (answered 2026-07-30, as recommended) | **Every citation, count and control the two reviews found, plus an O(1) category reverse lookup in `ref_cache`.**  The corrections: the exclusivity claim (three homes + R-CP); `_assemble_composition`'s new "partition by construction" paragraph, false for the third producer, which reads the ENGINE's account set through a `.get()` and reconciles it against the Python map; "5N calls -- 48 for 8 accounts" (5x8 is **40**; 48 is the render TOTAL); "four `Decimal(str(x))` coercions" (there were **three**, and the count is wrong in R-CS and the X-z4 bullet too); "all five `TestCalculateSavingsMetrics` cases" (the class had **six** and `100000/0.01` is in `TestNegativeAndBoundaryPaths`); the worked quotient `0.7193347` (it is `0.7193342`); and "a float caller raises `TypeError`" (a two-float caller raises `AttributeError`, after doing the division in binary float).  The controls: the gate arm asserts the COUNT with both reviewers' surviving mutants committed as controls; `years_covered` gets the gating case it never had (**a double-rounded revert survives all 7,668 tests today** -- X-z5 added the discriminating shape for `paychecks_covered` and not for the unit beside it); `_group_accounts_by_category`'s new `Raises: KeyError` gets the negative control its three sibling readers already have; and the unasserted precondition is asserted -- nothing checked that every `_CATEGORY_KEYS` value appears in `_CATEGORY_ORDER`, so a fifth category would 500 `/savings` on two `KeyError`s with every gate arm green.  **`ref_cache.acct_category_member(category_id)` is added** on the `transaction_type_is_income` / `ledger_class_is_debit_normal` precedent -- an id-keyed answer built once at `init()` -- so the classifier is FASTER than the code it replaced rather than 2.3-4.5x slower.  Kept because it is the right primitive whatever R-CT does: R-CT makes the call count small, and a linear scan with four cache lookups is still the wrong shape for a lookup.  Rejected: stating the per-call cost honestly and leaving the scan (the record would be accurate and the code would still be slower than what it replaced) | X-z8 |
| **R-CW** (answered 2026-07-30, as recommended) | **The raw-ratio rule STANDS, and the shape it can render is recorded rather than discovered later.**  Ruling R-CS was taken without this measurement, which is the reviewer's point and not a defence: against the developer's own `$5,667.63`/mo baseline, any liquid savings between **`$130.80` and `$283.38`** now renders `0.0 months / 0.1 paychecks / 0.0 years` on ONE line, where the old rule rendered three zeros.  Both figures are individually right -- `0.1` paychecks IS the better answer for `$200` of savings -- and the line still reads as self-contradictory.  It stands because the alternative is a figure that is **40% wrong about the money** (`$250` against `$1,000`/mo: `0.7` pay periods of runway rendered against a true `0.5`), and because near-zero savings is the case where a reader is least likely to be converting between the units.  **The window is written into the producer beside the measurement**, so the next reader meets it as a known consequence rather than as a bug report.  Rejected: reverting to the double-rounded derivation (restores the 40% error and the double-rounding the two neighbouring functions forbid in terms), and opening a UI finding on the footer's three-units-at-one-grain design (a real question, and not this arc's -- the balance plan is not the place to redesign a cockpit footer; the developer takes it up on the UI track if they want it) | X-z8 |

### Answered (developer ruling, 2026-07-31: X-x's seven forks -- four as recommended, and the fifth goes DEEPER)

**The trace ran first and no code was written for it.** It replaced the grep-shaped count in N-116
with an AST census that follows the DATA, then MEASURED the result on a prod-shape clone
(`shekel_f3_final`) with the pay calendar shifted three ways. Two of the seven forks were re-asked
because the first measurement made the first answer wrong.

**What the census found: N-116 counts ONE question and there are FIVE.** 96 branches in 49 `app/`
Python files (plus 8 in Jinja no Python census can see) resolving to about **50 distinct answers**.
They ask: *does the user have any periods at all* (Q1); *which period contains today* (Q2); *which
period contains date T* (Q3); *is there a period after this one* (Q4); *is the requested window
non-empty* (Q5). Q4 is a normal terminal state and Q5 is navigation -- neither is absence.

**The state that is DEFENDED is unreachable and the state that is REACHABLE corrupts money.** Q1 is
false for no owner: `register_user` writes a bootstrap period (`auth_service.py:692`),
`PayPeriodTruncateSchema` floors `keep_through_index` at 0 so index 0 survives every truncate, and
the only two `DELETE`s on the table (`pay_period_admin.py:330`, `:526`) both regenerate at least one
period inside the same transaction. Q2 is false three ways -- a lapsed schedule, a schedule opening
in the future, and an **interior hole**, which `_reject_overlapping_batch` permits because it
requires the new batch to start AFTER the latest end, not adjacent to it.

**Measured, same user and same instant, with only a 5-day hole differing** (the hole is
2026-07-30..08-03; an earlier draft of this section said four days and was corrected
2026-07-31 by X-x's design review)**:** `/grid` renders the
no-periods repair card while `/savings` reports net worth **`$233,096.49` -> `$236,325.04`**
(+`$3,228.55`) and liquid **`$4,076.92` -> `$8,591.92`** (+`$4,515.00`), its Checking tile
**`$406.92` -> `$2,932.41`**, its trend **58 points -> 0 points with `current_index = 0`**, and
`/dashboard` a third set of figures. Every wrong figure is `Account.current_anchor_balance`, the
derived CACHE -- and `$2,932.41` is the exact figure Section 5's own table names as the
pre-X-c2b2 scalar's FABRICATION, back on screen through a different door. `/debt-strategy` is
UNMOVED at `$177,277.97` because it reads the seam. **The hole is permanent**: the rolling top-up
counts `end_date >= today`, sees 52, and never fires (measured over four consecutive `/grid` loads).

**The seam is not at fault ~~and every defect above is an improvisation ABOVE it~~ -- FALSIFIED
2026-07-31 by X-x's design review, and corrected here rather than removed because the sentence
is what would stop the next reader looking.** `_cash_fold._PeriodSpans.containing` places a day
in a hole in NO column deliberately, and that is not inert: `_cash_sums` and `_assertion_sums`
then drop the fact while `_period_balances` samples by DATE and keeps it, so ruling R-K's
reconciliation identity fails by exactly that amount (`-$140.63` measured) and the grid's
"Timing & true-ups" row renders `$0.00`. Finding **N-128**, owner **X-l** (ruling R-DF). The
wrong FIGURES this trace measured are all above the seam; the broken IDENTITY is in it.

| # | ruling | consumed by |
|---|---|---|
| **R-CX** (answered 2026-07-31, as recommended) | **X-x is RE-SCOPED to what plan step X-l cannot subsume, and the degraded-vocabulary question is SEQUENCED into X-l rather than answered here.**  X-l makes the calendar total, which makes Q2/Q3/Q4 total and roughly 60 of the 96 branches UNREACHABLE -- so X-x as written would carefully choose degraded answers for a state X-l then deletes, which is rule 13's gold-plating with a census attached.  What X-l cannot subsume is the ~12 branches that publish a FABRICATED figure: every one reads a dense map keyed on `period.id`, and X-l's own fork (a) rules a derived period almost certainly carries no `id`, so those sites need an answer whatever X-l does.  X-x therefore does three things: collapse the five spellings of Q1 into one predicate, DELETE the fabrications, and split the four states apart.  Rejected: running X-l first (most-root, but it carries three unruled design questions of its own and the fabricated net worth stays live meanwhile), and X-x as written (a large share of the decision is discarded when X-l lands) | X-x1..X-x5 |
| **R-CY** (answered 2026-07-31, as recommended) | **The no-current-period answer is plan step X-v's rule EXACTLY, and N-116's premise that it "may well stay a degraded render" is FALSE.**  N-116 reasoned that no pay periods is a state a legitimately-new user is IN, so unlike the missing baseline it has no one-click repair.  The trace measured otherwise: every reachable form of it -- bootstrap expired, schedule lapsed, interior hole -- is repaired at `/pay-periods/generate`, the link `no_periods.html` already carries.  So `pay_period_service.require_current_period` raises `PayCalendarGapError` on the `require_baseline_scenario` / `BaselineMissingError` pattern, name for name, and ONE application-level handler answers: the repair page for a page, `204` for a safe-method HTMX fragment, an ERROR event either way.  Surfaces that never ask (`/templates`, `/settings`, `/analytics`, `/debt-strategy`) are untouched, exactly as the baseline rule leaves them.  Caller pre-checks DELETE rather than get rewritten.  Rejected: fabrications raise while empty regions degrade (two rules to keep in step where the app needs one, and the split is the state X-v deleted), and one degraded-render helper with no raising (keeps a page reporting a net worth it did not compute, which is the measurement) | X-x1, X-x2 |
| **R-CZ** (answered 2026-07-31, as recommended) | **A requested WINDOW that is empty is navigation, not absence, and stops answering with the absence card.**  `_resolve_grid_context` renders `no_periods.html` on `not periods` where `periods` is `get_periods_in_range(current.period_index + offset, n)` -- so `/grid?offset=9999` tells a user with 61 pay periods that they have none, and `offset` comes straight off the query string with no clamp.  It is one of the four states the census proves are different (absence, navigation, terminal, lookup failure) and the only one whose answer is currently borrowed from another.  Rejected: leaving it (the card's own copy already conflates two states -- "no pay periods have been generated yet, OR there is no period covering today's date" -- and this would make it three) | X-x4 |
| **R-DA** (answered 2026-07-31, as recommended) | **`onboarding.has_periods` means "a period covers today", the same question every other surface asks.**  It is `EXISTS(PayPeriod WHERE user_id = uid)` (`app/__init__.py:273`), rendered on EVERY page by `base.html`, and registration's own bootstrap row satisfies it -- so **a freshly registered user sees "Generate pay periods" struck through as ALREADY DONE on their first page load** (measured), and twenty days later `/grid` renders the no-periods card while the same HTML document's checklist still says the step is complete.  Two answers to one question in one document.  Single-sourcing it on X-x1's predicate makes the step outstanding until a real schedule exists AND makes it reappear if one ever lapses, which is exactly when the user needs it.  Rejected: excluding only the bootstrap row (fixes the sign-up display, leaves the step ticked forever and blind to a lapse), and deleting the step (removes the only prompt before the user opens the grid) | X-x3 |
| **R-DB** (answered 2026-07-31, **NOT as recommended -- it goes DEEPER**) | **Registration STOPS creating a bootstrap pay period.**  The recommendation was to route a first-time `/pay-periods/generate` through the existing `reset_pay_periods`, which reuses a gated path and creates no new write semantics.  The developer ruled the root instead: the bootstrap period is what makes the primary onboarding flow wrong, and working around it leaves an arbitrary period 0 in every user's history forever.  **What it costs, measured at the service tier**: on a form that says "Enter your next (or first) payday", `today+1` / `+5` / `+13` are REFUSED outright by `_reject_overlapping_batch`, `today` and `today+14` are clean accepts, and `today+20` / `+27` are accepted leaving a permanent 6-day and 13-day hole.  **The "13 of 14 refused" generalisation this ruling was taken on was WRONG and is corrected here** (X-x's design review, 2026-07-31): `generate_pay_periods` removes already-existing starts before the overlap guard sees them, so entering `today` works.  The ruling stands on the surviving fact -- thirteen consecutive paydays refused, and a permanent hole for every choice past `today+14`.  **What it reaches**: `accounts.current_anchor_period_id` is `NOT NULL` (migration `cfb15e782f86`) and that FK is the entire reason the bootstrap exists, so this step must decide between deferring the default Checking account until a schedule exists and relaxing the anchor -- which touches the invariants plan step X-e owns.  That is why it is a step with its own trace and not a line | N-123 -> X-ad |
| **R-DC** (answered 2026-07-31, as recommended) | **A mid-life schedule change FILLS the hole it would leave.**  `regenerate_pay_periods` keeps every started or locked period and rebuilds the tail from a new start date, so a user changing paydays can open a real hole -- and `reset` is unavailable to them, because it refuses once any transaction has settled.  The regenerate path covers the hole at the retained cadence before the rebuilt tail begins, so every day belongs to exactly one budget period by construction and the `period_index == calendar-order` invariant holds.  Rejected: stretching the last retained period's `end_date` to meet the new start (fewer rows, but it lengthens a period that may already hold settled money and posted ledger entries, which moves the fold's column boundaries and therefore FIGURES), and allowing the hole for X-l to answer (leaves the writer producing the state the reader has to survive, which is the wrong end) | N-123 -> X-ad |
| **R-DD** (answered 2026-07-31, as recommended) | **Both write-path fixes are their OWN step, sequenced immediately after X-x.**  R-DB and R-DC change what the app WRITES to the pay calendar -- creating periods, re-anchoring accounts -- while X-x reads preconditions and deletes fabricated figures.  Section 8's "a DRY refactor of a PREDICATE can move money" applies double to a writer, and this arc split X-c2b, X-g2 and X-g3 to keep a plumbing slip from reading as a fold slip.  So X-x ships the read-side correctness fix on its own PR and does not ride with a calendar writer.  Rejected: folding into X-x as separate commits (one PR, mixed contracts), and sequencing the writer FIRST (stops new holes earlier, but ships a calendar writer without the read-side guards X-x installs to grade it) | N-123, N-124 -> X-ad |

### Answered (developer ruling, 2026-07-31: X-x's two adversarial reviews -- one ruling is WITHDRAWN and the step is HELD)

**The reviews earned their cost a THIRTEENTH time, and this is the first time in this arc that a
review has overturned a ruling the developer had already made.** Two independent reviewers (design
and code) read the frozen tree. Both found the raise that leaked into the balance seam and both
found a public function with no callers. Each found what the other missed. **Three of this step's
own claims were falsified by measurement, and one of them was ruling R-CY's central
justification.**

**What the design review broke, in order of what it changed:**

* **R-CY's repair premise is FALSE for the interior hole.** The ruling rests on "every reachable
  form of it -- bootstrap expired, schedule lapsed, interior hole -- is repaired at
  `/pay-periods/generate`". Measured on the gapped clone: `_reject_overlapping_batch` bounds a new
  batch on `max(end_date)` over ALL periods, so with a schedule running to 2028 every date inside
  the hole is REFUSED and a date past the far end is accepted and creates NOTHING (`0 created`,
  flashing "Generated 0 pay periods"). X-x therefore converts the hole user from *wrong numbers on
  four pages* into *every page refuses and the button on the refusal page does not work*. **The
  step's own gate cannot catch this**: it asserts the card NAMES a repair, never that the repair
  repairs.
* **A hole breaks ruling R-K's reconciliation identity, INSIDE the seam.** `_cash_sums` and
  `_assertion_sums` drop a fact landing on a day in a hole (`spans.containing` answers `None`)
  while `_period_balances` samples by DATE and keeps it, so `balance_delta == net +
  reconciliation` fails by exactly that amount and "Timing & true-ups" renders `$0.00`. Measured
  `-$140.63` unexplained on the gapped clone. **This falsifies R-CX's "the seam is not at fault --
  every defect above is an improvisation ABOVE the seam"**, which was a load-bearing sentence of
  the trace.
* **Two of the step's measurements are wrong.** The hole is **5 days, not 4** (period 8 ends
  07-29, period 9 starts 08-04). And `today+0` is a CLEAN ACCEPT -- `generate_pay_periods` removes
  already-existing starts before `_reject_overlapping_batch` sees them, so the bootstrap's own
  start passes -- which makes "13 of 14 paydays are refused" wrong as a generalisation. The correct
  statement is **`today+1` through `today+13` are refused; `today` and `today+14` are clean; later
  leaves a hole**. That number was the measured justification quoted for R-DB.
* **The unreachability proof has a broken leg.** R-CX says both `DELETE`s "regenerate at least one
  period inside the same transaction". `truncate_pay_periods` does NOT regenerate when called from
  its own route; what holds the invariant is the Marshmallow floor plus the unstated fact that
  index 0 always exists. The floor lives in a schema, and the service accepts a negative
  `keep_through_index` from any caller.
* **"This commit provably changes NOTHING on any calendar" is false twice** -- the grid's card
  changed (`grid/no_periods.html` -> `errors/no_pay_calendar.html`, different copy), and a
  NON-HTMX GET of the two fragments went `204` -> a 200 page. The step's own test edit (adding
  `HX-Request` headers) is what kept the old assertion true, which is a changed answer described as
  no change.
* **R-DC is not implementable as stated** -- filling at the retained cadence does not close
  arithmetically without an irregular stub period, and for a lapsed schedule it re-creates N-124
  verbatim by running `populate_periods_from_active_templates` over historical fillers.

**What the code review broke:** roughly **80 tests stopped exercising what they name**, because a
page that now raises grades the repair card instead. Five XSS parametrizations pass off the flash
toast in `base.html`; three cross-user isolation controls pass off the nav link; nineteen CSP
scans scan the card. The fingerprint was in the step's own diff -- a `monkeypatch` parameter added
to a signature and never used.

**And a Section 7.6 gap neither review could close: the AST census is not in the tree**, so "96
branches in 49 files" and "about 50 distinct answers" are uncitable by any future reader.

| # | ruling | consumed by |
|---|---|---|
| **R-DE** (answered 2026-07-31, **NOT as recommended -- it holds the step**) | **X-ad ships BEFORE X-x, and X-x is HELD until it does.**  The recommendation was to fix `_reject_overlapping_batch`'s bound inside X-ad and let the card's existing CTA become honest; the developer ruled the ORDERING instead, which is stronger and needs no argument about what the card should say.  **The ground is that a refusal whose repair does not work is worse than the defect it replaces**: the hole user could at least read a wrong net worth before, and after X-x they can read nothing and fix nothing.  So the calendar WRITER is made correct first -- no bootstrap period (R-DB), an overlap bound that permits a batch which fills a hole rather than one that merely starts after the last end, and R-DC's gap fill re-decided per R-DF -- and only then do the readers refuse into a calendar the user can actually repair.  **This inverts the order R-DD set on 2026-07-31** ("X-ad sequenced immediately after X-x"), and R-DD's own reason survives the inversion: the two steps still do not ride together, and each still gets its own PR.  Rejected: a handler that diagnoses which of the three forms the user is in and links a different repair for each (a three-way diagnosis in a presentation layer, and it still routes the hole user through a multi-field form with a discard confirmation), and shipping X-x as-is (the measured regression above) | X-ad, then X-x |
| **R-DF** (answered 2026-07-31, as recommended) | **The hole's reconciliation defect is finding N-128 with plan step X-l as its owner, and R-CX's false sentence is corrected in the same commit.**  What the fold should do with a day belonging to NO pay period is X-l's question -- that step already owns "past the materialized rows every consumer improvises" and is the one redesigning the calendar as a total function -- while X-ad's writer fix is what stops NEW holes reaching it.  **The correction is not optional and is the point of the ruling**: R-CX told the next reader that the seam was sound and every defect lay above it, which is exactly the sentence that would stop them looking here.  Rejected: widening X-x to refuse ANY hole (it would refuse pages for a user whose hole is years away and irrelevant to what they are reading -- a far larger behaviour change than R-CY authorised), and giving `_PeriodSpans` a rule for uncovered days now (most-root, but it edits the cash clock ruling R-L settled deliberately, it moves figures, and it needs its own step and its own oracle) | N-128 -> X-l |
| **R-DH** (answered 2026-07-31, **NOT as recommended -- the developer pushed the design past the recommendation**; AMENDED the same day for the OPENING, and step S1-c DEFERRED.  **The AMENDMENT was REOPENED by the adversarial review and then REVERTED 2026-07-31 -- ruling on N-133/F1: "Revert + date the opening now".  The rule that ships has NO exception, and the opening now carries a stored user-supplied DATE (step 2's opening half), which is what answers the case the exception reached for**) | **An assertion is the CLOSING BALANCE for its civil day, and the day is the user's, not UTC.**  Opened by a live production defect: an ordinary bookkeeping session (read the bank, anchor, tick off what cleared) rendered the grid's projected end balance at **-$4,021.37** against a hand-computed **-$19.95**, because three rows recorded in the NINE SECONDS after the anchor (`-$4,001.42`) were subtracted from a bank balance that already contained them.  The recommendation was to adopt the day partition as the rule; the developer asked whether it is what a from-scratch design would do, and it is NOT -- it is the best available GUESS while nothing records when money moved, so the ruling promotes plan step **X-f** + finding **X5** from "after X-d" to the actual fix and demotes the day partition to the seam that carries it.  Six parts: **(a)** the day partition, assertion closes its day (measured against three alternatives -- gross plug `$40,554.34 -> $14,286.82`, net `-$6,998.90 -> -$940.06`, and it is the only rule under which the walk lands on the balance the bank shows).  **Those figures score the UN-AMENDED rule, which after the N-133/F1 revert is the rule that SHIPS: gross `$40,554.34 -> $15,367.94`, net `-$6,998.90 -> -$940.06`, worst `$4,161.47 -> $1,853.92` over 53 true-ups, verified against a pristine production clone.  The gross target this row first quoted, `$14,286.82`, was never reachable by any variant.  The amendment would have booked `-$2,997.48` net; both variants land the walk on `$1,307.66` and both give the current period `-$19.95`, so its cost was confined to March history and period 0's remainder (`$2,057.42`)**; **(b)** the civil day is `America/New_York`, storage unchanged, shipped in the SAME commit as (a) (22 of 139 settled rows land on a different day under UTC, 5 in a different pay period, and 2 Eastern evenings had one session split across two UTC days -- the shape that would defeat (a)); **(c)** envelope entries + anchor stays the process, made order-independent, with the invariant "recording a purchase and truing up by the same amount does not move the projected end balance" as a test; **(d)** `TransactionEntry.is_cleared` becomes DERIVED and its manual toggle is DELETED (a stored flag written as a side effect of the anchor save, with a manual override whose own docstring says it exists because the auto-rule is wrong); **(e)** a date means the day money HIT THE ACCOUNT, not the day of purchase; **(f)** "Timing & true-ups" splits into *Period timing* (a diagnostic that should read `$0.00`) and *Book vs bank* (the untracked spend), and the anchor form previews its own difference before saving.  Rejected: the shipped instant partition, settles winning same-day ties (worse than either), and keeping `is_cleared` with a better auto-rule (a denormalized copy of a derivable fact -- the `Account.current_anchor_*` disease X-e is already removing).  Full trace, measurements and build in **`anchor_settle_partition.md`**   **AMENDED 2026-08-01 at plan step S1-c, on parts (c), (d) and (f) (`anchor_settle_partition.md` Section 12).**  Building (d) as Section 10.6 recommended surfaced the defect underneath the whole fork: `transaction_entries.entry_date` was carrying TWO facts, so Section 10.3's "no date rule can answer both cases" was one field being asked two questions.  The column splits (ruling R-M as amended), and **(d) is RESTATED**: reconciliation is still DERIVED and the flag and its toggle are still deleted, but it derives from an OBSERVED `settled_on` rather than from a civil-day guess -- **a NULL is "not seen on a statement" and is NOT reconciled**, so the engine never guesses a posting day.  That satisfies Section 11.1's surviving premise (whether the bank posted a purchase is NOT derivable) which (d) as written could not.  **(c) is fully SATISFIED rather than half-broken**: with an observed date both invariants hold, in both orders, and the anchor-then-record order -- which today's shipped bulk clear gets wrong by the purchase amount on 14 of the developer's 53 same-day entries -- becomes correct.  **(f) ships as TWO rows with INDEPENDENT visibility flags** (a period carrying only true-ups must not render a permanently-`$0.00` timing row); measured on the clone, the split lands exactly on the hand-computed `-$427.22` / `-$160.05` halves.  The `+$534.08` Section 10.6 accepted **does not ship**: with NULL outstanding, no figure moves at all.  Its second half (the anchor form previewing its own difference before saving) remains OPEN at step 4. | X-f (widened), S1-c |
| **R-DG** (answered 2026-07-31, as recommended) | **The whole residue is fixed and RE-REVIEWED before anything is committed; nothing ships piecemeal.**  Every confirmed finding from both reviews, the corrected measurements, the ~80 blinded tests, the seam raise, the callerless `covers()`, and the citations.  **Two of the fixes are structural rather than one-off**: the AST census script is COMMITTED, because Section 7.6 forbids an uncited claim and a count whose instrument was never committed is one; and the blinded-test class gets a GATE -- an autouse fixture failing any test in which `pay_calendar_gap` fires outside an explicit allowlist -- because a one-time sweep leaves the next step that converts a surface free to re-blind the same tests.  Rejected: committing the rulings and the month-end clock fix now and holding the rest (banks the merge-gate fix, but splits one arc's review across sessions and the clock fix is what makes X-x1 independently green, so it is not independent of the thing being held), and reverting X-x1/X-x2 to rebuild from the corrected premises (discards work whose measured core -- the `$3,228.55` fabrication -- both reviewers confirmed correct) | the X-x residue leaf |

### Answered (developer ruling, 2026-08-02: X-d's four forks, all as recommended)

The step's design questions, asked after the trace and after the two measurements that decide three
of them. **Both measurements are on the dev-runtime clone at migration `d7c1f4a9e603`, and both are
positive-controlled**, because a zero from a hand-written probe is worth nothing on its own:

* **The two walks already agree.** `account_posting_service._walk.walk_account_ledger` (the postings
  the writer reads back today) and `cash_ledger.walk_cash_ledger` (the SOURCE rows X-d moves it to)
  produce identical corrections -- same `observed_on`, `pay_period_id`, `is_opening` and
  before-balance -- over **75 corrections across 7 (account, scenario) pairs, 0 mismatched**. So the
  writer swap is figure-neutral by measurement rather than by argument.
* **The checked-projection assert would already HOLD**: the linked ledgers' per-`entry_date` nets
  equal `cash_ledger.dated_deltas(walk)` collapsed per day over **79 dated nets, 0 divergent**.
  **So cash needs no heal pass**, where E1a needed `_reconcile_lineage_transfer_entries` before the
  loan assert could hold on real data. Negating the sign (E1a's loan convention) fails all 7 pairs,
  which is the probe's positive control AND the first measurement of `dated_deltas`' own claim that
  the cash walk books onto the linked ledger UN-negated.

| # | ruling | consumed by |
|---|---|---|
| **R-DI** (answered 2026-08-02, as recommended) | **The residue arm is DELETED, and a posting the source rows cannot explain becomes a LOUD refusal instead of a silent absorption.**  X-d's walk reads SOURCE rows, so `account_posting_service._walk._residue_source_days` -- postings whose `transaction_id` / `transfer_id` were SET-NULLed by a hard delete -- has no counterpart and cannot come forward.  Ceding it loses no coverage, because the arm is a NO-OP by construction: its own docstring records that the reverse-before-delete discipline nets residue to zero per account AND per period, "so every group here sums to zero and is dropped".  What replaces it is the checked-projection assert, under which such a row is a per-date mismatch that refuses the write.  **The door is already closed at every hard-delete path, traced 2026-08-02**: `transfer_service.delete_transfer:702` reverses through `sync_transfer_postings(xfer, settled=False)` before `db.session.delete`; `routes/transactions/mutations.py:550` calls `reverse_postings_before_delete`; `credit_workflow.py:172` / `:237` and `entry_credit_workflow.py:163` do the same; and `recurrence_engine.py:327` deletes WITHOUT reversing yet cannot reach a posted row, because `_recurrence_common.partition_regeneration_rows:254` skips any row whose status `is_immutable` and `ref.statuses` has `is_settled` as a SUBSET of `is_immutable` (Paid, Received and Settled are all immutable), so a posted row is never in its delete set.  Measured on the dev-runtime clone: **0** unlinked non-correction entries and **0** transaction-linked entries resolving to a missing / soft-deleted / non-contributing row, positive-controlled by the same census returning **170** transaction-linked, **19** transfer-linked and **129** correction entries (129 == 8 `account_opening` + 109 `account_trueup` + 6 `loan_opening` + 6 `loan_trueup`).  **The cost, stated plainly:** a future orphan turns the next write touching that account into a `PostingError` rather than a quietly wrong balance.  That is E1a's own stance for loans (the N-11 class becomes an F1-class data item for a human), and it is what fail-loud ledger authority means on the cash side.  *Rejected:* keeping the arm by feeding residue into the new writer (a second event source inside the step whose whole point is ONE, and it absorbs contradictory data by design), and excluding residue from the assert's posted side (keeps the blindness AND adds a fence that reports clean over the one class it cannot see -- the shape Section 8 exists to catch) | X-d |
| **R-DJ** (answered 2026-08-02, as recommended) | **Two distinct civil-day TYPES, and `ReconciledThrough.covers` narrows to the event one.**  Finding **N-135**'s obligation, discharged as ruled at step 3: `CashAnchorFact.observed_on` becomes an `ObservedOn` and `CashSourceFact.settled_on` a `MovedOn`, both frozen single-field records ordered against their OWN kind only.  After it `x <= fact.observed_on` -- verbatim the line step 3 deleted from `account_posting_service._walk` -- is a `TypeError`, and so is `fact.settled_on <= fact.observed_on`, which is the case N-135 cited when it ruled TWO types rather than one.  **Step 3's worry that narrowing `covers` would cost its totality did not survive reading the callers.**  Section 14.2 rules the rule TOTAL "in both the argument and the boundary", and the fear was that most of its six call sites pass a day that is not a settled day.  Traced 2026-08-02, every one asks about the SAME kind of day: `cash_ledger/_walk.py:290` and `account_posting_service/_walk.py:480` pass a source fact's `settled_on`; `cash_ledger/_amounts.py:319` and `routes/entries.py:153` pass a `TransactionEntry.settled_on`, which that model's own docstring calls "the day the bank TOOK the money"; `account_posting_service/_sync.py:294` passes a `JournalEntry.entry_date`, which `app/models/journal_entry.py:171` documents as the civil day the same `paid_at` falls on (and which R-DK deletes anyway); and `balance_at/_asset_contributions.py:329` passes `period.start_date`, which that feed itself emits as the modelled contribution's event date (`[(period.start_date, amount)]`).  So `covers` stays total over the CONCEPT -- every event has a day it counts from -- and stops being total over bare `date`, which is the only thing it was ever loose about.  A raw day enters the vocabulary through exactly two named doors: `MovedOn(day)` for a known day and `MovedOn.recorded(day_or_none)` for a stored nullable one.  *Rejected:* wrapping the two fields but leaving `covers` on `date` (closes the field hole and leaves every call site able to pass anything), and deferring N-135 a second time (X-d's own entry rules it an obligation, and X-d HALVES the surface, so deferring means wrapping a settled surface twice) | X-d |
| **R-DK** (answered 2026-08-02, as recommended) | **The self-heal SKIP predicate is DELETED: every posted change runs the walk, the reconcile and the assert.**  `account_posting_service._sync.self_heal_anchor_corrections` skips `sync_account_anchor_postings` when the emitted delta rides on top of every assertion, so an assert placed inside that sync would not run on the most common write there is -- ticking a bill dated after the last balance reading.  The predicate is deleted rather than the assert re-homed, on three grounds.  **Its own docstring already concedes it is optional**: *"running it after every emission is always correct and only ever costs a walk; everything below is the proof that a particular walk would write nothing."*  **It is a cost guard that spells the money rule**, and this arc has paid for that shape once already -- finding N-133 / F4's silent timezone-sign dependency lived in this exact predicate for its whole life, and Section 14.3 states the lesson verbatim ("A cost guard that spells the money rule itself can come to disagree with it").  **And it is measurably cheap once R-DL lands**: on Checking (55 assertions, 139 settled rows) the skip costs `0.73 ms`, and the full X-d sync -- walk, reconcile AND assert -- measures **`11.13 ms` over 12 SQL statements**, against the pre-X-d walk + reconcile at **`70.87 ms` over 110**, which is what made the skip look load-bearing.  (The `8.6 ms` this row quoted when the ruling was taken was a SUM OF PARTS, not a measurement of the assembled path; it is corrected here to the measured figure, per Section 8's "recompute before quoting".)  Deleted with it: `_has_posted_anchor_correction` and the latent scenario-clone arm it exists for, whose whole job was to name the case the skip gets wrong.  *Rejected:* asserting only inside the anchor sync and recording the gap (a fence that reports clean over the write it is least able to see), and asserting additionally at every source posting while KEEPING the skip (the same total cost, plus the predicate) | X-d |
| **R-DM** (answered 2026-08-02, as recommended, **with the end state named as its own step so it is not forgotten**) | **The checked-projection assert grades a FINISHED operation, never a half-finished one -- and the commit boundary is the end state, scheduled as X-ai.**  X-d's assert compares the posted ledger against the account's SOURCE ROWS, and every delete path reverses a row's postings to zero while the row still exists and still reads SETTLED -- forced by the schema, not chosen: `journal_entries.transaction_id` / `transfer_id` are `ON DELETE SET NULL`, so the reversal must be written while the link is live.  Between that reversal and the removal the rows and the ledger deliberately disagree, and the self-heal riding at the posting-sync tail ran the assert inside that window: **29 of the 56 suite failures, every one a delete / revert / restore**.  A second surface says the same thing differently -- `sync_transfer_postings(xfer, settled=False)` on a still-settled row -- because `settled=` is a caller's OPINION about a row that knows its own status (finding **N-144**).  **What ships now:** each sync splits into a non-asserting reconcile CORE and the checked wrapper -- the split `loan_posting_service` already ships (`_reconcile_loan_payment` for its reverse-before-delete, `sync_loan_postings` for the checked path), so no boolean selects between them, they are two names; ONE `posting_service.retire_transaction(txn, *, hard)` chokepoint reverses, removes and re-derives, collapsing the four transaction-delete sites that each spelled steps 1 and 2 by hand and none of which had a step 3; and `delete_transfer` re-derives both endpoints at its own end, beside the loan resync already there.  `retire_transaction` lives in `posting_service` and NOT `transaction_service`, which is forced rather than chosen: `transaction_service` imports `entry_service`, so `credit_workflow` -- one of its four callers -- could not import from it without closing the cycle `status_seam.py` records.  **What does NOT ship, and is scheduled instead:** the commit-boundary hook.  It is the most principled placement -- the invariant is "the COMMITTED books equal the COMMITTED rows", and the commit is the only moment that is definitionally the end of an operation, so it is the only form that also covers a future write path with no posting-sync tail at all.  It is refused HERE because X-d is already a writer swap, a module deletion and a type conversion, and this arc's own rule is that mixing mechanisms makes a slip unattributable ("the mix that makes a plumbing slip read as a fold slip, which is why this arc split X-c2b, X-g2 and X-g3"); it also interacts with `apply_anchor_true_up`, which deliberately wraps its own `commit()` in a `try` catching `StaleDataError` and `IntegrityError`.  **It gets step X-ai so the end state is scheduled rather than remembered.**  *Rejected:* an explicit re-derive at each of the five retire sites (five places must remember an ordering rule, and a forgotten one leaves a stale anchor correction nothing detects until the next sync -- the unowned obligation R-AO rules against), and making the assert tolerant of the in-flight state (hiding a contradiction, which the developer's own standing criterion refuses) | X-d, then X-ai |
| **R-DL** (answered 2026-08-02, as recommended) | **The anchor reconcile resolves its two ledger accounts ONCE per account rather than once per correction, and the fix rides INSIDE X-d.**  Measured 2026-08-02 on Checking: `account_posting_service._anchors._account_anchor_correction_target` calls `posting_reads._ledger_account_for(fact.account_id)` and `ledger_account_service.get_or_create_anchor_equity_account(owner_id, fact.account_id)` INSIDE the per-correction loop, so 53 non-zero corrections issue **106 SELECTs** resolving the SAME two ledger accounts of the SAME account -- `64.5 ms` of a `66.3 ms` reconcile that writes nothing.  Every anchor true-up, every account create and the deploy-wide backfill pay it today, so it is a live inefficiency and not an X-d artifact.  It rides inside X-d because X-d rewrites that function anyway (its corrections change type from `AccountAnchorCorrection` to `cash_ledger.CashAnchorCorrection`) and because R-DK is unaffordable without it.  It moves no figure: the same two accounts are resolved, once instead of 53 times.  **Measured after the hoist, on the same account: the whole X-d sync is `11.13 ms` over 12 SQL statements**, against `70.87 ms` over 110 before it.  Its firing control asserts the SQL COUNT rather than the elapsed time, so a reintroduction fails a test rather than a stopwatch.  *Rejected:* shipping it as its own commit ahead of X-d (an extra PR cycle for a function X-d rewrites in the next one), and recording it as a finding and leaving it (it would force R-DK to be declined for a cost that is an N+1, not a property of the design) | X-d |

### Answered (developer ruling, 2026-08-02: N-145 is not a size problem, and the status fence goes STRUCTURAL)

**The developer refused all four options N-145 offered and ruled the root instead**: *"I want to make
the fences structurally unnecessary."* That is step 3's own ruling -- the fence must be structural
rather than a detector -- applied to `shekel-transaction-status-bypass` (**W9907**), and the trace
that followed found a live production defect the four options would all have shipped past.

**Everything below is MEASURED, and each measurement is positive-controlled:**

* **The W9907 allowlist has two entries because there are TWO implementations of one seam.**
  `status_seam.py:11-13` states it in its own words -- *"It is the transaction analog of
  `transfer_service._apply_status_change` (which keeps its own private seam for a transfer's two
  shadow rows)"*. The allowlist is not a policy; it is the shape of a duplication.
* **Direct `.status_id` attribute writes in the whole of `app/`: FIVE.** AST census, 2026-08-02:
  `status_seam.py:93` (the seam) and `transfer_service.py:411` (the parent), `:412` / `:413` (the two
  shadows) and `:919` (the restore drift repair). Nothing else in the application writes it.
* **Every legal TRANSFER transition is also a legal TRANSACTION transition.** Executed against
  `_build_transitions` on both maps: **0** transfer-legal moves are transaction-illegal, and the
  reverse control FIRES (Projected -> Credit / Received, and all of Received's and Credit's rows are
  transaction-only). So mirroring a verified transfer status onto its shadows through the transaction
  seam can never be refused while Transfer Invariant 4 holds.
* **W9907 sees `setattr` only in its literal-string form** (`status_bypass.py:49-51`), and `app/`
  carries **17** dynamic `setattr(obj, field, value)` loops it therefore cannot see. ONE of them
  touches a status-bearing row (`routes/transactions/mutations.py:339`), correct only because a human
  wrote an explicit `continue`.  **CORRECTED 2026-08-02 by this step's adversarial design review: an
  earlier draft named `routes/transfers/templates.py:341` as a second such site and BOTH halves of
  that were wrong.**  It writes a `TransferTemplate`, which carries no `status_id` column at all (only
  `Transaction` and `Transfer` do), and it is gated by a POSITIVE allowlist
  (`_TEMPLATE_UPDATE_FIELDS`, `:75-78`) that has never contained the field.  The other 16 loops are
  unaudited as to which rows they can reach, which is the point rather than a gap in the census: the
  checker cannot see any of them.
* **Bulk `query.update({"status_id": ...})` / `.values(status_id=...)` sites: ZERO.** This is the one
  measurement that could have killed the structural answer -- finding N-65 proved a bulk update is
  invisible to every session listener -- and it comes back empty, so an attribute-level write door
  has no known bypass.
* **Reads of `.status_id`: 79 across 24 files**, which is what makes a read-only attribute
  affordable.

| # | ruling | consumed by |
|---|---|---|
| **R-DN** (answered 2026-08-02, the developer ruling the ROOT over all four options) | **ONE status seam, and the transition context stops being a caller's opinion.**  `transfer_service._apply_status_change` is DELETED and `status_seam.apply_status_change` takes a `Transaction` **or** a `Transfer`, deriving the transition map from the row's own type.  Three things fall out that no line-count option touched.  (a) it is what lets `_STATUS_SEAM_MODULES` reach ONE entry -- the module that owns the column.  **CORRECTED 2026-08-02, before this shipped, by this step's adversarial design review: an earlier draft of this clause said the allowlist "drops from two entries to ONE" as a consequence of the merge, and that is FALSE.**  Merging the seams removes the two ATTRIBUTE writes, but `transfer_service` also writes a status through two CONSTRUCTORS (`:143` and `:298`) that W9907's born-Projected rule refuses, so the entry survives the merge.  Measured: with the module removed from the allowlist, `pylint app/` reports exactly those two W9907s and nothing else.  The shrink belongs to X-aj2, and X-aj1's own step entry states that correctly -- this clause did not.  Kept visible rather than rewritten, because a ruling was taken partly on it.  (b) `verify_transition(current, new, context="transaction")` takes a caller-supplied STRING that must match the row it is about; Section 8 rules that an argument a caller can get wrong is a defect, not a contract, and derived from the row it cannot be got wrong.  (c) The three rows `transfer_service` writes never had `db.session.expire(row, ["status"])` run on them although BOTH status-bearing models declare `status` as `lazy="joined"` (three ROWS, two models) and the seam owns that expire (`status_seam.py:60-63`) -- **latent, not live**: every route commits before rendering and `expire_on_commit` is at its default, so it holds by accident rather than by construction.  **`Transfer` carries no `paid_at` column** (verified: `models/transfer.py` has none; `_apply_status_change` never assigned one), so the unified seam branches on the row type for the timestamp half rather than probing with `hasattr`, which plan step X-aa's lesson forbids (Section 8, "`hasattr` on a dataclass is not a test") -- **an earlier draft of this row cited R-CQ, which is the classifier RENAME and carries no such lesson; the misattribution reached `app/` before it was caught**.  *Rejected, with reasons measured rather than argued:* `posting_service.retire_transfer` (the import cycle that FORCED `retire_transaction` into `posting_service` -- `credit_workflow` cannot import `transaction_service` -- does not exist for transfers, and the soft branch mutates both shadows' `is_deleted`, which `CLAUDE.md` Transfer Invariant 4 reserves to the transfer service); the data-driven mirror loop (`setattr` in its dynamic form is invisible to W9907, so it would blind the fence in the one module allowed to write the column, to buy ~45 lines); the flat sibling `_transfer_restore.py` (it needs the fence's allowlist WIDENED for a line count, which is the opposite of this ruling) | X-aj |
| **R-DO** (answered 2026-08-02, as recommended) | **An illegally-drifted shadow status is REFUSED, not silently repaired.**  `restore_transfer` today rewrites a drifted shadow's `status_id` to its parent's with no transition check (`transfer_service.py:911-919`); routed through the one seam it is verified, so a shadow that cannot LEGALLY reach its parent's status -- a Settled shadow under a Projected parent -- raises `ValidationError` instead of being quietly corrected.  **The same function already refuses the other two corruption shapes exactly this way**: a wrong shadow COUNT (`:839`) and a wrong expense/income type pairing (`:856`) both raise "data integrity issue requiring manual intervention".  Silently rewriting the third destroys the evidence of how it happened, and a settled shadow silently reverted to Projected would strand its postings -- fail-loud ledger authority one layer up from the ledger.  Unreachable by any current code path, because the five direct writes above are the only ones and FOUR of the five are the transfer service's own -- the fifth is the seam's own assignment.  **The "all five" of an earlier draft was wrong and is corrected here rather than quietly** | X-aj |
| **R-DP** (answered 2026-08-02, as recommended) | **W9907 is DELETED, not shrunk -- the write door becomes structural.**  A one-entry allowlist is better than a two-entry one and is still a detector; step 3's ruling is that a restatement of the rule must be a `TypeError`, not a lint finding.  So `status_id` becomes a read-only attribute over the column and the only way to move a status is an operation that CANNOT skip the transition check -- after which `txn.status_id = 3` raises, `setattr(txn, "status_id", 3)` raises, and so does every one of the **17 dynamic `setattr` loops the checker is structurally blind to**.  Then the checker is deleted the way the balance NAME fences were deleted at D3 and E1e once W9910 made them redundant, which is this arc's own precedent for retiring a fence rather than maintaining it forever.  **The write door's exact shape is traced at the step and ruled there, not here**: the candidates are a `hybrid_property` over a renamed column, a value type only `verify_transition` can produce, and a model-level operation that verifies inside itself -- and the third inverts the Routes -> Services -> Models dependency rule, which is a real objection and not a preference.  Ruling the SHAPE from here would be guessing, which is what Section 8 forbids | X-aj |
| **R-DQ** (answered 2026-08-02, as recommended) | **The two remaining allowlist-bearing fences become their own PHASE, scheduled rather than remembered.**  W9907 is the smallest of three -- five write sites -- and it is the one blocking X-d, so it goes first and alone.  `shekel-ledger-model-bypass`'s `_LEDGER_MODEL_ALLOWLIST` and the balance seam checker's roughly a dozen module sets and per-module export maps are the same shape and are NOT left in prose: they are finding **N-147**, owned by the new **Phase G**, which runs after E2 because E2 moves the very modules those allowlists name and doing the fences first would re-cut them.  **AMENDED 2026-08-03 on the developer's instruction (*"restructuring keeps getting pushed off ... eliminate every fence, checker, and allowlist I possibly can"*): Phase G runs INSIDE E2, not after it.**  The dependency reasoning was right and its conclusion was one step too weak -- the structural replacement for both allowlists IS a module move, so E2 and G1 cut the same boundary ONCE rather than E2 cutting it and G1 re-cutting it later.  Running G1 after E2 left the two largest allowlists (16 names and 7) alive for the whole remaining arc.  The same instruction added **G2** for W9901 / W9902 / W9904, which the 2026-08-03 inventory found had no structural replacement scheduled anywhere.  `shekel-private-module-import` (W9910) is the model and is untouched -- its own docstring is the specification: *"name-INDEPENDENT and fail-closed by construction: it consults no producer list and no allowlist, so there is nothing to keep complete and nothing to rot"*  | Phase G |
| **R-DR** (answered 2026-08-02, and the step is DECOMPOSED into three commits) | **`restore_transfer`'s four preconditions move to `_transfer_validation`, and X-aj1 ships as three commits so each mechanism can be rolled back on its own.**  Raised by this step's own adversarial design review, which found the largest structural decision in the step was ruled by nothing: R-DO rules the new REFUSAL, not the extraction, and the extraction's first docstring miscited R-DO as its authority.  **The measurement is what forced the ruling.**  An earlier draft credited the room X-d needs to the seam merge; by AST extents the merge is **-13** lines, `update_transfer` **+5**, and the extraction **-54** -- so `transfer_service.py` reaches 937 of 1000 mostly because of a decision nobody had taken.  It is ratified on its merits and not on the room: the four checks are preconditions on the rows a mutation operates on, which is `_transfer_validation`'s stated single responsibility; they need no fence change (they READ `allowed_transitions` and write no `status_id`, so W9907 is untouched -- verified); and gathering them is what exposed the defect underneath, which is that `restore_transfer` set `is_deleted = False` FIRST and hand-restored it on each of three failing branches.  Validating before mutating deletes all three hand-rollbacks instead of adding a fourth, and makes the class of miss structurally impossible.  **It is NOT the flat sibling R-DN rejected**: that option was a NEW module created to dodge the W9907 allowlist for a line count; this is precondition code moving into the module that already owns precondition code.  *The three commits, in the order MEASUREMENT forced -- this row got the order wrong twice before building it:* **1** the EXTRACTION (`1688f508`), which moves the three existing preconditions unchanged and deletes the three hand-rollbacks; **2** the seam merge and the row-derived workflow (`63514efc`); **3** R-DO's refusal and R-DS's restore half (`1e75d0ce`).  **The extraction must go first and that is not a preference**: with `restore_transfer` still inline, the merge alone takes the module to **1015** lines and fails the ceiling, so a merge-first commit is not independently green.  The first draft put the extraction last; the second put it second; only building it showed it has to be first.  Ruled by the developer over ratifying it as one commit, on the arc's own R-DM rule that mixing mechanisms makes a slip unattributable | X-aj1 |
| **R-DS** (answered 2026-08-02, opened by the correctness review and RULED rather than left implicit) | **A status repair takes the PAIR's instant, and never invents one.**  Routing the drift repair through the one seam brought the seam's `paid_at` maintenance with it, and the seam's per-row rule -- preserve an existing instant, else stamp `now()` -- is right for a lone transaction and WRONG for a transfer's pair.  **Measured on the clean tree before the fix**: a shadow drifted to Projected with a NULL instant was repaired to Paid and stamped with TODAY (`None -> 2026-08-03`), while its sibling carried the real settle instant all along.  Since plan step E1a that civil day IS the `entry_date` the re-posted entry is filed under, so the repair moved money -- **finding N-146's own class, on the restore path, introduced by N-146's fix.**  The rule now: `_apply_status_to_all_three` resolves ONE instant for the pair before either shadow is written, preferring an existing one over `now()`, and `restore_transfer` repairs through that same applier rather than shadow-by-shadow.  Re-measured after: the repair takes the sibling's `2026-03-20 12:00:00` and invents nothing.  **It also restores a property the merge had silently dropped** (the review's M-2): the deleted seam computed one `now()` and assigned it to BOTH shadows, so the pair could never diverge, and a per-row seam let it -- which matters because `posting_service._entry_date` reads the INCOME shadow specifically and its docstring rests on the two being equal.  *Rejected:* giving the seam a "do not touch `paid_at`" sentinel (it closes the API gap but leaves the pair free to diverge, and a settled shadow with no instant then dates its postings from the period start), and leaving the repair outside the seam (which is what R-DO's refusal exists to end) | X-aj1 |

### Answered (developer ruling, 2026-08-03: X-ai is the RESTRUCTURE -- one verb, one trigger, both ledgers)

| ruling | decision | step |
|---|---|---|
| **R-DU** (answered 2026-08-03, the developer ruling the ROOT over the three options put, and explicitly *"even if it isn't fast or easy"*) | **The posted ledger gets ONE VERB and ONE TRIGGER, on BOTH ledgers, and the row-level posting writer stops being the interface.**  The three options put were "mirror the loan shape", "commit-boundary hook", and "hybrid" -- and the fork was mis-framed as a choice, which is recorded here rather than quietly re-drawn. They are two LAYERS of one fix: the loan shape fixes OWNERSHIP, the commit boundary fixes TIMING, and each without the other leaves the defect the developer named ("I have had enough of fixing bugs with band-aid fixes").  **The root cause is not where the assert runs, it is what the grader OWNS.**  A posting is a PROJECTION of a fold over source facts, so the only coherent interface is *"account A in scenario S changed; re-derive it from its facts"* -- never *"this row settled"*.  `sync_loan_postings` already is that verb; cash is not, and cash's ledger has its ownership split across two writers (`posting_service` books the source legs, `account_posting_service` the correction legs) with **nobody owning the whole**, so when the grader finds a discrepancy there is no module whose job it was to prevent it.  A commit hook ALONE would have hidden that (at commit time the row-level syncs happen to have run) rather than fixed it, and the first write path that changes facts without calling the row-level writer would get a refused commit with nothing able to heal it.  **What the one verb makes structurally impossible rather than fenced** -- the developer's stated goal, *"make the fences structurally unnecessary"*: **N-144** dies (there is no `settled=` parameter, because you never tell the writer a row's status -- it reads the row), **N-157** dies (there is no ordering rule to state in a docstring 7 of 9 call sites never read, because a re-derive is safe at any instant), **N-159** dies (reverse-before-delete stops being a discipline: delete the row and re-derive), **N-153** answers itself, **N-160** dies (a half-ledger writer cannot exist when the verb is whole-account), and **N-155** dies in all four confirmed forms because reconcile scope equals assert scope by construction.  **The double-entry objection is real and is answered, not deferred**: a journal entry spans TWO accounts, so "one owner per account" appears to collide with "one balanced entry per event".  It does not, because the verb reconciles each of the account's SOURCE FACTS to target, and a transfer appears in both endpoints' fact streams -- both endpoints compute the identical target from the identical rows, so the second re-derive is an idempotent no-op.  That is the same overlap the loan and cash paths already survive today.  **Both ledgers end up on one shape, ruled explicitly**: the loan side is the model but is NOT already finished -- its repair loop calls the CHECKED cash wrapper per transfer (N-155 (d)), two of its writers reconcile without grading (N-160), and its own `settled=` argument is a computed opinion (`_sync.py:233`).  *Rejected:* the commit hook alone (hides the ownership defect behind a later checkpoint -- the band-aid shape), the loan shape alone (leaves the timing fence every door must remember, which Section 8 rules weaker than a predicate), and scoping the hook by `session.dirty` inspection (the suite's fixtures commit hand-built settled rows no writer produced, so it would grade states the ledger was never asked to project).  **What is NOT ruled and must be MEASURED first** (Section 8, and the developer's own standard): the per-write cost of a whole-account cash re-derive (139 settled rows + 55 assertions on the real Checking account), and how many suite commits a registry-scoped hook would grade.  If the re-derive proves slow the answer is an incremental re-derive ON a correct design, never a return to row-level writes | X-ai |

### Answered (developer ruling, 2026-08-03: X-ai becomes the FROM-SCRATCH model -- the event owns the entry)

**The developer ruled the end state again, after X-ai-0's two adversarial reviews**: *"Write the plan
so that the end result is the from scratch model ... Correctness and best practice takes precedence
over everything."*  R-DU's direction stands -- one verb, one trigger, both ledgers -- and its
DECOMPOSITION does not, because R-DU named the re-derive's SCOPE and never named what owns a journal
entry.

**A THIRD adversarial review then attacked these rulings themselves and found five ship-blockers.
Everything below is the corrected text, and the withdrawn claims are named in R-DZ rather than
quietly rewritten**, because two of them would have shipped a 500 on the most-used delete route.

**Everything here is MEASURED, and each measurement was reproduced by a second party:**

* **Four of the seven posting source kinds have NO owning FK.**  Censused on the clone: `transfer`
  (19 entries) carries `transfer_id`, `transaction` (155) and `loan_payment` (15) carry
  `transaction_id`, and `loan_opening` (6), `loan_trueup` (6), `account_opening` (8) and
  `account_trueup` (109) carry NEITHER.  **129 of 318 entries -- 40% of the ledger -- cannot name the
  event they are a projection of.**
* **An event owns MANY entries, not one.**  Counted on the real ledger: **21 of the 128 linked
  transactions carry 3 journal entries each** under one `(transaction, period, entry_date)` key -- an
  original plus two deltas -- and across the 129 FK-less entries the same key collides on **43 groups
  covering 89 entries**.  That is what reconcile-to-target IS on an append-only ledger, and a first
  draft of R-DV said "one event, one entry".
* **`journal_entries` has no CHECK constraint** (`pg_constraint` reports zero `contype='c'` rows), and
  **the model records that as a DECISION, not an oversight** (`models/journal_entry.py:86-90`: a CHECK
  "would have to grow with every future source kind and reference ref-table IDs it cannot see").
* **The DB tier's existing fence is narrower than a first draft claimed.**
  `ck_account_postings_balanced` is a deferred constraint trigger, and it fires `AFTER INSERT OR
  UPDATE` -- **never on DELETE**.  The exclusion is deliberate and documented twice
  (`posting_infrastructure.py:141-145`, `models/journal_entry.py:55-57`): firing on DELETE would abort
  a legitimate CASCADE disposal mid-cascade.  What carries the delete case is an APPLICATION guard --
  `routes/accounts/crud.py:726` (Guard 5), whose own comment says "the balanced trigger does not fire
  on DELETE".
* **A Python session listener can be bypassed, and the census is 20** bulk `Query.update` /
  `Query.delete` sites across `app/` and `scripts/` (9 update, 11 delete), reproduced independently.

| # | ruling | consumed by |
|---|---|---|
| **R-DV** (answered 2026-08-03; **corrected the same day by the design review, which refuted its first framing**) | **A journal entry is the projection of exactly ONE SOURCE EVENT, and the EVENT owns it.  An account is the SCOPE of a re-derive and never an owner.**  Three source events project onto the ledger: a `Transaction`, a `Transfer`, and an ASSERTION (`AccountAnchorHistory` on the cash side; the synthesized origination plus each `LoanAnchorEvent` on the loan side).  **The relation is MANY entries to ONE event, and a first draft said "one entry per event" -- measured false: 21 transactions carry 3 entries each under one key.**  What an event owns is a RECONCILE GROUP whose entries SUM to the reconciled net, which is what `_posted_by_period` (`posting_service.py:148-204`) already assumes.  **The full key is `(source_kind_id, owning event, scenario_id, pay_period_id, entry_date)`** -- and the two the first draft omitted are both load-bearing.  `source_kind_id`: a loan payment produces TWO entries from one event (`transfer` for the cash leg, `loan_payment` for the split correction, the latter linked by `transaction_id` -- `loan_posting_service/_payments.py:25-34` states the shape), so without the kind a whole-account verb reading the ledger's source links would sum the split correction into the transaction reconcile and reverse it.  `scenario_id`: an assertion is per-ACCOUNT while entries are per-SCENARIO (`_sync.py:143-191` loops every live scenario), so one event owns N entries and without the scenario two scenarios' entries merge into one group -- latent today at one scenario, live the day scenario-clone ships.  **"Account A changed" is the QUERY, and it is the UNION of the account's facts and the ledger's existing source links** (finding **N-162**).  *Rejected:* account-owned entries (R-DU as written -- it creates R-DW's oscillation, and leaves 40% of the ledger unable to name its own source); and event-owned entries WITHOUT the account-scoped loop (a source change moves every later assertion's `balance_before`, so the loop is not optional) | X-ai |
| **R-DW** (answered 2026-08-03; the only ruling of the four the design review did not dent) | **A transfer's entry has ONE valuation, and a broken Transfer Invariant 3 becomes an ASSERT failure rather than a write oscillation.**  Under account-owned entries the same transfer entry is computed twice: re-deriving the FROM account values it from the EXPENSE shadow (`cash_ledger.settled_cash_leg`) and re-deriving the TO account from the INCOME shadow (`posting_service._settle_effective`, a `COALESCE(actual_amount, estimated_amount)` with no credit term).  R-DU's idempotence argument is explicit that "both endpoints compute the identical target from the identical rows"; **they do not read the same rows, and `cash_ledger/_events.py:265-277` already says so** -- the two "agree today only because Transfer Invariant 3 mirrors `actual_amount` onto both shadows and `entry_service` refuses entries on a shadow at all", which that module calls "exactly the 'two rules that happen to agree' shape this module claims to have ended, surviving on the rows that carry the largest cash movements".  So account-ownership does not merely inherit the disagreement: **it converts a static discrepancy into a WRITE OSCILLATION** -- each endpoint posts a delta back to its own target and every commit flips it.  Under R-DV a transfer is ONE event with ONE amount and the oscillation is unrepresentable.  **The rule that wins is the LEAF's** (`settled_cash_leg` over the income shadow), because the arc's direction is that the posted ledger is a projection of the facts -- and it is a no-op today, which the step MEASURES rather than assumes.  **The READ side keeps valuing each shadow independently, deliberately**: the walk is per-account, so a broken Invariant 3 makes the walk disagree with the ledger and the checked-projection assert REFUSES the write.  *Rejected:* valuing from `transfers.amount` (the grid shadow-edit path forwards an `actual_amount`, so it desynchronises from both the backfill and the oracle); and enforcing Invariant 3 in the schema INSTEAD (worth doing, and it does not close this -- two valuation rules would still both exist) | X-ai |
| **R-DX** (answered 2026-08-03; **its DB half was corrected twice by review and the limit is now stated exactly**) | **The IDENTITY invariants go to the DATABASE tier where they CAN go.  The FOLD stays in Python, because putting it in SQL would be the second implementation of the money rule this arc exists to delete.**  **To the DB:** at most one non-null source FK per entry, agreeing with `source_kind_id` (R-DY).  **NOT to the DB, and a first draft said otherwise:** "an anchor history row's `pay_period_id` contains its own `observed_on`" **cannot be a CHECK at all** -- the predicate needs `pay_periods.start_date` / `end_date` and PostgreSQL refuses a subquery in a check constraint -- and **live code produces the violation BY DESIGN**: `account_service.resolve_anchor_period_id` (`:54-95`) rule 2 falls back to the user's EARLIEST period when none contains the date, and `_reject_undatable_observation`'s own docstring names the outcome verbatim.  So it is a TRIGGER paired with a fix to rule 2, or it is nothing; it is **deferred to its own finding (N-168)** rather than smuggled into a migration.  **Staying in Python, and stated as a limit:** the checked-projection assert, so the grader remains bypassable by a bulk statement -- a MEASURED 20 call sites (finding **N-163**).  **A first draft justified accepting that with "the DB tier still refuses an UNBALANCED entry however it is written"; that is FALSE on the delete path** and the codebase says so twice: the balanced trigger fires `AFTER INSERT OR UPDATE` only, and `crud.py:726` (Guard 5) exists precisely because deleting a `LedgerAccount` would strand paired legs the trigger cannot see.  **The honest partition:** the DB refuses an unbalanced entry on every WRITE; on DELETE the invariant is carried by an application guard; and a bypassed re-derive leaves the ledger STALE, which reconcile-to-target self-heals at the next re-derive of that account.  *Rejected:* re-implementing the fold as a SQL trigger (a second statement of the money rule); and dropping the Python grader because it is bypassable (it covers every path the application uses, and naming the gap is what makes it honest) | X-ai |
| **R-DY** (answered 2026-08-03; **the design review ran the constraint and it broke every hard delete, so the shape is corrected here**) | **The source identity is an EXCLUSIVE ARC of typed FKs with an AT-MOST-ONE check, never an exactly-one check.**  Three new nullable FKs -- `account_anchor_history_id`, `loan_anchor_event_id`, `loan_params_id` -- beside the two that exist, with one named CHECK: **at most one non-null, and any non-null one agrees with `source_kind_id`**.  **"Exactly one" is REFUTED BY EXECUTION.**  `journal_entries.transaction_id` and `transfer_id` are `ON DELETE SET NULL`, PostgreSQL implements SET NULL as an UPDATE, and an UPDATE is CHECK-validated -- so with an exactly-one constraint in place, hard-deleting any posted transaction fails with `check_violation` inside `UPDATE ONLY budget.journal_entries SET transaction_id = NULL`.  Reproduced on the clone.  That would have 500'd `routes/transactions/mutations.py:555`, `transfer_service.py:754` and `routes/accounts/crud.py:749`.  **The orphan state is real, documented and CEDED by ruling R-DI**, whose whole subject is postings whose source row was SET-NULLed; a constraint forbidding it contradicts a standing ruling.  **The cost of the correction, stated:** with at-most-one, the CHECK no longer makes "every entry can name its event" a STORAGE invariant, so it fences the SHAPE (never two sources, never a mismatched kind) and not the COMPLETENESS.  **`ON DELETE` is ruled per FK rather than left to the migration**: all three new FKs are `ON DELETE SET NULL`, matching the two that exist -- `CASCADE` would delete journal entries and violate append-only, and `RESTRICT` would turn `pay_period_admin.truncate_pay_periods` into an `IntegrityError` (a period wipe CASCADEs its `account_anchor_history` rows, and under R-DZ a correction entry routinely lives in a different period from its history row).  **The CHECK must not hardcode serial ids**: `ref.posting_sources` ids are assigned by a sequence across three migrations, so the arc is expressed against a pinned-id migration or a `posting_sources`-side column joined by FK -- decided at X-ai-s, never as integer literals in DDL.  *Rejected:* a polymorphic `(source_kind_id, source_id)` pair (four columns shorter, and it throws away referential integrity and `ON DELETE` behaviour on 40% of the ledger); and persisting the synthesized origination as a real `LoanAnchorEvent` row to save the fifth column (it reintroduces the degenerate no-anchor-events state the synthesis deliberately removed) | X-ai |
| **R-DZ** (answered 2026-08-03, and it is the ruling the developer's own instruction forced: *"I feel like restructuring keeps getting pushed off"*) | **R2 for anchor corrections is a KEY-SHAPE change with NO migration in it, and it ships FIRST, alone, as plan step X-ai-r.**  The design review traced N-161's root past the FK to a single omission: `_posting_reconcile.posted_correction_legs` (`:153-160`) passes `extra_columns=[source_kind_id, entry_date]` into `summed_posting_legs`, whose `GROUP BY` is `:114-118` -- so the posted side **drops `pay_period_id`** -- and `_anchors.py:187` then re-supplies the period from `correction.anchor.pay_period_id`, the source row's CURRENT period, which is verbatim what the R2 rule forbids (`posting_service.py:31-39`).  The source reconcile obeys R2 for exactly one reason: `_posted_by_period` groups by `(pay_period_id, entry_date)`.  **Adding the period to the correction side's GROUP BY and to its target key delivers R2 with no schema change, no FK, and no backfill** -- traced on the 2026-06-03 pair: posted becomes `{(trueup, 06-03, p5): +3054.36, (trueup, 06-03, p6): -2854.36}`, target `{(trueup, 06-03, p6): +200.00}`, deltas `-3054.36` in period 5 and `+3054.36` in period 6, net unchanged.  **A first draft of this plan scheduled that fix BEHIND a migration on a misattributed root cause**, which is the deferral the developer objected to, committed inside the plan written to stop it.  **What the FK still buys, and it is a different thing: per-ASSERTION attribution** -- splitting the merged `+200.00` into `+386.85` at period 5 and `-186.85` at period 6, which needs an identity the ledger does not carry.  So the two are SEQUENCED, not merged: X-ai-r closes N-161's rule violation now; X-ai-s buys the identity later.  *Rejected:* shipping them together (it blocks a two-file correctness fix behind a migration, a backfill and an F1-class data decision); and shipping only X-ai-r (the merged key still cannot tell two same-day assertions apart, which is the second half of N-161 and the reason `merge_target_legs` exists).  **SUPERSEDED IN PART, the same day, by ruling R-EA: the POSTED-side half stands verbatim and the TARGET-side half does not.**  This row's traced target `{(trueup, 06-03, p6): +200.00}` -- a merge by `(kind, date)` filed at the LAST row's STORED period -- was refuted by an adversarial design review; the deltas it predicts (`±$3,054.36`) are not what shipped (`±$2,854.36`) | X-ai |

| **R-EA** (answered 2026-08-03, **and it SUPERSEDES R-DZ's target-side half; the developer took it after being shown the option space with worked figures, having twice asked "which is what I should do if I were building everything from scratch"**) | **An anchor correction books in the pay period CONTAINING the day it asserts, DERIVED through the one function both ledgers already share -- never read from the source row's stored `pay_period_id`.**  Three shapes were measured on a production clone, and the choice is not close.  **The rule that decides it was already written and neither R-DZ nor the first build had read it**: `account_service.resolve_anchor_period_id` (`:67-73`) states ruling R-DH verbatim -- *"The period is DERIVED from the day, not chosen beside it ... an assertion's period and the civil day it was true are two statements of one fact, and the moment they can be set independently they can disagree: an opening dated 2026-03-15 filed into the period containing today would put its correction's journal entry in a period its own `entry_date` falls outside"* -- which is EXACTLY the state R-DZ's key and the first build both produce for history row 50.  **`account_anchor_history.pay_period_id` is a CACHE of that same derivation, not an independent fact**, and its only consumer in `app/` was this writer (now zero -- finding **N-169**).  **The row that exposed it was written by a broken clock**: row 50 was created **21:28 Eastern on period 5's LAST day** and stored against period 6, which is verbatim the case `routes/accounts/crud.py`'s own comment (`:402-411`) was written to prevent -- *"the grid buckets the correction by `observed_on` and the ledger stamps it with `pay_period_id`, so the two surfaces disagree by the whole correction."*  **Measured against the surface that renders these figures**, the grid's "Book vs bank" row (`balance_at/_cash_periods.py:461`, which buckets on `observed_on`), over 61 periods of the production clone: deriving from the day agrees on **61 of 61**; the stored column disagrees on **2**; R-DZ's merged-at-the-latest-row disagrees on **2**.  **It also collapses the two halves to ONE rule** -- cash and loan both file through `loan_ledger.resolve_anchor_pay_period` against the entry's own date, where R-DZ's shape left cash reading a column and loan deriving from a date, two answers to one question.  *Rejected:* the STORED period (`±$2,667.51`; splits sequential deltas across a boundary so period 5 would close on an assertion the account held for two hours, and propagates a known-bad row into the ledger permanently); and R-DZ's merged-at-the-latest-row (`±$3,054.36`; files BOTH assertions' corrections into a period neither was asserted for, and preserves a collision tie-break after removing the collision).  **A cost is ceded and named**: the entry's period and the row's stored period can now differ, so the CASCADE coupling that made a period wipe dispose assertion and correction together no longer holds for a mis-filed row -- which is finding **N-168**'s repair, and is the ONE state where the two rules disagree at all | X-ai |

### Answered (developer ruling, 2026-08-03: X-d's resume fork -- the re-derive gets ONE name)

| ruling | decision | step |
|---|---|---|
| **R-DT** (answered 2026-08-03, and the option ruled did not exist when the fork was first put) | **The anchor re-derive becomes ONE named entry point, and the rule about WHEN it may run lives in that one docstring.**  `account_posting_service.resync_anchor_postings(account_ids, scenario_id)` is the ungated loop lifted out of `self_heal_anchor_corrections`; its three callers are the three shapes an operation ends in -- the self-heal (gate plus call), `posting_service.retire_transaction`, and `transfer_service.delete_transfer`.  **The fork it answers was the 1000-line ceiling, not the design**: R-DM's cash half was MEASURED at **+17 lines** in `transfer_service.py` at this codebase's comment density (**+12** squeezed), against **13** lines of headroom left by X-aj1 (finding N-152).  The three options first put were all bad in the same way -- squeeze the comments to land at 999 (N-152's own named anti-pattern, "a shave under pressure"), move `delete_transfer` to a flat sibling (a fifth shave of the same module), or pull N-152's package split forward (a fourth mechanism into a step that is already a writer swap, a module deletion and a type conversion, which is what R-DM's own reasoning forbids).  The extraction is none of them: it DELETES a duplicated loop, states the ordering rule once instead of restating it as a comment block at the third caller, and lands `transfer_service.py` at **997**.  *Also considered and NOT taken:* deleting the now-redundant endpoint resync in `_reconcile_postings_after_update` (it would free six more lines and its stated justification -- insurance against "the delta-keyed self-heal" -- is dissolved by R-DK, which X-d itself ships).  The developer declined to bundle a reversal of a documented deliberate decision into this step; it is recorded as finding **N-153** and owned by X-ai, which re-decides where the assert runs and therefore re-decides this | X-d |

### Answered (developer ruling, 2026-07-26: X-g's five forks, all as recommended)

**The trace ran first and no code was written for it** -- the step's own stated first action. Each
ruling below carries what the trace MEASURED on the prod-shape clone `shekel_f3_final` (and, where
it differs, on `shekel`), not what the shape of the problem suggested. Two of the five inverted
their own premise once measured, and a fifth fork (**R-W**) did not exist until the trace found it.

| # | ruling | consumed by |
|---|---|---|
| **R-R** (answered 2026-07-26) | **A contribution is partitioned by SOURCE, so the two feeds are disjoint BY CONSTRUCTION and there is no de-dup rule to get wrong.**  A recorded transfer is an ACTUAL / PLANNED event (it HAS a transaction row); a payroll deduction is a modeled CONTRIBUTION event (it never has one).  The replay therefore never reads `_average_transfer_contribution`, which today folds both feeds into ONE scalar at `investment_projection.py:444-447` -- mixing them into one number is exactly what makes them indistinguishable.  **The mechanism was confirmed and measured, and it is not live.**  The two row sets provably overlap: `loan_loaders.query_shadow_income` (transfer-linked Income rows) and `cash_ledger._facts._unwindowed_contributing_rows` (what the fold counts) select the same rows.  Measured by creating six `$500.00` projected Checking -> Roth transfers inside a ROLLED-BACK transaction: the fold read `$30,432.35` at period 14 while the shipped map read `$31,098.91` -- the map had DISCARDED the rows and applied its own `$500.00`/period, so today's single count is a side effect of the merge X-g deletes.  A naive union would have added **`$3,000.00` over six periods** (~`$26,000` over the horizon, before compounding).  Not live today: **no deduction targets an investment account** (all 12 `paycheck_deductions.target_account_id` are NULL in both databases) and **the three investment accounts hold ZERO transaction rows of any kind** in both databases, so `periodic_contribution` is `$0.00` for all three.  The partition is SOUND because nothing in the app creates a transaction from a deduction -- AST-scanned over `app/` + `scripts/` (never a regex, Section 8): the 5 modules that IMPORT `PaycheckDeduction` (`models/__init__`, `routes/salary/items`, `investment_dashboard_service`, `projection_inputs`, `retirement_projection`) and the 5 that CONSTRUCT a `Transaction` (`routes/transactions/create`, `carry_forward_service/_execute`, `credit_workflow`, `recurrence_engine`, `transfer_service`) have an EMPTY intersection.  **Two consequences the ruling owns, so X-g1 does not rediscover them.**  (a) The EMPLOYER half is not partitioned: `growth_engine.calculate_employer_contribution` sizes a MATCH from the period's employee contribution, so it reads the RESOLVED employee total for that period whichever feed produced it (a flat-percentage employer -- the real Empower shape, `type_id` flat at 5% of `$3,631.74` gross -- does not depend on it at all).  (b) `_average_transfer_contribution` SURVIVES for the what-if surfaces under ruling R-U: "keep contributing at this rate for 30 years" is a legitimate question about a rate, and the synthetic long-horizon periods have no dated record to fall back from.  It leaves the BALANCE path, it is not deleted from the tree.  Rejected: rows-only (a payroll-funded 401(k) would stop growing from contributions, which have no row by construction), rate-only (the transfer's expense leg still leaves checking while its income leg never arrives -- broken double entry), and both-with-an-explicit-de-dup (today's compensator at `investment_projection.current_period_transfer_contribution:529`; it works, and it is a rule a reader must remember rather than a shape that cannot go wrong). | X-g |
| **R-S** (answered 2026-07-26) | **An ASSERTION always wins, and before the FIRST one the balance holds FLAT -- ruling R-I's rule, for all five kinds.**  **The fork inverted once traced.**  Its own text assumed the reverse projection was a ruled model the fold would damage; the measurement says the reverse projection is the DEFECT.  The three modeled accounts carry **15 recorded assertions** (Roth 6, Trad IRA 6, Empower 3) and `build_investment_balance_map` reads only the LATEST, re-deriving every earlier period from a model.  At the period ending 2026-04-08 the app renders Roth `$26,604.63` against the user's own 2026-04-06 assertion of **`$23,851.08`**, Trad IRA `$11,360.85` against `$10,175.49`, Empower `$29,289.22` against its earliest record `$26,912.56` -- N-43's `-$6,315.57`, re-verified to the cent, now with its SIGN established: the fold reproduces every assertion and the model contradicts them (**N-74**).  The rule therefore is one rule, not three: `sample_cumulative` seeded at `first_assertion - sum(pre-assertion source deltas)` (R-I's own mechanism) replays every ASSERTION as a reset, so a period at or after an assertion reads that assertion plus its recorded rows, and the pre-tracking prefix holds flat.  It is ALREADY the Property's rule, stated in `build_appreciation_balance_map`.  Rejected: un-growing before the first assertion (worth about `$7` on ONE period of the 401(k) -- both IRAs' first assertions fall inside their earliest pay period, so no period end reads the region at all -- and it costs a second rule plus a surviving `reverse_project_balance` in the balance path), and keeping today's model (`/savings` keeps rendering `$6,315.57` of history that contradicts recorded facts, and keeps N-75 with it). | X-g |
| **R-T** (answered 2026-07-26) | **ACCRUAL events are DAILY, resolved in ONE sequential pass, and `sample_cumulative` is NOT changed.**  Between two events the balance is constant, so the whole horizon's accrual deltas resolve in one pass over the sorted step list and merge into it; the shipped sampler -- shared with the LOAN fold -- is untouched.  A daily step means a sampled date never lands inside an unresolved span, so the answer is exact at every date and can never become a function of which OTHER dates were asked for (the shape rulings R-G / R-H kept out of the leaf).  **Measured, both halves, and each scoped to what was actually run.**  COST: a synthetic bench of the resolving pass plus `sample_cumulative` over **900 steps and 840 dates** takes **0.70 ms**, against **0.20 ms** for the same sampler over today's 60-step shape -- **+0.5 ms** per account per full-horizon read, where the real `fold_cash_balances` over the 840-day horizon already costs **2.7-13.8 ms** per account (load included) and one `/savings` + `/investment` pair costs ~500 ms (N-72).  BENEFIT: not the total.  For INTEREST, measured against the SHIPPED `_interest._layer_interest`, a day-by-day replay of the same `calculate_interest` rule differs by **`$0.14`** over 840 days on the HYSA and **`$1.73`** on the Money Market (the daily replay is the higher of the two in both cases).  For the three INVESTMENTS the comparison is grain-only -- the same `period_return_rate` at two grains with contributions held out of both, since their contribution feeds are empty -- and it differs by at most **`$0.05`**.  What the grain actually costs is WHEN the money lands.  N-71 re-verified at period 30: the scalar returns the IDENTICAL value on the period's first and last day (Empower `$38,617.11` against `$328.50` of growth in that period, Money Market `$9,090.81` / `$261.24`, Roth `$29,843.76` / `$114.07`).  Rejected: segment-per-compounding-interval-plus-event (exact and cheaper -- 2 to 60 segments per account today instead of 840 -- but a date INSIDE a segment needs a partial-accrual read that is not booked as a step, which is one more rule for a `$0.5 ms` saving), and keeping the pay-period grain (N-71 stays open forever and "balance at a DATE" stays a lie for three of five kinds). | X-g |
| **R-U** (answered 2026-07-26) | **The replay owns the SEED and the history; the forward WHAT-IF keeps `growth_engine`.**  The chart is not a balance-at-T surface: `investment_dashboard_service` projects over SYNTHETIC periods (`growth_engine.generate_projection_periods`, a slider-driven horizon clamped to 40 years, `:721`) and re-projects the whole series for the what-if overlay with `contributions=None` (`:972`); `retirement_projection.py:593` does the same under a `return_rate_override` and a per-period `salary_basis`; `savings_dashboard_service/_horizon.py:413` runs a 30-year band.  A fold over STORED facts cannot answer a hypothetical, and cannot answer a date past the user's pay-period horizon at all.  So what changes is the SEED: `investment_seed_map` (`:249`, and `retirement_projection.py:492`) becomes the replay with ACCRUAL filtered out -- the FILTER Section 3.2 names -- and the surfaces keep their engine.  **The de-dup subtraction goes with it**: both seeds today subtract `current_period_transfer_contribution` (`investment_dashboard_service.py:318`, `retirement_projection.py:580`) because the seed includes recorded contributions the engine then re-applies for the current period.  Under R-R's partition the seed is read at the day BEFORE the projection window opens, so the overlap does not exist and deep-quality-hunt #9 / #14's compensator deletes rather than being ported.  Precedent, and it points the same way: plan step C5 made the property equity chart's DEBT line read `positions()` (finding B-2, `$299,701.35` wrong on 8 of 13 shapes) while its forward what-if kept projecting. | X-g |
| **R-W** (N-76; answered 2026-07-26) | **The grid renders the MODELED balance, with a "Growth" row that is the accrual producer's own answer -- ruling R-K's identity then holds for all FIVE kinds.**  A fork the plan did not have: the trace measured that the grid and `/savings` already answer ONE modeled account two ways.  `_grid.grid_balance_view` layers an accrual for INTEREST only (the gate is `_grid.py:435`, `accrual_params(account) is None` on its CASH arm since X-g3a `320a4641`, which both MOVED it and INVERTED it; this row's earlier citations `:271-277` and `:345-362` / `:346` drifted at X-g2b and X-g3a respectively and are corrected here per Section 7.6), so an INVESTMENT or APPRECIATING account's grid balance is its kind-blind cash-flow balance while `balance_map` returns the modeled one.  Measured at the last projected period on `shekel_f3_final`: Empower 401(k) grid `$31,070.06` vs `/savings` `$48,712.19` (**`$17,642.13`**), Roth `$5,916.95`, Trad IRA `$2,526.68`; on `shekel` the Property is `$21,675.99` apart.  The grid's interest column is `None` for every one of them, so nothing on screen explains the gap -- and both surfaces are reachable for these kinds (`account_resolver.is_cash_flow_account:41` admits every non-amortizing kind).  INTEREST accounts are byte-identical on both surfaces, which is the proof the unification WORKS: the Interest row already is the general shape.  Under one replay a typed grid row IS an event in the same stream, so the objection `_grid.py` records today ("a typed grid row would not move their modeled balance") stops being true and the identity becomes a property of the construction for every kind.  **Corrected 2026-07-27, after X-g2b measured it:** the identity is `net + reconciliation + accrual + CONTRIBUTION`, not `+ accrual` alone -- a modelled asset has two modelled tiers, and on the real Empower 401(k) the contribution is the larger of them (`$9,624.27` against `$8,152.58` over the horizon).  X-g3's entry carries what that opens.  Rejected: keeping the cash-flow basis with a caption (leaves `$17,642.13` of visible contradiction, the shape ruling R-K refused to ship for the cash subtotals -- and Section 8: a label is weaker than a predicate, which is already not a safety), and refusing modeled kinds on cash-flow surfaces the way ruling D4 refuses a loan (removes the contradiction by removing screens the developer uses; a loan is refused because its balance is not a transaction sum, while a modeled asset's IS one plus a rate -- the same shape an HYSA already renders correctly). | X-g |

### Answered (developer ruling, 2026-08-03: R-EB -- the anchor half, designed from scratch)

**RULED: Option 4, followed by Option 6.**  *"I want to do Option 4 the ledger is sum-of-postings,
the assertion is a reconciliation followed by Option 6 bank import."*  The design below ships as
**X-f1**..**X-f5**, and **X-f6** (bank import) is ruled as the sequenced follow-on rather than an
alternative -- it consumes X-f1..X-f5's machinery and cannot substitute for it.  The ruling was taken
after being shown the full six-option space with worked figures on the developer's own data, against
their standing instruction: *"research and investigate whether there is a better way of handling my
anchor balances and true-ups"*, *"I want to make the fences structurally unnecessary"*, and
*"correctness takes priority"*.

Three developer statements are recorded as INPUTS the design consumes: the figure they budget
against is the **projected end balance** ("balance after everything I have budgeted has been
accounted for"), not any headline balance; they are *"good with clearing the historical Equity
balance if it makes my finances more accurate"*; and they *"will tick items at reconciliation time"*
-- which their current workflow already is (*"update the checking anchor balance and mark which
expenses are already accounted for in that balance"*).

**Measured read-only against the production database, 2026-08-03.**  Checking (account 1),
2026-03-27..2026-08-03: 129 days, 55 assertions on 51 distinct days, one every 2.3 days.

| | |
|---|---|
| assertion days booking a NON-ZERO correction to Equity | **49 of 51 (96%)** |
| gross through `anchor_equity` (ledger account 30, 97 legs) | **$15,754.24** |
| of which the OPENING, which is legitimate | $689.16 |
| of which TRUE-UP PLUG | **$15,065.08** |
| net | **-$805.94** (opening +$689.16, true-ups **-$1,495.10**) |
| average gap at an assertion | **$321.52** |
| worst single gap | **$1,853.92** (2026-06-02) |

The mechanics are sound -- the trial balance closes at **$0.00** over all 643 postings, and five
orphaned correction days self-healed to zero through `_posted_only_key_period_id` (finding N-176).
**Gross is 10x net and the lag-1 autocorrelation of the correction series is -0.33**, with 22 of 42
consecutive non-zero pairs reversing sign: the plug is TIMING, not missing money.  Its generator is
measured too -- **65.2% of settled Checking rows share a click-minute with another row** (largest
batch 6), so for two thirds of the account the settle date is a bookkeeping-session artifact.

**The design.**  Four parts, each replacing one mechanism:

* **(a) The cash ledger is sum-of-postings.**  `balance(T) = opening equity + SUM(postings <= T)`.
  No assertion reset.  This is what the LOAN side already does in production: Mortgage and Van Loan
  each carry exactly ONE anchor and no recurring plug.
* **(b) `AccountAnchorHistory` becomes a STATEMENT RECORD.**  It stores what the bank showed and on
  what day, and books nothing.  Exactly one anchor per account books an opening equity entry.
* **(c) A true-up becomes a RECONCILIATION.**  The app shows the outstanding set and the difference;
  ticking stamps the STATEMENT date, not `now()`.  `_outstanding_scope` (`entry_service.py:819`) is
  already this primitive and needs its transaction twin.
* **(d) The residual after ticking is a REAL posting to a REAL account, never Equity** --
  Uncategorized Expense / Income, a chart mechanism that already exists
  (`ledger_account_service.py:101-103`, kind `fallback`, zero rows today) and is recategorizable.

**Why it improves the one figure the developer actually budgets against.**  R-DH (c) states the
invariant protecting the projected end balance, and its own note records that invariant as
**"NOT YET TRUE, and NOT YET TESTED"** -- today it holds only if the anchor is trued up by the same
amount AND the reconciliation derivation fires in the right order.  Under (a) it is arithmetic:

```
book = 1307.66            projected_end = 1307.66 - 500.00 - 827.61 = -19.95
record $150.27            book = 1157.39,  envelope remaining = 349.73
                          projected_end = 1157.39 - 349.73 - 827.61 = -19.95   UNCHANGED
```

`book` lands on **$1,157.39**, which is exactly the figure R-DH (c) says the user would have to true
up to by hand.  The ledger reaches it unaided; the second manual step stops being necessary rather
than being made reliable.

**The option space, and what the ruling rejected.**  *(1) status quo* -- rejected: $15,065.08 gross
invisible on the income statement, every fence permanent.  *(2) status quo plus better dates* --
**this was the plan as it stood (S2-b / X-f as scoped)**, and it is rejected AS AN ENDPOINT rather
than as work: it fixes the self-cancelling CHURN and does nothing for the untracked RESIDUE, because
money never recorded has no date to correct.  It survives as **X-f1**.  *(3) reclassify only* --
rejected as an option and kept as a STEP: correct only after (2), since reclassifying churn would put
$15,065 of phantom spend in the report.  It survives inside **X-f3**.  *(4) this design* --
**RULED**.  *(5) two ledgers with a clearing account* -- rejected outright, a third instance of root
cause 1 (two representations of one thing).  *(6) bank import (OFX/CSV/Plaid)* -- **RULED as the
follow-on**, and it REQUIRES (4) rather than replacing it: an import yields bank-dated facts that
must be matched against budgeted rows, and the unmatched residue still needs classification.  Options
1-3 are not on the path to it; (4) is.

**Accounting ground, checked against four independent systems.**  Standard bank reconciliation gives
timing differences NO journal entry (they adjust the BANK side of a worksheet); only unrecorded
book-side items get entries, into their REAL accounts.  Beancount's `pad` is the closest analogue of
today's design and its own documentation says to use it *"with caution, as it can hide larger
problems. Explicit adjustments are generally safer."*  QuickBooks documents Opening Balance Equity as
temporary, a persistent balance being a known symptom of unresolved reconciliation discrepancies.
GnuCash carries reconciliation status per SPLIT and requires the Difference to reach $0.00, forcing a
balance being an explicit button.  hledger keeps cleared/pending/unmarked as a FILTER over one
journal.  Unanimous: the plug is exceptional, explicit and visible, never the mechanism by which
normal balances are maintained.

**Why this is NOT `anchor_settle_partition.md` Section 11's rejected design.**  All four of that
section's killers are artifacts of KEEPING the reset, which (a) removes.  *Breaks one-granularity*:
there is no partition, and clearedness affects the reconciliation REPORT only.  *Reproduces
-$4,001.42*: nothing rides on top of anything, a recorded row being in the book balance from the
moment it is recorded.  *No correct implementation for transfers*: clearedness becomes a property of
a POSTING LEG, so each leg clears against its own account's statement -- GnuCash's model, and
strictly more correct since a transfer can clear on two days at two banks.  *Same-day duplicate index
discards the reconciliation*: that index dedupes assertions-used-as-resets, and the reconcile write is
already its own transaction (`routes/accounts/anchor.py:246-250`).

**What it deletes**: the reset in both walks (`cash_ledger/_walk.py:300`,
`account_posting_service/_walk.py:491`); `ReconciledThrough` and its **78 references across 14
files**; the anchor/settle partition rule and R-DH (a)'s accepted residual in both directions;
`account_posting_service/_anchors.py`; the R-I seed compensator (`_cash_fold.py:372-382`); X-e's
question, `Account.current_anchor_*` ceasing to be any balance path's input.  **It also deletes the
module N-161 is about, and both findings X-ai-r opened** (N-169's dead `CashAnchorFact.pay_period_id`
and N-170's two disagreeing day-to-period derivations) -- both are properties of the correction
family existing at all.

**Honest costs, stated so the ruling is not taken on a partial picture.**  The developer would see
roughly **$370/month of Uncategorized Expense** until recording tightens; that is the $1,495.10 net,
and it is money currently invisible.  **Between reconciliations the app stays optimistic by exactly
the unrecorded spend, which is UNCHANGED from today** -- on 2026-08-02 the app showed $1,307.66
against a bank near $752.62, and it would show the same; what changes is that the gap becomes an
itemized list rather than a silent absorption.  X-f3 MOVES MONEY and wants its own PR.  **The
inference that could be wrong**: that the records are mis-dated rather than materially incomplete.
The evidence is gross/net = 10x and the negative autocorrelation, from four months of one account.

**Sequencing consequence, RULED.**  **X-ai-s is a migration buying per-ASSERTION attribution for the
correction family this design deletes, so it is HELD pending X-f3** rather than run ahead of it.
X-ai-r shipped regardless and correctly (no migration, closes a live rule violation, and its LOAN
half survives X-f3 untouched -- loans keep their one opening correction).  **X-d is NOT superseded**:
its writer swap (`account_posting_service/_walk.py` deleted whole, the writer consuming
`cash_ledger.walk_cash_ledger` -- ruling R-H delivered), R-DJ's two day types, R-DK's skip deletion,
R-DL's N+1 hoist and R-DM's `retire_transaction` chokepoint all survive; only its `_anchors.py`
rewrite is discarded, and X-f3 makes its remaining scope smaller.  N-155's assert placement is still
X-ai's to answer and is unaffected by this ruling.

**Document disposition, RULED.**  `anchor_settle_partition.md` becomes the THIRD as-built record in
`archive/`: its steps 1-4 and S1-c have all SHIPPED and only S2-b is unshipped, which folds into
X-f1.  That restores Section 9 rule 1 (one live planning document) rather than working around it.
**The move ships WITH X-f1** (finding N-175) so the archive and the plan that supersedes it land
together rather than leaving a window where neither document is authoritative.

### Answered (developer ruling, 2026-08-03: X-f1's three forks, all as recommended -- and the first DELETES a column)

Taken after being shown each fork with its from-scratch answer stated, against the developer's
standing instruction: *"Which option is what I should do if I were building everything from
scratch?"*, *"I want to make the fences structurally unnecessary"*, *"I have had enough of fixing
bugs with band-aid fixes."*  Measured read-only on a fresh production clone (`shekel_xf1`, restored
2026-08-03 from `docker exec shekel-prod-db pg_dump`, head `d7c1f4a9e603`).

**R-EC -- `transactions.paid_at` is REPLACED by `transactions.settled_on`, not joined by it.**
*"Replace it (the from-scratch answer)."*  The column stores the CIVIL DAY the money moved, and the
click instant is deleted.

*Why the replacement rather than the addition, measured rather than argued.*  An AST census of
`app/` finds **14 sites that turn a `paid_at` instant into a civil day -- 11 call sites across 8
modules** over 3 helper layers (`to_display_civil_date`, `cash_ledger.settled_civil_day`,
`posting_service._civil_settle_date`).  **Zero templates, zero JavaScript files and zero APPLICATION-tier serialized
payloads read the column**: `grep` over `app/templates/` and `app/static/` returns nothing, and
`TransactionUpdateSchema.paid_at` (`schemas/validation/transactions.py:63`) is `dump_only` on a
schema that is never dumped -- there is no `.dump(` call in `app/routes/` or `app/services/` at all,
so that field is dead in both directions.  **One serializer DOES see it and the sentence originally
overreached**: `audit_infrastructure.py`'s trigger writes `to_jsonb(NEW)` on every
`budget.transactions` row, which is how R-EC measured the 106 audit rows in the first place.  It
consumes no SUB-DAY precision, so the ruling stands on the same ground; the claim is narrowed on a
neutral review.  **Nothing anywhere ORDERS or COMPARES two instants**: the
only relational use of `paid_at` in the repository is one test assertion (`test_grid.py:5397`,
`second_paid_at >= first_paid_at`), which holds on days.  So the instant's sub-day precision has no
consumer, and keeping it would leave two columns stating one fact -- root cause 1 of this arc, and
the mirror image of the defect S1-c spent a whole step removing (one column carrying two facts).
**The conversion cost is identical either way** (`tests/` carries 197 `paid_at` WRITES and 58 READS,
which move under both options), so the addition buys a maintained column with no reader.

*What the replacement buys beyond the deletion, each item measured:*

* **The display-timezone conversion retreats from 11 read sites to ONE write door.**  After this
  step `to_display_civil_date` has no caller at all and is deleted with its two wrappers.
* **`Transaction.days_paid_before_due` (`models/transaction.py:322`) stops deriving the day at
  all**: it converts an instant to a display date and subtracts a `DATE` column today, and becomes
  exact civil-date arithmetic with no zone in the path.  (A draft of this bullet called it "a 12th
  derivation"; it is one OF the eleven, as `balance_predicates.settled_day`'s own docstring
  enumerates.  Corrected on a neutral review.)
* **One of the four DATABASE-CLOCK reaches goes.**  `tests/test_services/test_frozen_db_clock.py:8`
  names `status_seam` assigning `db.func.now()` to `paid_at` as one of exactly four places
  PostgreSQL's clock answers, which finding N-65 had to build `_freeze_db_clock` to contain.  A day
  stamped `display_today()` comes from the Python clock the suite already freezes.  **ONE of the
  four reaches closes structurally; the rewriter survives for the other three** (61 `NOW()`
  defaults, one `CURRENT_DATE` default, 23 `onupdate` re-stamps).  A draft of this bullet said the
  mechanism was "made UNNECESSARY", which the step's own rewrite of that docstring contradicts --
  corrected on a neutral review.
* **`posting_service._transaction_entry_date` (`:386-390`) loses a whole query.**  It re-reads
  `paid_at` off the database rather than the ORM attribute for one stated reason -- to force a
  server-side `db.func.now()` to materialise -- and a plain Python `date` is never an unresolved SQL
  expression.  (`_entry_date`'s query SURVIVES: it reads a DIFFERENT row, the transfer's income
  shadow.)
* **The period-start GUESS is made once instead of on every read.**  **Ten** of the 11 sites fall
  back to the pay period's `start_date` when `paid_at` is NULL -- the eleventh,
  `days_paid_before_due`, returns `None` instead, which is why it is the one site whose behaviour
  the backfill CHANGES (finding **N-181**).  **8 live settled rows carry that shape today** (four
  transfer pairs).  Backfilled once, no reader guesses again.

*The costs, ruled with them in view rather than discovered later.*  It is a DESTRUCTIVE migration:
it carries a `Review:` line per `.claude/rules/database.md`, and its downgrade REFUSES once any row
carries a hand-corrected day (the `d7c1f4a9e603` precedent) because a date cannot reconstruct an
instant.  **And 42 of the 148 live instants are lost for good**: `system.audit_log` holds a
`paid_at` for only 106 distinct transactions, its retention window starting 2026-05-06.  Recorded
because the obvious reassurance -- "the audit trail has it" -- is FALSE for 28% of the rows, and was
measured before the ruling rather than assumed.  *Rejected:* archiving the 148 instants into a
side table first (a preservation mechanism for data with no reader, which rule 13 forbids).

**R-ED -- the settle day is EDITABLE on a finalised row, and it is POSTING-RELEVANT.**
*"Editable + posting-relevant (the from-scratch answer)."*

`_LOCKED_EDIT_FIELDS` (`routes/transactions/mutations.py:68`) freezes `estimated_amount`,
`actual_amount`, `category_id`, `pay_period_id` and `due_date` on an `is_immutable` row so an
already-paid movement cannot be retroactively rewritten.  **Every one of those is a BUDGET decision
the user made; the settle day is an OBSERVED FACT about their bank.**  Budget decisions lock so
history is not rewritten, observed facts get corrected when the bank says otherwise -- and this is
the line S1-c already drew one table over, where `TransactionEntry.purchased_on` is guarded against
the future and `settled_on` is freely editable on the inline form
(`grid/_transaction_entries.html:117`).  Locking it would make the correction UNREACHABLE: reverting
to Projected and re-settling stamps today, so "this actually cleared last Tuesday" would be
inexpressible, and X-f1 would close **N-173** only in principle.

**It MUST join `_POSTING_RELEVANT_FIELDS` (`:88`) in the same commit that opens the door.**  That
set today is `{status_id, estimated_amount, actual_amount, category_id}`, and it does not carry a
date because no date was editable.  A settle day that moves without a reconcile moves the rendered
balance while the posted ledger keeps the old `entry_date` -- the books silently stop matching the
screen, on the one surface this arc exists to keep in step.  The machinery is already built and
proven on the transfer side (**N-13**: the per-`(period, entry_date)` reconcile reverses the
stale-dated entry and re-posts at the new day); only the wiring is missing.  **The gate for this
half is a test that EDITS a settled row's day and asserts the ledger followed**, not that the
figure changed.

**R-EE -- the settle door stays ONE CLICK, and the true-up form gets its own statement date.**
*"Stamp today, correct later (the from-scratch answer)."*

The seam stamps `display_today()` on the first entry into the settled band; the user corrects it
afterwards under R-ED.  **This is an ACCURACY ruling, not a convenience one**: 65.2% of settled
Checking rows share a click-minute with another row (88 of 135, largest batch 6 -- **N-173**,
reproduced exactly on the 2026-08-03 clone), so a per-settle date prompt would be answered six times
in a minute and would be clicked through.  A dismissed prompt is a worse record than a default the
user corrects when the statement disagrees.  It is the same ruling S1-c took for the one-click
anchor editor, on the same ground.  *Rejected:* prompting at every settle; and pulling X-f2's
bulk-confirm loop forward into X-f1, which is not an alternative but the SEQUEL -- X-f2 builds
exactly that loop, and merging them makes one large step out of two shippable ones.

**The true-up form's own statement-date field ships here, and its PERIOD is derived from that day.**
`stage_anchor_true_up` hardcodes `observed_on=display_today()` (`anchor_service.py:269`) and its own
comment says the parameter "arrives with that consumer"; this is that consumer.  The anchor period
therefore stops coming from `get_current_period(as_of=display_today())`
(`routes/accounts/anchor.py:408-410`) and comes from
`account_service.resolve_anchor_period_id(user_id, as_of=observed_on)` -- **ruling R-EA and R-DH
verbatim, "the period is DERIVED from the day, not chosen beside it"**, which
`create_account` already obeys.  The same
`account_service._reject_undatable_observation` gate applies, so a future day and a day before the
schedule are refused with the message the account-create form already surfaces.  This is a
CONSEQUENCE of rulings already taken, recorded here rather than re-asked.

**What X-f1 does NOT do, stated so the scope is not read wider.**  It does not touch the anchor
RESET (X-f3's cutover), does not classify any residual (X-f3), and does not build the
outstanding-set reconcile for transactions (X-f2).  It gives a settle a day the user owns; what that
day is compared against is unchanged.

### Answered (developer ruling, 2026-08-03: the 22 historical-migration tests are DELETED)

Taken after being shown the option space with the measured facts, not a recommendation alone.  The
question: migration `a3f7c8e21b64` drops `budget.transactions.paid_at`, and 22 of the 29 tests in
`test_posting_cash_backfill.py` / `test_posting_ledger_backfill.py` invoke the FROZEN raw SQL of
`7d63529e4300` / `db239773c2fd`, which read that column.

**MEASURED before the ruling, and these facts carried it:** a real `base -> head` upgrade is
unaffected -- those migrations run at their own point in the chain, ~27 revisions before the drop;
on any database already past them they never run again; and `a3f7c8e21b64`'s downgrade REFUSES, so
Alembic cannot rewind past the drop either.  **The backfill's data path is unreachable with data,
permanently.**  The only future execution is a fresh template build, over an empty table, where the
migration returns early.

**RULED: delete the 22, keep the 7 that never reach the frozen SQL, and record in each migration's
own docstring that its data path became unreachable and why.**  The alternative offered was an
autouse fixture that re-adds the dropped column and feeds the SQL synthetic instants; it was
declined.  A third option (assert the SQL at source level) was declined as weaker than what it
replaced -- this document already rules a static check weaker than an executed one.

**Two corrections to how this was justified, both from the review that followed.**  The deletion
DID cost coverage and the first draft implied it did not: the executed-downgrade case, the
zero-effective-transfer exclusion, and the two `BackfillAndGoForwardAgree` cases (found afterwards
in the live oracles, same class, also deleted) have no survivors -- the last were the only place an
INDEPENDENT implementation of the sign / amount / date rules was compared against the go-forward
builder.  And the stated reason "keeping them needs a schema that is not the application's" was
FALSE: `alembic upgrade base -> <that revision>` gives exactly that schema.  The true reason is that
the harness has no per-revision template fixture.  Both corrections are written into the modules and
migrations themselves, where the next reader of that SQL will be.

### Answered (developer ruling, 2026-08-03: X-f1c's four forks -- and the third is the ROOT CAUSE option)

Taken after being shown each fork with its measured facts and a stated recommendation, under the
standing instruction *"I want root cause solutions only"* / *"I want to make the fences structurally
unnecessary."*  Three of the four were opened by MEASUREMENT during the build trace, not by the plan:
X-f1c as scoped would have closed **N-181** on zero rows, broken the only unlock path on every
settled row, and shipped a stale cache the moment its own new field was used.

**R-EF -- the settle-day door ships on BOTH forms, because the transaction door alone corrects
NOTHING.**  *"Both doors."*

The plan step says *"`settled_on` on the full-edit form"* and names X-f1c as the door that corrects
finding **N-181**'s eight fabricated timeliness days.  Measured on the production database
2026-08-03: those eight rows are transactions **1457, 1458, 1823, 1824, 1826, 1827, 2161, 2162** --
**all eight are transfer SHADOWS** (four pairs, transfers 54 / 154 / 155 / 322).  A shadow never
renders the transaction popover: `routes/transactions/forms.py:70-97` redirects it to
`transfers/_transfer_full_edit.html`, which PATCHes `transfers.update_transfer`.  So the transaction
door alone corrects **0 of 8**, and the claim that X-f1c IS N-181's door would have been false.
Separately, `_transfer_status.apply_settle_day_correction` -- built at X-f1b -- had **no production
caller at all**; `update_transfer`'s `settled_on` kwarg was reachable only from tests.  This ruling
gives it its first one.  *Rejected:* the transaction door alone (closes N-181 on no rows); the
transfer door alone (leaves the other 148 settled rows uncorrectable, which is the main case R-ED
was ruled for).

**R-EG -- a settle day submitted alongside a REVERT is dropped, not refused.**  *"Revert wins;
ignore the day."*

Both full-edit forms submit every enabled field on Save, and the documented way to unlock a finalised
row is to set Status to Projected **in that same form** -- so a revert arrives as
`{status_id: Projected, settled_on: <the day the row already carried>}`.  The seam's
`reject_settle_day_without_settled_status` refuses that pair with a 400, and
`_transfer_status.apply_settle_day_correction:215-219` does the same, so shipping the field without
this ruling would have made **the only unlock path fail on every settled row** -- a defect the step
would have introduced, not found.

The day is a stale ECHO of the state being left: the user picked Projected, which says the money did
not move.  The rule is `status_seam.settle_day_for_status`, ONE function both route doors consume,
placed beside the refusal it defers to for the same reason
`reject_settle_day_without_settled_status` is module-level -- a caller has to ask before the seam
can.  **It does NOT weaken the service guard**: a day handed to `apply_status_change` for an
unsettled status still raises, because a SERVICE caller asserting both facts is asserting them on
purpose.  Forgiving at the form door, fail-loud at the service door.  *Rejected:* the 400 (breaks
the unlock path); a JavaScript rule that blanks the input on status change (a financial invariant in
a place a disabled script or a crafted POST bypasses, and the server still needs one of the other
two answers underneath).

**R-EH -- `accounts.current_anchor_balance` and `current_anchor_period_id` are DELETED.**  *"Delete
the two columns now, inside X-f1c."*

R-EE gives the true-up form a statement date, which makes a BACK-DATED assertion reachable.  Those
two columns are a denormalized cache of the LATEST `account_anchor_history` row --
`cash_ledger/_facts.py:116-118` says so in those words, and `resolve_anchor` logs
`EVT_ANCHOR_CACHE_RECONCILED` when they disagree and lets the history row win.  `stage_anchor_true_up`
writes the cache UNCONDITIONALLY, so a back-dated true-up would leave it holding a balance the ledger
does not consider current.  **Measured: Checking carries 55 assertions from 2026-03-27 to 2026-08-03
and the schedule floor is 2026-03-26**, so essentially ANY back-dated statement entry on the account
the developer budgets from lands before the latest assertion.  The grid header, the dashboard card,
the cockpit cards, the Property market value and the retirement seeds all read that cache, so the
wrong number would RENDER while the projection used the right one.

The developer took the root-cause option over the offered guard.  *Rejected:* writing the cache only
when the new assertion is the latest (correct, ~5 lines, but it maintains a denormalization this
arc's own root cause 1 names); and bounding the new field so it cannot precede the newest assertion
(throws away the correction the field exists for -- a statement that arrives late for a superseded
day could never be recorded).

**The measured cost, stated because the ruling was taken before all of it was known and the
question's cost estimate UNDERSTATED it.**  Re-measured 2026-08-04 by
`grep -rln 'current_anchor_balance\|current_anchor_period_id'`: **24 `app/**/*.py` files** (not the
12 first stated -- that number was wrong, and it is the sizing the ruling was taken against) and
**5 templates**, of which **3 carry a real read** (`accounts/form.html:49`, `loan/setup.html:38`,
`grid/_anchor_edit.html:56,90,92`) and 2 mention the column only in comments
(`savings/dashboard.html`, `savings/_cockpit_balance.html`).  The file count includes docstring
mentions, so the true read-SITE count is lower than 24; the MODULE count is the one that was
understated.  Two sites are not merely reads:

* `pay_period_admin._period_ids_that_are_account_anchors` uses the COLUMN as the anchor lock that
  refuses deleting a pay period, backed by a deferrable `ON DELETE NO ACTION` FK.  Its replacement
  is a query over `account_anchor_history.pay_period_id` -- whose FK is `ON DELETE CASCADE`, so a
  deleted period would take balance ASSERTIONS with it.  The replacement set is a strict SUPERSET of
  today's (it locks any period holding an assertion, not only the latest), which is more correct,
  but it is a behaviour change to a destructive operation and needs its own gate.
* `retirement_projection` and `investment_dashboard_service` read the column as a FALLBACK for an
  account the seam omitted -- a state `current_anchor_period_id IS NULL` produces, which the schema
  forbids.  Those fallbacks are dead defence and go with the column; the ones that are NOT dead
  (the no-current-period arms in `dashboard_service`, `_projections`, `retirement_projection`) each
  need a stated replacement rather than a mechanical substitution.

**It is therefore its own leaf and its own commit(s), and it runs BEFORE R-EE's statement date** --
so back-dating never lands on a codebase that still has a cache to stale, and so a settle-day defect
and an anchor-cache defect cannot arrive in one unrevertable change.

**R-EJ -- a settle day in the FUTURE is refused, at the seam.**  *"Refuse it at the seam."*

A hole X-f1c OPENS, found by its own build trace rather than by a review, and reachable by an
ordinary user typing in the new box -- not only by a crafted POST.  Worked on a `$120` expense
settled today against a `$1,000` checking balance: correcting its day to a month out leaves the row
Paid and re-dates its ledger entry, and `cash_ledger/_walk.py:290-300` absorbs a settled source only
into an assertion dated **on or after** it -- so the `$120` rides on top of every assertion until
that day arrives.  **The grid would show `$1,000` today and `$880` a month from now: money already
spent, still sitting in the balance.**

**A neutral adversarial review found the same defect INDEPENDENTLY and measured it end to end
through the live routes**, which is the strongest form this record takes -- two derivations, one
number.  On a `$1,000` anchor: a `$100` expense settled three days ago reads `$900`; PATCH its day to
`today + 400` and the route answers **200** and the balance reads **`$1,000`** again.  The transfer
side is identical at `$200`.  The review also named the trap the tooltip sets: the box says
*"Correct it if your statement shows a different day"*, and **a statement's most common disagreement
is a PENDING item with a future posting date** -- so the single most likely thing a user types here
is the thing that silently inflates their balance.

The rule lives on `status_seam.apply_status_change`, beside the `datetime` refusal, for the reason
that one is there: ONE door, every write path, a type of value the seam does not accept rather than
a check each caller remembers.  Both date inputs also carry `max` = today, so the browser refuses
first and the server is the backstop -- the layering `accounts/form.html` already uses for
`observed_on`.

*The precedent that was weighed and NOT followed, and the review sharpened WHY.*
`TransactionEntry.settled_on` -- a purchase's posting day, the closest analogue -- deliberately
carries no `max` (`_transaction_entries.html`: *"I bought this and my bank has not taken it yet is a
true statement"*) and `entry_service` bounds it only from below.  The difference is not merely that
one is status-bound: **the two point in opposite directions.**  A future ENTRY posting day is the
CONSERVATIVE side -- `ReconciledThrough.covers` answers False, so the debit stays reserved and the
balance stays low.  A future `Transaction.settled_on` is the other side: it takes settled money OUT
of the balance.  The entries door's rationale therefore does not carry over and must not be cited
here.  `Transaction`'s own class docstring already specified the rule: *"a 'not in the future' rule
is not expressible in a CHECK (it is not immutable) and lives at the write door instead."*  X-f1c is
that write door, and it is the first one where a USER supplies the day.

*Rejected:* a browser `max` with no server rule (leaves the balance error reachable by the replayed
/ stale-page / second-tab class this arc has twice ruled a live defect -- N-146 and N-178); allowing
it to match the entries door; and adding a PAST floor as well, which is more guard than the measured
defect -- a very old day rewrites the opening assertion's correction, which is strange but not wrong.

**R-EI -- the true-up editor's statement date goes on a SECOND LINE.**  *"Second line inside the
form."*

`grid/_anchor_edit.html` is ONE partial rendered on five surfaces (grid header, dashboard balance
card, each cockpit card's balance cell, the investment/retirement hero, the cash hero), currently a
compact `d-inline-flex` row.  A labelled "as of" date input on its own line below the amount leaves
the amount row's width untouched, so the cockpit card and the mobile grid header cannot reflow.
*Rejected:* inline beside the amount (roughly doubles the row's width on the tightest surface); and
hiding it behind an "as of today (change)" link (preserves one-click, but costs discoverability on
exactly the case the ruling exists to serve).

### Answered (developer ruling, 2026-08-04: N-186's question -- the two producers key on DIFFERENT facts, and the fix is its own step)

**R-EK -- the resolver's history/projection cut is a PROXY for the day the money moved, and
replacing it is X-an, not X-f1.**  *"Its own step, right after X-f1."*

N-186 asked what the parallel run should assert for a payment whose cash moved in an earlier period
than its installment, and forbade re-dating the fixture until that was answered.  The trace answered
a question underneath it: the two producers do not merely SAMPLE differently, they key on different
facts, and one of them is a proxy.

* The posted ledger dates a payment's journal entry by **the day the money moved** --
  `posting_service._entry_date` (`posting_service.py:256`) reads the income shadow's `settled_on`.
* The resolver's replay/projection cut keys on the payment's **pay-period start** --
  `is_confirmed_payment_eligible`'s `period_start <= as_of` (`rate_period_engine.py:395`), with
  `_build_monthly_override`'s complement on the same field (`_payoff.py:174`) so the two partitions
  stay exact.

A pay period is a BUDGETING fact -- which paycheck funds the outlay -- and the lender never sees it.
When the cash moves before its pay period begins, the ledger has booked the payment while the
resolver still calls it a projection, so **the same installment is counted twice**: once as
confirmed history from the ledger, once as a forward planned outlay from the override map.

**REPRODUCED 2026-08-04 with a CONTROL that moves ONLY the pay period the payment is filed in** --
same amount, same cash day, same installment, same posted split -- which is what makes this a
producer defect rather than a fixture artifact.  *What was compared is the ledger's own balance at
today (equal in both columns), not the two ledgers row by row; an earlier draft said
"byte-identical", which asserts a whole-ledger diff that was never run.*  Loan $100,000
@ 6%, today frozen 2026-02-10, the 2026-04-01 installment paid on 2026-02-10:

| | pay period AFTER the cash day | control: pay period CONTAINS the cash day |
|---|---|---|
| ledger balance at today | `97,997.25` | `97,997.25` |
| resolver balance at today | **`99,001.12`** | `97,997.25` |
| schedule rows | 02-01 conf, 04-01 conf, 03-01 proj, **04-01 proj** | 02-01 conf, 04-01 conf, 05-01 proj |
| last schedule row | `2032-10-01` | `2032-12-01` |

The producers differ by exactly **one payment's principal, `$1,003.87`**, and installment 2026-04-01
renders as both confirmed history and a future payment.

**What it does NOT touch, MEASURED rather than assumed** -- the first draft of this ruling asserted
a persisted cash-flow consequence and the control refuted it.  The grid's per-period loan balance map
is correct (it folds `balance_at._plan`'s materialized shadows, not the resolver's projected rows);
`loan_figures.payoff_date` is `2032-11-01` in BOTH columns; and therefore the recurrence end date
`loan_recurrence_sync` WRITES (`loan_recurrence_sync.py:284`, derived at `:277-278`) is unaffected.
**Only the middle one of those three is a measured number**; the first is a structural read of
`positions()` (its future half folds `memoized_plan`, built from `loan_loaders.projected_income_shadows`
-- PROJECTED shadows only, so a confirmed early settle cannot enter it) and the third is an inference
from the second.  Both were re-derived independently by a neutral review and hold; the word
"measured" applied to all three, and does not.  The damage is confined
to `LoanState.schedule` and the payoff scenarios sharing `_build_forward_inputs`: the loan detail
page's amortization table, its payoff / interest summary, and its band chart.

**It PREDATES X-f1 and X-f1 explicitly scoped it out.**  `git show
fac90200:app/services/posting_service.py` shows `_entry_date` already returning
`_civil_settle_date(paid_at, xfer.pay_period)`, so marking a future-period loan payment paid has
always produced this; R-EJ's write-door guard did not create it, it merely stopped the fixture from
HIDING it behind a settle dated in its own future.  And this document's own scope sentence for X-f1
reads *"It gives a settle a day the user owns; what that day is compared against is unchanged"* --
which is precisely what X-an changes.

**Reachability: `$0.00` on today's production data, one ordinary click away.**  Measured read-only on
`shekel-prod-db` 2026-08-04, counting settled rows that CARRY an instant (`paid_at IS NOT NULL`;
the denominator matters and an earlier draft left it unstated):

| account type | settled | undated | with instant | early | late |
|---|---|---|---|---|---|
| Auto Loan | 4 | 1 | 3 | 0 | 0 |
| Mortgage | 5 | 1 | 4 | 0 | 0 |
| Checking | 139 | 4 | 135 | **9** | **10** |
| Money Market | 7 | 2 | 5 | **2** | 0 |
| HYSA | 1 | 0 | 1 | 0 | 0 |

So **0 of 7** loan payments carry the shape and **19 of 135** Checking rows do -- **and 2 of 5 Money
Market rows do, so this is not a Checking habit**, which the first draft's framing implied.  The
excluded undated rows are N-181's; the X-f1b backfill dates them to their own period's `start_date`,
which is inside the period by construction, so the loan side is 0 of 9 either way.

Only the EARLY direction is harmful -- a late settle's divergence window `[period_start, settle_day)`
is already in the past at any render, while an early settle's window `[settle_day, period_start)`
CONTAINS today until the pay period begins.  Rule 7 applies verbatim: a finding that costs `$0.00`
today is a defect waiting for the data to change.

*Rejected:* **fixing it inside X-f1** (correct, and declined -- it grows a step already carrying a
destructive migration and two edit doors into the loan resolver, which is the mixing this arc splits
commits to avoid); and **accepting two bases and narrowing the parallel run to a window** (declined
as the band-aid it is -- it would ship the duplicated installment knowingly and blind the oracle in
exactly the region where dating bugs live).

**What X-f1c does instead, and the coverage it honestly loses.**  The fixture is re-pointed to the
early settle the app actually produces and both producers can see: cash out 2026-01-30, booked in the
pay period CONTAINING that day, paying the 2026-03-01 installment -- early against the INSTALLMENT,
which is what a borrower means by paying early.  **Non-vacuity PROVEN by mutation**: dating the split
correction at the due date instead of the settle day (the pre-R1 shape) makes it fail loud with
`walk 1003.87 vs posted 1498.88` at the settle day and a stranded `-495.01` at 2026-03-01.  What is
genuinely no longer covered is the cash-before-its-own-pay-period case, and that is N-187's, owned by
X-an -- recorded rather than left implicit, because a re-pointed fixture that quietly drops a case is
how N-182 happened.

### Answered (developer ruling, 2026-08-04: X-f1c's three adversarial reviews -- and the settle-day bound was half a rule)

Three neutral reviews ran against the finished tree before the commit: one on the app code, one on
test integrity, one auditing every factual claim in this document's own new prose.  **All three found
something, and the app-code review found a live defect the step itself opened.**

**R-EL -- a settle day BELOW the schedule is refused, at the same seam and by the same bound an
anchor observation already uses.**  *"Floor at earliest pay period."*

R-EJ bounded the correction box ABOVE and stated its money consequence carefully.  **Nothing bounded
it below**, and the review showed the other direction moves the same money by the mirror mechanism:
`cash_ledger/_walk.py:291-300` ABSORBS a source dated at or before an assertion into it and then
executes `running = anchor.anchor_balance`, discarding the row's delta -- so the projection RISES by
the row's amount while the row still reads Paid on the grid.  `fields.Date()` deserializes
`"0202-08-04"` to a real `date`, so the input is an ordinary year typo, not a crafted request.
Worked on a `$1,000` anchor observed three days ago carrying a `$100` settled expense: the grid reads
`$900`, and after the typo it reads `$1,000` with the `$100` becoming an unexplained plug against
Uncategorized at the next anchor re-derive.

**The floor is not a new rule and that is the point.**
`account_service.earliest_observable_day` -- `min(the user's earliest pay period start, today)` --
has enforced exactly this bound on an anchor's `observed_on` since finding **N-133**, for the same
reason.  It MOVES to `pay_period_service.earliest_recordable_day` (a leaf module the caller can
import without inverting any dependency rule) and both doors now call the one function; the
account-facing name stays as a delegating alias so its callers are untouched.  Both settle-day inputs
carry `min` = that day, the layering `accounts/form.html` already uses.

**Measured on the production database before the ruling**, over the 148 settled rows carrying an
instant: the floor would have refused **0** of them (oldest settle day `2026-03-27`, schedule floor
`2026-03-26`).  *Rejected:* the account's earliest ASSERTION, which is tighter and sits exactly on the
absorb boundary but would have refused **4 of 148** -- a shape the developer's own data proves
legitimate; and leaving it to its own step, which R-EJ's own precedent forbids (a hole X-f1c OPENS is
closed inside X-f1c).  **Stated so it is not read as more than it is: an in-range typo stays
expressible and nothing can prevent that.**  What the floor removes is the catastrophic class.

**R-EL WAS RULED TWICE, and the first placement was WRONG -- the suite proved it.**  Built at the
SEAM beside the future-day ceiling, it broke **six** existing tests whose scenario is a loan payment
budgeted to a 2026 pay period whose cash moved on 2025-12-20 or 2025-12-31: the year-boundary
attribution rule (L9), with the seeded schedule starting 2026-01-02.  **That shape is legitimate**,
and two things were wrong with the case put to the developer for the first ruling, both stated here
because the correction is the finding:

* **the measurement covered only EXISTING rows** (0 of 148 refused), not the shapes the app must
  still permit -- recording money that moved before you started budgeting is one, and step **X-f6**'s
  bank import produces it in bulk; and
* **the absorb behaviour is CORRECT for a genuine pre-schedule settle**, by ruling R-EB's own model:
  an assertion RECONCILES, so anything dated before it is already inside the asserted balance.  The
  floor was therefore never protecting an invariant -- it exists solely to catch a typo, and at the
  seam it did that by refusing a shape that is sometimes real.

**Re-ruled: the floor is a DOOR rule.**  It moves into `status_seam.settle_day_for_status` -- which
this same step already established as the edit doors' rule, and which all three HTTP doors call --
so a crafted or stale POST is bounded while service, import and backfill callers are not.  The
CEILING stays at the seam and the asymmetry is principled: no caller can legitimately record money
that has not moved, but plenty can legitimately record money that moved before the schedule began.
Only a FORWARDED day is bounded; a day dropped beside a revert writes nothing, so bounding it would
break the unlock path R-EG exists to keep open.  All six tests pass untouched, which is the point --
they call the service.  `TestTheSettleDayFloor.test_the_service_itself_still_accepts_a_pre_schedule_day`
fails if the bound ever drifts back to the seam.

**What the other two reviews found, all repaired in this pass.**  The test-integrity review found
**two tests that could not fail** and proved one by running it: `TestTransferSettleDay` hard-coded
`date(2026, 7, 14)` as a settle day in a module with no clock freeze, so R-EJ's brand-new guard made
it fail under `SHEKEL_FAKE_TODAY=2026-06-01` -- the N-131 / N-132 / R8 fixture-clock class, armed by
this step's own guard and invisible until the weekly sweep ran AFTER merge.  The other asserted the
correction input was pre-filled by testing `txn.settled_on.isoformat() in body`, on a row settled
TODAY, against a template that also renders `max="<today>"` -- the same string twice, so `value=""`
passed it.  Both repaired and both re-proved by mutation.  It also found a dead `if submitted_day is
None` branch in `settle_day_for_status` (finding **N-184**'s shape re-introduced one file over,
deleted), an asymmetric ledger assertion whose sibling's docstring claimed the asymmetry was already
fixed, two untested render rules -- including the undated-settled-transfer repair box that is the
whole reason the transfer door exists -- a duplicate test now grading the door/service
COMPLEMENTARITY across every status instead, and two docstring overclaims.

The claims audit checked every citation in this document's new prose against the code, the git
history and the production database.  **The measurements held exactly** (0 of 7 loan payments, 19 of
135 Checking, N-181's eight row ids, the `ON DELETE CASCADE` FK, `git show fac90200`'s `paid_at`
derivation).  **Two claims were false and are corrected in place**: this document said "nothing
pushed" when `git ls-remote` answers `578a9136` and a `[WIP -- RED, NOT FOR MERGE]` commit is already
published, and the N-186 row's owner `closed (X-f1c)` is outside Section 9 rule 6's vocabulary --
which the rule's OWN GATE caught (`tools/plan_gate`, 16/17) once it was finally run.  **That gate is
not in `tests/`, so `./scripts/test.sh` never ran it and a green suite hid the violation.**  Four
overclaims were narrowed ("byte-identical", "MEASURED not assumed" over two inferences, an unstated
denominator, "the two 2099 suites" where there is one) and four stale citations re-pointed.

### Answered (developer ruling, 2026-08-04: X-f1c3's four forks -- and the third DELETES a column the code's own docstring says nothing reads)

Taken after being shown each fork with its measured facts and a stated recommendation, under the
standing instruction *"root cause solutions only"* / *"I want to make the fences structurally
unnecessary"* -- and, on the third, *"I feel like at least part of the restructure keeps getting
pushed off."*  That fork was put as a choice between guarding the column and retargeting its FK;
the developer refused both and asked for the from-scratch answer instead, which is what widened it.

**The substitution moves no figure, and that is measured before anything else.**  Read-only on
`shekel-prod-db` 2026-08-04: **9 accounts, 78 assertions, and `accounts.current_anchor_*` agrees
with the latest `account_anchor_history` row on every single one -- 0 divergences.**  So every read
re-pointed below returns the number it returns today, and `verify_balance_baseline.py` is a real
control on this step rather than a formality.

**R-EM -- the four "no current period" fallbacks ASK THE SEAM, they do not read a stored balance.**
*"Ask the seam for today's balance."*

Four producers answer "what is this account's balance" with the raw last-asserted figure when no pay
period contains today: `dashboard_service.py:115` (the dashboard hero), `_projections.py:168` (the
/savings tile), `retirement_projection.py:604` (the retirement table) and
`investment_dashboard_service/_context.py:201` (the investment hero).  Four hand-written copies of
one rule, each rendering a stored assertion under a label that says *current*.  They now read
`balance_at` at `ctx.as_of`: **the seam answers a DATE and has never needed a period to do it**, so
the condition that produced the fallback does not produce a missing answer.  That is ruling R-CA's
own argument (a raw cache column presented as a current balance is "a wrong figure wearing a
plausible shape") applied to the four sites it had not reached.

*Rejected:* substituting the resolver at all four (byte-identical and safe, and it preserves four
copies of a rule plus a second answer to the balance question on four screens); and splitting the
rule, seam for the three heroes and stored for /savings, which is the shape this arc keeps finding.

**R-EN -- the C-17 optimistic lock leaves the cash true-up, because the true-up stops writing the
row it locked.**  *"Delete the lock from the true-up path."*

**Measured, not reasoned** (probe run against the dev database inside `shekel-dev-app`, rolled
back): on account 1 at `version_id=33`, adding an `AccountAnchorHistory` row and flushing leaves it
at **33**; the very next line writes `current_anchor_balance` and the same flush takes it to **34**.
SQLAlchemy's `version_id_col` increments on an UPDATE of the versioned row and on nothing else, so
once `stage_anchor_true_up` only INSERTs, `StaleDataError` is structurally unreachable on this path
and `AnchorTrueUpOutcome.STALE_CONFLICT` is dead code.

The step does not work around that -- it accepts what it means.  **An assertion history is
append-only, so a second tab overwrites nothing**: two assertions of different balances are two
facts, the later-observed one is current, and neither is lost.  That is verbatim the contract
`apply_loan_anchor_true_up` has documented since Commit 16 (*"the last writer's row wins on display
while neither is lost"*), and this ruling makes the cash half the same shape rather than the
exception.  Same-balance double-submits are still idempotent -- the F-103 unique index is what
catches those, and it is untouched.  Deleted with it: `STALE_CONFLICT`, `_anchor_conflict_response`,
the conflict cell branch in `grid/_anchor_edit.html`, the form's `version_id` hidden input and the
`revert_context` plumbing that existed to route a 409 back to its opener.  **The full account edit
form keeps its own version check** -- it writes real columns, so its lock still has a row to guard.

*Rejected:* bumping `version_id` explicitly so the lock survives -- a write to `accounts` whose only
purpose is to keep a lock alive, which is a mechanism with no fact under it; and keeping the
stale-FORM check alone, which would then fire only when some unrelated edit touched the account
while telling the user their balance edit collided.

> **THIS RULING'S RATIONALE WAS PARTLY WRONG AND IS CORRECTED HERE, 2026-08-04 (finding N-190).**
> The ruling STANDS -- the lock really is unreachable, `STALE_CONFLICT` really is dead code, and
> a second tab really does overwrite no ASSERTION.  What was false is the scope of the sentence
> *"a second tab overwrites nothing"*: **append-only is a property of ONE table in a transaction
> that mutates three.**  `apply_anchor_true_up` also runs
> `sync_account_anchor_postings_all_scenarios`, a reconcile-to-target -- read what is posted,
> subtract it from the walked target, INSERT the difference -- and that is a read-modify-write with
> no unique index behind it.  The deleted `version_id` UPDATE had been serialising it BY ACCIDENT,
> because it autoflushed and took a row lock before the walk.  Deleting the column deleted the
> accident.
>
> Reproduced on an account reconciled at `$4,000.00`: two concurrent true-ups both answer 200, both
> assertions survive, and the linked ledger settles at `$1,000.00` against a resolved `$2,000.00` --
> trial balance still `$0.00`, because the anchor-equity leg carries the mirror-image error.
>
> **And the precedent this ruling cited carried the same defect.** `apply_loan_anchor_true_up` is
> fed by an append-only event table and UPDATEs no row, so the LOAN reconcile never had even the
> accident: it has been unserialised since Commit 16.  Citing an append-only contract as evidence
> that a reconcile is safe is the mistake, and it was made twice.
>
> The replacement is ONE per-user advisory lock taken INSIDE the reconcile
> (:mod:`app.services.user_write_lock`), covering both ledgers and all four entry points --
> see N-190 for the ruling on its scope and the reasoning behind per-USER rather than per-account.
> **The rule that survives: an append-only TABLE never licenses an unserialised read-modify-write
> in the same transaction.  Name the tables the transaction writes, not the one the ruling is
> about.**

**R-EO -- `account_anchor_history.pay_period_id` is DELETED.  An assertion is (account, day,
balance) and nothing else.**  *"Delete the column."*

**A balance assertion is a fact about a bank.**  "On day D, account A held $B" is true regardless of
how the user's paychecks are scheduled.  Filing it under a pay period is filing a bank fact under a
budgeting artifact -- and the column is `ON DELETE CASCADE`, so the budgeting artifact can destroy
the bank fact.  That, and not the FK's action, is the defect.

**The column has no reader, and the codebase already said so.**
`cash_ledger/_events.py:132-134` states it in those words -- *"It is a CACHE of a derivation, not an
independent fact, and no reader of THIS FIELD survives in `app/` as of plan step X-ai-r"* -- which is
finding **N-169**'s second half.  Re-verified here, and the one exception it names is exactly what
X-f1c3 removes: `_facts.py:211` reads it only to compare against the cache column this step deletes.
`AnchorPoint.period` has **zero readers in `app/`** (only `.balance` and `.observed_on` are read),
and `account_posting_service/_anchors.py:234` refuses the column BY NAME and derives from
`observed_on` instead, because projecting it *"put the posted ledger at odds with the grid's Book vs
bank row by the whole correction"*.

**And it is already WRONG on production data**: 2 of 78 assertions carry a period their own
`observed_on` falls outside -- row 45 (account 8, day `2026-05-21`, filed in the `2026-03-26` ..
`2026-04-08` period) and row 50 (account 1, day `2026-06-03`, filed in the `2026-06-04` ..
`2026-06-17` period).  Those are finding **N-168**'s two rows, measured live.  Deleting the column
deletes the defect class; retargeting its FK would have carefully preserved two self-contradictory
rows.

**What it removes is much larger than the column, and this is the part that answers "the restructure
keeps getting pushed off".**

* **`reset_pay_periods` currently destroys the user's assertion history and fabricates a
  replacement.**  It wipes every period, the CASCADE takes all 78 assertions with them, and
  `_reanchor_accounts` writes 9 synthetic rows labelled `"origination (pay-period reset)"` carrying
  only the last balance -- **69 real observations of what the bank said, deleted to satisfy an FK**.
  With no FK the assertions simply survive, and `resync_user_account_anchor_postings` re-derives
  their corrections onto the rebuilt schedule.  (Not reachable on today's data: 158 settled
  transactions block the reset.  Reachable for any user who resets before settling anything, and
  rule 7 applies verbatim.)  `_reanchor_accounts`, `preserved_balances`, `_DEFER_ANCHOR_FK_SQL`,
  the `SET CONSTRAINTS` deferral and the whole deferrable-FK apparatus go with it.
* **`PeriodLockReason.ACCOUNT_ANCHOR` becomes unreachable by construction** and is deleted with
  `_period_ids_that_are_account_anchors`.  Assertions no longer live in periods, so a period delete
  cannot take one; what remains to protect is the period's POSTED state, and `LEDGER_POSTINGS`
  already outranks `ACCOUNT_ANCHOR` in `_resolve_lock`'s precedence.  Measured: **all 10 periods
  holding an assertion carry an unbalanced ledger account, so all 10 are already locked by the
  higher-precedence reason** -- the lock being deleted is refusing nothing today that survives
  without it.
* **`account_service.resolve_anchor_period_id` becomes CALLERLESS and is deleted.**  Its only two
  production callers are `create_account:345` (which needed a period for the row and the cache
  column, both gone) and `_reanchor_accounts:789` (deleted above).  **That closes finding N-170
  structurally**: the app carried TWO day-to-period derivations that disagree -- the writer's
  "containing, else EARLIEST" against the ledger's "containing, else LATEST ending before" -- and
  deleting the writer's leaves ONE, which is what N-170's own recorded candidate resolution asks
  for.  `AccountSpec.anchor_period_id` goes with it.
* **A true-up no longer needs a pay period at all**, so `_true_up_request_gates`' `"No current pay
  period found"` 400 and `update_account`'s `if current_period:` fork both have nothing left to
  decide.  **That is finding N-134 closed structurally, at X-f1c3 rather than X-f1c4** -- earlier
  than this document scheduled it, because the column drop forces the arm that writes a balance with
  no history row to stop existing.

The F-103 unique index re-keys from `(account_id, pay_period_id, anchor_balance, observed_on)` to
`(account_id, anchor_balance, observed_on)`.  That is STRICTLY tighter and **rejects 0 of the 78
existing rows** (measured): the period was derived from the day, so two rows sharing a day shared a
period except across a schedule rebuild -- the one case the tighter key now also catches.

*Rejected:* retargeting the FK to `NO ACTION` (the recommendation the fork was written around -- it
adds a migration and a deferral dance to protect a column nothing reads and which is already wrong
on 2 rows, and leaves the reset still destroying 69 assertions); leaving it `CASCADE` with the app
lock as sole guard (ships the data-loss path and makes a routable check the only thing in front of
it); and giving it its own step behind X-f1c3, which was offered and declined because X-f1c3 would
then have to ship an interim anchor lock and an interim reset path for the next step to delete.

**`RESTRICT` was considered and is wrong**, and the mechanism is on the record one table over:
`models/transfer.py:108-120` (F-136 / C-43) documents that PostgreSQL evaluates every referential
action for a single DELETE in one pass, so a user-cascade would raise on a `RESTRICT` even though
every row is destined for deletion.  *That comment's worked example is `transfers`, not
`account_anchor_history`* -- the mechanism carries, the example is this document's inference and is
labelled as one.  The question is moot here only because the FK is being deleted outright; it is recorded
so a future reader does not "fix" the FK back into existence with the wrong action.

**R-EP -- "as of when was this balance asserted" gets ONE source: the assertion's own
`observed_on`.**  *"One source: the assertion's observed_on."*

Three surfaces, three answers.  The grid header renders `account.updated_at` (`grid/grid.html:14`,
and the OOB snippet `_true_up_success_response` swaps in at `anchor.py:355`) -- an instant that moves
on ANY account edit and has nothing to do with a balance.  The account-detail and investment heroes
render `AnchorPoint.observed_on`, which is correct.  The dashboard pulse renders
`dashboard_service._get_last_anchor_date`, a THIRD statement of "which assertion is latest" that
orders by `created_at` alone -- so it names a different row than the resolver's
`(observed_on, created_at, id)` the moment a back-dated assertion exists, which is what X-f1c4 makes
routine.

**This step forces the first one**: with no column write, `updated_at` freezes and the grid caption
stops moving on a true-up.  All three go to `observed_on`, and `_get_last_anchor_date` is deleted --
the third "latest assertion" ordering with it.  `dashboard_pulse_service._anchor_day`'s own docstring
already named this the source it wanted and named the reason it had not moved (*"changing
`dashboard_service._get_last_anchor_date`'s contract, which has callers beyond this module"*); this
ruling is that change.

*Rejected:* repairing only what the step breaks and recording the pulse for later -- it would ship a
known-divergent third rule into the release that makes it reachable; and moving the pulse onto the
resolver's ordering while keeping it on the RECORDING day, which keeps two clocks on one caption for
a distinction ("when you last checked" vs "when it was true") that `observed_on` already expresses.

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
**X-ad -> X-x -> X-y -> X-i -> X-j -> X-k -> X-l -> X-m -> X-n -> X-d -> X-e -> X-f -> X-p -> X-ab ->
X-ac ->
E2** (X-g4a, X-g4b, X-o, all three X-q leaves, X-r, X-h, X-s, X-t, X-u, X-v, X-w, X-aa and X-z, which
used to open this line, have SHIPPED; **X-v** and **X-w** were appended 2026-07-28 out of X-t's own measurement and its
two adversarial reviews, **X-x** the same day out of X-v's own AST census, which measured the
period-absence family at 63 branches, **X-y** on 2026-07-29 out of X-v's two adversarial
reviews, which found fifteen surfaces answering this state without the seam, and **X-z** on
2026-07-30 out of X-w's own trace, which found the liability rule written twice; **X-ab** and
**X-ac** were appended 2026-07-31 out of X-z's two adversarial reviews, which found the
asset-vs-liability rule alive on the WRITE path and a finding cited in code but never filed;
**X-ad** the same day out of X-x's own trace, which measured the calendar WRITER refusing 13 of the
13 of the 14 paydays after today that a new user can enter, and leaving a permanent hole on
every later one -- all
seven are rule 7's "its own step, never a deferral") (X-h .. X-k added 2026-07-27 by ruling R-AO, X-l .. X-p the same day by R-AQ,
X-q / X-r the same day by R-AV / R-AW out of X-o's trace, and X-s the same day out of X-q2's two
adversarial reviews -- sequenced after X-h on X-h's own ground, since the controls X-h repairs are
what grade a step that moves a client payload and a rendered caption; they
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
| pre-anchor | the scalar FABRICATES `$2,932.41` for 2026-06-03; the map has **no entry at all** for the same 8 periods. **The same figure came back through a different door and X-x's trace found it** (2026-07-31): on a calendar with no period covering today, `/savings` renders `$2,932.41` for Checking again -- not from the scalar, which is deleted, but from `Account.current_anchor_balance`, which is the column that figure was always a copy of |
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

- [x] **X-o** `fix(savings): the debt-line question uses the debt-line predicate` -- **SHIPPED
  dev 2026-07-27 (`68c22fa0`)**, closing **B-16**. **Sequenced FIRST of the new steps because it is a LIVE defect on a rendered screen and
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

- [x] **X-q** `fix(savings): one debt-free date, one derivation` -- **DONE**, in the three commits
  its leaves took (`3b7823e1` / `bad97e6a` / `be6cfae6`; rule 6's convention for a decomposed step).
  Closes **N-98**, **N-99** and
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
  any code, on the X-g / X-g4 precedent. **RULED 2026-07-27 (R-AX, as recommended): the derivation
  stays loan-only and the SURFACES say so**, which is plan step X-q3 below.

  **Decomposed on the arc's own line, because one half moves a rendered figure and the other cannot:**

  * [x] **X-q1 THE DERIVATION** -- **SHIPPED dev 2026-07-27 (`3b7823e1`)**: one debt-free outlook
    (`_debt_line.loan_payoff_outlook`, a three-state `LoanPayoffOutlook`), both surfaces read it.
    The not-yet-originated shape now reads `2048-12-01` on both where the caption said
    `2029-02-22`. **Two behaviour changes beyond the fix, both stated because neither is obvious**:
    `has_unclearing_debt` widens (a not-yet-originated loan whose plan never clears it can now
    poison the date), and a payoff date already in the PAST now reads `is_loan_free=False` where
    the per-loan filter it replaced read the empty list as "no loans" and reported a borrower with
    an uncleared loan as loan-free. The developer's ruling on the past date: the outlook REPORTS it
    and the Horizon falls back for AXIS SIZING only, because `_milestone_axis_x` clamps a past
    target to index `0.0` and would plant the flag on "Today". Two predicates that could not fire
    were deleted rather than re-pointed (`_loan_ad_current_principal`'s `is_paid_off` arm, and
    `_compute_principal_paid_fraction`'s move to `is_retired`, bit-identical in every state).
    Its adversarial review found six real defects in the first draft -- a docstring naming the
    wrong clamp end, the `$900,000` incident attributed to the wrong mechanism, two money
    assertions read back off the producer, and the past-payoff branch shipping with NO test -- all
    fixed rather than reworded.
  * [x] **X-q3 THE CAPTION** -- **SHIPPED dev 2026-07-27 (`bad97e6a`)**, closing **N-99** under
    ruling R-AX. The date is derived over the debts that HAVE a payoff model, so the surfaces say
    that: `Debt-free <mon>` -> `Loans paid off <mon>` on the cockpit footer, `No debt-free date at
    current payments` -> `No loan payoff date at current payments`, `debt-free <mon>` -> `loans
    paid off <mon>` on the dashboard debt track, and the Horizon's flag `Debt-free` -> `All loans
    paid off` (the machine `kind` is unchanged). `_debt_line.debt_without_payoff_model` sums the
    owed magnitude of every liability that is NOT an amortizing loan -- `abs()`, matching the
    net-worth reducer, because a card is anchored owed-as-NEGATIVE -- and the footer names it
    ("excludes `$500.00` revolving"). Three existing assertions changed WITH the labels, which is
    the developer confirming the expected behaviour changed rather than a test edited to pass.
    Ships out of execution order (before X-q2) because it is the caption half of X-q1's own
    ruling and the two read one derivation.
  * [x] **X-q2 THE SURFACE** -- **SHIPPED dev 2026-07-27 (`be6cfae6`)**, closing **N-100** and
    **N-102** under rulings R-AZ / R-BA / R-BB. **The rendered payload is BYTE-IDENTICAL on both
    databases** (13,480 bytes on `shekel`, 13,472 on `shekel_f3_final`), which is the proof this
    step promised, and the loan baseline is unmoved.

    `horizon_end` went as a duplicate of `dates[-1]` -- the review brute-forced the identity over
    **4,034,784 inputs** (every day across 8 years x ~1,400 payoff offsets, plus `None`) with zero
    mismatches, so "by construction" is measured and not asserted. `is_loan_free` went as a
    republication of `LoanPayoffOutlook`'s derived property. `compute_net_worth_horizon` went as a
    public export with zero `app/` callers while every sibling narrow producer has one, and its 10
    test call sites now read the horizon out of `compute_dashboard_data`, the only path production
    has. Two tests went with it, and the second one's property was proved still covered BY MUTATION
    (giving `build_horizon` its own core data reddens
    `test_dashboard_data_resolves_each_loan_once` with "loan 2 was resolved 2 times").

    **What it added is a GATE at both ends of the boundary, not a note.** The producer's key set is
    a literal; the route removes each key in turn and requires `_serialize_horizon` to raise a
    `KeyError` NAMING that key. Both were shown to fire on the exact defect -- re-adding one unread
    key reddens both -- and the route arm asserts WHICH key raised, so an incidental miss from a
    later refactor cannot pass as proof.

    **N-102 is decided, and the reason is not cost** (ruling R-BB): the badge reads the seam's
    `is_paid_off`, which the seam defines as the CONGRATULATION predicate, and the moment to
    congratulate is the live list when the loan clears. The archived dicts stay two keys and the
    drawer runs no engine or seam call.

    **Four false claims in the files it touches were corrected in-commit**, all found by hand or by
    its reviews: `_debt_line`'s module docstring still said X-q3's captions were "NOT done here"
    with N-99 open (both shipped at `bad97e6a`) and its `_cockpit.html` citation had drifted seven
    lines; `_horizon` said the client's flag plugin carries a milestone's `kind` (it reads only `x`
    and `label`); `_orchestrator` said "Two public entry points" and that `_build_projection_context`
    is shared by "both" (five and four); and a stray-quote typo from X-r.

- [x] **X-r** `refactor(savings): the projection dict carries the seam's figures` -- **SHIPPED dev
  2026-07-27 (`1204a99e`)**, closing **N-101** (ruling R-AW). **NO figure moved**, and it is B-16's
  ROOT rather than another of its symptoms.

  **What it also carried, because its own review found it and rule 4 leaves no third option:**
  X-q3's `revolving_debt` had broken `compute_debt_summary`'s "identical figures by construction"
  promise -- the narrow producer projected LOANS only, so the one key that is about the debt that is
  NOT a loan read `$0.00` there while the full build reported the real figure. Nothing rendered the
  difference, which is what made it worth fixing rather than noting. `_project_loan_accounts` is now
  `_project_debt_accounts` and projects the liabilities too; a test asserts the two paths agree on
  EVERY key and fails against the old restriction. **Two of this step's own claims were proved
  wrong by the same review and fixed**: a payment assertion a different site on the same page
  satisfied (measured by mutation -- rendering the RATE in the payment slot kept it green), and a
  docstring whose account of the Jinja hazard was inverted (the money and date sites RAISE; the
  badge guard is the one that degrades silently, and it was already covered).

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
  re-point, **re-read 2026-07-27 after X-q moved two of them**: `_metrics.py` (the originated and
  retired tests in `_compute_principal_paid_fraction`, and the rate / payment reads in
  `_accumulate_loan_debt`), `_debt_line.py` (`debt_line_loans` and `loan_payoff_outlook`, which is
  where the `is_retired` and `payoff_date` reads moved at X-q1), `_horizon.py` (the milestone
  loop), `savings/_cockpit.html` (`:157`, `:182`, `:188`, `:189`) and `savings/dashboard.html`
  (`:223`), plus **14 test sites in two files at
  `d1c218c2`** (`test_savings_dashboard_service.py` and `test_balance_at.py`) and the **two more X-o
  adds** in its firing controls -- 16 to read, never sed. **Jinja is
  where the risk is**, not Python: an undefined attribute renders as empty rather than raising, so
  every template site is checked by a rendered assertion and not by the page merely loading.

- [x] **X-h** `test(balance): four controls that cannot fail are not controls` -- **SHIPPED dev
  2026-07-28 in its five commits** (`6337606e` B-17, `7d61c67f` N-94, `8e739298` N-45, `86c38e28`
  N-65, `6b1373ab` the gate; the header ticks with the last, rule 6's convention for a decomposed
  step). **NO production
  change, so the baseline provably cannot move.** Closes **B-17**, **N-45**, **N-65**, **N-94**
  (ruling R-AO). One root: Section 7.3 requires every guard carry a negative control shown to fire,
  and these four cannot fire. B-17 asserts `_metrics` behaviour on an `_ad` dict the TEST builds, so
  changing the production builder leaves it green (**its `_projections.py:241-243` citation was
  already STALE when this was written -- see AS BUILT below**); **N-94**'s per-kind
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
  their text. **Where it RUNS was corrected on the developer's question, 2026-07-28, and the
  question was the right one:** shipped under `tests/`, it fired only on a full-suite run and at PR
  time -- and pull requests here open at the END of an arc, a dozen steps after a stale owner
  appears, which is about when the hand-passes found the last four. A gate whose own trigger depends
  on someone choosing to run the suite is the discipline it exists to replace. It now lives in the
  database-free tier the custom checkers already use (`tools/plan_gate`, `pytest <dir> -c
  /dev/null`), with a pre-commit hook scoped to THIS DOCUMENT and to the gate file, so editing the
  ledger is what runs it -- at commit time, before anything is pushed. Under `tests/` it also
  inherited the autouse `db` fixture and needed PostgreSQL running to grade a text file; it now
  takes `0.04s` and no database, which is what makes a commit-time hook possible at all. Verified by
  planting one row against a ticked step: `git commit` exits 1, HEAD does not move, and the message
  names the row and the shipped owner. **Its firing control is free and must be
  shown: point one row at a ticked step and watch it fail.** Written against the ledger AS IT NOW
  STANDS it also catches two more stale owners this triage found by hand -- **N-46** (owner cited
  `X-c2b3`, which is ticked) and **N-73** (cited `X-g2b`, ticked) -- both corrected in this same
  pass, which is the gate earning its place before it ships.

  **AS BUILT (2026-07-28). Five commits, and four of the five corrected something this entry got
  wrong** -- which is the step's own thesis applied to its own plan.

  * **B-17** (`6337606e`). The entry's citation was STALE: plan step X-r had already deleted the
    field-by-field copy, so the test's hand-built dict carried `loan_figures` whole and the
    `loan_result is None` fallback this entry names no longer exists. The blindness was
    unchanged. Repaired by asserting through `compute_debt_principal_progress`, the entry
    `dashboard_pulse_service.py:831` calls. Control, against one injected production defect
    (`is_originated=True` forced into the builder): repaired test 1 failed, PRE-repair test
    1 passed.
  * **N-94** (`7d61c67f`). Confirmed and MEASURED: loan `$200,000.00` asserted vs `$200,000.00`
    modelled (honest), property `$400,000.00` vs `$401,005.45` and investment `$100,000.00` vs
    `$100,576.29` (both blind). The property case additionally had to EXCLUDE `property_detail`,
    which this entry did not know: it still reads the cache column (N-83), so the unpatched set is
    non-uniform for a reason unrelated to the injection. Control, with a NO-OP injection: repaired
    2 failed, pre-repair 2 passed.
  * **N-45** (`8e739298`). **This entry's proposed fix was MEASURED and does not work.** Giving the
    synthetic importer a real `path=` still flags, because `_importer_file_inside` must resolve the
    same unresolvable `app.services.balance_at._context`. The test moved onto the hermetic on-disk
    fixture tree instead, and the CLASS was closed with an autouse fixture that drops the astroid
    registrations each test leaves behind (`0.46s -> 1.63s` over 146 tests; a full `clear_cache()`
    is `13.4s` and finds nothing more). A probe with that fixture found exactly ONE cache-dependent
    test in the file, so the instance and the class are both closed.
  * **N-65** (`86c38e28`). Shipped on its own as this entry allowed, and it is not the shape the
    entry describes. The database is reached THREE ways, not one: 61 columns take their INSERT
    value from a `NOW()` server default, 23 re-stamp on UPDATE, and `status_seam` ASSIGNS
    `db.func.now()` to `paid_at`. The first draft stamped mapped objects and the full suite
    returned **41 failures, every one a bulk `query.update(...)`** that never enters the unit of
    work. The shipped fix is two mechanisms -- a flush listener for defaults that never appear in
    any SQL, and a statement rewriter for every call that does. **The verification arm was itself a
    control that could not fire** (`\b` after `now()` can never assert) and was caught only by
    being made to fire on demand.
  * **The gate.** Built with the strict cell GRAMMAR rather than the "parse the IDs out" this entry
    proposed: scanning a cell for anything id-shaped would try to validate the `N-73` inside
    `X-e (widened 2026-07-27; see also N-73)`. A cell must MATCH `owner [ / owner ]`, each
    optionally annotated, and anything else fails with the cell quoted. **A FOURTH parser hazard
    this entry does not list, measured on the live ledger:** the table escapes a literal pipe as
    `\|` (N-73's row carries `` `Decimal \| None` ``), so rows split on UNESCAPED pipes only -- a
    naive `split("|")` reads that row as six cells and reports a correct row broken. **Eleven
    controls over seven arms: nine plant a defect and must BITE, two plant a legitimate shape and
    must stay SILENT** (a fenced `##` line, and a ` / ` inside an annotation) -- because a gate that
    cries wolf is uninstalled rather than fixed, so its false-positive behaviour needs pinning as
    much as its true-positive. **On the live ledger it found exactly one violation** -- the **E2** pointer
    row, whose owner read `Section 5, Phase E2`, outside rule 6's vocabulary; re-pointed to
    `E2-0 / E2-n`, the phase's own two live steps. Every other owner was already live, which is
    what the three hand-passes bought.

  **What N-65's clock EXPOSED, and neither was caused by it** (both ruled by the developer,
  2026-07-28, both as recommended):

  * `TestPostedLoanBalanceSums` (6 of its 11 tests) freezes today to 2027-01-01 and asserts as of 2026-12-31
    while settling at "now". They pass only because the REAL clock is before 2026-12-31 -- **on
    2027-01-01 they go red with no code change.** Every settle now passes `settled_on=`
    explicitly; no expected figure moved, because `paid_at` bounds a posting's VISIBILITY and never
    its split. The class docstring's "no assertion depends on the wall clock" was false and now
    records why.
  * `test_retirement_projection_entry_aware` asserted a 401(k)'s modelled tile EQUALS its cash
    basis. Ruling R-Y retired that at X-g2b; it held only because the anchor was stamped four
    months past the end of its own seeded window. The basis stays the hand-computed `$49,545.71`;
    the projection now reads the seam's own modelled map and is asserted strictly above the basis,
    the shape the per-kind cross-page classes already use. Measured on a clean `$50,000` anchor at
    7%/yr: `$0.00` the day before the assertion, `+$9.27` the day of, `+$92.77` at the period end
    the map answers at.

  **Its adversarial review earned its cost a SEVENTH time, and it attacked the gate hardest of
  all.** Three ways the gate could have passed while seeing nothing, none of which a premise floor
  can catch, all three demonstrated and all three now closed at their source with their own
  controls: (i) a row whose id cell is EMPTY was silently dropped and its owner never read, because
  `set("") <= {"-", ":"}` is `True`; (ii) a DUPLICATE checkbox id let the last occurrence win, so
  re-listing a shipped step as unticked would blind the gate's primary arm -- and this document
  re-parents steps routinely (`X-c2c4` is one); (iii) Section 5 already carries a fenced diagram,
  and a `##` line inside any fence truncated the section, losing every step after it and failing
  the rows that own them with a diagnosis blaming the LEDGER for what the PARSER lost. Two more:
  the vocabulary words passed BARE, though rule 6 requires `operator` to state its question and
  `developer-decision` to be dated; and an annotation containing ` / ` was torn into two bogus
  owners. **It also caught the count in this very entry** -- "eight negative controls, one per arm"
  described neither the file nor the arms.

  **And it caught this step committing the exact tail it shipped a gate to prevent.** The four rows
  X-h closed were moved to the archive's register with their status cells still reading "OPEN" and
  "recorded, NOT fixed" against a closed-by that says CLOSED -- the rule-2 half-move, four fresh
  instances, in the same pass. All four now carry a CLOSED sentence naming what closed them and
  what each row had got WRONG before it closed. **Two PRE-EXISTING broken rows surfaced with them**
  (`N-100` and `N-102` in the archive each carried a duplicated cell and rendered as six columns);
  both repaired. The gate reads only this file, so nothing catches the archive -- recorded here as
  the available extension rather than shipped unruled.

  **A SECOND adversarial review, on the four repaired tests, found two DEFECTS in the clock and one
  assertion this step had WEAKENED.** All three fixed here; the first is the sharpest thing in the
  step.

  * **The rewriter failed OPEN, and its result depended on test ORDER.** It was bound lazily from
    inside the flush listener, so a frozen test whose only writes were bulk `query.update(...)`
    never installed it. Same test, same assertion: `updated_at 2026-07-28` in a fresh process,
    `2026-03-20` after some earlier test in that worker had flushed ORM state under a freeze. That
    is finding N-45's own class -- a green earned on a sibling's warm process state -- reintroduced
    two commits after the commit that deleted it, in a function whose docstring names the shape it
    must avoid. It also made "two mechanisms, each covering what the other structurally cannot"
    false as stated: mechanism 2 was INSTALLED BY mechanism 1. The rewriter is now bound eagerly by
    the session-scoped `setup_database` fixture, before any test runs.
  * **A FOURTH way the database answers, undisclosed and unfrozen.**
    `transaction_entries.entry_date` defaults to `db.text("CURRENT_DATE")` -- a `TextClause`, so an
    `isinstance(..., now)` derivation is structurally blind to it, and an omitted column renders no
    SQL for the rewriter to see. Caught from PostgreSQL's own error DETAIL, the row carrying frozen
    timestamps beside a wall-clock `entry_date`. The derivation now reads BOTH spellings of a
    default and carries the column's TYPE, because a `DATE` column must be answered with a date.
  * **The retirement repair was a wiring identity and an inequality, and both survive a wrong
    RATE.** Injecting a 10x modelled return left the test green. It now also pins the figure --
    `$49,637.72`, with its derivation -- and that injection fails it.

  Two more the review measured and this entry now states correctly: `X-h`'s N-94 control reports
  **3** failed under a no-op injection, not 2 (the loan case fails too, which is right -- a no-op
  injection must be caught for every kind); and N-45's "this directory's rootdir cannot import
  `app`" is INVOCATION-SPECIFIC -- true under the `pytest` console script that CI and pre-commit
  use, false under `python -m pytest`, which puts the cwd on `sys.path`. The repair removes the
  dependence either way.

  **Full suite 7621 passed.** One finding opened, and it is production rather than test:
  **N-105**, owner **X-s**.

  **An extension considered and DECLINED for this step** (developer ruling 2026-07-28): a fourth
  gate arm requiring a ticked box to carry a commit hash (rule 2's first half, which nothing
  checks). It is a second rule with its own false-positive shapes -- `X-c2c4` ticks with ANOTHER
  step's hash and `X-g` ticks with the last of ten -- so it is recorded here as available rather
  than shipped unruled.

- [x] **X-s** `refactor(savings): the payload and the debt summary publish what is read` --
  **SHIPPED dev 2026-07-28 (`bbdfc2c0`)**, closing **N-104**,
  **N-105** and **N-106**. Opened by X-q2's two adversarial reviews, which found N-100's root
  surviving one level inside the dict X-q2 had just certified and one package over from where X-r
  fixed it; X-h's trace then added N-105 and this step's own trace added N-106.

  **DECOMPOSED into three leaves but SHIPPED AS ONE COMMIT (`bbdfc2c0`), and the reason is recorded
  rather than glossed:** the three leaves' PRODUCTION files split cleanly, but their tests do not --
  71 interleaved hunks in `test_savings_dashboard_service.py` alone -- so a three-way split could
  not be taken without leaving an intermediate commit RED, which is worse than a coarser green one.
  All three boxes tick with the one hash, the convention `X-g` (ten commits, one tick) and `X-c2c4`
  (ticked with another step's hash) already established.

  **TRACED 2026-07-28 on rulings R-BC / R-BD / R-BE / R-BF, then R-BG / R-BH out of its reviews.** Two
  of the four forks came back WIDER than recommended and both widen the same way -- they replace a
  property that would need a guard with one that cannot be violated. Section 4 carries the rulings
  and their measurements; what follows is what each commit does.

  **Sequenced after X-h and not before it**, on X-h's own ground: X-s1 changes a client payload and
  X-s3 moves rendered captions, and the four controls that currently grade this package are the ones
  X-h repairs. That is a schedule with a reason, which Section 9 rule 7 distinguishes from a deferral.

  **Its two adversarial reviews earned their cost a SEVENTH time, and the sharpest finding was in
  the FIX rather than the code.** X-s2's first docstring claimed the no-baseline rule was "stated
  HERE and nowhere else"; it is stated in four places, and the correction then cited **two function
  names that do not exist** (`_net_worth._account_balance_series`, `_orchestrator._sparkline_maps`)
  -- the invented-citation class this arc has paid for repeatedly, committed while fixing an
  overclaim. Both are now real symbols, verified by walking the AST for the function enclosing each
  guard. The reviews also found: a dead producer surface X-s1 had just CREATED (ruling R-BG), a dead
  field in X-s3's own new value object (R-BH), two holes in X-s1's own guard (it read only
  `milestones[0]`'s keys, and nothing pinned the serializer's OWN output -- re-adding X-q2's deleted
  `horizon_end` passed every test), a single-use constant whose doc-comment described a consumer
  that does not exist, two re-exports with no importer anywhere, and four stale line citations this
  commit's own edits had shifted. **Every one is fixed in the commits below**, and the two guard
  holes carry firing controls of their own.

  **One process lesson, paid here:** the two reviews ran CONCURRENTLY with fixes being applied to
  the tree they were reading, and the correctness reviewer reported the artifact changing underneath
  it -- so its gate results graded a tree that no longer existed. Both gates and both real-data
  harnesses were re-run on the final tree. Review a frozen tree, or expect to re-run everything.

  * [x] **X-s1 THE PAYLOAD** (`bbdfc2c0`) -- the serialized milestone becomes `{label, x}`, the top-level chart
    payload drops `assets` / `liabilities`, and the producer's milestone dict drops `kind` so
    X-q2's mutation guard reaches the nested dicts with no new mechanism (ruling R-BC). The client
    reads `milestone.x` / `milestone.label` and never `data.assets` / `data.liabilities`
    (`net_worth_cockpit.js:393`, `:407`, `:419`, `:173-192`); the composition bands already carry
    the two totals, which `test_savings.py:3409-3410` asserts today. Changes the rendered
    `data-chart` JSON, so it gets its own live-render check; moves no money.
  * [x] **X-s2 THE PREDICATE** (`bbdfc2c0`) -- `_project_one_account` states each of its two conditions ONCE and
    the missing-baseline guard is hoisted to cover both arms (ruling R-BF, finding N-105). No figure
    moves and the state is unreachable in production; what changes is that `/savings` degrades
    instead of raising `ValueError` for a user with no baseline scenario, which is what the other
    four account kinds already do.
  * [x] **X-s3 THE VALUE OBJECT** (`bbdfc2c0`) -- the debt summary becomes a frozen `DebtSummary` carrying the
    seam's `LoanPayoffOutlook` WHOLE, its three parallel DTI nullables collapse into one
    `DtiMetrics | None`, the dashboard track composes it instead of copying-and-extending it, and
    the /savings Liabilities footer gains the third caption "All loans paid off" (rulings R-BD /
    R-BE, findings N-104b and N-106). `is_loan_free` gains its first `app/` reader. No figure
    moves: every field keeps its value and its rounding.

- [x] **X-t** `refactor(savings): the shapes this package guarantees` (`db1e45a4`, `b3ff3343`,
  `709cda23`, `21893ec5`, `d4e0d4e7`) -- closes **N-107**, **N-108**, **N-110** and **N-111**
  (ruling R-BJ, out of X-s's two adversarial reviews). **One question in three places:** which of
  this package's shapes are GUARANTEED and which are merely tested. No figure moves on either
  database, on a harness built for this step because the seam one is blind above the seam.

  * [x] **X-t1 THE PROJECTION** (`db1e45a4`) -- the per-account dict becomes a frozen
    `AccountProjection` carrying a nested `LoanDetail \| None`, so loan-ness is ONE structural
    question and the seam's `LoanFigures` cannot be flattened again (N-111). `is_liability` becomes
    a DERIVED property: the page asked that rule two ways over one set of balances, the grid cell
    off a stored key and `compute_net_worth_today` off the account beside it.
    `compute_account_balance_cell` returns the projection itself and the cockpit balance partial
    reads it, so the cell's contract is stated once instead of three times.
  * [x] **X-t2 THE PREDICATE** (`b3ff3343`) -- `BalanceContext.has_baseline` states the seam's
    no-baseline precondition ONCE and `require_scenario` raises on that same property (N-107). The
    finding counted four spellings; the tree held **18**. Two of the three in this package sat
    under one caller and ANSWERED DIFFERENTLY -- measured by restoring them, a no-baseline user was
    served `net: [0.0 x 10]` across ten real periods with `current_index: 4`, a fabricated flat $0
    line -- so one guard in `_compute_net_worth_section` replaced both and the region now degrades
    to an empty series with no Horizon.
  * [x] **X-t3 THE VOCABULARY** (`709cda23`) -- `_ASSET_BANDS` is DERIVED from the display
    categories and the template's band-order copy is deleted (the legend iterates the producer's own
    composition map); the three homes that cannot import it -- the chart script, the CSS tokens, the
    Jinja microcopy -- get a static gate (N-108). The finding said a stray band ships "a dead float
    series"; it also stops the drawn stack reconciling to the drawn net line.
  * [x] **X-t4 THE FLAG** (`21893ec5`) -- ruled: a milestone's LABEL is its identity, a duplicate is
    a display outcome, and a consumer identifies a flag by the `(label, date)` pair, unique by
    construction (N-110). De-duplicating was priced and refused: planted, it kept a small loan's
    flag and dropped the debt-free one.
  * [x] **X-t5 THE REVIEW RESIDUE** (`d4e0d4e7`) -- **both reviews found the same top defect and it
    was inside the fix**: X-t2's docstrings claimed this package had "TWO seam doors, and they are
    the only two". It has three -- `compute_property_equity` reaches `loan_figures` through
    `home_equity_service` -- so a borrower with a Property securing a mortgage and no baseline got a
    `ValueError` where every other tile degraded (pre-dating the step; EXECUTED by a reviewer). The
    end-to-end guard could not catch it because its fixture was loan-only, and writing the Property
    fixture found a second dead control of my own: `secured_by_account_id` is not a field, and
    SQLAlchemy accepts the assignment silently. Also: `_horizon` held two MORE band literals whose
    union had to equal the vocabulary X-t3 had just gated in four languages (derived now, and the
    gate asserts the partition); three of five gate arms scanned RAW source, so a band dropped
    behind a `//` comment satisfied the arm that exists to catch it; and nine stale or INVENTED
    citations, including `_net_worth._sum_net_worth_totals`, which has never existed in any commit.

- [x] **X-u** `refactor(savings): one debt projection per render` (`70c5cf39`, `e2cdc589`) -- closes **N-109** (rulings
  **R-BS** / **R-BT** / **R-BU** / **R-BV**, which reverse R-BI on evidence R-BI did not have).
  `compute_tracks_section` runs `_project_debt_accounts` TWICE and the seam-batch builder
  THREE times per dashboard render, because the two narrow debt producers each run the
  full load -> params -> project pipeline over the same loan set. `principal_paid_fraction` becomes a
  `DebtSummary` field, which deletes `DebtTrack`,
  `compute_debt_principal_progress` and the duplicate pass together.

  **The trace ran first, as this entry required, and it answered the membership question the step
  existed to be careful about.** R-BI's objection was that the two producers "answer over DIFFERENT
  membership rules" -- owed-today for the money figures, all-loans-ever for the marker -- so merging
  them re-opens what X-q settled at a measured cost of 19 years. Measured: they are **reducers over
  ONE list**, not two loan sets. Both are handed the same `loan_ads`; neither predicate has to move;
  and the fraction is identical over the narrow and the full projection on both databases. So the
  merge changes no rule, and what it deletes is the duplication that made two docstrings promise
  the agreement instead of the structure providing it.

  **Measured before and after, on both databases** (`shekel` / `shekel_f3_final`): debt projections
  per tracks render **2 -> 1**, seam-batch builds **3 -> 2**, SQL **92 -> 83** and **84 -> 75**,
  median wall clock **103.4 -> 98.2 ms** and **94.8 -> 91.7 ms**. No figure moves: both real-data
  harnesses are byte-identical, and the above-seam one was shown FIRING on a planted
  one-basis-point drift in the merged field before it was trusted.

  **What it leaves, stated so it is not mistaken for closed: finding N-115** (ruling R-BU). The
  duplicate PROJECTION goes; the duplicate LOADS behind it stay, because sharing a `BalanceContext`
  is not sharing the loads. That is X-i1's input-tier memo, whose entry is widened in the same
  commit to name the three loaders it did not previously cover -- one of which is a full
  paycheck-engine run, twice per render.

- [x] **X-v** `refactor(balance): one answer for a state the app cannot produce` (`7d4e4986` rulings -> `dbf154c7` code) -- closes **N-112**
  and **N-113** (rulings **R-BW**..**R-CB**).

  **The trace inverted this entry's own premise, and the rulings block carries the measurements.**
  The step was entered as "read each of the 12 degraded values rather than rename the predicate".
  The AST census the entry demanded found **13** (`tax_report_service.py:374` hides one behind a
  local alias). The route sweep the entry did NOT demand found **8 endpoints across 3 doors that
  500**, none of which spells the rule anywhere -- `_load_route_context`, `resolve_home_equity`,
  `_load_debt_accounts`. And the developer's question ("why not enforce a default scenario and
  backfill?") measured out as: **there is nothing to backfill.** Registration writes a baseline for
  every owner, nothing deletes or un-baselines one, no path promotes a companion to owner, and
  `integrity_check`'s **DC-08** already asserts it as critical. The only baseline-less row on either
  database is the COMPANION, who is 404'd off every balance surface by `require_owner` (swept: 0
  5xx). So the 19 degraded values, the 2 fabricated figures and the 3 crash doors all defend a state
  the application cannot produce, and they answer it **seven different ways**: a full-page recovery
  card, `204`, a 404, a blank cockpit with a `$0.00` hero, `Account.current_anchor_balance`
  presented as a *current balance*, a hidden chip, and an unhandled 500.

  **DECOMPOSED on the arc's own line: the first commit cannot move a figure, the second is the
  deletion.**

  * [x] **X-v1 THE ANSWER** -- additive. `BaselineMissingError` in `app/exceptions.py`,
    `require_scenario` raising it, ONE app-level handler (card / `204` / ERROR log), the recovery
    template moved out of `grid/` to the one place that now renders it, and **the route sweep
    promoted to a permanent gate** (ruling R-CB), shown firing against a tree with the handler
    removed. Nothing else changes: every existing guard still short-circuits ahead of the raise, so
    the only behaviour that moves is the 3 doors that 500 today.
  * [x] **X-v2 THE DELETION** -- `BalanceContext.scenario_id` becomes the raising accessor
    (R-BX), `has_baseline` goes with its last reader, and **18** display-tier guards, the `$0.00`
    hero's `or ZERO` and the investment tile's anchor-column fallback are deleted (R-BY, R-CA).
    `AccountProjection.current_balance` stops being nullable, which takes **eight** `or ZERO`
    reducers with it -- the hero, the goal reducer, the debt line, the Horizon, three metrics and
    the group subtotal. Two guards SURVIVE with their reasons written at them:
    `loan_recurrence_sync` (a writer, where a raise would roll back the user's edit) and
    `liability_owed_at_dates` (the degenerate case of its own rule). It reverses X-t2's `/savings`
    degradation under R-BZ, which the developer confirmed per CLAUDE.md rule 5.
  * [x] **X-v3 THE REVIEW RESIDUE** (rulings R-CC..R-CF). Two adversarial reviews of the frozen
    tree found the step's own central claim false: "no caller pre-checks left" held for the balance
    SEAM and not for the application. The balance sheet was asserting `in_balance = True` over a
    ledger it could not read; a mutating htmx request was answered with silence; the ERROR event
    logged the wrong user id in the one case it exists to diagnose; and the new gate's coverage
    assertion could not fail. **Both reviews found the ledger statements independently, and
    neither instrument this step built could see them** -- which is the finding, not a footnote.

- [ ] **X-y** `refactor(balance): the baseline decision that is not the balance seam's` -- closes
  **N-117**. The fifteen surfaces that resolve the baseline DIRECTLY
  (`get_baseline_scenario`) rather than through a `BalanceContext`, and so answer this state
  without the seam ever being asked: the grid's two create-form fragments and the carry-forward
  preview (`400 "No baseline scenario"`), template and transfer generation (a silent commit that
  generates NOTHING), `salary/profiles` (a flash telling the user to **register a new account** --
  a competing repair story, wrong since `/grid/create-baseline` shipped), `period_population`
  (`return 0`), `spending_report_service` (`return None`), both posting syncs (a silently narrowed
  scenario set), and `escrow_rates` / `loan/params` (which hand a NULLABLE id onward to a query --
  the hazard `scenario_id_or_none`'s own docstring names). **X-v deliberately did not reach these**
  and its ruling R-CC took only the two that fabricate a financial statement; the rest are a
  different question one tier down -- "what may a WRITE do without a scenario" -- and they deserve
  their own answer rather than X-v's by extension. Sequenced after X-x, which decides the twin
  question for the period preconditions.

- [ ] **X-x** `refactor(balance): one pay-calendar precondition, one answer` -- closes **N-116**.
  X-v's sibling one axis over. **RE-SCOPED 2026-07-31 by its own trace (rulings R-CX..R-DA), and the
  trace inverted the row's premise.** N-116 said no pay periods is a legitimate state a new user is
  IN, so its answer may well stay a degraded render. It is not one state, the reachable form of it
  has a one-click repair, and the version that IS unreachable is the one 96 branches defend.

  **The census, and it replaces the row's own count.** N-116's 63 was a line-anchored grep's floor;
  an AST pass that taints period-valued expressions and reports only ABSENCE tests (never a
  comparison over a period's FIELDS) measures **96 branches in 49 `app/` files**, plus 8 in Jinja no
  Python census can see, resolving to about **50 distinct answers**. They are FIVE questions, not
  one: *any periods at all* (Q1), *which period contains today* (Q2), *which contains date T* (Q3),
  *is there a next one* (Q4 -- a normal terminal state), *is the requested window non-empty* (Q5 --
  navigation).

  **Q1 is unreachable for an owner and Q2 corrupts money.** The reachability proof and the measured
  cost are in ruling R-CX's preamble; the short form is that a 5-day calendar hole moves `/savings`
  net worth by **`+$3,228.55`** and puts `Account.current_anchor_balance` on screen as a current
  balance, while `/grid` renders the repair card at the same instant.

  **DECOMPOSED on the arc's own line: the first commit adds a door nobody walks through, the second
  changes what an uncovered calendar renders, and none of the five may move a figure on a covered
  one.**

  * [ ] **X-x1 THE ONE ANSWER** (ruling R-CY) -- `PayCalendarGapError`,
    `pay_period_service.require_current_period` / `covers`, the application-level handler and its
    repair page, on the `require_baseline_scenario` / `BaselineMissingError` pattern name for name.
    **It takes the GRID's two pre-checks as its first callers rather than shipping a door nobody
    walks through**: an unreachable handler has no negative control, and Section 7.3 does not have
    an exemption for "the caller arrives next commit". The grid already answered this state with a
    card and its fragments with `204`, so this commit provably changes NOTHING on any calendar --
    it moves who decides, which is the whole point, and gives the handler its three end-to-end
    controls on real doors.

    **It also carries the month-end time bomb its own first full run exposed**, because a step
    cannot be graded by a suite that is red for reasons of its own: six
    `test_cross_page_balance_equality` cases were failing at `HEAD` and at all 25 preceding
    commits, on a calendar-month fixture read on the last day of a month. The production behaviour
    was measured CORRECT and is unchanged (Section 8 carries the measurement, including the fix
    that was tried and rejected); the module gets the mid-period frozen clock
    `tests/test_services/` has had all along, plus the precondition guard that stops it drifting
    back onto a boundary. **The suite is 7,686 green.**
  * [ ] **X-x2 THE FABRICATIONS** (ruling R-CY) -- the branches that publish a figure the app did
    not compute stop doing so and take the raising accessor: the anchor-cache substitutions in
    `_projections._current_balance_from_map` and `_context._resolve_current_balance`, the
    fabricated `$0.00` in `_metrics._recent_settled_expenses_monthly` /
    `income_service.get_current_gross_biweekly` / `salary/_helpers._compute_total_pre_tax` /
    `investment_projection`'s three YTD readers, and `build_trend_periods`' `current_index = 0`
    into an empty list. It carries this file's residue too (the developer's standing rule): the
    two vacuous `or Decimal("0.00")` on the `NOT NULL` `current_anchor_balance`, which is ruling
    R-CH's shape surviving in `_context.py`.
  * [ ] **X-x3 THE ONE PREDICATE** (ruling R-DA) -- `onboarding.has_periods` asks Q2 rather than
    Q1, so the checklist and the page it renders on cannot disagree.
  * [ ] **X-x4 THE STATES SPLIT** (ruling R-CZ) -- an empty requested window stops answering with
    the absence card, and the card's copy stops naming two states.
  * [ ] **X-x5 THE HARNESS** -- `verify_savings_producers.py`'s docstring says in terms that "what
    remains is X-w's OWN tolerance, and X-x deletes it": the dict-or-attribute `_get` reader and
    the two-spelling readers in `_today_figures` and the archived row. Verified dead by RUNNING it
    against this tree, on the X-w5 precedent.

- [ ] **X-ad** `feat(periods): the pay calendar a new user can actually enter` -- closes **N-123**
  and **N-124** (rulings R-DB, R-DC, R-DD). **The WRITE half of X-x's trace, and it is its own step
  because it moves money-adjacent state**: it creates periods and re-anchors accounts, where X-x
  only reads and deletes. Sequenced immediately after X-x so the read-side guards X-x installs are
  what grade it.

  **Registration stops creating a bootstrap pay period** (R-DB). Measured at the service tier, on a
  form that says "Enter your next (or first) payday": `today+1` / `+5` / `+13` are REFUSED,
  `today` and `today+14` are clean accepts, and `today+20` / `+27` are accepted leaving a permanent
  hole -- so the bootstrap either blocks the user's real payday or guarantees the state X-x's
  readers refuse to answer. (The `today+0` accept was found 2026-07-31 by X-x's design review,
  correcting this entry's original "13 of 14 refused".) **Its trace must decide what replaces the
  FK guarantee**: `accounts.current_anchor_period_id` is `NOT NULL` (migration `cfb15e782f86`) and
  that is the whole reason the bootstrap exists, so the fork is between deferring the default
  Checking account until a schedule exists and relaxing the anchor -- and the second reaches the
  invariants plan step **X-e** owns, which is why nothing here presupposes it.

  **A mid-life schedule change fills the hole it would leave** (R-DC): `regenerate_pay_periods`
  covers the gap between the last retained period and the rebuilt tail at the retained cadence, so
  every day belongs to exactly one budget period by construction. Stretching the retained period's
  `end_date` instead is REJECTED at R-DC -- it lengthens a period that may hold settled money and
  posted ledger entries, which moves the fold's column boundaries.

  **N-124 rides here because it is the same writer**: with rolling enabled, a lapsed schedule is
  healed by `top_up_rolling_window` appending contiguously from the last end, which measured
  **61 -> 113 -> 132 periods over two `/grid` loads and +991 transactions**, 19 of those periods
  entirely in the past. The window is a forward top-up and it backfilled history.

- [x] **X-w** `refactor(savings): the containers this read path still passes untyped` -- **SHIPPED
  dev 2026-07-30** in seven commits: the rulings `03272174`, then `f3d75fe4` / `fcc8cd36` /
  `c70acee5` / `88240253` / `740a005d`, then the residue rulings `5c078076` and the residue
  `38f8d879`. Closes
  **N-114**. X-t1 typed the per-account projection; the same render still passes two untyped dicts
  between modules. The dense per-account map (`{account_id, balances, is_liability}`) STORES the
  liability flag the projection now derives, so the page single-sources that rule in one container
  and not in the other; and the archived-account rows carry a `current_balance` that is
  `Account.current_anchor_balance` -- which finding N-103 says is not a loan's balance at all --
  under keys that read like the projection's. **Sequenced after X-v** for one reason: X-v decides
  what each degraded state SAYS, and the dense map is built from accounts rather than projections,
  so its no-baseline answer is one of the twelve X-v has to read. Neither costs a cent today; the
  measured cost of the last container in this class was B-16 and N-98.

  **The trace (2026-07-30) moved this step from "type a dict" to "delete a container", and the
  measurement that turned it is one line**: `_net_worth` took TWO shapes for ONE account set on ONE
  render -- `compute_net_worth_today(list[AccountProjection])` beside
  `compute_net_worth_series(list[dict])`. N-114's stored flag is what that asymmetry produced, not
  the defect itself. Rulings **R-CG..R-CJ**; an AST census then counted **eight** record containers
  crossing a module boundary on this path where the finding named two, and R-CI rules which of them
  are records. **DECOMPOSED on the arc's own line**, five commits, none of which may move a figure:

  * [x] **X-w1 THE DENSE MAP** (ruling R-CG, R-CJ). `AccountProjection.balances`;
    `to_net_worth_account_data` and `build_account_net_worth_maps` deleted; the projection's seam
    batch covers loans; the category memo indexed rather than defaulted.
  * [x] **X-w2 THE ARCHIVED ROWS** (ruling R-CH). `ArchivedAccount(account, last_anchor_balance)`;
    the vacuous `or ZERO` deleted. N-103's question stays X-e's.
  * [x] **X-w3 THE NET-WORTH REGION** (ruling R-CI). `compute_net_worth_today`,
    `compute_net_worth_series` (built ONCE, so the orchestrator's post-return
    `series["current_index"] = ...` mutation dies) and `compute_property_equity`.
  * [x] **X-w4 THE GOAL ROW** (ruling R-CI). `_build_goal_datum`'s eleven keys, read by two
    templates through two packages.
  * [x] **X-w5 THE HARNESS** . `verify_savings_producers.py`'s own docstring says "the X-t1
    tolerance goes when X-v ships, the X-u tolerance when X-w does". **X-v did not delete the X-t1
    one** (verified against `dbf154c7`), so both go here, and the generic dict-or-attribute reader
    survives as X-w's OWN tolerance -- named as such, and X-x deletes it.
  * [x] **X-w6 THE REVIEW RESIDUE** (rulings R-CK..R-CN, plus corrections to R-CI and R-CJ).
    Two adversarial reviews of the frozen tree found the same two defects independently -- the new
    cockpit-hero guard cannot fire on the regression it names, and the arc's flagship hero-vs-trend
    assertion carries a failure MESSAGE that raises `TypeError` at the exact moment it is needed --
    and **NINE of the step's own citations were wrong**, which is this arc's signature defect
    committed inside the step that quotes it. The four rulings above; the nine citations; the two
    type hints the step dropped; and a `Raises: KeyError` contract that three functions now state
    and nothing tested.

    **One premise of the step was also FALSE and is corrected wherever it was written**: "Jinja
    answers a missing attribute with `Undefined`, which renders as an empty string, so typing the
    producer does not protect the template". A BARE `{{ value }}` does render empty -- but the
    `money` macro opens with `{% if value < 0 %}`, and `Undefined.__lt__` RAISES, so a renamed
    money field 500s and the pre-existing `status_code == 200` assertion already covered that
    class. The guards are still right; the reason written beside them was not, which is Section 8's
    "a correction can carry the defect it corrects" one axis over.

- [x] **X-aa** `refactor(savings): the two records the last step reported and did not take` --
  **SHIPPED dev 2026-07-30** (rulings `5c2ba585`, code `c10d5d12`). Closes **N-119**. Ruling R-CO. `calculate_trajectory` and `calculate_savings_metrics` become
  value objects at their producer; `GoalProgress.trajectory` stops being nullable (its producer has
  three returns and all three are full dicts); and `savings/dashboard.html`'s
  `{% if gd.trajectory %}` guard goes, because a four-key dict is never falsy and a test that cannot
  fail is not a guard. **Sequenced FIRST, ahead of X-z**: the dead guard and the unreachable
  nullable are live residue of plan step X-w4 in the package X-w owns, and X-z is a predicate merge
  that wants a clean tree under it.

- [x] **X-z** `refactor(savings): one classifier, one liability rule` -- **SHIPPED dev 2026-07-31**
  (rulings `b6b1446e` / `2fcecdd2`, code `8c8d19f6` / `d80e06fe` / `9e1187c3` / `8cc0656c` /
  `bffb18cc` / `7c453074` / `e8bccf4f` / `5e77d0db`). Closes **N-118** and
  **N-120** (rulings R-CP..R-CW). The
  liability rule has TWO id-based spellings and they are equivalent by reading rather than by
  construction: `net_worth_account_data.is_liability_account` (the account type's `category_id`
  against the cached LIABILITY id) and `_display.account_category_key(...) == "liability"`. The
  net-worth hero reduces with the first, the composition band keys off the second, and this page's
  own stated identity -- the hero equals the trend at the current period equals the Horizon's index
  0 -- holds only while they agree. **Found by plan step X-w's trace and deliberately not taken
  there**: Section 8's own lesson is that a DRY refactor of a PREDICATE can move money, so it wants
  its own commit, its own firing control, and a decision about where the shared classifier LIVES
  (the display order belongs to `_display`; the classification rule does not). Sequenced after X-w
  because X-w is what leaves both spellings visible in one reducer.

  **Its trace found the hazard's worst surface and a THIRD spelling the row does not name.** The
  Horizon's three band producers must partition the account set exactly once and they select with
  BOTH spellings (`_liability_band` on `ad.is_liability`; `_asset_bands` and
  `_retirement_investment_bands` on the category map), so a divergence counts an account TWICE with
  opposite signs or ZERO times. And `savings/_cockpit.html:139` / `:269` compare
  `category_name == 'liability'` as a bare Jinja literal -- the liability group's danger subtotal
  and the whole debt-summary footer -- which the X-t3 band gate reads that same file for and cannot
  see. The two Python spellings agree on every account on both databases today (10 and 9, zero
  disagreements), which is what makes this a construction fix rather than a bug fix.

  **DECOMPOSED on the arc's eight-times-proven line: four commits cannot move a cent and the fifth
  moves a figure.**

  * [x] **X-z1 THE CLASSIFIER** (`8c8d19f6`) (ruling R-CP, R-CQ) -- `account_category(account)` is the one place
    a `category_id` meets a cached id; `is_liability_account` and `account_category_key` both build
    on it; `_net_worth._LIABILITY_BAND` is deleted for `_display.LIABILITY_KEY`; the module is
    renamed `app/services/account_category.py`.
  * [x] **X-z2 ONE MAP** (`d80e06fe`) (ruling R-CR) -- `compute_dashboard_data` builds the category map once and
    threads it to both sections; `_group_accounts_by_category` buckets in one INDEXED pass
    (`48 -> 8` classifier calls for 8 accounts).
  * [x] **X-z3 THE JINJA ARM** (`9e1187c3`) (ruling R-CR) -- the band gate grows an arm over the cockpit's bare
    `category_name == '<key>'` comparisons, its negative control planted in the REAL template.
  * [x] **X-z4 THE TWO INPUTS THAT CANNOT BE NONE** (`8cc0656c`) (ruling R-CS) -- `calculate_savings_metrics`'
    two nullable parameters and the four `Decimal(str(x))` coercions; moves nothing.
  * [x] **X-z5 THE COVERAGE UNITS** (`bffb18cc`) (ruling R-CS) -- each of the three units quantized ONCE from
    the raw ratio. **MOVES ONE FIGURE**: `paychecks_covered` `1.5 -> 1.6` on `shekel_f3_final`,
    nothing on `shekel`. Its own commit for that reason, and it adds the non-divisible fixture
    shape no existing test carries.
  * [x] **X-z6 THE REVERSE LOOKUP** (`7c453074`) (ruling R-CV) -- `ref_cache.acct_category_member(category_id)`,
    an id-keyed answer built once at `init()` on the `ledger_class_is_debit_normal` precedent, so
    the classifier is a lookup rather than a scan over four members with four cache calls.
  * [x] **X-z7 THE CATEGORY ON THE RECORD** (`e8bccf4f`) (ruling R-CT) -- `AccountProjection.category`;
    `category_key_by_account_id`, both threaded parameters and `_sum_composition_at_period`'s third
    argument DELETED; `is_liability` becomes a field comparison. Classifier calls per render
    **~488 -> 8**.
  * [x] **X-z8 THE REVIEW RESIDUE** (`5e77d0db`) (rulings R-CU, R-CV, R-CW) -- the false exclusivity claim in
    three homes plus R-CP, `_assemble_composition`'s false partition paragraph, four wrong counts,
    a worked quotient, a wrong exception name, the gate arm that asserts everything but the count
    (with both reviewers' surviving mutants committed as controls), the `years_covered` case a
    double-rounded revert survives all 7,668 tests without, the missing `Raises: KeyError` control,
    and the unasserted `_CATEGORY_KEYS` / `_CATEGORY_ORDER` completeness precondition.

- [ ] **X-ab** `refactor(ledger): one asset-vs-liability rule, read and write` -- closes **N-122**
  (ruling R-CU). `ledger_account_service.ledger_class_id_for_category` asks plan step X-z's question
  a second time on the POSTING path, and the two agree by reading. **It is not a residue fix**: it
  decides which ledger class a real account's paired posting account carries, so it is the write
  path, and Section 8's "a DRY refactor of a PREDICATE can move money" is the case rather than the
  caveat. Its trace has to answer what a re-class does to accounts that already carry postings
  (`account_validation:192-193` exists to refuse exactly that), which is why it is a step and not a
  line.

- [ ] **X-ac** `refactor(savings): one liquid-savings reduction` -- closes **N-121**. The cockpit
  reduces `_sum_liquid_balances` over one `account_data` TWICE per render and publishes the answer
  under two context keys (`total_savings` and `net_worth.today.liquid`), which is ruling R-AZ's "one
  fact under two keys" beside a redundant computation. Collapsing it changes what the page
  publishes, so it wants its own commit and its own render diff.

- [x] **X-ae** `fix(app): a submitted digit string is parsed, not predicated` -- closes
  **N-136**, **N-140**, **N-141** and **N-143**, and opened **N-139** and **N-142**.
  **MERGED TO `main` 2026-08-02: PR #79, merge `a778703f`, commit `cbca7eed`** (see the
  as-built note at the end of this entry).  Not yet deployed; no migration. **The step SHIPPED WIDER THAN IT
  WAS SCOPED, on the developer's ruling of 2026-08-02**, because two adversarial reviews
  independently proved its central claim false; the original four-door scope is recorded first
  and what actually shipped follows it. **`str.isdigit()` is used as the guard for an operation
  it does not license, at FOUR doors.** Of the 888 characters with `isdigit() == True`, **128 make `int()` raise** (measured with
  `unicodedata` 16.0.0; `'\N{SUPERSCRIPT TWO}'` is one), and `app/error_handlers.py` registers
  400 / 403 / 404 / 429 / 500 / `BaselineMissingError` and **no `ValueError` arm** -- so each site
  is a reachable unhandled 500 on forged input:

  * `accounts/anchor.py:282` -- the reconcile POST, unconditional.
  * `loan/params.py:469` -- the collateral POST, unconditional, and its own comment claims it treats
    a bad value *"as a clear rather than crashing"*, which is exactly the property it lacks.
  * `settings.py:442` -- the companion edit context. **Conditional**: `int()` sits inside the
    generator's predicate, so it never evaluates for a user with no active companions.
  * `mfa_service.py:251` -- **the LOGIN path, and it is not an `int()` guard at all.** A 6-character
    all-`isdigit()` code passes the shape check and reaches
    `hmac.compare_digest(candidate_otp, code)` at `:258`, which raises
    `TypeError: comparing strings with non-ASCII characters is not supported`. Reachable:
    `schemas/validation/auth.py:336-338` validates only `Length(max=6)`, and neither
    `verify_totp_code` nor `routes/auth/_helpers.py:132` catches it. It fails CLOSED (no
    authentication is granted) but as a 500.

  **Ruled 2026-08-01: ONE shared helper for the three int parses**, not a predicate swap -- three
  routes restating "turn a submitted string into an int id" is this arc's own
  one-question-many-implementations shape, and the third site is how a fourth gets written.
  **`str.isdecimal()` is NOT the sound predicate and an adversarial review refuted the first draft
  of this entry for saying so**: `('1' * 4301).isdecimal()` is `True` and `int()` raises on
  CPython's 4,300-digit conversion limit (`sys.get_int_max_str_digits()`), which a submitted field
  reaches trivially. **The only sound form is to attempt the parse** -- `try: int(raw) / except
  ValueError: return None` -- which is why this is a helper and not a one-token edit. The MFA site
  needs its own fix (the code must be ASCII decimal, not merely `isdigit()`); a shared int-parse
  helper does not close it, and it is listed here because it is the same unsound predicate, not the
  same repair.

  **Rides in its OWN PR after #76.** `settings.py` is opened by no step and `accounts/anchor.py` was
  opened by step 3 itself, but **`loan/params.py` IS in a balance step's scope** -- X-y's entry
  names it among the fifteen surfaces it closes, and so does N-117 -- so this should land BEFORE
  X-y rather than being called independent of it. The first draft of this entry claimed "two route
  files no balance step opens" and a review refuted it. No money moves and nothing is written: at
  every site the raise precedes all DB **writes** (a SELECT does precede it -- `get_or_404`, or the
  companions query), the 500 handler rolls the session back, and the two POST routes' real
  authorization guard is the service-side re-scoping, which is unchanged.

  **AS BUILT (2026-08-02), AND WIDER THAN RULED.** Every one of the four raises was reproduced
  against a real request before anything was written -- including `/mfa/verify` on the LOGIN path,
  which raised `TypeError: comparing strings with non-ASCII characters is not supported` out of a
  real two-step login. The shared rule is `app/utils/digit_strings.py`: `is_ascii_digits`,
  `parse_row_id`, `parse_row_ids`. **Pure, with no Flask import**, which is what lets `mfa_service`
  consume the predicate without breaching the services-are-isolated-from-Flask boundary -- so the
  fourth site's "own repair" and the three id parses share ONE definition after all, rather than the
  four this entry expected.

  **THE FOUR-DOOR SCOPE WAS WRONG, and two neutral adversarial reviews proved it independently.**
  The step claimed "the ONE answer to what row does this string name" while two larger surfaces
  were still lax, and the developer ruled on 2026-08-02 that both ship here rather than behind a
  later step:

  * **N-140, the URL converter, and it is the most serious thing this step touched.** Werkzeug's
    `IntegerConverter.regex` is `r"\d+"` compiled WITHOUT `re.ASCII`, and its `to_python` is a bare
    `int()`. Measured: `/accounts/١/details` returned output BYTE-IDENTICAL to `/accounts/1/details`
    across **123 path parameters**; and a path segment past CPython's conversion limit raises
    `ValueError` **inside `url_adapter.match()`** -- ahead of the view, ahead of `@login_required`,
    ahead of any session -- so it is an **UNAUTHENTICATED** unhandled 500, worse than any of
    N-136's four doors. Reachable in production: `gunicorn.conf.py` sets `limit_request_line = 8190`
    and neither nginx config narrows the header buffer, so a ~4.4 kB request line arrives. Closed by
    `app/url_converters.py`, ONE registration overriding the built-in `int` name, which is what
    makes it one rule rather than 123 edits. A census supports the override: **all 123 parameters
    are row ids**, none uses `signed` or `fixed_digits`.
  * **N-141, the schemas.** `fields.Integer` is crash-safe but as lax as `int()`: measured on this
    project's own declarations it reads `'١٢'`, `'１２'`, `' 12 '`, `'+12'`, `'1_0'`, `'007'` as
    ids, and `'-5'` and `'0'` as ids that name no row. **73 `*_id` declarations** across 11 schema
    modules now use `RowId` (`app/schemas/validation/_helpers.py`), which consumes the same
    `parse_row_id`. The suite passed **7,768 / 0** immediately after the conversion, so no existing
    test depended on any of the seven lax spellings.

  Two smaller restatements went with them (the reviews' finding 9): `savings.py`'s goal-mode parse
  and `_recurrence_form_helpers.py`'s conflict-decision key parse. Neither crashed -- both already
  attempted the parse -- but both read `decision_١٠٦` as row 106.

  **Four things this entry had wrong, all found by measurement or by review rather than by
  re-reading.**

  * **The int4-overflow theory was mine and it is FALSE.** Every `id` column is `db.Integer`
    (Postgres `int4`), so a 40-digit id that parses cleanly looked like a second unhandled 500 the
    ruled fix would not close. Measured at all three id doors: `200`, `302`, `302`. psycopg sends
    the oversized value as `numeric` and Postgres compares `int4 = numeric` without complaint.
  * **"Attempt the parse" was NOT sufficient.** `POST /accounts/<id>/reconcile` with
    `entry_ids='١٠٦'` returned `200` and really stamped entry 106 as settled: Eastern Arabic
    numerals pass `isdigit()` **and** convert cleanly. **Ruled 2026-08-02: one ASCII-digits rule,
    defined once and shared.**
  * **ASCII alone did not deliver "one spelling" either, and the first build shipped believing it
    had.** `"007"`, `"0000007"` and a hundred leading zeros are all ASCII digits `int()` reads as
    `7`. `parse_row_id` now requires the value to round-trip -- `str(row_id) == raw` -- which is the
    literal statement of "the spelling a template would have emitted", and closes a `bytes`
    argument slipping past the `str` type hint as a side effect.
  * **The collateral door did not implement its own new ruling.** It kept a `.strip()`, and
    `"\xa0".strip()` is `""` -- so a forged non-breaking space still took the CLEAR path under a
    `Secured-by link updated.` flash, verbatim the behaviour this step added a docstring saying was
    closed. It also meant `" 2 "` linked at that door while the other three refused it: four doors,
    three behaviours, in the step whose deliverable is one rule. The strip is gone.

  **The collateral door's semantic was re-ruled in the course of it (developer, 2026-08-02).**
  Fixing the crash forced the question the crash was hiding: `update_collateral` read BOTH `""` and
  a malformed value as "clear the link", so a forged field silently destroyed a real link under a
  success flash. Exactly `""` is the picker's own blank option and still clears; anything else
  naming no id is refused with the same `INVALID_COLLATERAL_LINK` answer as an id naming no
  account, and nothing is written. That constant is now NAMED in `account_validation.py` because
  the validator is no longer its only emitter.

  **A SECOND pair of adversarial reviews then found five more defects in the widened build**, and
  every one is fixed here rather than recorded for later:

  * **An ABSENT `collateral_account_id` still cleared the link**, because the field was read with a
    `""` default -- so the forged-POST case the step had just written four tests to refuse took the
    clear path anyway, under a success flash. Only a SUBMITTED `""` clears now.
  * **`RowId` broke `dump`**: marshmallow calls `_format_num` from `_serialize` OUTSIDE
    `_validated`'s try/except, so the rule escaped as a raw `ValueError` on `dump(0)`. It overrides
    `_deserialize` instead -- strictness belongs on the submission, not on the app rendering its
    own rows.
  * **`RowId` truncated a float**: `1.9` named row 1, which is `"007"` naming row 7 on the
    non-string path, in the field whose deliverable is one spelling per id.
  * **A THIRD lax surface, on the form door this step claims to own**:
    `transfers/_helpers.py`'s `request.form.get("source_txn_id", type=int)` -- the only
    `request.form` site among the 43 `type=int` uses, so N-142's "query-string" scope did not
    cover it.
  * **A FOURTH, inside the surface N-141 declared fully converted, and the gate could not see it**:
    `recurrence_pattern` is a `ref.recurrence_patterns` primary key in two schemas, and the
    completeness gate matched on a `_id` SUFFIX. **The gate is now an ALLOWLIST** -- 32 named
    non-id integers (counts, years, months, indices) -- so a row id called anything at all fails
    it, and a second arm fails on a stale allowlist entry.

  **A THIRD review pass then found eight more, and every one is fixed here.** Three mattered:
  the module docstring still claimed to own the QUERY answer while 42 `type=int` sites are openly
  N-142's (**a false completeness claim, which is the exact defect class this step keeps being
  caught on -- third time**); the converter's census excepted `version_id` as "a counter whose CHECK
  is `> 0`", which is wrong -- the two `<int:version_id>` parameters resolve to
  `escrow_component_versions.id`, an ordinary serial PK, so the census is STRONGER without the
  exception, and that census is the whole justification for overriding a Flask built-in; and the
  completeness gate was **blind to `fields.Int`**, marshmallow's documented alias for the same
  class, which an AST over source sees as a different token. The gate now matches both spellings
  and both assignment forms, controlled: a row id declared as `fields.Int` fails it.

  **Verified.** **56 new test functions / 63 collected cases, 0 removed** (7,728 at `HEAD` ->
  **7,790** in the working tree). The character-set assertions are EXHAUSTIVE over the codepoint
  space (all 878 non-ASCII `isdigit()` characters, all 128 that make `int()` raise) and each asserts
  a non-zero population FIRST so none can pass vacuously. **FIVE firing controls, every count
  re-measured against the final code rather than carried from an earlier build** -- the first
  version of this paragraph quoted 18 and 2, both from the build before the round-trip landed, and
  a review caught them:

  | control | fails |
  |---|---|
  | the full pre-fix rule (Unicode predicate + unguarded `int()`, no floor, no round-trip) | **79** suite-wide, **46** within this step's own tests |
  | Werkzeug's lax `<int:>` converter restored | 4 |
  | `register_url_converters` moved AFTER `_register_blueprints` | 5 |
  | the retired collateral semantic (strip + clear on anything unparseable) | 5 |
  | `RowId` gutted to a plain `Integer` | 11 |

  Every planted file was restored byte-identically from a scratchpad copy, verified with
  `sha256sum -c`. Suite **7,790 passed / 0 failed** under `America/New_York` and under CI's
  `TZ=Pacific/Kiritimati`, each run ALONE (a first attempt collided with a concurrent run on the
  shared per-worker test databases and both results were discarded); `pylint app/ scripts/`
  10.00/10; plan gate 17 passed. **Zero migrations and no figure can move** -- no balance producer,
  model or query is touched.

  **A dependency risk this step introduced, RAISED and then CLOSED in the same step (N-143).**
  `RowIdConverter` subclasses Werkzeug's `IntegerConverter`, and Werkzeug was **not pinned** --
  which turned out not to be "floating inside Flask's range" at all: Flask declares
  `werkzeug>=3.1.0` with **no upper bound**, and the Dockerfile installs with a plain
  `pip install -r requirements.txt` (no lock file, no constraints file), so every image rebuild
  re-resolved Werkzeug from PyPI and a 4.x release would have satisfied it silently. The exposure
  also PREDATED this step: `routes/static_pass.py` has always imported `werkzeug.security.safe_join`
  as its path-traversal guard, unpinned. **The developer ruled it pinned (2026-08-02)** --
  `Werkzeug==3.1.6`, which is this file's own rule rather than an exception to it, every entry there
  being a `==` pin of something the app imports directly. The pin is verified to BIND
  (`ResolutionImpossible` against a conflicting constraint, not a silent re-resolve), and the
  converter's import moved to the public `werkzeug.routing` namespace to narrow the surface.
- [x] **X-af** `test(periods): the fixtures build their window on the USER's clock` -- closes the
  MERGE-GATE half of **N-137**; the app half is deliberately NOT here and is **N-138**'s.
  **SHIPPED TO `main` 2026-08-02, PR #77, merge `dbee3812`** (commit `209e8b6c`), sequenced ahead
  of PR #76 because it is a merge-gate blocker rather than a feature. `main` itself failed 8 tests under CI's `TZ=Pacific/Kiritimati` outside the
  04:00-09:59 UTC window, so no PR could merge -- the **N-131** precedent, where the month-end clock
  fix took its own PR ahead of the work it was blocking.

  **It is a TEST-ONLY change, and that scope was ruled on a measurement rather than chosen for
  safety.** `conftest._today_relative_start_date` and 14 sites in `test_accounts.py` generated pay
  periods relative to the PROCESS day, so a fixture whose docstring promises "today falls in period
  4" put the USER's today in period 3 whenever the process day was a Monday. Fixing the fixtures is
  correct on its own ground -- they were asserting a property about the user's calendar using
  someone else's clock -- and it is what CI was actually red on.

  **Why the app-side default is NOT in this step, having been built and then reverted.** Moving
  `get_current_period` / `get_current_and_future_periods` to `display_today()` is defensible in
  isolation, but it moves "which period is it" while leaving "what day is it" on the process clock
  in the SAME call paths, and an adversarial review measured that converting three app sites from
  agreeing-but-wrong into **actively disagreeing**:

  * `dashboard_pulse_service.py:613` renders `today_offset` from `date.today()` against a
    `current_period` resolved on the display clock. Measured: `{'days_total': 13, 'today_offset':
    14}` with the app change, `0` on `main` -- and the function's own docstring asserts the
    invariant `start_date <= today <= end_date` that this breaks.
  * `routes/salary/_helpers.py:140` vs `:152` -- a WRITE path: the paycheck is computed for the
    display-clock period while regeneration starts at `effective_from=date.today()`, so the period
    the app calls current is never refreshed.
  * `routes/salary/cockpit.py:257` vs `salary_cockpit_service._window_with_index` -- the same
    `end_date >= today` predicate on the two different clocks in one render.

  **And neither review could certify completeness, because no existing gate can see this class**:
  `tests/test_services/`'s autouse `freeze_today` patches `date.today()` and `datetime.now()`
  TOGETHER, and `SHEKEL_FAKE_TODAY` calls `time_machine.travel` with a tz-AWARE target, which
  rewrites `os.environ["TZ"]` to the display zone -- so the weekly calendar sweep runs with the two
  clocks equal by construction and **cannot detect a clock split at all**. A step-3-style structural
  answer needs an instrument first. That is N-138's first task, not this step's.

  Verified **7,724 passed / 0 failed under BOTH `TZ=Pacific/Kiritimati` and `America/New_York`**,
  measured inside the failing window on 2026-08-02, with **zero `app/` changes** -- so no figure can
  move and no production behaviour changes. `pylint app/ scripts/` 10.00/10; plan gate 17 passed.

- [ ] **X-ag** `feat(pylint): lax digit acceptance is refused, not remembered` -- closes
  **N-139**. X-ae converted every submitted-id surface it found -- four `isdigit()` doors, the URL
  converter, 73 schema declarations, two local restatements -- and the AST now finds **exactly one**
  digit-predicate call site in `app/` and `scripts/`, `digit_strings.py:91`, the implementation of
  the replacement. Nothing stops the next one.

  **What this step must NOT do is what its first two drafts specified**, and both were refuted
  before it was written, which is why the instrument is an open question rather than a stated scope:

  * Draft one argued a checker is right here *because step 3 proved one would be blind at a
    bare-local site*. **That is the claim step 3's own adversarial review refuted and
    `anchor_settle_partition.md` 14.1 withdrew** -- adding the names to the vocabulary matches them,
    and resolving a `Name` to its `Assign` is one astroid hop, less exotic than `package_privacy.py`
    already does.
  * Draft two specified matching `isdigit` / `isdecimal` / `isnumeric` by METHOD NAME, and X-ae's
    adversarial reviews measured that gate reporting CLEAN over the defect it exists for: a bare
    `try: int(raw) / except ValueError` passes it and reintroduces the many-spellings defect --
    **and that is precisely the form the 2026-08-01 ruling specified and measurement rejected**, so
    the checker would have blessed the wrong fix. `re.fullmatch(r"\d+", "١٠٦")` matches for the
    same reason, and Werkzeug's converter and `fields.Integer` -- the two largest surfaces X-ae
    closed -- spell `isdigit` nowhere at all.

  **The trace's first job is therefore to decide what the SIGNAL is.** The defect is Unicode-wide
  digit acceptance, and it has at least four spellings (a digit predicate, a bare `int()` on a
  submitted value, a `\d` regex without `re.ASCII`, a lax field or converter class). A gate that
  catches one of the four while three stay open is the "reports success while missing its own site"
  shape this arc has already ruled worse than none.

  What is settled: a checker is the only AVAILABLE shape, because the receiver is `str` -- a builtin
  nobody can give a narrower type -- so there is no `ReconciledThrough` to write and the developer's
  *structural-over-detector* ruling has nothing to prefer. `anchor_settle_partition.md` 14.5 is the
  governing precedent: a type and a checker fence COMPLEMENTARY holes. `digit_strings`, `RowId` and
  `RowIdConverter` are the type-shaped half and fence only what routes through them.

- [ ] **X-ah** `fix(routes): a query-string id is parsed like every other id` -- closes **N-142**.
  The one submitted-id surface X-ae did not convert: 42 `request.args.get(..., type=int)`
  call sites, where Werkzeug catches the `ValueError` (so no crash) but the coercion is `int()` (so
  `'١٠٦'` is `106`, `' 2026 '` is `2026`, `'1_0'` is `10`).

  **It needs a per-site ruling, which is why it is a step and not part of X-ae.** The path
  parameters were all 123 row ids and the schema fields all 73 row ids, so each took one blanket
  rule. These are MIXED: `account_id` and `period_id` are row ids, but `year`, `month`, `offset`,
  `periods` and `show_all` are not, and `offset=0` / `show_all=0` are meaningful -- so a blanket
  `parse_row_id`, whose floor is `MIN_ROW_ID`, would silently refuse them. The step owes a second
  small rule in `digit_strings` for the non-id case: ASCII-strict, canonical, but admitting zero.

- [ ] **X-am** `refactor(status): the settled band has two members, not three` -- closes **N-177**.
  The `Settled` status carries **0 rows on both tables in production** and no writer anywhere
  assigns it: `StatusEnum.SETTLED` has four `app/` references and not one of them is an assignment
  to a row (`jinja_globals.py:63` registers a global no template reads, `state_machine.py:205`
  builds the transition map, `balance_predicates.py:145` puts it in `settled_status_ids()`). Its
  only door is the status `<select>` in the two full-edit popovers, which render every
  `ref.statuses` row and disable the illegal ones.

  **It is in this arc's scope because it is a member of the set the cash walk folds on.**
  `settled_status_ids()` is `{Paid, Received, Settled}` and every reader consumes the SET, never the
  member -- so the balance engine cannot tell the three apart, and `Settled` is `is_immutable` for
  the same reason `Paid` and `Received` already are. Its whole distinct meaning is one line of the
  transition map: `settled: {settled}`, terminal, no revert. **The step must decide whether that
  meaning is wanted at all** -- a deliberate archive lock is a defensible feature and an unreachable
  one is dead vocabulary; today it is the second wearing the first's clothes, because nothing tells
  the user what picking it costs them.

  Its trace owes three things before a line is written: whether any row anywhere (including the two
  clone databases) has ever carried it; what a delete does to `state_machine`'s two workflow maps
  and the `is_immutable` column; and whether the seam's **preserve-on-re-entry** rule has any other
  re-entry case once `Paid -> Settled` is gone (see N-177's row -- that rule is what stops archiving
  a payment from re-dating its money, and X-f1 depends on it).

- [ ] **X-an** `fix(loan): a payment is history from the day its money moved` -- closes **N-187**.
  Ruled **R-EK** (2026-08-04), sequenced immediately behind X-f1 because X-f1 is what gives the app a
  stored, user-correctable day to key on.

  **The resolver's replay/projection cut is the last place in the loan half that PROXIES the day the
  money moved.**  `is_confirmed_payment_eligible`'s upper bound is `period_start <= as_of` on
  `PaymentRecord.payment_date`, which `loan_payment_service` fills from the shadow's
  `pay_period.start_date`; the posted ledger dates the same payment's entry from the shadow's
  `settled_on`.  The step moves the CUT onto `settled_on` for a CONFIRMED payment -- the identical
  fact `posting_service._entry_date` writes -- so the two producers share ONE definition of "already
  happened" and the parallel run can assert at every boundary again.  A PROJECTED payment keeps the
  pay-period start: its cash has not moved, so the pay period IS the plan.

  **It deletes a proxy rather than adding a guard**, which is the standing instruction, and it does
  not make the resolver read the ledger: `settled_on` is a column on the transaction row, so the
  parallel run stays two independent producers rather than collapsing into a tautology.

  Three things its trace owes before a line is written:

  * **The complement must move with the cut.**  `_build_monthly_override:174` excludes a confirmed
    payment on the SAME field so replay and projection are exact complements (in XOR, never both and
    never neither).  Changing one without the other double-counts or drops a payment outright -- the
    very defect this step exists to close, re-introduced by the fix.
  * **The RATE key rides on the same field** (`replay_schedule`'s
    `period_for_date(periods, payment.period_start)`), deliberately NOT the due date -- finding
    **N-36**, because `_redistribute_to_distinct_months` INVENTS a due date for a biweekly collision
    and a rate keyed on an invented date would move a replayed balance.  `settled_on` is a fact by
    the same standard, so moving it is defensible; it changes a figure only for an ARM whose rate
    changes between the cash day and the pay-period start.  **Measure that set before deciding, and
    decide it explicitly** -- carrying a third date on `PaymentRecord` is the alternative and is
    worse (three dates for two facts, this arc's own root cause 1).
  * **The regression lock the re-point gave up.**  X-f1c's fixture now exercises early-against-the-
    INSTALLMENT; this step owes the early-against-the-PAY-PERIOD case as a parallel run that holds at
    every boundary, plus a schedule assertion that no installment date appears twice.  The
    reproduction and its control are recorded in ruling R-EK and are the acceptance criteria.

- [ ] **X-al** `fix(pylint): a duplicate-code disable that suppresses nothing is a finding` --
  closes **N-154**. `useless-suppression` is enabled in `.pylintrc` exactly so a stale disable is
  itself reported, and it is BLIND to a `duplicate-code` one. Measured both directions at X-d
  (2026-08-03): removing `_attribution.py`'s left `pylint app/` at 10.00/10, and planting one back
  left it at 10.00/10 with no `I0021`.

  **The blindness is upstream and not a misconfiguration**, so this repo needs a gate of its own:
  `duplicate-code` (R0801) is a close-time checker over a similarity graph rather than a per-line
  message, so pylint's suppression accounting has no line to credit the disable to. The shape most
  likely to work is a pre-commit arm that strips each `duplicate-code` disable in turn and fails if
  the tree stays clean without it -- the same "exercise the gate, do not read it" stance plan step
  X-t3's band gate was repaired under (Section 8). The instrument is not ruled here; the step's
  trace decides it, and it must be shown FIRING on a planted stale disable before it is believed.

  **FIFTEEN live `duplicate-code` disables remain in `app/` and not one has been re-measured**
  (counted 2026-08-03: 4 in `models/`, 5 in `routes/`, 6 in `services/`). X-d re-measured exactly
  the one it inherited; naming that limit is the point, because a sweep that checked one and
  reported "clean" is the completeness claim X-ae's own record says to distrust. The step's first
  deliverable is therefore the CENSUS -- how many of the fifteen suppress nothing today -- because
  that number decides whether the gate is a cleanup or a standing fence.

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

    **Its tier is WIDENED by finding N-115** (2026-07-28, out of X-u's design review, ruling R-BU).
    The five loaders above are the ones N-72 / N-89 / N-92 / N-93 named, and they do NOT cover the
    budget dashboard's tracks section, which pays twice per render for three more:
    `_load_dashboard_core_data` (the accounts query plus `get_all_periods` plus
    `get_current_period`), `_load_account_params` (the AccountType query, `LoanParams`,
    `EscrowLine` + versions, and the investment-params load), and
    `_get_current_paycheck_breakdown` -- **two full `calculate_paycheck` runs per render**, measured
    on both databases. The third is the expensive one and it is the reason this is not a tidy-up:
    the SECOND breakdown alone costs **7.2 ms / 7 SQL** in-request on `shekel` and `7.2 ms / 7 SQL`
    on `shekel_f3_final`, against the 9 SQL X-u's whole deletion removed. Its arguments all come off
    `core`, so the same commit collapses the three copies of
    `_get_current_paycheck_breakdown(user_id, core.all_periods, core.current_period)`
    (`_orchestrator.py`, three call sites) to one that takes `core`.
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

- [ ] **X-aj** `refactor(status): one status seam, and the fence is structural` -- rulings **R-DN**,
  **R-DO** and **R-DP**; closes **N-145** and **N-146**. **It runs BEFORE X-d** because N-145 blocks
  X-d and this is what answers it, and because it changes a mutation path X-d also touches -- shipped
  after X-d it would mix a status refactor into a writer swap's rollback.

  **It is not a line-count step and must not be built as one.** The 1000-line ceiling on
  `transfer_service.py` is the symptom; the cause is that the module carries a SECOND implementation
  of the transaction status seam, which is also why the W9907 allowlist has two entries. Merging the
  two is worth **-13** lines by itself; the module reaches **987** (from 999) only because
  `restore_transfer`'s four preconditions move out with it, which is **-54**, and the review fixes
  then added 50 back.  **Stated this way
  because an earlier draft credited the whole ~62 to the merge and that is false** -- the room X-d
  needs comes mostly from the extraction, so the extraction is a decision in its own right and is
  ruled as one (**R-DR**) rather than riding in as a side effect.

  **The step's own first action is the trace R-DP defers to it**, and no code is written before it:
  which structural write door replaces the checker. Three candidates, each with a real objection to
  weigh rather than a preference: a read-only `hybrid_property` over a renamed `_status_id` column
  (the 5 constructor kwarg sites and `Transaction(status_id=...)` itself stop working and need a
  factory); a value type only `verify_transition` can produce (nothing structurally prevents a caller
  constructing one, so the safety is a convention again -- the thing this step exists to stop); and a
  model-level operation that verifies inside itself (cleanest to use, but the model would import the
  state machine, inverting `CLAUDE.md`'s Routes -> Services -> Models rule). **The trace measures the
  blast radius before it rules**: 79 reads across 24 files, 5 constructor kwargs, the Marshmallow
  schemas, and the `filter_by(status_id=...)` / `filter(Model.status_id == ...)` query forms, which
  a `hybrid_property` must keep working at the CLASS level or the design is dead on arrival.

  **What is already measured and does not need re-deriving** is in R-DN's block: five direct writes
  in all of `app/`, the transfer map proved a strict subset of the transaction map with its control
  firing, zero bulk `status_id` writes, and 17 dynamic `setattr` loops the checker cannot see.

  **The controls this step owes**, each shown firing on a planted defect before it is trusted: the
  N-146 regression (an identity re-submit must not move `paid_at` OR the posted `entry_date` -- it
  fails at `HEAD` today, which is how the defect was found); R-DO's refusal, exercised on a shadow
  drifted to a status its parent cannot legally reach; and, once the write door lands, a control
  proving a bare `status_id` assignment and a dynamic `setattr` BOTH raise -- the second is the one
  that matters, because it is the class W9907 was blind to and therefore the class no existing test
  covers.

  **DECOMPOSED at the trace, on a measurement, and the split is forced rather than chosen.** Merging
  the two seams does NOT by itself shrink the allowlist, because `transfer_service` also writes a
  status through two CONSTRUCTORS -- `Transfer(status_id=spec.status_id)` (`:298`) and
  `_build_shadow`'s `Transaction(status_id=xfer.status_id)` (`:149`) -- and W9907's constructor rule
  admits only a born-PROJECTED value. Those two writes are the same question R-DP has to answer
  anyway (under a read-only attribute, `Transaction(status_id=...)` stops working at all five
  constructor sites in `app/`), so they belong to the write-door leaf and not to the seam merge.

  * [ ] **X-aj1** the seam merge and what the two reviews turned it into. Closes **N-146** and lands
    **R-DN**, **R-DO**, **R-DR** and **R-DS**. Takes `transfer_service.py` to 987 lines -- 13 of
    headroom against X-d's ~9, which fits and is thin (**N-152**) -- so **X-d unblocks here**; `transfer_service` stays in the W9907 allowlist until X-aj2, for the two
    CONSTRUCTOR writes the merge does not touch. **Three commits, by ruling R-DR**, so a slip in one
    mechanism cannot read as a slip in another:

    * [x] **1** (`1688f508`) -- **R-DR**'s extraction: the three EXISTING preconditions move to
      `_transfer_validation` unchanged, and the three hand-written `is_deleted` rollbacks go with
      them because validating before mutating makes them unnecessary. 999 -> 938. **First, because
      the merge does not fit without it** (measured: merge-first reaches 1015).
    * [x] **2** (`63514efc`) -- **R-DN**'s seam merge. `_apply_status_change` deleted;
      `status_seam.apply_status_change` takes either row type; `verify_transition` /
      `allowed_transitions` take the ROW instead of `(id, context)`; `_build_transitions` loses its
      string parameter and the unreachable `ValueError` that came with it. Closes **N-146**, and
      lands **R-DS**'s pair instant, which the merge needs to be correct.
    * [x] **3** (`1e75d0ce`) -- **R-DO**'s refusal (the fourth precondition) and **R-DS**'s restore
      half, with five controls. Last, because both land INSIDE the function commit 1 creates.

    **Both adversarial reviews earned their cost a THIRTEENTH time, and the sharpest finding was
    inside the fix again.** The correctness review MEASURED that routing the drift repair through
    the seam stamped `paid_at = now()` on a shadow that had none -- N-146's own defect class, on the
    restore path, created by N-146's fix (**R-DS**). The design review measured that the room X-d
    needs comes mostly from an extraction nobody had ruled (**R-DR**), and that R-DN(a)'s allowlist
    claim was false and contradicted by this document's own step entry. **Six claims of mine were
    wrong and are corrected in place rather than quietly**: the ~55-line attribution (three places),
    R-DN(a), R-DO's "all five", the second `setattr` site (a `TransferTemplate`, which carries no
    such column), "all three models", and a `hasattr` lesson cited to **R-CQ**, which is the
    classifier rename -- that one had already reached `app/`. **Three mutants were run and all three
    now die**; two survived the controls as first written, and the atomicity control was asserting on
    the row the applier writes LAST, which is a control that cannot fail.
  * [ ] **X-aj2** the structural write door and the DELETION of W9907 (**R-DP**), which is also where
    the born-status rule is ruled -- carrying **N-149**. Its trace decides the three candidates
    R-DP names, and it must rule what a row may be BORN as: today `create_transfer` runs no legality
    check at all, so "every row is born Projected and every other status is a verified transition"
    is available and is the rule that makes the constructor question disappear -- but it would refuse
    creations the tests currently make (Received, Cancelled, Paid), and one of those, **Received, is
    not in the transfer map at all**, so the refusal would be correct and the test wrong. That is a
    behaviour change on a creation path and gets its own worked ruling, not a build decision.

- [ ] **X-ak** `refactor(transfers): a shadow inherits its parent's fields by ONE rule` -- closes
  **N-148**. **Root: the transfer -> shadow mirror is written THREE times and the three already
  disagree.** `_build_shadow` states it at construction (`transfer_service.py:143-160`),
  `update_transfer` states it per-field on edit (`:582-652`), and `restore_transfer` states it again
  as drift repair (`:898-972`) -- and `scenario_id` is mirrored at construction (`:148`) while being
  absent from the drift-repair list, although that function's own docstring claims it re-syncs
  "every field the service mirrors from the canonical parent" (`:779-781`).

  **Transfer Invariants 3, 4 and 5 are `CLAUDE.md` CRITICAL invariants and they currently rest on
  three lists staying in step by memory.** Adding a mirrored field means remembering three places;
  the measured proof that this does not hold is that it already has not.

  Not a live money defect on today's data -- nothing in the application edits a transfer's scenario,
  so the one disagreeing field cannot drift by any application path -- which is exactly why rule 7
  applies: a finding that costs `$0.00` today is a defect waiting for the data to change. **Sequenced
  after X-aj** because X-aj deletes one of the three statements' status half, and unifying a rule
  while one of its statements is being deleted decides half a design.

  **It is NOT folded into X-aj, by the developer's ruling of 2026-08-02**: unifying the mirror
  CHANGES behaviour (restore would begin repairing `scenario_id`), and X-aj's whole value is being
  provably behaviour-neutral apart from the changes R-DN, R-DO and R-DS name. Mixing them is the
  shape ruling R-DM refused -- "the mix that makes a plumbing slip read as a fold slip".

  **RE-SCOPED 2026-08-02 by X-aj1's adversarial design review, and the re-scope inverts the step's
  first action.** As originally written this step unified three mirror STATEMENTS into one copier --
  which would have made a denormalization cheaper to maintain, and this document has already ruled
  that exact shape out of existence twice. R-DH (d) deleted `TransactionEntry.is_cleared` as *"a
  denormalized copy of a derivable fact -- the `Account.current_anchor_*` disease X-e is already
  removing"*, and X-e rules that column *"a reconciled cache or it is nothing"*. **A shadow's
  `status_id`, `pay_period_id`, `estimated_amount`, `due_date` and `is_override` are the same shape**
  (finding **N-150**), so this step RULES THE COPY FIRST and only then decides what to do about the
  copiers. Three options, and the trace measures before it picks:

  * **remove the copy** -- readers resolve through the parent. The counterweight is real and is why
    this is not obvious: Transfer Invariant 5 says the balance calculator queries ONLY
    `budget.transactions`, so a shadow's own columns are load-bearing on every read path (79 Python
    reads plus 26 Jinja reads of `status_id` alone), and a shadow that does not look like a
    transaction is the whole mechanism gone;
  * **make it structural at the DATABASE** -- a deferred constraint or trigger asserting the
    equality. This is the only form a bulk `UPDATE` cannot bypass, which matters because finding
    N-65 measured that the database is reached three ways and no session listener sees the third;
  * **keep the copy with its cost stated**, after which unifying the copiers is the right follow-up
    and this step proceeds as originally written.

  Only the third leaves X-ak as a copier-unification. **The step may not skip the question**, which
  is what the original scoping did by treating "three statements" as the defect rather than as the
  symptom.

- [ ] **X-d** `fix(cash): the posted account ledger is a checked projection` -- E1a's shape for
  cash. The posting writer consumes X-a's walk instead of its own, and the per-visible-date assert
  (`sum(postings) == fold(ACTUAL events)`) makes a stale posting a detectable, repairable cache
  inconsistency. Ship-gated on a prod-data sweep for walk-invisible legacy rows, exactly as E1a
  was; any found row is an F1-class human decision, never a silent exclusion.

  > **PARKED AGAIN 2026-08-03, by the developer's ruling, and this time on a
  > DESIGN defect its own adversarial reviews found.**  The work is five commits
  > on `feat/xd-checked-projection` (off `feat/one-status-seam` = PR #80):
  > `fb8efb9f` rulings, `15773163` code, `78d476de` as-built, `0d539fcf` the
  > green review residue, and one deliberately-RED commit carrying the
  > regression test for the defect.  **Nothing here is for merge.**
  >
  > **The defect is finding N-155, and it is the step's own assert.**  The
  > checked-projection assert compares an account's WHOLE linked ledger against
  > its WHOLE source-row walk, but it rides on the PER-ROW write path (the
  > self-heal at each sync's tail).  Ruling R-DM ordered the DELETE window
  > correctly and missed that the identical window exists in every BATCH loop:
  > any operation that settles N rows of one account and posts them one at a
  > time grades a half-finished state and REFUSES the write.  Three confirmed
  > production defects, one of them reproduced twice independently.  **The
  > developer ruled the placement its own design step rather than patching the
  > loops** -- batching at each site would make the ordering an obligation four
  > or more callers must remember, which is the shape R-DM itself rejected.
  >
  > **What was built and is worth resuming** (all verified green before the
  > regression test was added): 7,799 passed / 0 failed under BOTH
  > `America/New_York` and CI's `TZ=Pacific/Kiritimati`; `pylint app/ scripts/`
  > 10.00/10; the checker package 10.00/10 with its 146 unit tests; the plan
  > gate.  On the dev-runtime production clone the read baseline is
  > BYTE-IDENTICAL (9 accounts, 427 grid cells, 5,978 daily points) with the
  > diff shown to fire on a planted one cent; and the WRITE path reconciles that
  > clone to **0 transactions / 0 transfers changed**, census unmoved at 318
  > entries / 643 postings / trial balance `$0.00`, with the assert passing on
  > all 7 non-loan accounts against a ledger the DELETED walk wrote.  That probe
  > was shown to fire too (a one-cent correction mutation takes the census to
  > 389 / 785 **while the trial balance still reads `$0.00`** -- which is why it
  > counts entries and not the balance).
  >
  > **The parts that are unambiguously right and that the redesign should
  > keep:** the writer swap itself (`account_posting_service/_walk.py` deleted
  > whole, the writer consuming `cash_ledger.walk_cash_ledger` -- ruling R-H
  > delivered at last); R-DJ's two day types, now fenced on BOTH operands
  > (N-135 discharged); R-DK's skip deletion; R-DL's N+1 hoist (`70.87 ms` over
  > 110 SQL statements to `11.13 ms` over 12); R-DM's `retire_transaction`
  > chokepoint collapsing the four delete sites; and R-DT's one re-derive name.
  > What is in question is only WHERE the assert runs.
  >
  > **Resume list, in order:**
  >
  > 1. **X-ai decides the placement**, widened by this to the whole question
  >    rather than only the commit-boundary hook.  The commit boundary is the
  >    only moment definitionally the end of an operation, which is what R-DM
  >    already said and what N-155 is the evidence for.
  > 2. Re-land X-d's writer swap behind that decision.
  > 3. The residue findings this step's two reviews opened and did not take:
  >    **N-156** (the size gate split a second module), **N-157**
  >    (`resync_anchor_postings` is not the chokepoint its docstring claims),
  >    **N-158** (the assert's sign convention is an unnamed operator in each
  >    caller), **N-159** (transfer retirement is two halves a caller must pair).
  > 4. The citation sweep this step's own record over-claimed: four `ledger_before`
  >    references survive in `app/` against a "13 stale citations repaired"
  >    claim, and `test_cash_walk.py:270` still names the deleted walk.  **Add
  >    one more, found 2026-08-03 by the X-ai loan trace:** `posting_resync.py:47`
  >    cites `loan_posting_service._sync._resync_stale_transfers`, a name that
  >    exists nowhere in the repo or in its history (`git log -S` returns
  >    nothing); the function is `_reconcile_lineage_transfer_entries`.  Same
  >    class as the `_sync_postings_after_update` repair the parking commit made,
  >    in the module that commit did not re-read.

  **PULLED FORWARD 2026-08-01, past X-ad / X-x / X-y / X-i / X-j / X-k / X-l / X-m / X-n, by the
  developer's ruling that the partition's fence be structural rather than a detector**
  (`anchor_settle_partition.md` Section 14). Step 3 removed the duplicate RULE; what survives is
  the duplicate DATA -- the read walk folds transaction rows and this walk folds the posted copy of
  the same events -- and R-H already ruled that only one walk closes it. **None of the nine steps
  it passes is stated as a prerequisite**; that order is priority, not dependency, and it was
  re-checked at this ruling. Two things make the step smaller than its entry implies: the two
  absorb loops are now textually identical (step 3 made them so deliberately, so this is a
  DELETION), and `cash_ledger._walk.dated_deltas`' docstring already specifies what the writer
  books and in which sign. Two things make it no smaller: it changes a WRITER, so it rides alone
  in its own PR per this document's own rule; and its residue arm has no counterpart on the read
  side -- `_residue_source_days` reads postings whose source row is gone, which a source-row walk
  cannot see by construction, so this step must decide whether that defence moves to the
  checked-projection assert or is ceded (named at `cash_ledger._walk`'s module docstring so the
  decision is made rather than discovered). It also inherits `ledger_report_service/_attribution.py`'s
  two duplicate date loaders and the `duplicate-code` disable holding them apart, which step 3
  deliberately did not extract into a third shared home.

  **X-d CARRIES AN EXPLICIT OBLIGATION from step 3, ruled 2026-08-01: make
  `CashAnchorFact.observed_on` and `CashSourceFact.settled_on` non-bare.** Step 3 fenced the
  derived boundary (`cash_ledger.ReconciledThrough` has no ordering against a civil day) but left
  those two FIELDS as plain `date`s, so `x <= fact.observed_on` -- the exact line step 3 deleted --
  still compiles in any new module. The developer ruled the fields must be wrapped too and ruled it
  SEQUENCED HERE rather than done at step 3, on a measurement: after step 3 every remaining read of
  the two is a legitimate raw-date use (period bucketing x2, day-keying x3, the journal entry's
  `entry_date` column, the loader's sort key), it needs TWO distinct types rather than one (a single
  shared type would still compare a settled day against an observed one), and the hazard it closes
  needs a THIRD walk over `cash_anchor_facts` -- which this step deletes, halving the surface the
  wrap has to cover. Doing it here wraps a settled surface once instead of wrapping and re-cutting.
  **It is an obligation, not a nice-to-have: a fence whose limits are stated only in a docstring is
  a convention, and this document's own Section 8 rules a label weaker than a predicate.**

  **It does NOT carry N-136, and the first draft of this paragraph said it did.** That draft
  claimed X-d "reopens this cluster"; a neutral review asked for the citation and there is none --
  this step's scope is the posting walk, `dated_deltas`, `_residue_source_days` and
  `ledger_report_service/_attribution.py`, and it opens no route file. N-136 is owned by **X-ae**,
  which is the step that actually touches the three parse sites. Recorded rather than silently
  re-homed, because an ownership claim is subject to this document's own citation standard.
- [ ] **X-ai** `refactor(posting): the posted ledger gets one verb and one trigger` -- the END
  STATE ruling **R-DM** named, **R-DU** ruled, and this entry decomposes.

  **RULED 2026-08-03 (R-DU): this is the RESTRUCTURE, not a placement move, and it lands on BOTH
  ledgers.**  The developer ruled the root over the three options put, explicitly accepting the
  larger scope ("correctness and best practice takes precedence over everything ... I want to make
  the fences structurally unnecessary").  The step's own subject changes with it: **the row-level
  posting writer stops being the interface.**  You do not tell the ledger "this row settled"; you
  tell it "account A in scenario S changed; re-derive it from its facts."  Everything below is the
  decomposition of that sentence.

  **RE-RULED 2026-08-03 (R-DV..R-DY) INTO THE FROM-SCRATCH MODEL, after X-ai-0's two adversarial
  reviews.**  R-DU's direction stands and its DECOMPOSITION does not: R-DU named the re-derive's
  SCOPE (the account) and never named what OWNS a journal entry.  **An entry is the projection of
  exactly ONE source event, and the event owns it; the account is the scope of the loop, never an
  owner.**  That one answer is what makes N-161 and N-162 die by construction instead of being
  fixed, deletes two helpers rather than porting them, and removes the write OSCILLATION that
  account-owned entries would have created on the largest movements in the ledger (R-DW).

  **THE SEVEN SUB-STEPS, in the order the design forces** (rule 6's convention for a decomposed
  step: one checkbox, ticked with the last of its commits).  **X-ai-r runs FIRST and carries NO
  migration** (ruling R-DZ): the R2 violation N-161 names is a key-shape change in two files, and a
  draft of this plan had scheduled it behind a migration on a misattributed root cause.

  * **X-ai-0 `MEASURE`, and nothing is designed until it reports.** Two numbers, both currently
    unknown and neither guessable: (1) the per-write cost of a whole-account cash re-derive on the
    production clone -- the real Checking account is 139 settled rows and 55 assertions, against
    R-DL's measured `11.13 ms` over 12 SQL statements for the anchor half alone; (2) how many suite
    commits a registry-scoped hook would grade, and how many of those are FIXTURE states rather than
    writer states, via a no-op `before_commit` listener that only records the `(account, scenario)`
    pairs it would grade. Grep cannot answer the second honestly, and X-d is the precedent for what
    guessing costs: it found N-155 from a red suite.

    **Two things about HOW, verified 2026-08-03 so the step does not open by hunting.** (1) **The
    cost half is measured with a scratch probe, not against shipped code** -- a whole-account cash
    re-derive does not exist yet, because building it IS X-ai-a. The probe drives
    `cash_ledger.walk_cash_ledger` plus the existing per-source reconcile over the account's settled
    rows; both are on `dev` today. (2) **The measurement style and its control already exist and
    should be copied, not reinvented**: R-DL's hoist is pinned by asserting the SQL STATEMENT COUNT
    rather than elapsed time (`TestTheControlsPlanStepXdOwes`,
    `tests/test_services/test_account_posting_service.py` on `feat/xd-checked-projection` at
    `2b11aaed`), which is what makes the number survive a slow CI box. **The substrate is ready**:
    the dev-runtime database is at the repo's migration head `d7c1f4a9e603` and reads **318 journal
    entries / 643 postings / 9 accounts / trial balance `$0.00`** -- the same census X-d's as-built
    record measured, so it is that prod-shaped clone and needs no re-clone before the first number.

    **AS MEASURED, 2026-08-03, AND TWO NEUTRAL ADVERSARIAL REVIEWS THEN REFUTED THREE OF THIS
    RECORD'S OWN CLAIMS.**  What follows is the corrected version; the withdrawn claims are kept
    visible at the end, because two of them were the sentences the affordability argument rested on.
    The substrate was re-verified rather than assumed (migration head `d7c1f4a9e603`; 318 entries /
    643 postings / 9 accounts / trial balance `$0.00`; Checking at exactly the 139 settled rows and
    55 assertions this entry claimed).

    **(1) WHAT IS MEASURED, and it reproduces to the digit.**  A second party re-ran every probe
    independently: all eight SQL statement counts reproduce EXACTLY and the elapsed figures within
    3.4%.  Every row is steady-state -- 0 journal entries emitted -- so these are no-op costs:

    | shape | Checking (139 rows, 55 assertions) | what it includes |
    |---|---|---|
    | today: ONE settled row's `sync_transaction_postings`, at target | **`4.15 ms` / 7 SQL** | what a single write costs now |
    | `walk_cash_ledger` + the per-source reconcile looped + the anchor sync | **`406.01 ms` / 696 SQL** | **NOT a re-derive -- see (4)** |
    | the batched DETECTION reads | **`7.74 ms` / 8 SQL** | reads only: no reconcile, no emission, no assert |
    | `sync_loan_postings`, the one verb AS BUILT | Mortgage **`26.96 ms` / 38 SQL** | walk + 3 reconciles + the E1a assert |

    **The load-bearing fact is the RATIO, and it survived every attack: 8 statements against 696.**
    573 of the 696 are the per-row loop at **4.12 statements per source** (139 syncs), which is
    R-DL's N+1 shape again on the SOURCE half.  The detection floor is quoted at **8** rather than
    the 7 the probe issues because the probe is HANDED the owner id its caller already resolved,
    where `reconcile_account_anchor_corrections` pays `account_owner_id(account_id)` itself -- a
    correction from the review, so the honest contrast is 8-for-a-whole-account against 7-for-one-row
    rather than the tidier 7-against-7 an earlier draft printed.

    **X-ai-a MUST BUILD A BATCHED RECONCILE AND MUST NOT LOOP THE PER-SOURCE ONE.**  That conclusion
    is carried by the statement ratio alone, deterministically, and needs none of the withdrawn
    inferences below.

    **(2) WHAT IS NOT MEASURED, stated plainly because an earlier draft claimed it was.**  **Nobody
    has measured what a BUILT cash verb costs.**  The `7.74 ms` is detection only; the `26.96 ms`
    beside it is a loan carrying **28 payments and 1 anchor** against Checking's 139 facts and 55
    assertions, so it is not a proxy for the cash workload.  An earlier draft concluded "a built cash
    verb lands in the shipped loan verb's class (`21-27 ms` / 31-38 SQL)" and that is **WITHDRAWN**:
    it is an extrapolation across a 5x fact count and a 55x assertion count, offered in the one step
    whose own rule is that nothing is designed until the measurement reports.  **X-ai-a owes this
    number once the verb exists**, pinned as a statement count in the R-DL style.

    **(3) THE HOOK'S SCOPE.  A no-op `before_commit` listener graded nothing and recorded only the
    pairs, over the full suite** (7,798 passed / 0 failed on every run):

    | scope | commits |
    |---|---|
    | suite commits, total | **16,169** |
    | **a REGISTRY-scoped hook** (R-DU's answer) | **11,917 (73.7%)**, over **12,459** pairs |
    | a full session-inspection accumulated across every flush | 10,347 |
    | reading `session.dirty` AT `before_commit` | ~830 |

    **Nothing fans out**: 11,402 of the 11,917 grade exactly one pair, 495 grade two, 13 three and 7
    four.  The maximum over 16,169 commits is **4**.

    **The registry figures are corrected DOWN from an earlier draft's 11,953 / 12,558, because the
    probe leaked.**  It keyed its registry on `id(session)`, and the suite's per-test teardown calls
    `session.remove()`, which fires NEITHER `after_commit` nor `after_soft_rollback` -- so a
    non-committing test left an entry on a dead address and CPython recycled it.  Found by an
    adversarial review, which measured the contamination directly (~1% of commits, ~1.5% of pairs,
    always INFLATING the registry) before the fix; re-keying on a token in `session.info` moves the
    count to 11,917 / 12,459.  **The instrument had the defect the arc keeps finding in its own
    controls, and only a second party exercising it found it.**

    **The session-inspection figures are NOT stable and are quoted to two significant figures
    deliberately.**  Across three runs the weak form read 683, 731 and 826, and the blind-spot count
    3,996, 3,432 and 3,431 -- and the runs are not repeats of one measurement, because the inspector's
    model list changed between them.  An earlier draft called these figures stable on the strength of
    the REGISTRY halves agreeing; that was a claim about the wrong column and is **WITHDRAWN**.  What
    is robust is the DIRECTION, which every run agrees on and which is the only thing the fork needs:
    session inspection is both **over-inclusive** (1,861 commits carry a source row no writer posted)
    and **under-inclusive** (3,431 commits ran a writer while the inspector saw nothing).

    **The join argument was BACKWARDS, and correcting it strengthens it.**  An earlier draft said the
    cash fact model is "three tables that all carry `account_id`" against a loan model needing a join
    through `EscrowComponentVersion.line_id`, and counted 143 of those.  `TransactionEntry` carries
    `transaction_id` and NO `account_id`, and `settled_cash_leg` reads a row's credit entries -- so
    it is a cash fact needing exactly the same join.  Re-measured with it in the inspector:
    **1,425** entry rows passed a flush naming no account, **ten times** the escrow figure, plus
    **84,506** `PayPeriod` flush-passes that name no account at all.  The join problem is
    overwhelmingly on the CASH side, which is a stronger argument for the sync-populated registry
    than the loan-only framing it replaces.

    **What the hook costs the suite, priced LIKE FOR LIKE** -- an earlier draft priced the batched
    row at fixture scale and the assembly row at the real Checking account's, which is a category
    error of roughly 28x and is **WITHDRAWN** along with the "85 minutes on a 65-second suite" figure
    it produced.  At fixture scale (the clone's 1-row and 7-row accounts, the only proxies that
    exercise the source half at all) the assembly costs `14.13-29.96 ms` per pair and the batched
    reads `2.95-3.31 ms`, so over 12,459 pairs: **`+176` to `+373 s` assembled against `+37` to
    `+41 s` batched.**  Batching is worth **5-9x** on the suite, not the 165x the mismatched
    pricing implied.  **And the class of cost is not new**: the suite already makes **1,849
    `sync_loan_postings` calls**, so a whole-account re-derive is already its ordinary cost on the
    loan side.  Writer census: `sync_account_anchor_postings` 11,152, `sync_loan_postings` 1,849,
    `sync_transfer_postings` 1,478, `sync_transaction_postings` 498.

    **(4) THE MOST VALUABLE OUTPUT IS NOT A NUMBER: the probes' BLIND SPOTS are X-ai-a's design
    requirements.**  A second reviewer planted defects the instruments could not see, and each one
    names something the verb must do that the plan's own sentence does not say:

    * **A walk-driven re-derive CANNOT SEE A SOURCE THAT LEFT THE SETTLED SET** (finding **N-162**).
      Reproduced twice independently: flip one settled Checking row to Projected and the walk drops
      from 139 facts to 138, the assembly emits **0** entries, and the row's legs
      (`{8: -105.36, 18: +105.36}`) stay posted forever.  X-ai-a's sentence -- "every source fact's
      entry reconciled to target" -- inherits the hole verbatim, because the walk names only settled
      rows.  **The verb's source set must be the UNION of the walk's facts and the ledger's
      already-posted source links.**
    * **A per-source ATTRIBUTION corruption is invisible to a per-`(period, date, ledger)` diff.**
      Re-point one entry's `transaction_id` at another transaction and the account's per-date nets do
      not move, while the shipped per-source writer proves the ledger is off target by emitting a
      correction.  Any assert the verb ships must key finer than the account's dated nets, or state
      that it does not.
    * **A transfer is valued by TWO rules and the verb inherits the disagreement.**  The walk values
      this account's leg with `settled_cash_leg(shadow)`; `posting_service._settle_effective` values
      it with `COALESCE(actual, estimated)` on the INCOME shadow, with no credit term.
      `cash_ledger/_events.py:265-277` already names this ("two rules that happen to agree", held
      only by Transfer Invariant 3) and assigns it to X-d.  Measured now: change the income shadow's
      `actual_amount` and the FROM account's diff does not move while the writer's target does.  **A
      verb reading only its own account's facts inherits that blindness**, so X-ai-a must consume one
      valuation rule or except the transfer path explicitly.
    * **A mis-stamped `source_kind_id` makes a correction invisible to its own reconcile** and it is
      then double-posted.  `posted_correction_legs` filters on source kind, so the kind is load-bearing
      identity, not a label.

    **(5) AND THE MEASUREMENT FOUND A LIVE RULE VIOLATION: finding N-161**, re-rooted below after
    the review showed the first diagnosis was the probe's key choice rather than the defect.  The
    anchor-correction reconcile does not obey the R2 attribution rule the source reconcile obeys, and
    `_anchors.py` implements R2 correctly in its own defensive branch while violating it in the
    branch that runs for every ordinary correction.

    **THE WITHDRAWN CLAIMS, listed so a later step does not cite one:** (a) "a built cash verb lands
    in the shipped loan verb's class" -- not measured, an extrapolation across a 5x/55x workload gap;
    (b) "`+5,099 s`, 85 minutes on a 65-second suite" -- priced two rows of one table on two different
    scales; (c) "the two censuses differ by 2 commits, so the count is stable" -- true of the registry
    columns, asserted of the session-inspection ones, which move 4-20%; (d) "a clean diff proves the
    batched target agrees with what the shipped writers posted" -- it agrees where the data does not
    exercise the difference, and the ONE group that does exercise it is what became N-161; (e) the
    anchor cross-check called "an independent reproduction" of R-DL's `70.87 ms` / 110 -- it is the
    same unhoisted code path measured twice (R-DL's hoist rides on X-d's parked branch), the counts
    differ 118 against 110 because the two are scoped differently, and what survives is only the
    weaker claim it was offered for: the probe drives the real reconcile and not a stub.

  * **X-ai-r `R2 FOR CORRECTIONS` -- NO MIGRATION, and it ships FIRST and ALONE.**  Ruling R-DZ,
    **whose TARGET-side half is SUPERSEDED by ruling R-EA (below): the period is DERIVED from the
    assertion's day, not read from the row.**
    `_posting_reconcile.posted_correction_legs` gains `JournalEntry.pay_period_id` in its
    `extra_columns` (and so in `summed_posting_legs`' `GROUP BY`), the correction key becomes
    `(source_kind_id, pay_period_id, entry_date)` in both `account_posting_service/_anchors.py` and
    `loan_posting_service/_anchors.py`, and `_anchors.py:187`'s `periods[key] =
    correction.anchor.pay_period_id` stops being the attribution -- the period comes from the KEY,
    which is the period of the postings being corrected.  **Two files plus the loan twin; no schema
    change, no backfill, no data decision.**  It closes N-161's rule violation: the anchor reconcile
    starts obeying the same R2 rule the source reconcile has obeyed since the 2026-07-02 review.
    `_posted_only_key_period_id` is DELETED by it: a target-less key now carries its own period.
    **What it does NOT close is the second half of N-161** -- two same-day assertions still merge
    into one key, so the per-ASSERTION split waits for X-ai-s.

    **AS BUILT (2026-08-03).  The R-DZ figures below were REPLACED before the step shipped, and the
    reason is ruling R-EA.**  A draft of this entry read *"on the clone it emits `-$3,054.36` in
    period 5 and `+$3,054.36` in period 6 ... the step's gate is a full re-derive of the clone
    reporting every moved `(period, ledger)` cell with the 2026-06-03 pair expected and anything else
    a defect."*  That gate stands; **its expected figures do not.**  As shipped the step emits
    `-$2,854.36` in period 5 and `+$2,854.36` in period 6, landing period 5 on `+$200.00` and period
    6 on `$0.00` -- because R-DZ's target key (the row's STORED period) was refuted by an adversarial
    design review and by measurement.  See R-EA for the trace; the short form is that the stored
    column is a CACHE of the same day-to-period derivation, the row that exposed this was written by
    a broken clock, and projecting it would have put the posted ledger permanently at odds with the
    grid.  **Measured on a clone of PRODUCTION, not dev**: 4 of 389 ledger cells move (all the
    2026-06-03 Checking pair), **0 of 12,636 rendered figures** move, the trial balance holds at
    `$0.00`, and a second re-derive emits nothing.  Positive control: the same re-derive at `HEAD`
    on the same clone emits **0 entries**, so every delta is attributable to the key change.  Five
    firing controls, all five RED at `HEAD`.  It ships a data change with no migration -- the deploy
    hook runs `backfill_all_account_anchor_postings_after_migration` unconditionally
    (`entrypoint.sh` -> `scripts/init_database.py`), so prod gains exactly **+2 entries / +4
    postings** at deploy.
  * **X-ai-s `THE SOURCE IDENTITY` -- the migration.**  Ruling R-DY's exclusive arc: three new
    nullable FKs (`account_anchor_history_id`, `loan_anchor_event_id`, `loan_params_id`), all
    `ON DELETE SET NULL`, plus ONE named CHECK -- **at most one non-null, agreeing with
    `source_kind_id`**.  Never "exactly one": that was measured to break every source hard delete,
    because SET NULL is an UPDATE and an UPDATE is CHECK-validated.  **The arc must not hardcode
    `ref.posting_sources` serial ids**; this step rules between pinning them in a migration and
    joining a `posting_sources`-side column, and does not put integer literals in DDL.

    **The backfill is a STATED RULE with a measured residue, not a match.**  A first draft claimed
    each of the 129 FK-less entries resolves to exactly one event by `(account, source kind,
    entry_date)`; measured, that gate is unmeetable three ways.  (a) **10 entries have NO candidate
    event** -- five net-zero pairs at 2026-04-29, 05-06, 05-15, 06-16 and 07-07, dates on which the
    account has no anchor row at all.  They are R-DH residue: written when the entry date was
    `_utc_civil_date(asserted_at)`, then reversed AT THEIR OWN DATE when R-DH moved the day to
    `observed_on`, so their date is by construction a day no assertion was observed on.  Each
    corresponds to a history row observed the day BEFORE, so the rule that resolves them is
    **`created_at`'s civil date**, stated as a second backfill rule rather than discovered
    mid-migration.  (b) **THREE groups share `(account, observed_on)`, not one** -- 2026-04-15 (rows
    22/23/24, a TRIPLE), 2026-05-07 (37/38) and 2026-06-03 (49/50).  (c) **The mapping is not 1:1 in
    either direction**: 2026-04-15 has 3 events against 2 posted entries and 2026-05-07 has 2 against
    1, because reconcile-to-target emits DELTAS.  **So the backfill attributes each legacy entry to
    the LAST history row of its merged key** -- which is not a guess but a reading of the rule the
    ledger was written under -- and the gate is that every one of the 129 resolves under one of the
    two stated rules, with the count of entries per event REPORTED rather than required to be 1.

    **That justification was `_account_anchor_correction_targets` "already files a merged target at
    the latest row's period", and X-ai-r FALSIFIED it** (ruling R-EA): it now files by the period
    CONTAINING the assertion's day.  The 129 must be re-censused against the post-X-ai-r ledger
    before this migration is written -- the 2026-06-03 group in particular collapses from two entries
    spanning periods 5 and 6 to one in period 5, so "the last row of its merged key" is answering a
    question whose shape changed.
    **The FK is nullable and the CHECK is at-most-one, so an unresolvable entry is representable**
    rather than blocking the migration.

    It ships alone in its own PR (it carries a migration).  **It moves no figure**: the FK is written
    and read by nothing until X-ai-a.  The anchor-period data repair is NOT here -- see N-168.
  * **X-ai-a `THE ONE VERB, cash -- event-owned entries, account-scoped loop.`**
    `posting_service.rederive_account(account_id, scenario_id)`: one batched pass computing a target
    per OWNING EVENT.  Seven properties, each ruled:

    1. **The event set is a UNION** (R-DV, N-162): the walk's facts UNIONED with the distinct source
       links already on the ledger, so an event that left the settled set reconciles to zero.
       Measured: without the union a reverted row's legs stay posted and the re-derive emits nothing.
       **It covers three of N-162's four forms**; a HARD-deleted row's link is already NULL, so the
       ledger side names nothing and ruling R-DI still owns that fourth case.
    2. **The reads are BATCHED** (X-ai-0): 8 SQL statements against 696.  Its firing control asserts
       the STATEMENT COUNT, R-DL's style.
    3. **A transfer is valued ONCE** (R-DW), by the leaf's rule, and the step MEASURES that this
       moves no figure.
    4. **The key is `(source_kind_id, owning event, scenario_id, pay_period_id, entry_date)`**
       (R-DV).  The kind and the scenario are load-bearing, not decoration -- without the kind a loan
       payment's split correction is summed into the transaction reconcile and reversed; without the
       scenario two scenarios' entries merge into one group.
    5. **It CONSUMES the cash walk, which makes the single pass a fixpoint** -- and that is a
       PRECONDITION, not a follower.  `account_posting_service/_walk.py:32-37` reads source facts
       back FROM THE LEDGER, so an anchor target computed against it is stale the moment the same
       pass emits a source delta; that is why today's self-heal runs AFTER emission.  Only
       `cash_ledger.walk_cash_ledger` makes the target a pure function of source data.  **X-d's
       writer swap therefore lands HERE**, and the "X-d re-lands alone so its rollback isolates a
       writer change" sentence does not survive it.
    6. **Cross-account closure** (finding **N-165**): a re-derive of A emits legs on B's ledger (a
       transfer's other endpoint; a loan payment's cash entry).  The verb RETURNS the
       `(account, scenario)` pairs its emitted legs touched and the caller re-enqueues them, with a
       stated termination argument -- the second re-derive of an account already at target emits
       nothing, so the fixpoint is reached in one extra round.
    7. **One re-derive per `(account, scenario)` at a time** (finding **N-166**): a per-pair advisory
       lock, on the pattern `pay_schedule_service.lock_schedule` already uses.  Reconcile deltas are
       legitimately many-per-key, so no uniqueness constraint can catch a double post, and widening
       the unit of work from one row to a whole account widens the window.

    It owes the number X-ai-0 could not measure: **what a BUILT verb costs**, as a statement count.
  * **X-ai-b `THE ONE VERB, loan.`**  The loan side takes the same shape.  Its `settled=` argument at
    `_sync.py:233` -- the one it passes into the CASH writer -- is a computed opinion of the shape
    **N-144** names; its repair loop calls the CHECKED cash wrapper per transfer (**N-155 (d)**) and
    may DELETE rather than move once cash owns its own stale dates; and two exported writers
    reconcile without grading (**N-160**), which under one verb cannot exist.  Ending both packages on
    one shape is what lets **N-158**'s `posting_deltas(walk)` accessor land on `cash_ledger` and
    `loan_ledger` together.
  * **X-ai-c `THE ONE TRIGGER.`**  `rederive_account` is drained at `before_commit` from a registry
    the write paths populate.  **The registry arrives at X-ai-a, not here** -- if the one interface
    (`account_changed`) lands with the verb and drains synchronously, this step is a single change to
    WHERE the drain happens and touches no call site.  **Scoped by the registry, not session
    inspection** (X-ai-0 measured why: over-inclusive at 1,861 fixture-state commits, under-inclusive
    at 3,431 writer commits, and `TransactionEntry` needs the same join escrow does, 1,425 times
    against 143).  It grades **11,917 of 16,169 commits**, never more than **4** pairs on one, and
    dispatches by account kind.  Here **N-144**'s parameter is deleted, **N-157**'s ordering rule
    ceases to exist, **N-153** answers itself, and the deploy hook's three separate commits
    (`init_database.py:348-350`, committing at `:234` / `:286` / `:329`) become one
    reconcile-then-assert pass that heals rather than refusing.  It owns the ERROR DISPOSITION: a
    `PostingError` from `before_commit` propagates out of `session.commit()` through three narrow
    `try` blocks that do not expect it -- `anchor_service.apply_anchor_true_up` catches
    `StaleDataError` / `IntegrityError`, `sync_all_scenarios_or_duplicate` catches `IntegrityError`
    and ROLLS BACK, and `routes/transactions/carry_forward.py:135-137` catches only `NotFoundError` /
    `ValidationError`.
  * **X-ai-g `THE FENCES THAT BECOME UNNECESSARY.`**  Every fence the steps above made redundant is
    DELETED, not maintained.  `settled=` goes from both writers (N-144); the ordering-rule docstrings
    go (N-157); `merge_target_legs` goes (R-DV -- verified deletable: the walk resets `running =
    fact.anchor_balance` after each correction, so correction n+1's `ledger_before` already absorbs
    correction n, and splitting a merged key into per-event keys is exact).  The surviving Python
    grader is the only one left, with **N-163**'s 20 bulk-statement sites classified -- each proven
    unable to touch a posted row, or routed through a writer, or named in the one docstring that says
    what the grader cannot see.

  **X-d's WRITER SWAP MOVES INTO X-ai-a, and the rest of X-d re-lands after X-ai-g.**  A draft of
  this plan said the swap stays in X-d and that X-d "re-lands ALONE so its rollback isolates a writer
  change"; the design review showed those cannot both hold.  X-ai-a's one-pass fixpoint REQUIRES the
  source-row walk -- the postings-sourced `account_posting_service/_walk.py` reads its facts back
  from the ledger, so an anchor target computed against it is stale as soon as the same pass emits a
  source delta (`_walk.py:32-37`; it is why the self-heal runs after emission today).  So the swap is
  a precondition of X-ai-a, not a follower, and X-ai-a is the step whose rollback must isolate the
  writer change.  What survives as X-d's own: R-DJ's two day types (finding **N-135**) and R-DK's
  skip deletion.

  **PRIOR SCOPE, kept because the reasoning is still the step's** -- widened 2026-08-03, and X-d is
  PARKED BEHIND IT.  This step is no
  longer "move the hook"; it owns the whole placement question, because X-d's own two adversarial
  reviews found that the per-row placement REFUSES an ordinary user action (**N-155**: carrying
  forward two partly-spent envelopes is an unhandled 500, and two more confirmed defects beside it).
  The developer refused the alternative of batching at each known loop -- that would make the
  ordering an obligation four or more callers must remember, which is the shape R-DM itself rejected
  when it declined "an explicit re-derive at each of the five retire sites".  **The contrast that
  points at the answer is already in the tree**: the LOAN sync reconciles everything and asserts
  ONCE at the end, and its own assert has no batch window -- though the loan sync is NOT clean of
  N-155 as a whole, which the loan-side scope below states exactly.  The step also inherits
  **N-153**, **N-157**, **N-158** and **N-160** -- which read as questions about where the assert
  runs and what it is handed, and which ruling R-DU DISSOLVES rather than answers, each for the
  reason recorded on its own row. X-d places the assert at
  the end of each posting sync, which is the end of every operation the app performs TODAY; X-ai
  moves it to the only moment that is definitionally the end of an operation -- the commit -- so the
  invariant is checked exactly where it is claimed to hold.

  **Why it is a step and not a preference.** The invariant is "the COMMITTED books equal the
  COMMITTED rows". X-d approximates that by asserting after the last mutation of each KNOWN caller,
  which holds only while every future caller also ends there -- a convention, and Section 8 rules a
  convention weaker than a predicate. A commit-boundary hook is the only form that also grades a
  write path with NO posting-sync tail at all, which is the class X-d cannot see.

  **THE ROOT CAUSE IS NOT "WHERE THE ASSERT RUNS", IT IS WHAT THE GRADER OWNS -- traced 2026-08-03,
  and this is the sentence the step should be designed from.** The loan sync's immunity is not that
  it asserts LAST; it is that **its reconcile scope equals its assert scope.**
  `sync_loan_postings` (`loan_posting_service/_sync.py:95-149`) walks the loan's WHOLE fact stream
  and reconciles every leg that lands on the ledger it then grades -- the payment splits (all of
  them, `reconcile_loan_payment_splits`), the anchor corrections (all of them), AND the OTHER
  writer's cash entries, through `_reconcile_lineage_transfer_entries` (`:152-236`). So by the time
  `_assert_checked_projection` (`:239`) reads the ledger back, nothing on it is outside what this
  call just brought to target. The cash side is the opposite shape: `_reconcile_transaction_postings`
  reconciles ONE ROW (`posting_service.py:786`, keyed `JournalEntry.transaction_id == txn.id`) and
  `reconcile_account_anchor_corrections` reconciles only the CORRECTION legs -- then the assert
  grades the whole linked ledger, **including the source legs neither of them has authority to
  write**. A grader whose scope exceeds its reconcile's scope must fail on any half-posted batch,
  which is exactly what N-155 measured. The commit boundary fixes it because the commit is after all
  N rows are posted; a whole-account reconcile at the sync tail would fix it too. Both are on the
  table, and the step must rule between them rather than assume the hook.

  **THE LOAN SIDE IS IN THIS STEP'S SCOPE, ruled 2026-08-03**, and not as a courtesy sweep: the loan
  package is BOTH the design the cash side should learn from AND a confirmed carrier of the same
  defect through a different door. Five items, each traced:

  * **N-155 (d) is CONFIRMED, not plausible, and the trigger is named.** The loan sync's repair loop
    calls the CHECKED cash wrapper -- `sync_transfer_postings` at `_sync.py:233` -- once per stale
    transfer. That wrapper's self-heal grades the funding account, and the cash walk sees transfer
    shadows like any other settled row (no `transfer_id` filter:
    `balance_predicates.balance_contributing_clause`, and `cash_ledger/_events.py:13` states it
    outright). So with TWO stale-dated transfers drawn on one checking account, iteration 1 re-dates
    transfer A and then grades Checking while transfer B is still at its old date: the walk expects B
    at its settled day, the ledger holds it at the stale one, `PostingError`. **The loop is defeated
    by the assert in exactly the state the loop exists to repair** -- the same shape as N-155 (c), and
    the residue is real (E1a measured `+$2,410.95` at 2026-07-02 against a reversal dated 2026-06-18
    on the production Mortgage). **The claim "the loan side does not have this defect" is therefore
    too broad and is narrowed here: the loan's OWN assert has no batch window; the loan SYNC inherits
    the cash one because it is a caller of the cash writer.**
  * **The grader must dispatch by account kind, and no such dispatch exists today.** The cash grader
    structurally refuses loans -- `_load_non_amortizing_account` returns `None` for an amortizing
    account (`account_posting_service/_sync.py:79-101`, applied at `:135-137`, ahead of the walk) --
    which is load-bearing, not incidental: a loan payment's cash leg lands on the LOAN's linked
    ledger at `transfer_service.py:348` and its split correction only at `:349`, so a grader that ran
    between them would refuse a correct operation. An account-generic commit hook must route each touched
    account to the right walk (loan -> `walk_loan_ledger`, non-loan -> `walk_cash_ledger`) or
    reproduce that skip deliberately.
  * **"Which accounts did this commit touch" is materially harder for loans.** A cash account's fact
    stream is two tables that both carry `account_id` (`transactions`, `AccountAnchorHistory`). A
    loan's is five loaded by `walk_loan_ledger` (`loan_ledger/_walk.py:217-242`): `LoanParams`,
    `LoanAnchorEvent`, `RateHistory`, escrow lines and their versions, plus the settled income
    shadows -- and `EscrowComponentVersion` carries only `line_id` (`models/escrow_line.py:138`), so
    a `session.dirty` inspection reaches the account only through a join. This is a real argument for
    the sync-populated registry over session inspection, and it belongs in the fork.
  * **The deploy hook's three separate commits become load-bearing.** `init_database.py:348-350`
    runs the cash resync, then the loan backfill, then the anchor backfill, **each committing its
    own transaction** (`:238`, `:290`, `:333`). The cash resync re-dates a loan payment's cash entry
    while that payment's split correction still carries the old date; the loan backfill heals it in
    the NEXT transaction. An assert-only commit hook covering loans would refuse that first commit --
    and this is not hypothetical: the one production re-date R-DH (b) produced was a **mortgage**
    payment (`posting_resync.py:56-59`). So the step must either merge the three hooks into one
    transaction or make the hook reconcile-then-assert. **Either way the hooks' ORDER stops being the
    protection**, which is the same correction the parking commit already made to that docstring for
    the cash half.
  * **Two loan write surfaces reconcile without grading at all** -- `sync_loan_payment_postings` and
    `sync_loan_anchor_corrections`, both exported (`loan_posting_service/__init__.py:94,103`), no
    `app/` caller, test-only today. Recorded as **N-160**; a commit-boundary grader closes them for
    free, a sync-tail redesign must decide them explicitly.

  **What it must decide.  Two of the four were RULED by R-DU on 2026-08-03 and are struck here
  rather than left reading as open:** ~~which accounts a commit touched~~ (**ruled: the registry the
  syncs populate**, not `session.dirty` inspection, on the two independent arguments below) and
  ~~whether the hook asserts only or reconciles-then-asserts~~ (**ruled: reconciles-then-asserts**,
  so it heals rather than refusing -- the deploy hook's three commits are the case that separates
  them).  **Still open, and genuinely so:** whether `before_commit` is the right SQLAlchemy hook
  given that writing inside it needs an explicit flush; and how a raised `PostingError` interacts
  with the three places that wrap a
  commit or a sync in a narrow `try` -- `anchor_service.apply_anchor_true_up` catches
  `StaleDataError` / `IntegrityError` around its own `commit()` (`anchor_service.py:349-381`),
  `_append_loan_anchor_and_sync` commits at `anchor_service.py:449` behind
  `sync_all_scenarios_or_duplicate`, which catches `IntegrityError` and ROLLS BACK
  (`loan_posting_service/_sync.py:388-396`), and `routes/transactions/carry_forward.py:135-137`
  catches only `NotFoundError` / `ValidationError`, which is what makes N-155 (a) a 500 rather than a
  message.
  A `PostingError` is a `ShekelError` and passes through all three; that is the correct disposition,
  but it moves where four routes' failures originate and the step owns saying so.

  It also carries **N-144**: with the assert at the commit boundary, `settled=` can stop being a
  caller-supplied opinion about a row that knows its own status, which is what makes the disagreement
  R-DM ordered around UNREPRESENTABLE rather than merely well-sequenced. **N-157 and N-158 land on
  BOTH packages or neither**: the ordering rule N-157 wants stated belongs on
  `sync_account_anchor_postings` AND on `sync_loan_postings` (10 call sites through 5 public entry
  points funnel into the loan one -- `params.py:129` / `:181`, `escrow_rates.py:148` / `:383`,
  `anchor_service.py:439`, `_transfer_loan_posting.py:282` / `:342`, `pay_period_admin.py:540`,
  `baseline_service.py:78`, `init_database.py:289` -- counted 2026-08-03, the mirror of N-157's nine
  through five on the cash side), and N-158's `posting_deltas(walk)` accessor is only a fix if it
  lands on `cash_ledger` and `loan_ledger` together, since the sign convention it names is what
  differs between them.

  **PREPARATION, traced 2026-08-03 after PR #80 merged, so the step opens on facts rather than on a
  reconstruction.** Three things a reader would otherwise have to re-derive:

  * **WHERE THE STEP STARTS: on `main` the LOAN assert is the ONLY checked-projection assert in
    production.** Measured against `origin/main` at `dde107f6`: `loan_posting_service/_sync.py`
    carries `_assert_checked_projection` (E1a, live), `account_posting_service/_sync.py` carries
    none, the shared `_posting_reconcile.assert_ledger_projects_facts` does not exist, and
    `account_posting_service/_walk.py` -- the postings-sourced walk -- **is still live**. So the cash
    assert, `posting_resync.py` and `cash_ledger/_days.py` exist only on the parked branch. **X-ai's
    first subject is therefore the loan assert, by force rather than by preference**, and the cash
    one arrives already at the new placement when X-d re-lands. This is also why N-155 costs `$0.00`
    today: (c) names a module main does not have, and (d)'s loop cannot trip a cash assert that is
    not shipped.
  * **FEASIBILITY IS UNBLOCKED, and it was the largest unmeasured risk.** A commit-boundary grader is
    worthless if the suite never reaches a commit -- the usual transaction-per-test harness would
    make the invariant invisible to all 7,799 tests. It does not: this suite is **database-per-test
    by template clone** (`DROP DATABASE ... WITH (FORCE)` then `CREATE DATABASE ... TEMPLATE
    shekel_test_template`, `tests/conftest.py:820-905`), so isolation comes from a fresh database
    rather than from a rolled-back transaction and **every `db.session.commit()` in the suite is a
    real commit**. A `before_commit` hook fires in the suite exactly as it does in production.
  * **The same fact CONSTRAINS the scoping fork, and points the same way the loan fact-model
    argument does.** Because tests really commit, they commit states no posting writer produced:
    `tests/_test_helpers.add_txn` builds a bare `Transaction` at any status the caller names and
    flushes, "the caller commits" (`:2783-2796`), which is the "settled row the ledger has never
    seen" shape X-d's own conversion already met 26 times. Scoping the hook by inspecting
    `session.dirty` / `new` / `deleted` would grade those; a **registry the posting syncs populate**
    grades only accounts a WRITER touched in that transaction. That is the same answer the loan
    fact-model gives (five tables, `EscrowComponentVersion` reaching the account only through
    `line_id`), reached independently. **This was the recommendation, and ruling R-DU adopted it on
    2026-08-03: the sync-populated registry.**

  **What is NOT known, and must not be guessed:** how many suite commits a hook would grade, and how
  many of those are fixture states rather than writer states. Grep cannot answer it honestly (the
  builders are many and the statuses are set indirectly). **Step 1 of X-ai is therefore a
  measurement, not a design**: arm a no-op `before_commit` listener that only records the
  `(account, scenario)` pairs it WOULD grade, run the suite, and count. That number decides whether
  the hook can grade everything it sees or must be registry-scoped, and it is a day's work cheaper
  than discovering it from a red suite -- which is how X-d found N-155.

  **Sequenced BEFORE X-d, which is parked behind it** (developer ruling 2026-08-03, reversing this
  entry's original order). The placement decision changes what X-d's writer swap lands on, so
  shipping X-d first would ship a known 500 and then move it.
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

  > **Every line number in the four bullets above is HISTORICAL, and the files behind two of them
  > are gone.**  This is the census as X-e measured it; **X-f1c3a re-pointed every rendered read at
  > `cash_ledger.resolve_anchor` and X-f1c3c deleted both columns**, so the writers, the reads and
  > the guards named here no longer exist to cite.  `app/services/balance_at/_investment.py` was
  > deleted outright at `17c57cde` -- its modelled-asset work went to **`_asset_fold.py`**
  > (created `17ead4c5` one day earlier, cut over at `560b3339`), and
  > `investment_growth_since_anchor` was ALREADY live in `_kind_correct.py` while the copy in
  > `_investment.py` was dead (ruling R-AR).  *A first version of this note credited
  > `_positions.py`, which is the LOAN producer from plan step C3a/C3b (`df775017`, nine days
  > BEFORE the deletion) and absorbed none of it -- an attribution asserted without being checked,
  > inside a note whose whole purpose is correcting stale citations, and caught by a neutral claims
  > audit.*  Separately,
  > `savings_dashboard_service/_data.py`'s column read is now
  > `cash_ledger.resolve_anchor(acct).balance` at `:200`.  The rows that inherited these citations
  > -- **N-4**, **N-5**, **N-73**, **N-83**, **N-103** and **cash D4** -- carry the QUESTION, not
  > the address; each states its own current status.  Kept rather than re-pointed line by line: a
  > census re-written to today's addresses stops being a record of what was measured, and this arc
  > has paid twice for a paragraph edited into agreeing with the present (Section 8).

  **X5 is DECIDED here rather than left as "own arc, if ever"** (ruling R-AO): whether an
  `AccountAnchorHistory` row gains an `effective_date` -- separating "when this was true" from "when
  it was typed", which is what a backdated statement assertion needs -- is a question about the SAME
  table this step is deciding the shape of, and answering the column's fate without it decides half
  a design. It may still be declined; it may not be left unasked.
- [ ] **X-f** `feat(transactions): the app records when money moved` -- **RE-SCOPED AND PULLED
  FORWARD 2026-07-31 by ruling R-DH (finding N-130); it is no longer sequenced after X-d and it is no
  longer a shrink-the-row follow-up.**

  > **REDESIGNED FROM SCRATCH AND DECOMPOSED 2026-08-03 on ruling R-EB (Section 4): Option 4, then
  > Option 6.**  The paragraph below is the scope R-EB SUPERSEDES, kept because its measured headroom
  > still stands and because the reason it was superseded is itself the finding -- it fixes the CHURN
  > half of the defect and not the RESIDUE half, since money that was never recorded has no date to
  > correct.  The leaves carry **N-171**..**N-176**.  **X-f3 MOVES MONEY and takes its own PR with no
  > backlog**, per this document's own rule for X-f.

* [ ] **X-f1** `feat(transactions): a settle carries the day the money moved` -- absorbs **S2-b**.
  **SCOPED BY RULINGS R-EC / R-ED / R-EE (Section 4, 2026-08-03), and the first of them DELETES a
  column**: `transactions.settled_on` REPLACES `paid_at` rather than joining it, so a settle has ONE
  clock instead of an instant plus 11 derivations of a day from it.  Plus the true-up form's own
  statement-date field, closing the last surface where one column does two jobs (R-DH (e) vs R-M).
  **Ships the `anchor_settle_partition.md` archive move with it** (N-175), so the superseded plan and
  the plan that supersedes it land together.  Closes **N-173**, **N-175**.

  Five commits, the rulings FIRST (the X-u lesson, Section 8):

  * [ ] **X-f1a** the rulings, before any code cites them.
  * [ ] **X-f1b0** `fix(transfers): a re-settle does not re-date the money` -- closes **N-178**, a
    LIVE defect the conversion trace found and REPRODUCED.  Ships BEFORE the column change and on
    the CURRENT column, so it is independently revertable and provable against `paid_at` rather
    than tangled with a rename.
  * [x] **X-f1b** the column, the migration, the seam, and all 11 readers.  **Figure-neutral BY
    CONSTRUCTION**: the backfill is the deleted derivation verbatim
    (`COALESCE((paid_at AT TIME ZONE 'America/New_York')::date, pay_period.start_date)` for settled
    rows, NULL otherwise), so the gate is `verify_balance_baseline.py` byte-identical on two
    databases.  The seam stamps `display_today()` on FIRST entry to the settled band and
    **PRESERVES on re-entry** -- `Paid -> Settled` is a re-entry, and that rule is what stops
    archiving a payment from re-dating its money (**N-146**'s class, and why **N-177** constrains
    this step even though its status has zero rows).  `to_display_civil_date` and both its wrappers
    are deleted once callerless.
  ---

  #### X-f1b as BUILT: the test half, and the four things it found

  **GREEN and complete, 2026-08-03.**  The suite is **7,784 passed / 0 failed** under both
  `America/New_York` and CI's `TZ=Pacific/Kiritimati`; `pylint app/ scripts/` 10.00/10 with the full
  `--fail-on` set; **all three test locations** run -- `./scripts/test.sh`, `pytest
  tools/plan_gate/` (17), and `pytest tools/pylint/tests/` (146), the third being the one the parked
  build never ran and went RED on.

  **The park record said 102 tests failed and that the remaining work was "not the mechanical
  part".  That was right.**  Of the 102: ~66 were fixtures building a settled row with no day, 22
  drove a frozen migration's raw SQL against a column the head revision drops, and 14 were the
  anchor-reconciliation oracle written in INSTANTS end to end.  Each needed a different answer.

  **Verified on real production data, not a fixture.**  A fresh clone restored at prod's head
  (`d7c1f4a9e603`), baselined from a `fac90200` worktree, migrated, re-baselined from HEAD:
  `verify_balance_baseline.py` **byte-identical** over 9 accounts, 427 grid cells and 5,978 daily
  points.  **Positive control**: moving ONE settled row's day by 30 days produces a 34-line diff, so
  the identity is a measurement and not a blind harness.  The settled-iff-dated invariant holds
  EXACTLY on that clone -- 156 settled rows all dated, 0 non-settled rows dated -- and the migration
  now proves a THIRD invariant it had only assumed (below).

  **N-183 closed structurally, and it forced the extraction N-152 predicted.**  `update_transfer`
  assigned `settled_on` on both shadows directly; `create_transfer`'s born-settled branch did too.
  Both route through the seam now, so `Transaction.settled_on` has ONE writer in `app/`.  Making
  room for it hit the 1000-line ceiling at 987, which is exactly what **N-152** said the next change
  to that module would do -- so the status and settle-day appliers moved to
  `app/services/_transfer_status.py` (`apply_status_to_all_three`, `apply_settle_day_to_pair`,
  `apply_settle_day_correction`), leaving `transfer_service` at 934.  **The split is by
  responsibility, not by line count**: those three functions are the only place a transfer's three
  rows resolve a shared status and a shared day, which is Transfer Invariant 3.

  **The create path is narrowed deliberately, and the reason is worth keeping.**  Routing a
  born-settled create through `apply_status_to_all_three` would verify the PARENT's transition too,
  and the transfer workflow map has no `Received` entry -- so it would refuse a state
  `create_transfer` has always accepted.  Rejecting that state may well be right; it is a create-path
  rule and not this step's to decide, so only the two rows that carry the column are written.

  **N-179 closed one layer deeper than the parked build had it.**  The seam refused an instant it was
  HANDED; nothing stopped `txn.settled_on = <datetime>`, which PostgreSQL truncates on the UTC
  session clock.  The rule now lives on the COLUMN -- `models.transaction.reject_settle_instant`
  behind a SQLAlchemy `@validates` hook -- so the constructor, a plain attribute write and every ORM
  path refuse it, and the seam calls the same function early only so a rejected call has not already
  moved `status_id`.  **That is a type the column does not accept, not a checker hunting call
  sites.**  Residual, stated rather than implied: a bulk `query.update()` bypasses the ORM attribute
  layer, the same boundary `LoanAnchorEvent`'s append-only guard states for itself.

  **N-182 closed, and every repair is mutation-verified.**  `freeze_today` now threads `at_time`
  into the DATABASE half of the freeze; the two halves were noon UTC and the caller's instant, hours
  and a civil date apart, which is the split that helper's own docstring was written about.  The new
  pin freezes at 01:00 UTC -- 20:00 EST / 21:00 EDT the previous evening, so it separates the two
  calendars on both sides of DST -- and asserts the seam takes the EASTERN day.  **Seven mutants
  planted, seven killed, one control each**: `display_today` -> `date.today`; the settled-status
  guard dropped; the instant accepted; the preserve arm dropped; the `@validates` decorator dropped;
  the shared refusal disabled; and a non-status edit moving a settled row by 7 days.

  **THE 22 MIGRATION TESTS WERE DELETED ON THE DEVELOPER'S RULING (2026-08-03), and two more of the
  same class were found afterwards.**  `test_posting_cash_backfill.py` (12) and
  `test_posting_ledger_backfill.py` (10) drove `7d63529e4300` / `db239773c2fd`'s frozen raw SQL,
  which reads `t.paid_at`.  Measured before the ruling: a real `base -> head` upgrade is unaffected
  (those migrations run at their own point in the chain, long before the drop), on any database past
  them they never run again, and `a3f7c8e21b64`'s downgrade REFUSES so Alembic cannot rewind past the
  drop -- **the data path is unreachable, permanently**.  The alternative offered was a fixture that
  re-adds the dropped column; the developer ruled deletion.  Both migrations and both test modules
  now record what went and why.  A neutral review then found the same shape twice more, as
  `TestBackfillAndGoForwardAgree` inside the two LIVE reconciliation oracles, and those went too.

  **What the deletion actually cost, named rather than glossed** (the review's finding, and it
  corrects an overstatement of mine): the executed downgrade case has no survivor -- the one left is
  a `read_text()` regex over the migration SOURCE and cannot catch a downgrade that deletes the wrong
  rows; the zero-effective-transfer exclusion has no survivor; and the two `BackfillAndGoForward`
  cases were the only place an INDEPENDENT implementation of the sign / amount / date rules was
  compared against the go-forward builder (the two surviving tests of that name reuse the go-forward
  builder, so they are true by construction).  **And my justification was overstated**: keeping them
  would NOT have required "a schema that is not the application's" -- `alembic upgrade base -> <that
  revision>` gives exactly that schema.  The honest statement is that the harness has no
  per-revision template fixture.

  **The ORACLE was re-derived in DAYS, not renamed** -- and doing it found a divergence older than
  this step.  `_latest_assertion` ordered on `(created_at, id)` while `cash_ledger.resolve_anchor`
  orders on `(observed_on, created_at, id)`: since plan step 2 made `observed_on` a user-supplied
  column, a balance asserted for an earlier day but typed later is not the current one, and the
  oracle would have named it.  **An oracle stating a DIFFERENT rule than the engine is the shape that
  lets both be wrong together while the sweep reports clean.**  The source side restates the refusal
  rather than importing `settled_day`, so the two sides still read different tables with independent
  code.  Every hand-computed figure in that file is unchanged, which is the evidence the conversion
  was faithful: the fixtures now say `observed_day_of(<the same instant expression>)`, which is
  verbatim what the deleted derivation computed.

  **The migration grew a THIRD gate, because the other two were gated and this one was assumed.**
  `posting_service._entry_date` dates a transfer's journal entry from the INCOME shadow alone and
  rests on the pair carrying one day (Transfer Invariant 3).  The upgrade now refuses if any settled
  transfer's shadows disagree.  Measured on a production clone: 0 diverge.  **Positive-controlled**:
  planting a 3-day divergence makes the migration refuse and leaves the head at `d7c1f4a9e603`.  Its
  downgrade refusal also moved from `RuntimeError` to `NotImplementedError`, which is what
  `.claude/rules/database.md` names and what all three existing unconditional refusals use.

  **FOUR THINGS THE SECOND PAIR OF REVIEWS FOUND, and one of them was mine.**

  1. **I dated two PROJECTED rows.**  A blanket edit that added `settled_on=current.start_date` to
     five grid fixtures caught two whose status is deliberately Projected -- the converse violation,
     and under a copy-pasted comment saying "a settled row carries the day its money moved".  Found
     by a reviewer, not by my own census, which was looking only for the other direction.
  2. **`test_settle_day_preserved_on_non_status_update` could not fail**, and the reviewer PROVED it:
     with `_apply_regular_update` wrapped to move every settled row it touched by 7 days, that class
     and 703 tests around it stayed green.  It captured the original day and then asserted only `is
     not None`.  **That is N-146's class on the transaction side** -- an ordinary notes edit moving a
     settled payment's money -- i.e. the live production defect that opened X-aj1, with no cover.  It
     back-dates through the seam and asserts the LEDGER now, and the reviewer's own mutation kills it.
  3. **The `@validates` hook had zero tests**, found INDEPENDENTLY by both reviews.  The seam's
     refusal test proves the seam, not the column: it asserts the row was left untouched, i.e. that
     the assignment never ran.  Deleting the decorator shipped a green suite.  Pinned now, and the
     deletion kills the pin.
  4. **A control I had already "fixed" once still could not fire** (**N-184**).  The dump-only
     normalization case pinned `TransactionUpdateSchema.paid_at`, the only such field in the package,
     which R-EC deleted -- so it passed on `unknown=EXCLUDE`.  Re-pointing it at a locally-declared
     `dump_only` field did NOT help: marshmallow discards the key either way, so the branch is
     invisible downstream of `load()`.  Only asserting on the helper makes it falsifiable.

  **The lesson, and it is the third time this arc has paid it: a repair for "a control that cannot
  fail" is itself a control, and needs the same mutation.**  Two of the four above are repairs that
  did not fire until they were planted against.

  * [ ] **X-f1c** the edit doors -- **RE-SCOPED 2026-08-03 by rulings R-EF..R-EI into FOUR leaves, and
    the re-scope is itself the finding.**  As written it was one leaf ("`settled_on` on the full-edit
    form... and the true-up form's statement date"), and the build trace measured three things that
    sentence got wrong: the transaction door corrects **0 of the 8 rows** N-181 names (all eight are
    transfer shadows, and a shadow's popover is the TRANSFER form); the field as specified would
    **400 the only unlock path** on every settled row; and the statement date makes a back-dated
    assertion reachable, which **stales the anchor cache** the grid header and five other surfaces
    render from.  The order below is load-bearing: **c3 runs BEFORE c4** so back-dating never meets a
    cache.

    * [ ] **X-f1c1** `feat(transactions): the settle day is editable on a settled row` -- ruling
      **R-ED**.  `settled_on` on `TransactionUpdateSchema` and the full-edit popover (rendered only
      for a settled row), OUT of `_LOCKED_EDIT_FIELDS` (a budget decision locks; an observed fact
      gets corrected) and INTO `_POSTING_RELEVANT_FIELDS`, routed through the seam in ONE call with
      `status_id` so the generic `setattr` loop is not a second writer (finding **N-185**).  Carries
      ruling **R-EG**'s shared `settle_day_for_status`.  Closes **N-184** -- the `dump_only` arm in
      `_normalize_empty_inputs` has had no instance since R-EC deleted `paid_at` and is
      unfalsifiable through `load()`, so it and its test go.  Preceded by the module split its own
      1000-line ceiling forced (the three transfer-shadow branches to `_shadow_mutations.py`,
      `_finalised_edit_response` + `_LOCKED_EDIT_FIELDS` to `_helpers.py`), which is what **N-152**
      predicted for the next change to a module at the gate.
    * [ ] **X-f1c2** `feat(transfers): the settle day is editable on a settled transfer` -- ruling
      **R-EF**.  The door **N-181**'s eight rows are actually reached through, and
      `apply_settle_day_correction`'s first production caller.  Adds `Transfer.settled_on`, a
      read-only property returning the INCOME shadow's day (the row `posting_service._entry_date`
      dates the journal entry from), so the popover -- rendered from two blueprints -- asks one
      question.  No setter: assignment raises, which keeps the seam the single writer structurally
      rather than by review.  Closes **N-181**.
    * [ ] **X-f1c3** the anchor's one home -- **RE-SCOPED 2026-08-04 by rulings R-EM..R-EP into
      THREE leaves, and the re-scope is again the finding.**  As written (ruling **R-EH**) it was one
      leaf dropping two `accounts` columns, and the trace measured three things that sentence did not
      have.  The census is **65 references over 24 `app/` modules and 5 templates** (R-EH's own
      re-measure said 24 files and it was right; the earlier "12" was not), plus **273 occurrences over 266 lines in 45 test
      files** (an earlier draft said "312 across ~50" and no `grep` variant reproduces it; the
      `app/` half of the same sentence is exact).  **Substituting moves NO figure: the cache agrees with the latest
      assertion on 9 of 9 production accounts.**  The order below is load-bearing -- the resolver
      exists before anything drops, and the assertion is freed from the period before the reset path
      that fabricates one is deleted.

      * [ ] **X-f1c3a** `refactor(accounts): the asserted balance has one resolver` -- **no schema
        change**, so it reverts clean.  `cash_ledger.resolve_anchor` loses its `scenario_id`
        parameter (with the cache gone its ONLY use, the reconciliation log payload, goes -- and
        several new callers hold no scenario) and gains a batch twin so the account-iterating pages
        do not pay N queries.  Every read of `accounts.current_anchor_*` re-points to it.  Deletes
        the FIVE guards that branch on `IS NULL` for a `NOT NULL` column and are therefore
        unreachable (`_kernel.py:320` / `:432`, `_inputs.py:286`,
        `investment_dashboard_service/_context.py:276`, `retirement_projection.py:573`) -- finding
        **N-73**.  Carries **R-EM** (the four no-current-period fallbacks move to the seam) and
        **R-EP** (one `observed_on` caption; `dashboard_service._get_last_anchor_date` deleted).
        Templates take a passed-in value instead of reaching through the ORM.  Closes **N-73**,
        **N-83**'s CACHE half, **N-103**'s premise and **cash D4**.
      * [ ] **X-f1c3b** `refactor(accounts): an assertion is a day and a balance` -- ruling
        **R-EO**.  Destructive migration (`Review:` line): drops
        `account_anchor_history.pay_period_id` and its CASCADE FK, and re-keys
        `uq_anchor_history_account_period_balance_day` to `(account_id, anchor_balance,
        observed_on)` -- strictly tighter, **0 of 78 production rows rejected**.  Deletes
        `AnchorPoint.period` and `CashAnchorFact.pay_period_id`.  **`reset_pay_periods` stops destroying and fabricating the
        user's assertion history**: with no FK to the wiped periods, 69 real observations survive a
        schedule rebuild that deletes them today, and `_reanchor_accounts` shrinks from "restore the
        balance by writing a fresh origination row" to re-pointing one column.  Closes **N-168**,
        **N-169**'s cash half, **N-170**.

        *The build trace moved FOUR deletions from this leaf to the next, and the reason is
        worth stating:* `_reanchor_accounts`, `preserved_balances`,
        `account_service.resolve_anchor_period_id` / `AccountSpec.anchor_period_id`, and
        `PeriodLockReason.ACCOUNT_ANCHOR` with `_period_ids_that_are_account_anchors` all cannot go
        HERE, because `accounts.current_anchor_period_id` still exists at this point -- a schedule
        rebuild must still re-point it and the lock still has a column to read.  They die with that
        column in X-f1c3c.  *A first correction to this bullet moved two of the four and left the
        lock behind, so the plan contradicted the code comment written beside it; a claims audit
        caught that.*  The leaf order (b then c) is unchanged and correct: freeing the
        assertion FIRST is what lets the reset stop fabricating.
      * [ ] **X-f1c3c** `refactor(accounts): drop the anchor cache columns` -- ruling **R-EH**'s
        original scope, now the last leaf.  Destructive migration (`Review:` line): drops
        `current_anchor_balance`, `current_anchor_period_id`, `ck_accounts_anchor_balance_present`
        and the deferrable `NO ACTION` FK, taking `_DEFER_ANCHOR_FK_SQL` and the deferral apparatus
        with them, plus `_reanchor_accounts`, `preserved_balances`,
        `account_service.resolve_anchor_period_id` and `AccountSpec.anchor_period_id` (all
        callerless once no row and no column needs a pay period).  The three writers stop writing.
        Carries **R-EN** -- the C-17 lock, the
        `STALE_CONFLICT` outcome and the 409 conflict cell leave the true-up path, because an
        append-only assertion overwrites nothing (**measured**: a history INSERT leaves
        `Account.version_id` at 33, the column write takes it to 34).  **Working downgrade**, not a
        refusing one: the columns are an exact function of the latest assertion, which every account
        is guaranteed to have.  Closes **N-134**, **N-4**, **N-5**.
      **Runs BEFORE X-f1c4.**
    * [ ] **X-f1c4** `feat(accounts): a true-up carries the statement day it was read from` --
      rulings **R-EE** / **R-EI**.  `observed_on` on `AnchorUpdateSchema` and a second line in
      `grid/_anchor_edit.html`.  **Its period half is already done by then**: X-f1c3b deletes the
      pay period from the assertion entirely, so "a period can no longer be chosen beside a day"
      (R-EA / R-DH) is true by construction rather than by derivation, and **N-134 closes at
      X-f1c3c** rather than here.  What remains is the field itself and its render.
      Closes **N-173**.
  * [ ] **X-f1d** the archive move (**N-175**).

  **The invariant this step establishes, and how it is enforced.**  A row is settled if and only if
  it carries a settle day.  Measured on the 2026-08-03 clone it already holds exactly: **0 of 741
  non-settled rows carry a `paid_at`**, and all 156 settled rows get a day from the backfill.  It is
  enforced STRUCTURALLY rather than by a fence -- `status_seam.apply_status_change` is the single
  door that writes `status_id` (W9907), and it writes the day in the same call, so the two cannot
  diverge by construction.  A `CHECK` cannot express it (the predicate lives in `ref.statuses`, and
  a constraint cannot join), and hardcoding the three settled ids into one would be the magic-number
  defect.  A reader that finds a settled row with no day therefore FAILS LOUD rather than falling
  back -- the arc's established `PostingError` stance -- because silently dropping such a row from
  the fold is silent money loss.

* [ ] **X-f2** `feat(accounts): the true-up is a reconciliation` -- the outstanding set covers
  TRANSACTIONS as well as entries (`_outstanding_scope`'s transaction twin, `entry_service.py:819`),
  ticking stamps the STATEMENT date rather than `now()`, and the form shows the difference before it
  is saved -- R-DH (f)'s second half, ruled 2026-07-31 and still unbuilt.  **No figure moves**: this
  records facts and changes no producer.  The developer's existing workflow already IS this loop
  (*"mark which expenses are already accounted for in that balance"*); only the recording changes.

* [ ] **X-f3** `feat(cash): the ledger is sum-of-postings and the residual is classified` -- **THE
  CUTOVER.  MOVES MONEY.  OWN PR, NO BACKLOG.**  The assertion stops resetting the ledger
  (`cash_ledger/_walk.py:300`), `balance(T)` becomes `opening equity + SUM(postings <= T)`, and the
  reconciliation residual posts to Uncategorized Expense / Income
  (`ledger_account_service.py:101-103`, kind `fallback`) instead of to `anchor_equity`.  **The reset
  deletion and the classification cannot ship apart** -- removing the reset without a classification
  path lets book and bank diverge permanently.  Ship-gated on the R-DH (c) invariant becoming a TEST
  that passes without a true-up, in both orders.  Closes **N-171**, **N-172**, **N-174**.

* [ ] **X-f4** `refactor(cash): delete what the cutover orphans` -- `ReconciledThrough` and its **78
  references across 14 files**, `account_posting_service/_anchors.py`, the correction machinery, the
  R-I seed compensator (`_cash_fold.py:372-382`).  Byte-identical by construction; the baseline
  harness is the gate.  **State the deletion of `_posted_only_key_period_id`'s defensive branch
  explicitly** -- it has fired in production (N-176) -- rather than letting it go unnoticed.  Closes
  **N-176**, and takes **N-161**, **N-169** and **N-170** with it by deleting the family they are
  properties of.

* [ ] **X-f5** `fix(ledger): the opening equity account holds only the opening` -- one balanced
  entry: debit Uncategorized Expense **$1,495.10**, credit Checking Anchor Equity **$1,495.10**,
  leaving exactly the **-$689.16** opening credit.  Verified to the cent against ledger account 30's
  97 posted legs.  Developer approved 2026-08-03 (*"I'm good with clearing the historical Equity
  balance if it makes my finances more accurate"*).  This is QuickBooks' documented Opening Balance
  Equity procedure and it makes the four-month income statement honest.

* [ ] **X-f6** `feat(import): the bank says when money moved` -- **RULED as the follow-on, not an
  alternative (R-EB).**  A bank import (OFX / CSV / Plaid) is the terminal state named in
  `anchor_settle_partition.md` 12.11: the only thing that removes the date guess without asking the
  user anything.  **It CONSUMES X-f1..X-f5 rather than replacing them** -- an import yields
  bank-dated facts that must be MATCHED against budgeted rows, and the unmatched residue still needs
  classification, which is exactly X-f2's outstanding set and X-f3's residual path fed automatically
  instead of by hand.  **Its first act is a trace, not code** (the E2-0 shape): which import surface,
  what matching rule, and what a match does to `settled_on`.  Opens after X-f5 ships.

  A live production defect proved this root is a correctness
  defect in the PROJECTED END BALANCE, not noise: `-$4,021.37` rendered against a true `-$19.95`
  because two data-entry clocks are partitioned at second granularity.  The plan of record for this
  step is now **`anchor_settle_partition.md`**, which owns the trace, the measurements, R-DH's six
  parts and a four-step build; finding **X5** (an anchor's `effective_date`) is absorbed into it as
  `account_anchor_history.observed_on` rather than being decided at X-e.  The paragraph below is the
  ORIGINAL scope, kept because its measured headroom still stands.

  Ruling R-N's follow-up, and
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

### Phase G -- the allowlist-free fences (RULED 2026-08-02 R-DQ; RE-RULED 2026-08-03 to run INSIDE E2)

**The developer's standing instruction, in their own words: "I want to make the fences structurally
unnecessary."** A fence that carries a list of module names is a rule stated in prose plus a detector
that has to be kept complete; a fence that is structural cannot be got wrong in the first place. This
arc already owns both the precedent and the model. The precedent: the balance NAME fences were
DELETED at plan steps D3 and E1e rather than maintained, once private packages made them redundant.
The model: `shekel-private-module-import` (**W9910**), whose own docstring is the specification --
*"name-INDEPENDENT and fail-closed by construction: it consults no producer list and no allowlist, so
there is nothing to keep complete and nothing to rot."*

**RE-RULED 2026-08-03: it runs INSIDE E2, not after it, on the developer's instruction** -- *"I feel
like restructuring keeps getting pushed off ... I want to eliminate every fence, checker, and
allowlist I possibly can."* R-DQ deferred this because "E2 moves the very modules those allowlists
name, and doing the fences first would re-cut them". That is true and it is equally an argument for
doing it AS PART OF the move: the structural replacement for both surviving allowlists IS a module
move, so E2 and G1 cut the same boundary once instead of twice. Running G1 after E2 meant the two
largest allowlists (16 names and 7) outlived the entire arc, which is the deferral the instruction
names. **W9907 is deliberately NOT here** -- it is the smallest of the three (five write sites in all
of `app/`) and it BLOCKS X-d, so it ships first and alone as plan step **X-aj** under R-DN / R-DP.

**THE MEASURED INVENTORY, 2026-08-03, so the end state is a number and not an aspiration.** `app/`
is gated by **8 custom checkers carrying 16 hand-maintained module sets holding 36 names**:

| checker | structural replacement | where |
|---|---|---|
| **W9910** `shekel-private-module-import` | none needed -- **it IS the structure.** Its own docstring: "consults no producer list and no allowlist, so there is nothing to keep complete and nothing to rot" | **KEEP** |
| **W9903** `shekel-disable-rationale` | none -- it polices pylint's own disables, not a domain rule | **KEEP** |
| W9907 `shekel-transaction-status-bypass` | `status_id` becomes a read-only attribute | X-aj2 (R-DP) |
| W9908 `shekel-ledger-model-bypass` (3 sets, 16 names) | move the ledger models INSIDE the owning package; W9910 then covers it with no list | **G1** |
| W9909 balance seam (6 sets, 7 names) | the same package placement, plus a type where the rule is value-level | **G1** |
| W9901 `shekel-decimal-from-float` | a `Money` type that cannot be constructed from a float | **G2** |
| W9904 `shekel-bare-money-quantize` | the same `Money` type, with rounding as a method | **G2** |
| W9902 `shekel-refname-compare` (2 sets, 4 names) | a `DisplayLabel` whose `__eq__` against `str` RAISES -- step 3's `ReconciledThrough` shape | **G2** |

**The achievable end state is 2 checkers and 0 allowlists**, and both survivors are allowlist-free, so
nothing has to be kept complete. **Three of the eight had no structural replacement scheduled
anywhere until 2026-08-03**, which is why G2 exists.

- [ ] **G1** `refactor(gates): the ledger-model and balance-seam fences stop carrying name lists` --
  closes **N-147**. **Its first action is a trace and no code is written before it**, because the two
  remaining allowlists are not the same problem wearing two hats and this step must not assume they
  are. `shekel-ledger-model-bypass`'s `_LEDGER_MODEL_ALLOWLIST` names the modules permitted to import
  the three ledger models directly; the balance seam checker carries roughly a dozen module sets plus
  a per-module EXPORT map, which is a different shape again -- it encodes what each producer may
  publish, not merely who may import it.

  **What the trace must establish, per allowlist, before any of them is touched**: whether the entry
  exists because a boundary is genuinely absent (in which case the fix is the boundary, as W9910 was
  for the name fences), or because a legitimate member is spelled as an outsider (in which case the
  fix is the spelling), or because the rule is a value-level invariant a TYPE could carry (step 3's
  `ReconciledThrough` shape, and X-aj's shape for `status_id`). Only the first of those three is a
  package move; ruling all three the same way is the error this step exists to avoid.

  **The deletion of each checker is the LAST commit of its own arm, never the first** -- the C3b3
  prove-the-successor-first rule, which E2-n above states for the same reason and which this arc has
  now applied eight times. A fence removed before its structural replacement is measured leaves the
  invariant with nothing at all defending it.

- [ ] **G2** `refactor(money): the value types that retire three checkers` -- retires **W9901**,
  **W9904** and **W9902**, the three the 2026-08-03 inventory found with no structural replacement
  scheduled anywhere. **Its first action is a trace and no code is written before it**, because the
  two halves are different sizes and only one of them is obviously worth it.

  **The money half.** `Money` -- a value type over `Decimal` that cannot be constructed from a
  `float` and whose rounding is a method carrying the app's rule. It retires W9901 and W9904
  together, because both exist only because the raw `Decimal` constructor and `.quantize` are
  reachable. **Measured 2026-08-03: 44 `Numeric(12, 2)` money columns and 37 `.quantize(` call
  sites**, so this is the largest single refactor in the inventory and its trace must decide whether
  it lands at the ORM boundary (a `TypeDecorator`, so every money column returns `Money` and the
  blast radius is the type, not the call sites) or as a hand-conversion. The `TypeDecorator` route is
  the one that makes the checkers redundant BY CONSTRUCTION rather than by coverage.

  **The label half is small and should not wait for it.** `DisplayLabel` -- a type returned by a ref
  table's `.name` whose `__eq__` against a `str` RAISES `TypeError`, which is exactly step 3's
  `ReconciledThrough` shape ("a restatement of the rule is a `TypeError` rather than a lint
  finding"). It retires W9902 and its two module sets. The Jinja half needs its own answer, because a
  template comparison is not type-checked at all -- so the trace must establish whether the template
  hook survives as the ONLY fence in this phase, and say so rather than assume the type covers both
  languages.


## 6. The findings ledger

**Only UNRESOLVED findings are here, and every one of them has an OWNER** (Section 9 rule 6). The
CLOSED registers are in the two as-built records: the 20 that Phase X's shipped steps closed are in
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

**Two more came out of X-q2, and both were BORN with an owner** (rule 6), which is the difference
from the four stale resolvers above: **N-103** (the archived drawer renders a column that is not a
loan's balance -- owner **X-e**, which already listed that read) and **N-104** (N-100's root
surviving inside the milestone dicts and in the debt-summary dict's flattening -- owner **X-s**, a
step created for it in the same commit).

**Since 2026-07-28 this is no longer checked by hand: plan step X-h shipped the gate**
(`tools/plan_gate/test_balance_plan_ledger_integrity.py` -- the path corrected here 2026-07-28,
X-h's fifth commit `cd002872` having moved it out of `tests/` and this paragraph not having followed
it), which parses this document on **every commit that touches it** (a `pre-commit` hook scoped to
this file and to the gate, plus the CI step that runs the checker tests) and fails on an owner
naming a ticked step, an owner naming an ID that is not a Section 5 checkbox, an owner outside rule
6's closed vocabulary, an empty owner cell, or a row an unescaped `|` has split. **Run against the live ledger it found exactly one violation** -- the **E2** pointer
row, whose owner read `Section 5, Phase E2`; it is now `E2-0 / E2-n`, the phase's own two live
steps. Every other owner was already live, which is what the three hand-passes above bought and
what nothing now has to buy again.

**The ledger stands at 104 rows.**  The four newest are X-f1c3c's: **N-190**, the unserialised
posting reconcile -- a defect ruling R-EN opened on the cash side and DISCOVERED on the loan side,
where it had been live and unserialised since Commit 16 -- plus **N-191** (the app's civil day rests
on a compose variable at 78 call sites) and **N-192** (a `PostingError` that lost its constraint and
is now held out of reach by code alone) and **N-193**, the advisory-vs-row-lock cycle N-190's own fix opened -- found by the adversarial review that ran before the commit, which is what that review is for.  Six (**N-171**..**N-176**) are the from-scratch anchor
investigation's, owned by the **X-f1** / **X-f3** / **X-f4** leaves ruling **R-EB** created, and the
91st is **N-177** -- the dead `Settled` status, opened by X-f1's trace **on the developer's own
question** and given the new step **X-am** on their ruling -- and the 92nd is **N-178**, a LIVE
re-dating defect the same trace found and REPRODUCED, owned by **X-f1b0**.  **N-179..N-183 were X-f1b's THREE adversarial reviews**, which refuted three of the step's own claims; **N-179, N-182 and N-183 CLOSED when the step shipped** and their resolutions are in the X-f1b as-built entry, leaving N-180 (X-e) and N-181 (X-f1c).  **N-184 and N-185 are the same step's SECOND pair of reviews**, run against the finished tree: one found a control that could not fail inside the repair for a control that could not fail, the other found that the two halves of this step's own invariant are fenced unequally.  **N-186 is X-f1c's, and nothing reviewed it into existence** -- ruling R-EJ's write-door guard refused a test fixture, and the refusal turned out to be the finding: the R1 regression lock for "early settle, then time passes" encodes a settle dated three weeks in its own FUTURE, which is not an early settle at all.  **It CLOSED at X-f1c by opening N-187, which is the more serious row**: answering N-186's question meant tracing the two producers, and they turned out to key on different facts -- the ledger on the day the money moved, the resolver on the pay-period start -- so an installment paid before its pay period begins renders TWICE on the loan detail page.  A fixture refusal that would have been settled silently by moving a date instead surfaced a `$1,003.87` producer divergence that predates X-f1, and ruling **R-EK** gave it the new step **X-an**.  **N-177 above is the third time in this arc a
developer question has opened a finding no review did** (N-119 at X-aa, N-174 at X-f, N-177 here) --
named rather than written as "this one", because the N-186 / N-187 paragraph now sits between the
claim and its referent and neither of those came from a developer question. **X-f's build opened N-130 (the production defect), N-131 / N-132
(both CLOSED at the step), and its adversarial review then opened N-133 -- the one row in this arc
that a MEASUREMENT contradicted a ruling on**: R-DH (a)'s opening amendment, made mid-build on a
hypothetical and never re-scored, was 3.2x worse on the net plug than the rule the ruling's own table
scores. The review reached it by re-running that table rather than by reading it, which is Section
8's "score the rule you shipped, not the rule you designed" paying out for the first time.
**N-133 is now CLOSED** (developer ruling: revert the amendment, and date the opening), and the
review OF that residue opened **N-134** -- an anchor edit that moves the balance without writing its
history row. That second review is the arc's clearest evidence yet that a fix needs its own
adversarial pass: it found eight items in the residue, four High, including one the residue itself
INTRODUCED (`create_account_of_type`'s new default made two existing tests stop testing the case
they name -- N-132's shape, from a new cause). X-x's
trace opened **N-123** (the pay-calendar writer that refuses
the paydays between `today+1` and `today+13` and permits a permanent hole) and **N-124** (the
forward rolling window backfilling
history), both owned by the new step **X-ad**, and re-measured **N-116** from 63 branches to 96
across five distinct questions. Plan step **X-x2** then opened **N-125** (the salary cockpit, the
seventh answer to one question, deliberately not widened into) and **N-126** (a callerless public
contribution producer, found by the call-graph pass that stopped X-x2 from "fixing" a fabrication
nothing reaches), both owned by **X-x** itself. **X-x's two adversarial reviews then opened three
more**: **N-127** (the interior hole has no working repair, owner **X-ad** -- the finding that made
ruling R-DE hold the whole step), **N-128** (a hole breaks R-K's reconciliation identity inside the
fold, owner **X-l**) and **N-129** (~80 tests blinded by the refusal conversion, owner **X-x**).
X-u closed N-109 and opened N-115; X-v then closed **N-112** and
**N-113** (both archived, `dbf154c7`) and opened **N-116** (the period-absence twin, owner X-x) and
**N-117** (the fifteen surfaces that answer this state without the balance seam, owner X-y); X-aa then closed **N-119** and its review opened **N-120** (the emergency-fund footer's
double-rounded derived units, owner X-z). X-w's
trace opened **N-118** (the liability rule's two id-based spellings, owner X-z) and X-w then
CLOSED **N-114**, whose row stays here as a closed pointer until the next archive extraction.
**Its two adversarial reviews opened NOTHING**, which is a first for this arc: everything they
found was residue of the step itself, and plan step X-w6 shipped all of it.  **The developer's
QUESTION then opened one they had not** -- **N-119**, owner X-aa: the two records X-w reported in
prose and gave to nobody, and the unreachable nullable X-w4 wrote into the container it typed.
**This sentence is checked by the gate**, and it needed to be: it read `38` against a 40-row table
until 2026-07-28 -- drift left by an earlier step that updated the rows and not the prose about
them. `stated_count_violation` makes it a predicate rather than a number somebody remembers to
edit, on the same ground rule 6 is a gate; correcting it by hand is exactly what had already been
done, and is what drifted.

| id | finding (one line) | worst measured | status | closed by |
|---|---|---|---|---|
| FU-1 / F1 | **The Van Loan's one unexplained true-up STEP -- an operator question, not a code fix.** RE-SCOPED 2026-07-25 on a fresh PROD clone: the duplicate same-day anchors the finding named are DEV-CLONE pollution (created 2026-07-07 during arc development), not production. Prod's account 8 carries exactly THREE anchors (origination 2023-02-14 `$32,402.45`, user_trueup 2026-05-22 `$17,020.47`, user_trueup 2026-06-23 `$15,663.59`) and an audit trail of 6 INSERTs / 0 UPDATE / 0 DELETE -- the shape was never there, so it was not silently repaired either. What DOES remain: the 2026-06-23 true-up moves the balance `$905.33` beyond what the recorded payment explains (after the 06-22 installment's `$451.55` principal the walk stands at `$16,568.92`; the anchor asserts `$15,663.59`). That is a user ASSERTION, which the architecture treats as authoritative by design, not a defect -- the Mortgage's own 2026-05-22 true-up reconciles to the cent (`$177,829.83` == the walk after two payments), so the machinery is not suspect | `$905.33` against the servicer's statement | **OPEN -- awaiting the OPERATOR.** Whether the `$905.33` matches the servicer's statement is a question only you can answer; it blocks nothing, and the ledger is self-consistent under E1a's assert either way. Converted from a Phase F step to a finding at the 2026-07-26 trim, because it is a question and not a commit | operator (unchanged by the R-AO triage) |
| FU-3 | Standing overpayment resolves at today for any as-of | -- | latent **RE-VERIFIED 2026-07-27** and it is the X-i class, not a C-phase note: `_resolution.py:294` calls `loan_standing_extra_for_account(account.id)`, which resolves through `recurring_transfer_query.py:72-76` off the CURRENT template row with no as-of, inside a resolution the context pins an `as_of` for. **TRIAGED 2026-07-27 (ruling R-AO): to X-i2.** | X-i2 |
| N-96 | **`balance_at.interest_by_period_for_account` is a public seam entry with ZERO `app/` callers.** AST-verified 2026-07-27 during X-g4b's review: the account-detail route reads `interest_projection_for_account`, and nothing reads this. `__init__.py` states as fact that it and `debt_schedule_rows` are "the two non-balance seam entries the out-of-cluster consumers (the account-detail route, the savings orchestrator) read" -- true of the second, FALSE of the first. Same class as the `calculate_interest` orphan X-g4b deleted, but it pre-dates that step rather than being created by it, so it was reported and not swept | a public seam entry no screen can reach, described as one two screens read | **OPEN -- found 2026-07-27** by X-g4b's adversarial review, AST-verified, deliberately NOT fixed in that commit (out of its scope, CLAUDE.md rule 6) | X-e |
| N-97 | **`app/utils/dates.py:314` cites `balance_resolver.daily_cash_balance_series` as a live consumer of `attribution_date`.** That producer was deleted at plan step X-c2b3 (the calendar's per-day line is the fold sampled at every day). The rule the sentence states -- one attribution rule shared by the calendar's day cells and the balance line's steps, so a flow's cell and its step land on the same day -- is STILL TRUE and load-bearing; only its named example is gone. Found 2026-07-27 in X-g4b's sweep, outside that step's 26-site scope | a present-tense claim naming a producer deleted a month earlier, in the docstring of the rule two surfaces share | **OPEN -- found 2026-07-27**, reported not fixed (pre-dates X-g4b) | X-p |
| N-103 (X-q2 trace + review) | **The archived-accounts drawer renders `current_anchor_balance` as "Last Balance", and for an amortizing loan that column is not a balance.** `_data._load_archived_accounts` read the column directly -- by design, since an archived account gets no engine or seam call -- and `savings/dashboard.html` labels it "Last Balance" (the citation was `_data.py:184`; **X-f1c3a re-pointed that read at `cash_ledger.resolve_anchor` and X-f1c3c deleted the column**, so the line now reads the ASSERTION at `_data.py:200`). `anchor_service.AmortizingAccountAnchorError` states the rule in terms ("a loan's balance is never `accounts.current_anchor_balance` -- it is ledger-derived"), and the cash true-up door REFUSES an amortizing account, so nothing keeps the column true for a loan; a loan true-up appends a `LoanAnchorEvent` and never touches it. This is archived finding B-15's shape on the one surface B-15's fix did not reach -- B-15 was the Mortgage column reading `$1.00` against a ledger of `$177,277.97`. **Not live: zero archived loans exist on either database** (the two archived accounts are cash), so nothing is on screen today; a user archiving either real loan sees it immediately. Options are (a) suppress the line for accounts whose balance is ledger-derived, (b) resolve archived loans through the seam -- which also decides N-102's badge, and (c) record `archived_at` and read the ledger at that instant, since "what it owed when you archived it" is a fact the app does not store (`updated_at` moves on every edit) | Mortgage column `$178,103.41` against `$177,277.97` owed; Van Loan column **`$0.00`** against **`$15,663.59`** owed -- the whole balance, because a loan's true-ups never write the column (measured on `shekel` 2026-07-27; `$15,205.63` on `shekel_f3_final`) | **OPEN -- found 2026-07-27** by X-q2's trace while pricing N-102's badge, and sharpened by its adversarial review, which found the anchor service's own sentence. Deliberately NOT fixed there: the developer ruled the badge NO and this figure to X-e, which owns the column's fate and already lists this exact read  **RE-POINTED 2026-08-04 (R-EM):** the four no-current-period fallbacks that made this figure a displayable one move to the seam, and the column it reads is deleted, so the drawer's "Last Balance" becomes the last ASSERTION rather than a cache column.  Whether an archived loan's line should render at all survives as this row's question | X-f1c3a |
| cash D4 | Anchor column vs history table: divergence detected, only logged | latent | latent  **CLOSED BY DESIGN at X-f1c3a**: there is no column left to diverge from the history table, so the detector and `EVT_ANCHOR_CACHE_RECONCILED` are deleted rather than upgraded to a repair | X-f1c3a |
| N-4 (A1) | Pay-period reset re-anchors EVERY kind, refreshing loan cash-anchor rows (balance-preserving `stage_anchor_true_up` inside the reset's deferred-FK transaction; same-value, not user-supplied) | -- | **OPEN** -- residue of the archived B-15 (a kind-blind true-up wrote a CASH anchor onto a LOAN; both real loans carried such rows), whose mechanism closed at A1 while these two writers did not  **RE-POINTED 2026-08-04 (R-EO).**  `_reanchor_accounts` -- the writer this row is about -- is DELETED: once an assertion carries no pay period, a schedule rebuild does not need to re-anchor anything, and the reset stops writing a cash anchor onto a loan (or onto anything else) | X-f1c3b |
| N-5 (A1) | Account-create factory writes an origination cash anchor for every kind -- a loan created with a balance seeds the column at birth (entangled with loan onboarding) | -- | **OPEN** -- residue of the archived B-15, as above: the mechanism that RENDERED the wrong anchor closed at A1, the writers that create one did not  **RE-POINTED 2026-08-04 (R-EH / R-EO).**  The factory stops writing a cash-anchor COLUMN at X-f1c3c; what survives is the origination `AccountAnchorHistory` row it writes for every kind, which is the residue this row is really about once the column is gone | X-f1c3c |
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
| N-56 (X-c2b1) | **The desktop grid's two self-refresh endpoints now compute the SAME per-period view, twice per `balanceChanged`.**  ``#grid-summary`` (the sticky ``<tfoot>``) fires ``/grid/balance-row`` and ``#grid-subtotals-income`` fires ``/grid/subtotal-rows``, and since X-c2b1 both read one ``grid_balance_view`` -- which is what makes ruling R-K's identity survive the live swap, but means the browser pays for the projection twice.  Measured on the prod-shape clone 2026-07-26 (real Checking, 60 periods, 5 runs): per endpoint ``272.3 -> 165.4 ms`` (balance row) and ``87.9 -> 165.6 ms`` (subtotal rows), so the PAIR is ``360.2 -> 331.0 ms`` -- no aggregate regression, because the balance row stopped building a second override map, but the duplication is now visible and avoidable.  The fix is the pattern ``subtotal_rows`` already uses for its own two ``<tbody>`` blocks: let the balance-row response carry the two subtotal sections as ``hx-swap-oob`` fragments, so ONE GET refreshes the whole reconciling block and the rows are one response as well as one row set.  Not done here because it changes the refresh topology (a user-visible behaviour change in a commit whose contract is "the rendered grid is unchanged") and it has to clear the ``<template>`` parser constraint ``_balance_row.html`` documents | ``165.6 ms`` of duplicate producer work per refresh | recorded, deferred **Its stated home 'or X-c2b2' is STALE -- X-c2b2 has SHIPPED.** **TRIAGED 2026-07-27 (ruling R-AO): to X-i**, as its own commit and NOT closed by X-i1's memo -- these are two HTTP requests, so each builds its own context and a per-pass memo cannot reach across them. **Owner reworded 2026-07-27 (R-AQ): the parenthetical carried a retired vocabulary word.** It is X-i's work and X-i1's memo does NOT close it -- two HTTP requests, two contexts, so a per-pass memo cannot reach across them; its fix is the `hx-swap-oob` topology this row describes, shipped as X-i's own commit. | X-i |
| N-58 (X-c2b2 review) | **The analytics calendar renders a flow on one day and the balance step for it on another, with no row to explain the gap.**  A day cell shows BOTH its flow chips and its end-of-day balance.  The chips are placed on the BUDGET attribution date (`calendar_service._get_display_day`: `due_date` clamped into the period, falling back to the period start); the balance line is the fold, which steps on the day the money MOVED -- `paid_at`'s UTC civil day for a settled row, `max(attribution, as_of + 1)` for a still-projected one (ruling R-G).  They agreed by construction until X-c2b2, because the retired ramp distributed the same still-projected rows over the same attribution days.  **Measured (finding N-42, same data):** `paid_at - due_date` is median **2 days**, p75 **6**, max **25** across 130 settled Checking rows, so on production essentially every past chip is now displaced from its own balance step.  This is the split the GRID met and ruling R-K answered with the "Timing & true-ups" row; the calendar has no such row.  The step's own fixture demonstrates it: `test_flow_strip_low_trough_warning_cells` renders a `$600` chip on Jan 2 with `$1,000.00` under it and the `$400.00` drop on Jan 5.  The false docstring that still claimed the two share one clock is corrected at the site | chip and step up to 25 days apart; median 2 | **recorded, NOT fixed (developer ruling 2026-07-26: record, rule at its own step).**  The option space: (a) place the chip on the cash clock, which changes which MONTH a row appears in; (b) give the calendar R-O's treatment (a reconciling figure); (c) rule the divergence acceptable and label it.  Plan step X-f shrinks the date noise at its source, so ruling after it may change the answer **TRIAGED 2026-07-27 (ruling R-AO): to X-j**, and it KEEPS its own row's sequencing -- it is ruled after X-f, because X-f shrinks the date noise that defines the answer. X-j closes the rest of the cluster without it. **RE-TRIAGED 2026-07-27 (ruling R-AQ): there is no deferred category: to X-p, its own step, SEQUENCED after X-f and not deferred behind it.** X-f shrinks the date noise at its source, so ruling before it decides the question against numbers X-f then changes -- a schedule with a stated reason, which is what a deferral is not. X-j decides which PRODUCER this surface reads; X-p decides which CLOCK its chips sit on, and whichever ships second re-verifies the first. | X-p |
| N-14 (C6b) | **`contractual_schedule_from_origination` is computed twice per pass on the property page** -- once inside the (now-memoized) `ctx.loan_plan` and once in the equity chart's `_back_projection_by_month` (both call it for the same loan). Deferred (developer ruling): pure-CPU (no query), only 2x, property-page only, and a full dedup via a fourth context memo must FIRST prove the two call sites' rate-change inputs are identical (`load_rate_changes(id)` vs `resolved.context.rate_changes`) -- a correctness check better done in its own focused change | -- | recorded, deferred **Its stated home 'or Phase D' is STALE -- Phase D is COMPLETE and archived.** **TRIAGED 2026-07-27 (ruling R-AO): to X-i1**, where the fourth per-pass derivation joins the other three on one context memo. Its own condition still holds and is X-i1's work: prove the two call sites' rate-change inputs identical BEFORE deduplicating them. | X-i1 |
| E2 | **RATIFIED 2026-07-26 -- promoted OUT of this ledger and back into Section 5 as a committed step; see "Phase E2" there for the scan, the three open questions and the sequencing.** The row stays so the id resolves. Original text: **The super-package boundary: the option that would dissolve the last name-keyed gate.** Move the read seam, the write cluster and the shared leaves (`loan_ledger` / `cash_ledger`) under ONE package whose shared internals are private to it, so the W9909 classification registry -- the last name-keyed surface -- dissolves structurally the way the W9906 call allowlist already did. Large reorganization with its own arrow risks (the D0b class, where scoping the step showed it would ADD four fence entries); W9910's per-boundary membership would need extending | -- | **OPEN, recorded, NOT committed to** (developer ruling 2026-07-24). The registry's residue is small, fail-closed and self-attest-pinned, so the reorg must earn its churn on its own merits. Recorded so the option cannot be forgotten. Converted from a Phase E step to a finding at the 2026-07-26 trim, because it is an option and not a commit. **CLOSED as a finding and RE-PROMOTED to a step the same day**: the developer ruled the fences are to become structurally unnecessary, which is the one argument the 2026-07-24 ruling did not weigh -- it asked whether the reorg earned its churn on correctness grounds, and it does not; it earns it on the standing goal. Sequenced LAST because every structural step ahead of it deletes code it would otherwise move and then delete (measured at the step) | E2-0 / E2-n |
| N-72 (X-c2c trace) | **A modeled asset's balance is three producers merged by a preference order; the window that would have compensated for it is CANCELLED and the merge is DELETED instead.** `_merge_balance_sources` (`_investment.py:395-424`) picks forward projection, else the cash base, else reverse projection, per period. Finding N-43 is a bug in that preference order -- the fold, being TOTAL, always has the period, so it always wins and silently replaces two RULED pre-anchor models. Two fixes exist: keep the base out of the merge's way (the window), or have no merge (one replay). **X-c2c3 was to ship the window; ruling R-V (2026-07-26) CANCELLED it and X-g ships the replay instead** -- so this row records a band-aid that was recorded, priced and then NOT paid for, which is the outcome this ledger exists to make possible. Also measured here, and NOT introduced by X-c2c: one `/savings` render builds the modeled base **14 times for 4 accounts** (3x per IRA from two general `build_maps` passes plus retirement's own, 1x more from `investment_seed_map`, 2x for the Home) and `/investment` **4 times for one account** -- a pre-existing redundancy whose cause is upstream (consumers not sharing a read pass), which the developer ruled recorded, not fixed, at X-c2c | `-$6,315.57` of net-worth history is what N-43 measured the preference order silently rewriting | **recorded; NO compensator ships (ruling R-V), and the merge is DELETED at X-g.** The `/savings` and `/investment` redundancy half of this row is UNAFFECTED by R-V and stays open **TRIAGED 2026-07-27 (ruling R-AO): the merge half is X-g4's deletion; the `/savings` + `/investment` REDUNDANCY half is X-i1's** -- one `/savings` render threads ONE context (`_data.py:67` -> `_orchestrator.py:95`), which is what makes a per-pass memo reach all 14 builds. **The MERGE half CLOSED at plan step X-g4b (`17c57cde`)** -- `_merge_balance_sources` was deleted with its module. What is left of this row is the REDUNDANCY half alone: a per-pass memo's work. **Its `14 builds for 4 accounts` figure is a PRE-X-g2b measurement and is not the count today** -- one of the 14 was `investment_seed_map`, which X-g2b deleted outright, and the producer the rest were counted through (`_investment.build_investment_balance_map`) went at X-g4b. X-i1 re-measures before it designs a memo around it. | X-i1 (the redundancy) |
| X5 | **Anchor `effective_date`: an optional feature, not a step.** An `AccountAnchorHistory` row is dated by its `created_at` -- the instant it was ASSERTED -- so a user cannot enter a balance they read off last month's statement and have it land on last month. Adding an `effective_date` column would separate "when this was true" from "when it was typed", which is what a backdated statement assertion needs. Nothing depends on it: every shipped step and every remaining one (X-c2c .. X-g) works on the assertion instant | -- | **OPEN, optional, NOT committed to.** Converted from a step to a finding at the 2026-07-26 trim, on the same ground as E2: an optional feature nothing sequences against is a recorded option, not a commit -- and as the last numeric ID in a letter-suffixed scheme it read as a step whose position in the order was ambiguous. Its old text also said "NOT a prerequisite for X-a .. X-e", which had gone stale twice over (X-f and X-g did not exist when it was written) **TRIAGED 2026-07-27 (ruling R-AO): to X-e**, which was widened to decide it. It may still be declined; it may not be left unasked, because it is a question about the same table X-e is deciding the shape of. | X-e (widened 2026-07-27) |
| N-79 (X-g2 trace) | **The investment chart's projection axis and its contribution timeline are on two different calendars, so the chart answers differently depending on the day it is opened.** `_assemble_chart_context` projects over SYNTHETIC periods starting at `date.today()` (`growth_engine.generate_projection_periods`, `investment_dashboard_service.py:721-723`), while `ctx.contributions` is `build_contribution_timeline(..., periods=all_periods)` -- REAL pay periods, each `ContributionRecord` dated on its period's `start_date` (`investment_projection.py:639`). `growth_engine._project_one_period` looks a record up by the projection period's own `start_date` (`:399`), so the two align only when today IS a pay-period start: on a payday every synthetic period inside the real horizon matches its record and the chart applies the RECORDED amounts, and on the other thirteen days none matches and every period falls back to the flat `periodic_contribution`. Same account, same inputs, two answers | not yet measured in dollars; the gap is `recorded amount - periodic average` per period over the real horizon, and it is `$0.00` on today's data because no account has a contribution feed at all (ruling R-R) | **recorded, NOT fixed.** It is inside the forward WHAT-IF engine ruling R-U deliberately KEEPS, not the balance path, so X-g touches neither half -- but it is the same two-clocks-in-one-figure shape as plan step C6c-ii's double count and the plan's Section 8 lesson names it ("when a rule says period, ask if it means instant"). The honest fix is for the synthetic axis to carry the recorded feed by DATE rather than by period identity. **Its NEAR half closes at X-g2b as a side effect of ruling R-AF**: an axis opening the day after the current period's end lands on the real pay-period boundaries exactly (verified `2026-07-30..2026-08-12` against real period 9's own dates on both databases), so every synthetic period inside the real horizon matches its record whatever day the page is opened. The FAR half survives -- past the user's last pay period there is no record to match, which is where the flat fallback is the honest answer anyway **TRIAGED 2026-07-27 (ruling R-AO): the surviving FAR half is genuine RESIDUE.** Its near half closed at X-g2b via ruling R-AF; what is left is one chart's synthetic axis past the real pay calendar, sharing a root with nothing else here. **RE-TRIAGED 2026-07-27 (ruling R-AQ): there is no deferred category: the surviving FAR half goes to X-l**, which shares its root with N-82 -- past the materialized pay calendar the app improvises, and `_project_one_period`'s lookup IS by date (`:399`), so there is simply nothing out there to match. Its near half closed at X-g2b via ruling R-AF. | X-l |
| N-73 (X-c2c trace) | **Five balance sites guard against a NULL anchor on two `nullable=False` columns.** `Account.current_anchor_balance` and `current_anchor_period_id` are both `nullable=False` (`app/models/account.py:91`, `:100`) with a `current_anchor_balance IS NOT NULL` check constraint (`:55`), and there are **0 NULLs across all 19 account rows in both databases**. Yet `_kernel.build_account_balance_map:508`, `_kernel.base_account_balance_map:341`, `_kernel.interest_projection_for_account:395`, `_kernel.interest_by_period_for_account:440` and `_investment.get_anchor_period_index:127` each branch on `is None`, and two of them return a `None` / empty map that every caller must then handle -- so a state the schema refuses is propagating optionality through the seam's signatures (line numbers re-verified 2026-07-26; the originals drifted by two at `c649b322`) | -- | **recorded, NOT fixed** (out of X-c2c's scope, rule 6). It is not merely dead: X-g removes the anchor PERIOD from the balance paths entirely, at which point four of the five guards have nothing left to test. **Confirmed at X-g2's trace and re-scoped**: the replay reads no anchor period at all, so from X-g2b the guards test a state the schema refuses AND that the producer beneath them no longer consults -- but deleting them changes `balance_map`'s `\| None` contract, which every net-worth consumer handles, so they stay for X-e. **Corrected at X-g2b's trace: FIVE becomes FOUR**, because `base_account_balance_map` deletes with the ladder (ruling R-AD) and its guard at `:341` goes with the function rather than surviving as one of the five **Owner CORRECTED 2026-07-27: it cited `X-g2b`, which is TICKED** -- the second row X-h's ledger gate would have failed on. The NEED closed there; the GUARDS are X-e's.  **RE-POINTED 2026-08-04 (R-EH).**  All five guards are deleted at X-f1c3a, not re-shaped: the columns they defend against go, so the branch is not merely dead but inexpressible | X-f1c3a |
| N-82 (X-g2b trace) | **Past the pay-period horizon the replay's ACCRUAL keeps running while its CONTRIBUTION tier stops.** A contribution event is dated on a real payday (`_asset_contributions._dated_events` walks `pay_period_service.get_all_periods`), and there are no paydays past the user's last pay period; the accrual window's end is the caller's own furthest requested date. So a date beyond the calendar reads growth-only. Today's producer has the opposite wart -- `_kind_correct.balance_at` resolves the date to a period and `find_period_containing_date` falls back to the latest period that ENDED earlier, so a past-horizon date clamps to the final column. Measured at 2029-01-01, six months past a horizon ending 2028-07-12: Empower **`+$2,501.92`**, Roth `+$1,754.08`, Property `+$5,427.07`, Money Market `+$272.24` | `+$5,427.07` at six months past the horizon; `$0.00` on any rendered surface | **RULED at R-AG (2026-07-27): let the fold answer and record this.** Not live -- the only non-loan caller of that scalar in production is the `/investment` hero cell, at today (the other four call sites are loan-gated), so nothing reads a past-horizon modelled date. The honest fix is a payday cadence that outlives the calendar, which is materially larger than X-g2b and belongs with whatever step extends the pay-period horizon **TRIAGED 2026-07-27 (ruling R-AO): genuine RESIDUE.** Already RULED (R-AG) as record-not-fix, and its honest fix -- a payday cadence outliving the calendar -- belongs to whatever step extends the pay-period horizon, which no step here is. **RE-TRIAGED 2026-07-27 (ruling R-AQ): there is no deferred category: to X-l, and R-AG's record-not-fix half is SUPERSEDED.** R-AG's three grounds were one concession ('correct in principle') and two costs ('invents a calendar the app does not have', 'materially larger than this step'); cost is no longer a ground. R-AG's OTHER half STANDS and is what keeps X-l safe -- the fold stays TOTAL and is never clamped at a horizon. The honest fix R-AG itself named, a payday cadence that outlives the calendar, IS X-l. | X-l |
| N-83 (X-g2b trace) | **A Property's value is answered two ways on adjacent screens, and X-g2b widens the gap.** `/savings` net worth and the grid read the modelled map (which appreciates from the latest assertion's own day, ruling R-Y); the property detail page's equity HERO (`home_equity_service.resolve_home_equity:137`) and its equity CHART (`property_equity_chart._value_series`) BOTH read `Account.current_anchor_balance` -- the denormalized CACHE column -- and deliberately flat-carry it through today ("the last honest value"). Measured on `shekel`: the modelled current-period value is `$350,794.53` today against `$350,000.00` flat on the property page, a **`$794.53`** gap that X-g2b widens to **`$965.03`**. It is ruling R-W's shape on a different pair of surfaces (one account, two producers, no row explaining the difference), and it is also cash D4's cache column reaching a screen | **`$965.03`** after X-g2b, growing with the time since the last assertion | **recorded, NOT fixed -- and X-g2b TRIED and reverted, which is the row's most useful content.** Pointing `resolve_home_equity` at the seam inside the cutover produced two new defects its adversarial review caught: the equity CHART then double-counted the appreciation between today and the current period's end (its value became as-of the period end while `_value_series` still compounded from `today`), and the hero netted a period-end value against a today-dated debt leg. The second could not be fixed without moving the loan tile's own date convention as well, which is a third surface. So this stays PRE-EXISTING -- X-g2b changes its size, not its existence -- and the cross-page tests now assert the gap EXPLICITLY from both sides, so it cannot drift and this finding's own commit must update them. Its resolution is R-W's: one producer, with the difference rendered rather than implied, and ONE date for the whole property page. Note both readers take the CACHE column, so plan step X-e touches them too. **X-g3b ADDED a THIRD surface to the disagreement without widening it** (shipped 2026-07-27): the grid's current column for the Property is now `$350,965.03` on `shekel`, which is `/savings`' own figure to the cent (900 of 900 pairs across both databases), against the property page's flat `$350,000.00` -- so this row's resolver now has three readers to reconcile, not two **TRIAGED 2026-07-27 (ruling R-AO): the row SPLITS.** Its DISPLAY half -- one account answered two ways on adjacent screens with no row explaining it -- is X-j's, alongside N-87, because it is the same defect on a third surface pair. Its CACHE half -- both property readers taking `Account.current_anchor_balance` -- is X-e's. Neither half closes without the other, so whichever ships second must re-verify the first. | X-j (display) / X-f1c3a (cache) |
| N-85 (X-g2b) | **`interest_by_period_for_account` has no production caller and survives on its own tests.** The account-detail page reads `interest_projection_for_account` for BOTH halves of its projection (the balance chart and the "Interest, next 12 mo" chip) since finding N-64 collapsed the two seam calls into one; this sibling entry -- which returns the interest map alone -- is exported from the seam, exercised by 5 test call sites in 3 files, and called by nothing in `app/`. That is the dead-code-alive-for-its-own-tests shape plan steps C3b4 / D2a / F2 / E1e each deleted. It was PORTED to the replay at X-g2b rather than deleted, deliberately: that step's contract is to move producers onto one event stream, not to prune the seam's public surface (rule 6), and deleting a public entry with its tests is a different commit's work | -- | **recorded, NOT fixed.** Delete it with the rest of the incumbent at plan step X-g4, which is already reading this list; a two-line port is the cheap way to keep it honest until then **Owner CORRECTED 2026-07-27 (it cited `X-g4`, which is now TICKED) and the row is DE-DUPLICATED: this and N-96 are ONE defect**, found twice -- N-96 by X-g4b's adversarial review, which did not notice this row. X-g2b (`560b3339`) PORTED the entry to the replay and X-g4b did not delete it, so the name is still exported and still callerless -- AST-verified on 2026-07-27: zero `app/` calls, 5 test call sites in 3 files. N-96 carries the extra fact (the `__init__` sentence that names it as a surface two screens read) and both now point at the same owner; whichever ships closes both. | X-e |
| N-87 (X-g3 trace) | **The dashboard pulse justifies its cash basis by agreement with a grid that stopped agreeing at PR #47.** `dashboard_pulse_service.compute_pulse_section` (`:75`) reads the seam's kind-blind `cash_balance_map` (`:146`) and its comment gives three reasons (`:123-133`); the middle one is that the kind-correct map would "accrue interest into an HYSA's chart, amortize a loan, or compound an investment, **diverging from the grid that deliberately keeps the SAME account on the cash-flow view**". The grid has rendered the INTEREST-accrued balance since PR #47, so that clause has been false for ONE of the three kinds it names (interest) since PR #47 (`87cfdc5e`, 2026-06-28).  The review corrected an earlier "two of the kinds": INVESTMENT is still on the cash basis on BOTH surfaces today, which is X-g3's whole premise, and an AMORTIZING loan is refused by `account_resolver.is_cash_flow_account:41` so it reaches neither. **The two surfaces read at DIFFERENT dates and are measured apart** (corrected from the review, which caught the first draft conflating them).  The HERO reads `cash_balance_at(account, ctx, current_period.end_date)` -- the CURRENT period -- and diverges from the grid's current column by **`$55.88`** on the Fidelity Savings and `$17.79` / `$5.95` on the Money Market (`shekel` / `shekel_f3_final`).  The PULSE's forward trough/peak scan runs the whole horizon, where at the last projected period the cash view answers `$5,363.56` against the grid's `$5,779.68` (**`$416.12`**, identical on both databases) and `$16,644.27` against `$17,348.99` (**`$704.72`** on `shekel`; `$16,159.51` against `$16,819.41`, `$659.90`, on `shekel_f3_final`).  Neither surface carries a row explaining the difference.  **It is not hypothetical on the developer's own data: `resolve_grid_account` returns the Empower 401(k) on `shekel` today**, so after plan step X-g3b the DEFAULT `/dashboard` hero reads `$31,070.06` while the DEFAULT `/grid`'s current column reads `$31,751.40` -- the same account, the same period, two pages. The same two producers (`dashboard_service.py:97` `cash_balance_at`, `dashboard_pulse_service.py:146` `cash_balance_map`) feed `/dashboard`'s hero and its runway chart, and `calendar_service.py:689` / `:889` feed the analytics calendar the same way; all three are reachable for a modelled account, because `resolve_grid_account` and `resolve_analytics_account` admit every non-amortizing kind (`account_resolver.is_cash_flow_account:41`). Plan step X-g3 extends the same gap to `$6,263.60` / `$2,662.70` / `$17,776.85` on the three investments and `$21,856.66` on the Property. **The adversarial review found it is worse than "two adjacent screens", and this is the row's sharpest evidence:** the pulse's "Lowest point ahead" and "Highest point ahead" chips each carry a `view in grid` LINK (`templates/dashboard/_pulse.html:75-95`) built from `url_for('grid.index', offset=...)` with **no `account_id`**, so the grid re-resolves through the very same `resolve_grid_account` the dashboard used. One click joins a captioned figure to a different Decimal for the same account and the same period -- and the pulse chart's own `aria-label` says "Projected end balance for the next six months" (`:104`) against the grid row literally titled "Projected End Balance" (`_balance_row.html:88`) | **`$704.72`** live today; **`$21,856.66`** after X-g3, at the last projected column | **RULED at R-AK (2026-07-27); BOTH false clauses are now deleted -- the pulse's at X-g3a `320a4641`, and `dashboard_service`'s at X-g3b, which the X-g3a review MISSED even though this row named both producers by line.** The corrections shipped with their measured figures in the comments, so neither surface justifies its basis by an agreement that ended at PR #47. **The divergence is now LIVE on the developer's own default screens:** `resolve_grid_account` returns the Empower 401(k) on `shekel` (the saved `default_grid_account_id`), so the default `/dashboard` hero renders `$31,070.06` at the current period against the default `/grid`'s `$31,751.40` -- **`$681.34`**, same account, same period, one click apart. The pulse's OTHER argument is untouched by ruling R-W and was never weighed against it -- modelled growth inflates the "lowest point ahead", so a real future dip below zero could be hidden, which is a RUNWAY-safety property of the question `/dashboard` asks and not of the question the grid asks. Deciding it needs its own measurement (how often does an HYSA's accrual lift a trough above zero on real data?) and its own ruling, which is a different commit's work. It is finding N-76's shape on a second pair of surfaces -- one account, two producers, no row explaining the difference; CLOSED at X-g3b, register in `archive/cash_arc_as_built_2026-07-27.md` -- and the calendar half sits beside finding N-58. **One option R-AK's rejected list did not contain, surfaced by the review and left for this row's own commit:** if the two SERVICES legitimately answer different questions, the defect is the NAVIGATION that equates them -- so re-targeting or dropping the trough / peak `view in grid` links when the dashboard account models a return is strictly smaller than either option R-AK weighed, and does not touch a balance producer at all **TRIAGED 2026-07-27 (ruling R-AO): to X-j**, which it anchors -- it is the largest live contradiction the arc has left, and X-j does NOT pre-empt the runway-safety fork this row records: that is ruled at X-j's trace, before any code, with its own measurement. | X-j |
| N-89 (X-g3 trace review) | **The modelled contribution tier re-queries the whole pay-period calendar that its caller already loaded.** `_asset_contributions.contribution_events` ends in `pay_period_service.get_all_periods(account.user_id)` (`:250`), and that function is UNMEMOIZED -- a fresh `SELECT ... ORDER BY period_index` on every call (`pay_period_service.py:207-212`). Every grid entry has already loaded exactly that list and passed it in (`routes/grid.py:188`, `:740`, `:820`, `:877`), as has every `/savings` and `/investment` reader, so an INVESTMENT account pays for the calendar twice per read -- and one `balanceChanged` fires two grid endpoints that each rebuild the whole view (finding N-56), so 4 calendar loads of which 2 are redundant per interaction. **It is not fixable by taking the periods as an argument, and that is the point:** the tier LOADS rather than TAKES precisely because Section 8's "an argument a caller can get wrong is a defect, not a contract" was paid for here -- the cash fold once took the period list its visibility rule needed and a caller passed a WINDOW, moving a balance by `$150,000.00`. The honest fix is a memo on the read pass's `BalanceContext`, the shape `ctx.loan_plan` already has, so the list is loaded once per request and cannot be a caller's choice | 1 redundant calendar query per modelled read; 4 per grid `balanceChanged` | **recorded, NOT fixed.** Out of X-g3's scope (rule 6) and shared with `/savings`, so it belongs with whatever step memoizes the calendar on the context -- the same place finding N-14's second `contractual_schedule_from_origination` call is waiting **TRIAGED 2026-07-27 (ruling R-AO): to X-i1.** | X-i1 |
| N-90 (X-g3 trace review) | **Ruling R-K's identity is a property of the construction only in its BOUNDARY form; the form the screens render needs contiguous ordered periods and is unverifiable at the leftmost column.** The producers state the boundary form and it is airtight: `AssetPeriodFigures` (`_asset_fold.py:285-292`) and `CashPeriodFigures` (`_cash_fold.py:539`) both value each period over its OWN span `(start - 1 day, end]`, which is why `_asset_fold.asset_period_view`'s docstring can promise the periods "need be neither contiguous nor ordered" (`:716`).  `cash_period_view`'s own promise is narrower still -- "need not be contiguous and need not start at the account's anchor" (`_cash_fold.py:665-666`), with no order clause at all, which strengthens this finding rather than weakening it (the review corrected the attribution). What R-K, R-W, R-AH, the templates and `_assert_grid_view_reconciles` (`test_balance_at.py:2311-2323`) all actually use is the COLUMN-TO-COLUMN form `balance[p] - balance[p-1]`, which additionally requires `balance[p-1] == balance(p.start - 1 day)` -- true only when the rendered set is contiguous and in order. Nothing enforces it: the templates iterate whatever `periods` they were handed, and the test zips adjacent entries and assumes it. It holds today because all four grid entries pass `all_periods`, so this is caller discipline standing in for a structural property, which is the exact substitution this arc exists to remove. Second half: the leftmost rendered column has no predecessor, so its identity is unverifiable ON SCREEN in every window -- including `?periods=1`, the mobile This Period arrow-nav state, where it is the ONLY column | none measured (every production caller passes the full contiguous set) | **recorded, NOT fixed.** Not introduced by X-g3 -- it has been R-K's shape since X-c2b1 -- and closing it means either rendering the boundary form or having the view state its own contiguity, both of which are changes to what the grid displays rather than to what it computes **TRIAGED 2026-07-27 (ruling R-AO): to X-j**, whose whole subject is the RENDERED identity rather than the computed one. | X-j |
| N-91 (X-g3b-0 review) | **The modelled-contribution feed is measured against a clock nobody pinned, and the seam owns the handle it does not pass.** `_inputs._contribution_inputs_for_accounts` calls `income_service.get_current_gross_biweekly(user_id)` with neither of that function's two keywords (`income_service.py:54-58`), so the employer-match basis resolves against the helper's own implicit `date.today()` (`:120`) and against the user's first `is_active` salary profile ACROSS ALL SCENARIOS (`:110-116`), rather than against the read pass's pinned `ctx.as_of` / `ctx.scenario`. The deduction half is scenario-blind for the same reason (`projection_inputs._active_deductions_query:193-202` filters user and active only). It is the unnamed-clock shape `_kind_correct.balance_at` describes in its own "two dates, deliberately distinct" note (`:236-239`) and that `BalanceContext.build`'s `as_of` parameter exists to remove (`_context.py:180-186`) -- so a HISTORICAL read (`BalanceContext.build(user_id, as_of=<past>)`) models an account's contributions at TODAY's gross. **Measured on both databases: the gross is `$3,631.74` at today, `$0` at 2026-01-15 (before the first pay period) and `$3,722.53` at 2027-06-30** (the post-raise figure), so the value genuinely moves with the date it is not given; the scenario half is latent (one active profile, same answer either way). PRE-EXISTING and not a regression -- the retired `_AssembledInputs` took a `ctx` and never threaded it either -- and inert at HEAD, because the only non-default `as_of` in `app/` (`tax_report_service.py:373`) reaches `loan_interest_in_year` and never the contribution feed | `$90.79` per period of gross basis between today and a 2027 read; `$3,631.74` against `$0` for a pre-horizon one. `$0.00` live today (no historical-`as_of` caller reaches the feed) | **recorded, NOT fixed.** Threading `ctx` would CHANGE which profile is picked and which period the gross is measured at, which moves money -- so it needs its own measurement and its own ruling, not a slot inside a refactor that proved itself byte-identical. The parameter was NOT kept against that future fix: an argument nothing reads is one a caller can get wrong (Section 8), and `_contribution_inputs_for_accounts`' docstring now states this cost rather than claiming the feed is context-free -- which is what the review corrected **TRIAGED 2026-07-27 (ruling R-AO): to X-i2**, the money-moving half, which is exactly the trace-and-rule this row asks for rather than a slot inside a refactor. | X-i2 |
| N-92 (X-g3b-0 review) | **The contribution feed is the seam's one un-memoized per-pass derivation, and it is the most expensive loader in the set.** `_contribution_inputs_for_account` issues an investment-params query, a deductions query and a FULL paycheck-engine run (`get_current_gross_biweekly` -> `load_tax_configs` + `get_all_periods` + `calculate_paycheck`) on every call, with no cache. **Measured, best of five on both databases: `9.3-9.5 ms` for an INVESTMENT account and `0.0 ms` for a PLAIN one** (the loader skips the engine when no account in the set has params, so the cost is investment-only). `retirement_projection._resolve_seed_balances` (`:567-571`) loops `balance_at.balance_at` over every account in its context and every one of them is a retirement account, so that is one engine run per account -- on top of the one `build_maps` already did at `:498`. The seam memoizes loan resolutions, plans and payoffs on the context precisely to end this shape (`_context.py:136-164`; `resolved_loan`'s "ELEVEN times for two loans" note at `_resolution.py:136-140`); the feed is what is left. PRE-EXISTING: `_assemble_inputs([account], ctx)` cost exactly the same | `~9.4 ms` per investment account per seam read; `~28 ms` per pass on the developer's three real investments | **recorded, NOT fixed.** Out of X-g3b-0's scope (rule 6), and it shares a fix with finding N-91 rather than competing with it: a `ctx.contribution_feeds` memo through `_memoize_once` is the same shape `ctx.loan_plan` already has, and the commit that adds it is the natural place to decide what clock the feed is loaded at. Note it also shares a home with N-89 (the calendar re-query) and N-14 (the second `contractual_schedule_from_origination`) -- three per-pass derivations waiting on one context memo **TRIAGED 2026-07-27 (ruling R-AO): to X-i1.** | X-i1 |
| N-93 (X-g3b review) | **Every grid render entry now pays the modelled contribution load, including the one that reads none of it.** Plan step X-g3b made `grid_balance_view` load the account's real `ContributionInputs`, which for an INVESTMENT costs an investment-params query, a deductions query and a full paycheck-engine run: measured best-of-five on both databases at `2.7 -> 14.8 ms` (an APPRECIATING asset `2.7 -> 3.7 ms`; PLAIN and INTEREST inside run-to-run noise). The grid has FOUR render entries and each pays it. **The sharpest sub-case is `subtotal_rows`**, which resolves the whole modelled fold to render three rows that read only `income` / `expense` / `net` -- and plan step X-g3a's own entry declined to hand that endpoint an `accrual_label` because "handing it a label would add a context variable nothing reads", which is the same argument one level cheaper. It also compounds finding N-89: `income_service.get_current_gross_biweekly` loads the pay-period calendar (`income_service.py:127`) on top of the route's own load and `contribution_events`', so a modelled grid render loads it THREE times where N-89 recorded two, and one `balanceChanged` firing `balance_row` + `subtotal_rows` costs ~6 loads and 2 engine runs | `~12 ms` per modelled grid render entry; 1 extra calendar load per modelled read on top of N-89's | **recorded, NOT fixed.** The load itself is not waste -- it is what the INVESTMENT kind's return is modelled FROM (ruling R-AJ (a)), so it cannot be gated away without reinstating the defect X-g3b removed. What is addressable is the per-pass repetition, and that is findings N-89 / N-92's shared fix (a context memo), plus a narrower view record for `subtotal_rows` if that endpoint's cost is ever measured to matter **TRIAGED 2026-07-27 (ruling R-AO): to X-i1.** | X-i1 |
| N-86 (X-g2b review) | **The `/investment` limit CARD and the projection beside it read two different YTD boundaries, and only one of them is a function of the window.** `_compute_limit_info` renders `ytd_contributions` (the total THROUGH the current period -- what the user has actually put in this year), while `growth_engine.project_balance`'s `ytd_contributions_start` must hold exactly the periods the projection's window EXCLUDES. Those coincide only when the window opens past the current period, which is the case on this surface after ruling R-AF and is NOT the case on either of `retirement_projection`'s axes. X-g2b resolves the projection's half once, beside the window it depends on (`_context._projection_ytd`), so the two call sites cannot disagree; what is recorded is that the app now carries TWO correct YTD boundaries whose difference is invisible in the rendered figures. The durable version is for the engine to take the axis and derive the boundary itself, which is the same "one derivation" argument ruling R-AF made for the seed | `$1,000.00` of annual-limit room per period of divergence, compounded over the horizon -- the defect this finding's fix closed, measured on a `$23,500` limit at `$1,000`/period with today in the year's 15th period. `$0.00` on today's data: no real investment account has a contribution feed | **recorded, NOT fixed** -- the boundary is correct on both surfaces today and pinned in both directions by `TestTheAnnualLimitSeedFollowsTheWindow`. It is recorded because a THIRD projection surface would have to know the rule to get it right, and knowing a rule is what this arc replaces with structure **TRIAGED 2026-07-27 (ruling R-AO): genuine RESIDUE.** Correct on both surfaces today and pinned in both directions; its durable fix is inside the growth ENGINE's axis, which no remaining step touches. **RE-TRIAGED 2026-07-27 (ruling R-AQ): there is no deferred category: to X-m.** 'Correct on both surfaces today' is a statement about how many surfaces exist; the row's own text says a THIRD would have to KNOW the rule, and knowing a rule is what this arc replaces with structure. X-m has the engine derive the boundary from the axis it is handed, and `ytd_contributions_start` leaves the signature. | X-m |
| N-115 (X-u review) | **The dashboard tracks section pays TWICE for three loaders, and the expensive one is a full paycheck-engine run.** Plan step X-u deleted the duplicate debt PROJECTION; the duplicate LOADS behind it stayed, because `compute_tracks_section` shares one `BalanceContext` across its two producers and sharing the context is not sharing the loads. Measured on both databases with one active goal: `_load_dashboard_core_data` (accounts query + `get_all_periods` + `get_current_period`), `_load_account_params` (the AccountType query, `LoanParams`, `EscrowLine` + versions, the investment-params load) and `_get_current_paycheck_breakdown` each run **2x**, the last of them meaning **two full `calculate_paycheck` runs per render**. Its three call sites all pass `(user_id, core.all_periods, core.current_period)`, every argument off `core`, so the arg-threading closes with the memo | the SECOND breakdown alone: **7.2 ms / 7 SQL** in-request on `shekel`, **7.2 ms / 7 SQL** on `shekel_f3_final` -- against the 9 SQL X-u's whole deletion removed | **OPEN**, opened 2026-07-28 by X-u's adversarial design review, which counted the call graph where the step's own comment had counted one loader. Born with an owner (rule 6); X-i1's input tier is widened at its entry in the same commit, since three of these are not in the five loaders N-72 / N-89 / N-92 / N-93 named | X-i1 |
| N-116 (X-v census) | **The period preconditions are the baseline precondition's twin, at 63 branches, and nobody has read them either.** The same AST pass that re-counted N-112 measured **63** branches in `app/` on period absence (`current_period is None`, `not all_periods`, `not periods`), concentrated in `routes/accounts/detail` (5), `investment_dashboard_service/_chart` (4), `investment_projection` (4), `retirement_projection` (4), `routes/grid` (3), `dashboard_pulse_service` (3), `investment_dashboard_service/_context` (3). It is the same question X-v answers -- "what does this surface say when a precondition it needs is absent" -- and the same failure mode is already visible: `_projection_start` falls back to `date.today()` while `_current_balance_from_map` falls back to `current_anchor_balance`, two different degraded values for one state inside ONE package. **RE-MEASURED AND RE-WRITTEN 2026-07-31 by X-x's trace, which inverted two of the three claims above** (rulings R-CX..R-DA). The count was a grep's floor: an AST pass that taints period-valued expressions and reports only ABSENCE tests measures **96 branches in 49 files**, plus 8 in Jinja, resolving to about **50 distinct answers**. They are FIVE questions, not one -- any periods at all / which contains today / which contains date T / is there a next one / is the requested window non-empty -- and the last two are a normal terminal state and navigation, not absence. **"No pay periods" as this row states it is UNREACHABLE for an owner** (registration writes a bootstrap period, `PayPeriodTruncateSchema` floors `keep_through_index` at 0, and both `DELETE`s regenerate inside their own transaction), so 96 branches defend the state the app cannot be in. **The state it CAN be in has a one-click repair and corrupts money**, which is why this row's "its answer may well stay a degraded render" is withdrawn at ruling R-CY | **`+$3,228.55` of net worth and `+$4,515.00` of liquid**, measured on `shekel_f3_final` with a 5-day calendar hole: `/savings` reports `$236,325.04` against `$233,096.49`, its Checking tile renders `$2,932.41` (`Account.current_anchor_balance`, the derived cache), its trend collapses to 0 points with `current_index = 0`, and `/grid` renders the repair card at the same instant. Zero holes exist on either database today, and the hole is PERMANENT once made -- the rolling top-up counts `end_date >= today`, sees 52, and never fires | **OPEN**, opened 2026-07-28 by plan step X-v's AST census; re-measured 2026-07-31 by X-x's trace. Born with an owner (rule 6) | X-x |
| N-117 (X-v reviews) | **Fifteen surfaces decide the no-baseline state WITHOUT the balance seam, and neither instrument X-v built could see one of them.** They resolve the baseline directly (`get_baseline_scenario`), so X-v's AST census -- which followed `BalanceContext` -- was blind to them, and its route sweep graded only 5xx, so a surface answering with a plausible 200 passed. Both adversarial reviews found the class independently; the correctness one reached the worst instance by walking the call graph. **The two that FABRICATED A FINANCIAL STATEMENT were fixed in X-v3 under ruling R-CC** (the balance sheet reported assets / liabilities / equity of `$0.00` **and `in_balance = True`** over a ledger it cannot read; the income statement reported a period of zeros). The remaining fifteen answer a DIFFERENT question -- "what may a WRITE do without a scenario" -- five ways: `400 "No baseline scenario"` (the two grid create fragments, the carry-forward preview AND its POST mutator), a silent commit that generates nothing (template + transfer generation, `salary/_helpers`), `return 0` / `return None` (`period_population`, `spending_report_service`), a silently narrowed scenario set (both posting syncs), and a nullable id handed onward to a query (`escrow_rates`, `loan/params`) -- plus one that tells the user to **register a new account**, a repair story that has been wrong since `/grid/create-baseline` shipped | `$0.00` today and unreachable for the same measured reason as N-112 (the only baseline-less user is a companion, kept off every owner route by the ROLE check). The measured cost of the two already fixed was a balance sheet asserting its own tie-out | **OPEN**, opened 2026-07-29 by X-v's two adversarial reviews. Born with an owner (rule 6) | X-y |
| N-121 (X-z2) | **The cockpit reduces its liquid-savings total TWICE per render and publishes it under two keys.**  `_orchestrator._compute_emergency_fund_section` calls `_metrics._sum_liquid_balances(account_data)` for `total_savings`, and `_net_worth.compute_net_worth_today` calls the SAME function over the SAME `account_data` on the same render for `NetWorthToday.liquid`.  Two context keys carry the identical `Decimal`: `savings/dashboard.html:194,201` reads `total_savings` for the emergency-fund footer's guard and its "Based on $X savings" caption, and the cockpit hero chip reads `net_worth.today.liquid`.  It is ruling R-AZ's "one fact under two keys" with a redundant computation attached -- the reduction is pure CPU over data already in hand, so the cost is negligible and the DUPLICATION is the finding: a refinement to what counts as liquid lands on one reader and not the other, and the footer and the hero chip disagree with nothing failing.  The fix is not a merge but a decision about which key survives, since both are template-facing | `$0.00` today -- one function, one input, so the two are equal by construction on every render.  The measured cost is one redundant reduction over 8 accounts per `/savings` render | **OPEN**, opened 2026-07-30 by plan step X-z2, which made it visible by extracting the emergency-fund section, and FILED 2026-07-30 out of X-z's two adversarial reviews -- both found that X-z2's docstring claimed this row existed when it did not, which is rule 6's own failure mode written inside a claim of compliance with rule 6.  Born with an owner (rule 6) | X-ac |
| N-127 (X-x reviews) | **The interior hole has NO working repair, and plan step X-x points every refusal at it.**  `errors/no_pay_calendar.html` offers `/pay-periods/generate`; `pay_period_service._reject_overlapping_batch:61-62` bounds a new batch on `max(end_date)` over ALL the user's periods, so in the hole state the latest end is years ahead and no start date can fill the hole.  Measured on the gapped clone (latest end 2028-07-31, hole 2026-07-30..08-03), inside a rolled-back transaction: `2026-07-30` / `07-31` / `08-01` all REFUSED; `2026-08-04` ACCEPTED creating **zero** periods and flashing "Generated 0 pay periods"; `2028-08-01` ACCEPTED appending 52 past the far end with the hole untouched.  The bound protects no invariant in this case -- a batch that FILLS a hole overlaps nothing -- so the fix is the writer's predicate, not the card's copy.  **The lapsed and bootstrap-expired forms ARE repairable there**; only the hole is not, and it is the form this document calls permanent | `$0.00` in figures.  The cost is that plan step X-x, as written, would convert the hole user from wrong numbers on four pages into every page refusing with a button that rejects every input or silently creates nothing -- which is why ruling R-DE HOLDS X-x behind this | **OPEN**, opened 2026-07-31 by X-x's adversarial design review, which measured the repair rather than reading the link.  Born with an owner (rule 6) | X-ad |
| N-128 (X-x reviews) | **A pay-period hole breaks ruling R-K's reconciliation identity, and it is in the FOLD, not above it.**  `_cash_fold._cash_sums:868-877` and `_assertion_sums:905-911` both skip a fact whose day `_PeriodSpans.containing` cannot place (`if period_id is not None`), and `_budget_legs` never had it either -- while `_period_balances` samples the assembled fold by DATE and keeps it.  So the grid's documented identity `balance_delta == net + reconciliation + contribution + accrual` (`_grid.py:144-147`, `:177`) fails by exactly the amount that landed in the hole, and because the remainder is computed from the row set rather than as `balance_delta - net`, "Timing & true-ups" renders `$0.00` instead of surfacing it.  Measured on the period after the hole: no-hole clone `delta -2417.34 / net -2276.71 / reconciliation -140.63 / unexplained 0.00`; gapped clone `delta -2919.79 / net -2779.16 / reconciliation 0.00 / **unexplained -140.63**`.  **Plan step X-x does not refuse this state**: `require_current_period` fires only when the hole contains TODAY, so a hole in the past or future renders every page normally with a subtotal row that no longer reconciles.  N-123's own measurement is the way in -- a user who enters `today+20` at signup owns a permanent hole, and once today passes it this is their steady state | `-$140.63` on the gapped clone, and `$0.00` on `shekel` / `shekel_f3_final`, which carry no holes today.  The figure is whatever settled or asserted money falls in a hole; the BALANCE stays right, so nothing fails loudly | **OPEN**, opened 2026-07-31 by X-x's adversarial design review (ruling R-DF).  Owner **X-l**, which owns the calendar-as-total-function redesign and therefore owns what the fold does with a day belonging to no period; **X-ad** stops new holes reaching it.  Born with an owner (rule 6) | X-l |
| N-129 (X-x reviews) | **Converting a page to "raise" silently blinds every test whose fixture calendar does not cover today, including security controls.**  A test that requests a now-raising path grades `errors/no_pay_calendar.html` instead of the page it names, and PASSES whenever its assertions are satisfiable by the base template or are negative.  Measured with a recording handler over a full run: **84 unique node ids** fire `pay_calendar_gap`, of which 5 are intended -- a lower bound, since that run lost 2,018 tests to test-DB contention.  Proven cases: `test_xss_prevention.py:244::test_account_name` (5 parametrizations) is satisfied by the escaped flash toast in `base.html:...` rather than by the accounts cockpit rendering the name; `test_data_isolation.py:424-465 TestRetirementIsolation` (3 tests) passes because `b"Retirement"` matches the NAV LINK and its cross-user assertions are all negative, so three leak controls are vacuous on an error page; nineteen `test_security_headers.py` scans (CSP inline-style / inline-handler / CDN-origin) now scan the card.  **The fix is a GATE, not a sweep** (ruling R-DG): an autouse fixture failing any test in which the event fires outside an explicit allowlist, because the next step that converts a surface would otherwise re-blind the same tests | `$0.00` -- no production behaviour is wrong.  The cost is that ~80 controls, including one XSS and three cross-user isolation controls, stopped discriminating while staying green | **OPEN**, opened 2026-07-31 by X-x's adversarial code review, which measured it with an instrumented full run rather than by reading the diff.  Born with an owner (rule 6) | X-x |
| N-125 (X-x2) | **The salary cockpit is the SEVENTH answer to "no pay period covers today", and it is the one plan step X-x2 did not reach.**  `routes/salary/cockpit.py:264` blocks on `not periods or (current_period is None and requested_period_id is None)` and renders the cockpit with `_EMPTY_NO_PERIODS`, a page-specific generate-periods empty state; `routes/salary/views.py:61` redirects into it for the same state.  X-x2 collapsed the other six into ruling R-CY's one card -- `/grid`'s, `/dashboard`'s `_no_period.html` CTA, `/savings`' anchor-cache net worth, `/retirement`'s anchor-cache balances, the investment tile's, and the pulse fragment's -- and left this one because it is genuinely a different SHAPE: the cockpit legitimately renders a PAST period when `?period=` names one, so the precondition is "no period requested AND none current", not "no current period".  That is a real distinction and it is why the collapse is not mechanical.  **It publishes no fabricated figure** (the blocked render carries no money at all, measured on the gapped clone: `/salary` renders 12,807 bytes with zero currency strings), which is why it is a finding rather than a defect in the same class as the six | `$0.00` -- the state renders an empty page rather than a wrong number.  The cost is one more answer to keep in step: a change to the repair story lands on the card and not here | **OPEN**, opened 2026-07-31 by plan step X-x2, which measured it and deliberately did not widen into it.  Born with an owner (rule 6) | X-x |
| N-126 (X-x2) | **A public contribution producer has no caller in `app/`, and its whole body is a fabrication the trace was about to fix.**  `investment_projection.current_period_transfer_contribution:529` is reached by NOTHING in `app/` -- measured by an AST call-graph pass AND a grep, which agree -- and the only in-tree mention is a `retirement_projection:515` docstring saying the subtraction it performed "used to" happen.  Four tests import it, so it is alive only in its own coverage.  **It is recorded rather than deleted inside X-x2 because the deletion is not X-x's question**: this is the callerless-public-surface class findings N-85 / N-96 record, and the module also exports `_average_transfer_contribution` and the `InvestmentInputs` cluster around it, so the right unit is a look at what that module still owes anyone rather than one function removed mid-step.  **The trace nearly fixed its `ZERO` return as a live fabrication before the call-graph pass showed there is no caller to fabricate for** -- Section 8's "count the call graph, not the call sites", paying out in the opposite direction for once | `$0.00`: no `app/` path reaches it, so it renders nothing anywhere.  The cost is a public function whose tests read as coverage of a live surface | **OPEN**, opened 2026-07-31 by plan step X-x2's call-graph pass.  Born with an owner (rule 6) | X-x |
| N-133 (X-f adversarial review, 2026-07-31) | **CLOSED 2026-07-31 on `fix/n133-review-residue` -- ten of twelve items fixed, F3 obsolete, F11 the one still open.**  The OPENING amendment was never scored, and it was the only ruling in this arc that a measurement contradicted.  R-DH (a)'s EXCEPT clause was added mid-build on a hypothetical ("assert an opening of `$100`, record a `$100` transfer the same day") and the Section 3 table was not re-run against it.  Re-run: net plug **`-$2,997.48`** as shipped against **`-$940.06`** un-amended, gross `$17,282.84` against `$15,367.94`, worst single `$1,986.16` against `$1,853.92` -- on a method that reproduces the R0 / R2 / R3 rows to the cent.  Concretely, Checking's opening asserts `$2,746.58` on 2026-03-27 and FOUR settled rows carry that same civil day netting **`+$2,057.42`**, every one of them clicked 33 seconds to 1.6 hours AFTER the opening was typed, so the opening was read off a bank that already showed them; stacking them on top makes the walk read **`$4,804.00`** for a day the bank showed `$2,746.58`, and the next assertion books **`-$1,986.16`** instead of `+$71.26`.  **The amendment's motivating case has never occurred**: account 1 is the only account with ANY settled row on its opening's day, and there the amendment is wrong.  **And the case it protects is R-DH (a)'s own accepted residual pointed the other way** -- an opening's residual is bounded by the next assertion exactly as a true-up's is.  **Eleven more items** in `anchor_settle_partition.md` Section 8, of which four are structural: a FOURTH implementation of the partition untouched by the fix (`account_posting_service/_sync.py:304`, sound only because the display zone is WEST of UTC -- east of it, it silently UNDER-fires and strands a stale correction); the amendment is stated TWICE by hand, as a sort key and as a date boundary, so the two walks are held in step by convention; `dated_deltas`' tie-break was not moved with it and its docstring now claims a chronology it does not have; and the posting walk's monotonic source pointer assumes `observed_on` is non-decreasing in `created_at` order, which **step 2 breaks** the moment the column is user-supplied.  Plus: **ZERO net new tests** in `9c2c3130` (`def test_` counts identical in all 8 changed files, 7,675 collected at both commits), the two tests written for the amendment **cannot fail** (proven by reverting it), R-DH (c)'s two envelope invariants absent, and `resync_all_cash_postings` -- which rewrites the whole production ledger on every deploy -- untested.  **F2's blind tests were REPAIRED 2026-07-31**: the root cause was that `_origin_instant` is the ambient WALL CLOCK, so an hour offset genuinely can cross midnight and a smaller offset would have been flaky rather than blind -- the fix is a PINNED opening at 12:00 EDT plus an asserted same-civil-day precondition, the opening case parametrized BOTH directions, and `test_same_instant_settle_is_absorbed` renamed to `test_a_trueup_absorbs_a_settle_the_opening_rode_on_top_of`.  Negative-controlled: all three FAIL with the amendment reverted, where the opening test previously PASSED | **`$2,057.42`** on period 0's "Timing & true-ups" and on three days of March history; `$0.00` on the current period, which reads `-$19.95` under both variants because the walk resets at every later assertion.  The rest is `$0.00` today and latent: the zone-sign dependency cannot fire while the display zone is `America/New_York`, and the step-2 landmine cannot fire until step 2 | **RULED and APPLIED 2026-07-31: "Revert + date the opening now".**  The EXCEPT clause is deleted from R-DH (a) and from both walks; `account_anchor_history.observed_on` is a stored user-supplied `DATE` (migration `c4a19e7b2d80`, backfilled from the derivation it replaces so **0 of 15,682 seam figures moved**), the account-create form offers "Balance as of", and the anchor PERIOD is resolved FROM that day.  **F6 and F12 had to ship with it** -- the monotonic source pointer and the UTC-day dedupe index both assumed a DERIVED `observed_on`.  F4 closed (the self-heal skip compares days; `utc_day_start_instant` deleted as callerless), F5 closed by construction, F7 closed wider than scoped, F8 closed (counts CHANGED, one-way risk stated), F9 closed, F12 closed (and `AnchorPoint.as_of_date` deleted -- a UTC-day field with no production reader whose justification was that index).  **F2 closed**: `tests/test_services/test_anchor_settle_partition.py` holds R-DH (c)'s two envelope invariants, order-independence at projected-balance grain, and two `resync_all_cash_postings` tests, each negative-controlled by a distinct mutant.  Cost of the revert, re-measured: **30 failures on a 0-failure baseline and NOT ONE a financial re-ruling** -- the suite freezes today, so `seed_user`'s origination shared a civil day with its own settles and every "an account existed, then money moved" fixture had silently stopped saying so (N-132's shape one layer up).  **F11 was the last one open and it CLOSED 2026-08-01** -- measured on a clone, R-DH (d) STANDS AS RULED (Sections 10 and 11), and S1-c then shipped it as a re-ruling rather than as written (Section 12), so nothing here gates any longer; F3 is obsolete (PR #66).  **A SECOND adversarial review then ran against the residue itself (`anchor_settle_partition.md` Section 9) and found EIGHT more, four High -- all fixed.**  The largest was structural and two reviewers found it independently: `is_opening` was still derived in RECORDING order while every consumer read BUSINESS-DATE order, and it fired in this branch's own new test (a `$1,307.66` TRUE-UP posted tagged `account_opening`); fixed at the loader, which also deleted the two downstream re-sorts.  Also: both true-up writers picked the PERIOD on `date.today()` while dating the row `display_today()`; `observed_on` had NO lower bound (a year-1 date enumerates 740,560 accrual days per render and fabricates contribution history); the help text steered the user into the very case the ruling relies on the field to answer (measured: `$0.00` shown for an account holding `$500.00`); two captions dated the anchor from the keystroke; a SEVENTH UTC-day derivation F7 missed; the migration's two missing refusals; and **the fix RECREATED N-132's shape** -- `create_account_of_type`'s new day-before default made two existing tests stop testing the strictly-earlier arm they name.  Verified: standards 1, 2, 3, 5, 6 all PASS, `pylint app/ scripts/` 10.00/10 with zero messages, **7,687 passed / 0 failed**, 0 of 15,682 seam figures moved from the ruled reference | X-f |
| N-134 (N-133 residue review, 2026-08-01) | **An anchor balance can move with NO history row, and the cash walk then replays a history that disagrees with it.**  `routes/accounts/crud.py`'s `update_account` writes `current_anchor_balance` and appends an `AccountAnchorHistory` row only `if current_period` -- when no period contains today the balance moves and the row does not.  That breaks E-19's "a matching `AccountAnchorHistory` row from the moment it exists": the fold replays the OLD assertion while the cache says something else, which is exactly the divergence `cash_ledger._facts.resolve_anchor` logs as `EVT_ANCHOR_CACHE_RECONCILED` -- and the history row wins, so the user's edit silently does not take.  Found while routing that inline block through `anchor_service.stage_anchor_true_up` (the two had already drifted: the stager takes a `notes` label and the route did not, and `observed_on` would have become a THIRD hand-written copy).  Behaviour preserved VERBATIM through that refactor rather than changed under an unrelated ruling | `$0.00` today: it needs a user whose pay-period schedule has no period covering today, which the rolling-window generator makes rare.  The cost when it fires is a silently-discarded balance edit, not a wrong arithmetic result | **OPEN, and RULED 2026-08-03 (R-EE's consequence).**  Of the two shapes -- refuse loudly, or fall back the way `account_service.resolve_anchor_period_id` does (earliest period) -- the second is taken, because X-f1c4 makes `stage_anchor_true_up` take a DAY and derive the period from it.  `resolve_anchor_period_id` never returns `None` (it falls back to the earliest period and raises only for a user with no periods at all), so the `if current_period:` arm this row is about has nothing left to guard and is DELETED rather than re-shaped.  The fix is structural: a period can no longer be chosen beside a day, which is ruling R-EA verbatim  **RE-POINTED 2026-08-04 (R-EO), and the resolution CHANGED.**  The recorded fix was to fall back to `resolve_anchor_period_id`'s earliest period; that function is DELETED as callerless, because an assertion no longer carries a period at all.  So the `if current_period:` arm has nothing to guard for a stronger reason than the one recorded, and it goes at X-f1c3c with the column its else-branch wrote | X-f1c3c |
| N-135 (step 3's three adversarial reviews, 2026-08-01) | **The partition's fence covers the derived boundary and NOT the two bare fact fields, so the line step 3 deleted still compiles.**  `cash_ledger.ReconciledThrough` defines no ordering against a civil day, so `settled_on <= boundary` raises `TypeError` -- but `CashAnchorFact.observed_on` and `CashSourceFact.settled_on` are still plain `date`s (they have to be: they key a day, sort a stream and date a journal entry), so `x <= fact.observed_on` -- verbatim the restatement step 3 removed from `account_posting_service._walk` -- is writable in any new module with nothing to flag it.  **Step 3's own claim that a lint checker "could not have worked" was WITHDRAWN on this**: a checker over the assertion-day vocabulary sees exactly this shape and the type does not, so the two fences are COMPLEMENTARY rather than substitutes.  The escape hatch is the same shape one level down -- `boundary.observed_day` unboxes in one token, and a reviewer confirmed the whole suite stays green when the converged self-heal site is unboxed back to a bare `<=` | `$0.00` today: every remaining read of both fields is a legitimate raw-date use (period bucketing x2, day-keying x3, the journal entry's `entry_date` column, the loader's sort key), and the seventh implementation the reviews DID find is converged.  The cost when it fires is a fifth answer to the question that cost production `$4,001.42` | **OPEN, OWNED, and RULED 2026-08-01: wrap both fields, at X-d.**  Ruled sequenced rather than done at step 3 on a measurement -- the wrap needs TWO distinct types (one shared type would still compare a settled day against an observed one), it unwraps at ~8 raw-date sites, and X-d DELETES one of the two consumers, so doing it there wraps a settled surface once instead of wrapping and re-cutting.  X-d's entry carries it as an explicit obligation.  **CLOSED at X-d (ruling R-DJ), 2026-08-03**: `CashAnchorFact.observed_on` is an `ObservedOn` and `CashSourceFact.settled_on` a `MovedOn`, both frozen single-field records ordered against their OWN kind only, in the new `cash_ledger/_days.py` package FLOOR beside `ReconciledThrough`; `covers` narrows to the event kind.  Both `x <= fact.observed_on` and `fact.settled_on <= fact.observed_on` are now `TypeError`s.  The fence bit TWICE during the build -- the deleted walk's bare-`date` call, and a test comparing the two kinds by hand -- and `test_anchor_settle_partition.py` gained two controls for it (`test_a_bare_day_cannot_reach_the_rule_at_all` and `test_the_two_kinds_of_day_carry_no_ordering_against_each_other`).  Open until X-d ships it | X-d |
| N-131 / N-132 (X-f build, 2026-07-31) | **Two test-instrument findings the step produced, both in `anchor_settle_partition.md` Section 7.1.**  **N-131**: the cross-page locks were a month-end TIME BOMB, red on `main` at the unmodified shipping commit and firing ~12 days a year -- CLOSED by `fix/cross-page-month-end-clock` (`92879e86`), which warrants its own PR because it carries no application change and unblocks the merge gate.  **N-132**: fixtures separated their events by HOURS against a partition that now reads civil days, so they collapsed onto one day and stopped discriminating the case they named; and four fixtures built midnight-UTC instants to MEAN a civil day, which is the previous evening in Eastern | `$0.00` in production figures; the cost is a merge gate red 12 days a year and a set of controls that had quietly stopped controlling | **CLOSED** at the step (N-131 by the cherry-picked fix, N-132 by day-offset conversion with the reason recorded at each site) | X-f |
| N-130 (production, 2026-07-31) | **The anchor/settle partition is decided by CLICK ORDER, and it cost `$4,001.42` on production.**  An ordinary bookkeeping session -- read the bank, enter the anchor, tick off what cleared -- rendered the grid's projected end balance at **-$4,021.37** against a hand-computed **-$19.95**, because `cash_ledger/_events.py:391-398` partitions assertions against settled rows on the INSTANT and three rows recorded in the nine seconds AFTER the anchor (`-$1,958.87`, `-$131.60`, `-$1,910.95`) were subtracted from a bank balance that already contained them.  Neither clock measures when money moved: `paid_at` is `db.func.now()` at the click (`status_seam.py:105`) and a cash anchor carries no date at all, only `created_at` -- while the LOAN anchor beside it has taken a user-supplied `anchor_date` since Commit 16.  **One question, FOUR implementations**: the read fold (instant, settle wins ties), the posting walk (`account_posting_service/_walk.py:434`, instant, assertion wins), the envelope entry reconcile (`entry_service.py:799`, DATE-granular, inclusive, and comparing against `today` rather than against the anchor at all) -- the last two running inside ONE `apply_anchor_true_up` call and disagreeing -- and, found 2026-07-31 by the adversarial review and untouched by the fix, the self-heal skip (`account_posting_service/_sync.py:304`, a civil date pushed back through midnight UTC and compared against a raw instant), which is sound only because the display zone is WEST of UTC (N-133).  A SECOND live instance: `TransactionEntry.is_cleared` is a stored flag written as a side effect of the anchor save, so entry-then-anchor sets it and anchor-then-entry does not (three such rows in production on 2026-07-31), and its manual override exists because the auto-rule is wrong.  Measured: **65 of 139** settled Checking rows (**47%**, `$19,602.13` gross) are within an hour of an assertion; **32 of 48** anchor-days carry rows recorded after that day's last anchor (`$22,357.52`); the historical smoking gun is 2026-04-01/02, where the same `$804.06` was entered twice and the engine booked a `+$1,910.95` true-up to undo its own double count.  **This is ruling R-N's cost estimate inverted**: R-N recorded "the reconciliation row's size, not its correctness", and it is the projected end balance | **`$4,001.42`** live on production 2026-07-31; `$40,554.34` gross plug over four months against **`$15,367.94`** under the rule that SHIPS, `-$6,998.90` net against `-$940.06`, over 53 true-ups.  **The `$14,286.82` this row first quoted was never reachable by any variant (N-133); the OPENING amendment, reverted by the F1 ruling, would have booked `-$2,997.48` net.**  The `$4,001.42` and the `-$4,021.37 -> -$19.95` are unaffected: both variants fix production identically | **RULED 2026-07-31 (R-DH); S1-a + S1-b SHIPPED TO PRODUCTION 2026-07-31 (PR #67, merge `fd0ddfab`).**  Verified against a fresh production clone: current period `-$19.95`, all nine past period ends land on an asserted balance, R-K's identity holds on 60 period pairs, the fold and the posted ledger agree on every date once the deploy hooks run, and of 15,682 captured seam figures only Checking and ONE loan column (`-$276.72`, the single payment whose visible day moves) move.  S1-c DEFERRED; the review's 12 open items are N-133.  Plan, trace, measurements, verification and the four steps: `anchor_settle_partition.md` | X-f (widened) |
| N-123 (X-x trace) | **The pay-calendar WRITER refuses every payday from `today+1` to `today+13`, and leaves a permanent hole on every one after `today+14`.**  `auth_service.register_user:692` writes a bootstrap period covering `today..today+13`, because `accounts.current_anchor_period_id` is `NOT NULL` (migration `cfb15e782f86`) and the default Checking account needs one to point at.  `pay_period_service._reject_overlapping_batch` then refuses any batch starting on or before the latest existing `end_date`, so on the form that says "Enter your next (or first) payday" a payday of `today+1`, `+5` or `+13` is REFUSED, `today` and `today+14` are clean accepts, and `today+20` or `+27` is accepted leaving a 6-day or 13-day hole -- measured at the service tier, one fresh registration per case.  **`today+0` was added 2026-07-31 by X-x's design review, which found the step's original "13 of 14 refused" generalisation wrong**: `generate_pay_periods:117-124` removes already-existing starts from the batch before `_reject_overlapping_batch` sees it, so the bootstrap's own start date passes.  The same guard permits a hole on `regenerate_pay_periods`, which a user changing paydays reaches after `reset` has become unavailable to them (it refuses once any transaction has settled).  A hole is the state plan step X-x's readers now refuse to answer, so the writer is producing what the reader is defending against | `$0.00` on both databases today (zero holes, verified by a `lag(end_date)` scan) and `$3,228.55` the moment one exists -- that measurement is N-116's.  The onboarding cost is not a figure: thirteen of the fourteen paydays after today cannot be entered through the documented flow, and every payday beyond `today+14` buys a permanent hole | **OPEN**, opened 2026-07-31 by plan step X-x's trace, which reached it re-asking ruling R-DB's fork after the first answer was measured wrong.  Born with an owner (rule 6) | X-ad |
| N-124 (X-x trace) | **The forward rolling top-up backfills HISTORY.**  `pay_period_admin.top_up_rolling_window` counts periods with `end_date >= as_of` and, when short of the target, appends the deficit through `extend_pay_periods` -- which starts at `last.end_date + 1`, wherever that is.  For a schedule that lapsed, that day is in the PAST, so the window generates past periods and `populate_periods_from_active_templates` fills them with recurring rows the user never saw.  Measured on a prod-shape clone whose schedule ended 1,000 days ago: two `/grid` loads took it **61 -> 113 -> 132 periods and +991 transactions**, 19 of the new periods entirely historical, before `_future_period_count` reached its target.  It self-heals, which is why nothing has ever reported it, and what it heals with is fabricated history | `$0.00` in balance terms -- the generated rows are Projected, so the fold values them on the PLANNED tier and no settled figure moves.  The cost is 991 transaction rows and 71 pay periods per lapsed user, and a budget history that shows planned spending in months the user was not using the app | **OPEN**, opened 2026-07-31 by plan step X-x's trace.  Born with an owner (rule 6) | X-ad |
| N-122 (X-z reviews) | **The asset-vs-liability rule has a SECOND home on the WRITE path, and plan step X-z's own docstring said it could not.**  `ledger_account_service.ledger_class_id_for_category:156` compares an account type's `category_id` against the cached LIABILITY id and answers Liability-class / Asset-class -- the same column, the same cached id and the same two-way question `account_category.is_liability_account` answers for every read surface.  `_ledger_class_id_for_account:178` applies it to a real account, `create_ledger_account_for_account` pairs the posting account with the class it returns, `:282` re-classes on a type change, and `account_validation:192-193` uses it to decide whether a type change flips an account's linked-ledger class.  The two agree by READING, which is finding N-118's condition exactly, surviving where the money is POSTED rather than displayed.  **Not fixable inside a refactor that proves itself byte-identical**: a re-class changes the class an account's postings are booked against, and `account_validation` exists to refuse that for an account that already carries them, so the trace has to answer what happens to those before the predicate is merged | `$0.00` today: `AcctCategoryEnum` has exactly four members and both spellings compare against the same id, so they cannot disagree.  A FIFTH category ruled a liability moves every cockpit surface together and leaves this one behind -- the account's postings book against an ASSET ledger account, the balance sheet reports the wrong side, and nothing raises | **OPEN**, opened 2026-07-30 by BOTH of X-z's adversarial reviews independently, each reaching it by walking the call graph rather than by grepping the predicate's spelling (Section 8).  Born with an owner (rule 6) | X-ab |
| N-137 (CI on PR #76, 2026-08-02) | **"Which pay period is it now" is answered on TWO clocks, and it was a MERGE-GATE time bomb that blocked every PR including a hotfix.**  `pay_period_service.get_current_period` and `get_current_and_future_periods` default `as_of` to `date.today()` -- the PROCESS zone -- while `account_service.create_account` resolves an anchor's period from `display_today()`.  The two pick DIFFERENT periods whenever the process day and the user's civil day straddle a period boundary.  **This is ruling R2's defect one level up**: R2 fixed the two anchor CALL SITES by passing `as_of=display_today()` and left the DEFAULT, which **20 of the 24** `app/` call sites use.  A fourth site, `income_service.py:131`, passes the PROCESS day EXPLICITLY into the balance seam (`balance_at/_inputs.py`), where a slip at a raise boundary returns the PRE-raise gross the employer-match cap is computed from.  Invisible in production, and that is why it grew: the container pins `TZ: America/New_York`, so process == display there.  CI pins `TZ=Pacific/Kiritimati` to catch this class, and on 2026-08-02 it did -- 8 failures on PR #76, **reproduced identically on `main`**, so it was never that PR's defect.  The TEST side had the mirror of it: `conftest._today_relative_start_date` and 14 sites in `test_accounts.py` built pay periods on the process day, so a fixture promising "today falls in period 4" put the USER's today in period 3 whenever the process day was a Monday | `$0.00` -- no figure is wrong in production, where the two clocks are the same day by container config.  The cost was a merge gate that failed outside the **04:00-09:59 UTC** window (EDT; on EST it narrows to 05:00-09:59, so the gate is red MORE often for four months a year), on a repo whose `main` is branch-protected: a hotfix could not have been merged for most of 2026-08-02 | **The MERGE-GATE half is CLOSED -- X-af SHIPPED to `main` 2026-08-02 (PR #77, merge `dbee3812`): test-only, 7,724 green under both zones, zero `app/` changes.  The APP half is OPEN and is N-138's, deliberately, which is why this row is re-pointed at that decision rather than archived: the defect is half-fixed, and filing it as CLOSED would lose the half that is not.**  Moving the two defaults was BUILT, measured, and REVERTED: it converts three app sites from agreeing-but-wrong into actively disagreeing (`dashboard_pulse_service` renders `today_offset` 14 against `days_total` 13 where `main` gives 0, breaking that function's own stated invariant; plus a salary WRITE path and the cockpit's window).  A partial move is measurably worse than either endpoint, so the app-side clock ships as ONE piece | developer-decision (2026-08-02) |
| N-138 (X-af's trace, 2026-08-02) | **The app has TWO "today"s, no enforced rule about which is which, and -- the part that decides the fix -- NO INSTRUMENT THAT CAN SEE THE DIFFERENCE.**  269 real `date.today()` / `datetime.today()` CALL expressions: **78 in `app/` across 39 files, 191 in `tests/` across 29** (AST, post-X-af; the METHOD is part of the finding, because raw `grep` gives 267-381 depending on whether docstring mentions count, and a first draft of this row quoted 375/109/266, which reproduced under no method at all).  `display_today()`'s own docstring claims to draw the line -- storage and the replay boundary stay UTC, presentation uses the user's zone -- but **that sentence is itself wrong**: `date.today()` is the PROCESS zone, which in production is `America/New_York` and in CI is UTC+14, never UTC.  R2 already recorded three sites making the identical category error.  **Neither clock gate can detect a split**: `tests/test_services/`'s autouse `freeze_today` patches `date.today()` and `datetime.now()` together, and `SHEKEL_FAKE_TODAY` travels to a tz-AWARE instant, which makes `time_machine` rewrite `os.environ["TZ"]` to the display zone -- so the weekly calendar sweep runs with both clocks equal by construction.  N-137 is the fourth instance of this class (N-133 / R2, `anchor_settle_partition.md` 12.10, R2's three false comments, now the pay-period question) and every one was found by a merge gate rather than by a test | `$0.00` today: production pins the zone, so every site agrees there.  The exposure is any process that is NOT pinned -- CI, a cron, a script, the migration host -- plus the merge-gate lottery, which recurs for any site still on the wrong clock | **OPEN, and it is a DECISION before it is work.**  **The first task is the INSTRUMENT, not the sweep**: a `SHEKEL_FAKE_INSTANT` that travels to a NAIVE UTC datetime preserves `TZ` and lets the sweep see a split, and `freeze_today` must stop moving both clocks in lockstep.  Until that exists, no sweep can be verified and X-af's own reviews could not certify completeness.  Then choose: (a) sweep `app/` to `display_today()` with a `shekel-process-clock` checker allowlisting `app/utils/dates.py` -- the step-3 "make the wrong spelling impossible" pattern, and the only shape that stops a fifth instance; (b) sweep only the civil-window sites; or (c) rule the container pin sufficient and accept CI as the detector.  **Named sites to start from, measured by X-af's reviews**: `pay_period_service`'s two defaults, `income_service.py:131`, `pay_period_admin` x3, `dashboard_pulse_service.py:329/613/654/792`, `routes/salary/_helpers.py:152`, `routes/salary/cockpit.py:257`, and `conftest.py:1369` whose docstring already promises an alignment it no longer has | developer-decision (2026-08-02) |
| N-142 (X-ae's adversarial reviews, 2026-08-02) | **`request.args.get(..., type=int)` is the one submitted-id surface X-ae did NOT convert, and it is lax in the same way the three it did convert were.**  Werkzeug catches the `ValueError`, so there is no crash -- but the coercion is `int()`, so it is Unicode-wide: measured, `args.get('account_id', type=int)` returns `106` for `'١٠٦'`, `2026` for `' 2026 '`, and `10` for `'1_0'`.  **43 `type=int` call sites by AST**, of which 39 are `request.args`, 3 a `request_args` alias, and 1 was `request.form` -- that last one (`transfers/_helpers.py`'s `source_txn_id`) was NOT a query-string site at all and is fixed in X-ae; the 42 query-string sites remain.  **They were left out on a real distinction rather than overlooked**: unlike the path parameters (all 123 row ids) and the schema fields (all 73 row ids), these are MIXED -- `account_id` and `period_id` are row ids, while `year`, `month`, `offset`, `periods` and `show_all` are not, and a blanket `parse_row_id` would refuse `0` where `offset=0` and `show_all=0` are meaningful.  So each site needs a per-site judgement, which is a step | `$0.00` and no crash: every one of these is a read-path filter or a display window, every id among them is re-scoped by owner downstream, and a respelled id resolves to the SAME row the ASCII spelling would.  The cost is that one row id keeps many spellings on this surface after the step whose deliverable was one -- a correctness-of-record defect, not a money defect | **OPEN.**  Needs a per-site ruling: row-id params take `type=parse_row_id`, and the genuinely-non-id params (`year`, `month`, `offset`, `periods`, `show_all`) need a separate ASCII-strict int coercion that permits `0` -- which is a second small rule in `digit_strings`, not a reuse of `parse_row_id` | X-ah |
| N-144 (X-d trace, 2026-08-02) | **`settled=` is a caller's OPINION about a row that already knows its own status, and X-d's assert is what made the disagreement visible.**  `posting_service.sync_transaction_postings(txn, *, settled)` and `sync_transfer_postings(xfer, *, settled)` take the caller's word for whether the row's confirmed effect should be posted.  Censused at X-d across all twelve production call sites: **every one passes the row's own status** (`txn.status.is_settled` at `mutations.py:373` / `:721` / `:983` and `carry_forward_service/_execute.py:237`, `current_status.is_settled` at `transfer_service.py:521`, the restored status at `:984`, the row's settled sense at `loan_posting_service/_sync.py:233`) -- except the two RETIRE paths, which pass `False` while the row still reads settled, because the reversal must be written while the FK link is live.  Ruling **R-DM** ORDERED that window (the reversal stops asserting; the re-derive runs once the row is final) but did not remove the parameter, so a thirteenth caller can still hand the writer an opinion that contradicts the row | `$0.00` today: after R-DM the two retire paths are the only callers whose argument differs from the row, and both are chokepoints.  The cost when it fires is a posted ledger that disagrees with the rows it is a projection of, which is exactly what the checked-projection assert exists to refuse | **OPEN, and ruling R-DU (2026-08-03) upgrades the remedy from "removable" to "unrepresentable".**  The parameter was to be deleted once the assert left the retire window; under the one verb there is no parameter to delete, because a caller never states a row's status -- it names an account and the writer reads the rows.  Dies at **X-ai-c** | X-ai |
| N-145 (X-d build, 2026-08-02) | **`app/services/transfer_service.py` sits at 999 lines against pylint's 1000-line ceiling, so the next change to it -- whatever it is -- must split it first.**  X-d's cash half of ruling R-DM adds ~9 lines to `delete_transfer` (re-derive both endpoints' anchors AFTER the shadows are final) and cannot land until the module has room.  The gate is correct and the module genuinely needs a seam; WHICH seam is a design decision, and four were traced: a symmetric `posting_service.retire_transfer(xfer, *, soft)` chokepoint mirroring the `retire_transaction` X-d already adds (which SHEDS lines from `transfer_service`, since `delete_transfer`'s soft/hard branch moves into it); collapsing `restore_transfer`'s six near-identical shadow re-mirror blocks into one data-driven loop (~-45 lines, a DRY win, but unrelated to X-d and on a mutation path); moving `restore_transfer` whole to a sibling module (~-210 lines, cleanest structurally, but it reaches for several of the module's private helpers so the move is not mechanical); and raising the ceiling with a scoped exemption (**rejected on sight** -- silencing a gate is what `CLAUDE.md`'s automated-enforcement section forbids).  **CORRECTION, 2026-08-02: the third option's stated objection is FALSE, and it is recorded here rather than quietly dropped because an option was rejected partly on it.**  An AST census of every module-level name each top-level function references finds `restore_transfer` using ZERO names defined in `transfer_service.py` -- every name it touches (`_get_transfer_or_raise`, `_sync_loan_postings_if_loan`, `ref_cache`, `db`, `log_event`) is already an import from a sibling.  Same for `delete_transfer`.  The move was mechanical all along; what actually disqualifies it is different and was not noticed until the ruling trace -- `app.services._transfer_restore` is not in `_STATUS_SEAM_MODULES`, so the option needs the W9907 allowlist WIDENED to accommodate a line count.  **Noted while tracing and deliberately NOT actioned** (rule 6): `transfer_service.py:525-531` re-syncs both endpoints after a settled `paid_at` edit, which R-DK makes redundant -- `sync_transfer_postings` self-heals both endpoints and no longer skips | `$0.00` -- a size gate, not a money defect.  It BLOCKS X-d, which is why it is a row rather than a note | **ANSWERED 2026-08-02 by ruling R-DN, and the developer refused all four options.**  The ruling is that the size gate is a SYMPTOM: the module is over the ceiling partly because it carries a second implementation of the transaction status seam, which is also why the W9907 allowlist has two entries.  Merging them frees the room, though **measurement corrected the attribution**: the merge itself is -13 lines and the -54 that actually clears the ceiling comes from extracting `restore_transfer`'s preconditions alongside it (ruling **R-DR**).  `transfer_service.py` reaches 937 of 1000.  Open until X-aj ships it | X-aj |
| N-146 (X-aj trace, 2026-08-02) | **A notes-only save on a PAID transfer moves its money forward in the books to today, and it is the ordinary UI path.**  On a finalised transfer the full-edit form disables `amount` / `pay_period_id` / `category_id` / `due_date` -- and the template's own header comment states the consequence: *"a disabled input is omitted from the POST, so a notes-only save still succeeds"* (`_transfer_full_edit.html:9-14`).  The `status_id` `<select>` is NOT disabled and renders the current status pre-`selected` (`:72-76`), so the save submits `{version_id, status_id (identity), notes}`.  `_LOCKED_EDIT_FIELDS` (`routes/transfers/mutations.py:66-68`) therefore sees NO locked field, the finalised lock passes it, and `transfer_service._apply_status_change` fires on the legal identity transition -- where, unlike the transaction seam, it stamps `paid_at = now()` **unconditionally** rather than preserving an existing one (`transfer_service.py:415-424` against `status_seam.py:100-105`, whose docstring states the preserving rule and its reason: *"so editing a Paid row -- which re-submits its unchanged status_id -- never churns the original payment time"*).  **Since plan step E1a `paid_at`'s display-civil day IS the posted `entry_date`** (step C2's one clock), and `status_id` is in `_POSTING_RELEVANT_FIELDS`, so the reconcile then re-dates the ledger to match | **REPRODUCED AT `HEAD` in an isolated worktree (`73158c27`), with the exact form payload, not a crafted one.**  A transfer settled 7 days ago: `paid_at` `2026-07-27 02:17:26Z -> 2026-08-03 02:17:26Z`, and the posted ledger went from ONE entry dated `2026-07-26` to **THREE** -- the original, a reversal at `2026-07-26`, and a fresh posting at **`2026-08-02`**.  The money moved seven days forward, and every balance between those two dates with it.  The displacement is unbounded: it is the whole gap between the true settle day and the day the notes were edited, so a transfer settled months ago moves months | **OPEN.**  Closed by R-DN: the merged seam preserves an existing `paid_at` on an idempotent re-settle, which is the transaction seam's rule already.  The regression control is one of the three X-aj owes, and it FAILS at `HEAD` today, which is how this was found | X-aj |
| N-147 (R-DQ, 2026-08-02) | **Two custom checkers still enforce a rule with a list of module names, which is a rule stated in prose plus a detector that must be kept complete.**  `shekel-ledger-model-bypass` carries `_LEDGER_MODEL_ALLOWLIST` (`ledger_model_fence.py:80`); the balance seam checker carries roughly a dozen module sets -- `_SEAM_PRIVATE_CONTEXT_MODULES`, `_LOAN_LEDGER_DEFINING_MODULES`, `_LOAN_PAYMENT_SEAM_MODULES`, `_LOAN_RESOLVER_ENGINE_MODULES`, `_CASH_LEDGER_MODULES`, `_KIND_CLASSIFIER_MODULES` -- plus a per-module EXPORT map, which is a DIFFERENT shape: it encodes what each producer may publish, not merely who may import it.  `shekel-private-module-import` (W9910) is the counter-example and the model, consulting no list at all.  **This is not a hypothetical maintenance worry**: this arc has already deleted the balance NAME fences at D3 and E1e once a structural boundary made them redundant, so the pattern of a list-bearing fence being retired rather than maintained is the arc's own established practice | `$0.00` directly -- a fence that is merely name-keyed has never itself moved a figure.  The cost is that each list is a second statement of a boundary, and a stale entry is a false negative, which is the dangerous direction for a fence.  The measured precedent is the balance name fences, which needed maintaining at every module move for the whole of Phases A-E until the structural form replaced them | **OPEN.**  Ruled 2026-08-02 (R-DQ) into its own phase, sequenced AFTER E2 because E2 moves the very modules these lists name.  Its first action is a trace that rules each allowlist separately -- absent boundary, mis-spelled member, or value-level invariant a TYPE could carry -- because ruling all three the same way is the error | G1 |
| N-148 (X-aj trace, 2026-08-02) | **The transfer -> shadow mirror rule is written THREE times and the three already disagree.**  `_build_shadow` states it at construction (`transfer_service.py:143-160`), `update_transfer` states it per-field on edit (`:582-652`), and `restore_transfer` states it a third time as drift repair (`:898-972`).  **`scenario_id` is mirrored at construction (`:148`) and is absent from the drift-repair list**, while that function's own docstring claims it re-syncs "every field the service mirrors from the canonical parent" (`:779-781`) -- so the docstring is false of the code beneath it.  `CLAUDE.md` names Transfer Invariants 3, 4 and 5 CRITICAL, and all three currently rest on three lists staying in step by memory; the measured proof that memory does not hold is that it already has not | `$0.00` on today's data, and the reason is worth stating because it is what makes this rule 7's case rather than a live defect: no application path edits a transfer's `scenario_id` (`update_transfer` accepts no such kwarg), so the one disagreeing field cannot drift except by direct ORM mutation -- which is precisely the scenario the drift repair exists for.  A finding that costs nothing today is a defect waiting for the data to change | **OPEN.**  Ruled 2026-08-02 into its OWN step rather than folded into X-aj, because unifying the mirror CHANGES behaviour (restore would begin repairing `scenario_id`) and X-aj's value is being provably behaviour-neutral apart from R-DN's and R-DO's two named changes.  Sequenced after X-aj, which deletes the status half of one of the three statements | X-ak |
| N-149 (X-aj trace, 2026-08-02) | **`create_transfer` applies NO transition check, so a transfer can be BORN in a status the transfer state machine exists to exclude.**  `verify_transition` appears exactly once in `transfer_service` (`:410`, the update path); the create path's only status handling is the `paid_at` coherence check at `:278-286`, which reads `is_settled` and never asks whether the status is legal for a transfer at all.  **The excluded statuses are excluded for a stated money reason**: `state_machine.py:30-35` records that Credit is kept out of the transfer map because *"a transfer pushed into Credit would be balance-excluded on both accounts with no compensating payback -- it would silently vanish from both projections"*, and that the split was made because a crafted PATCH could reach those states.  The PATCH door was closed; the CREATE door was never gated.  Settled is reachable the same way, which is a row born terminal without ever having been Done | `$0.00` today, and the reason is a property of the callers rather than of the design: all THREE production `create_transfer` call sites hardcode `projected_id` (`transfer_recurrence.py:103`, `routes/transfers/templates.py:665`, `routes/transfers/mutations.py:301`), so nothing user-reachable creates a transfer in any other status.  The suite does -- Paid, Received and Cancelled -- and **Received is not in the transfer map at all**, so those fixtures are constructing states the application says cannot exist | **OPEN.**  Carried by X-aj2 rather than fixed in X-aj1, because the fix is the born-status RULE ("born Projected, every other status a verified transition"), which is the same rule that decides what replaces the two constructor writes when `status_id` stops being assignable -- and it refuses creations the current tests make, so it is a behaviour change needing its own worked ruling | X-aj2 |
| N-150 (X-aj1 adversarial design review, 2026-08-02) | **A transfer shadow STORES five fields that Transfer Invariant 4 says must always equal its parent's, and NOTHING enforces the equality.**  `status_id`, `pay_period_id`, `estimated_amount`, `due_date` and `is_override` are stored copies; verified 2026-08-02 that `models/transaction.py` and `models/transfer.py` carry no CHECK constraint on any of them, there is no trigger and no ORM event, and the transfer service is the only thing keeping them in step.  **This document has ruled the identical shape out of existence twice**: R-DH (d) deleted `TransactionEntry.is_cleared` as "a denormalized copy of a derivable fact -- the `Account.current_anchor_*` disease X-e is already removing", and X-e rules that column "a reconciled cache or it is nothing".  **Every artifact plan step X-aj1 adds exists because of the copy** -- the three-row broadcast, the subset proof that lets it not refuse mid-flight, ruling R-DS's pair instant, and ruling R-DO's whole refusal -- which is the measurement that this is a root and not a nit: a fix that only ever adds machinery to keep copies equal is treating the symptom.  The counterweight is equally real and is recorded so the step does not rediscover it: **Transfer Invariant 5 says the balance calculator queries ONLY `budget.transactions`**, so a shadow's own columns are read on every balance path, and a shadow that stops looking like a transaction takes the whole mechanism with it | `$0.00` today, and the reason is a property of the callers rather than of the design: only `transfer_service` writes these fields, so they cannot drift by any application path.  **They have already drifted in principle**: `restore_transfer` exists to repair exactly this, its repair list omits `scenario_id` (finding N-148), and X-aj1's own ruling R-DO had to decide what to do about a shadow whose stored status its parent's cannot legally reach.  The cost when it fires is a shadow the balance calculator counts under the wrong status, period or amount -- money, on the surface Invariant 5 points at | **OPEN.**  Owned by X-ak, whose scope this REVERSES: that step must rule the copy (remove it, make it structural at the database, or keep it with the cost stated) BEFORE deciding anything about the three copiers, because unifying them while the copy stands makes the denormalization cheaper to maintain -- the opposite of what R-DH (d) and X-e ruled for the same shape | X-ak |
| N-151 (X-aj1 adversarial correctness review, 2026-08-02) | **The two `mark_done` routes pass an explicit `paid_at=now()`, which wins over the seam, so a REPLAYED settle still re-dates a settled transfer.**  Ruling R-DN's preserve-don't-churn rule closes finding N-146 for every caller that lets the seam decide; `routes/transfers/mutations.py:384-387` and `routes/transactions/mutations.py:592-595` do not -- they hand `db.func.now()` in, and `update_transfer`'s explicit-`paid_at` branch applies it after the seam has run.  An identity `Paid -> Paid` through either therefore re-stamps the instant and, since plan step E1a, re-dates the posted `entry_date` with it.  **This is pre-existing and NOT introduced by X-aj1**, and it is narrower than N-146 was: both buttons render only for a Projected row (`_transfer_full_edit.html:156`, `grid/_transaction_cell.html:137`), so reaching it needs a replayed or stale POST rather than an ordinary edit | Unmeasured on real data, and deliberately so: the displacement is the same unbounded gap N-146 carried (the true settle day to today), but N-146 was reachable by an ordinary notes edit and this needs a replayed request, so it is a smaller door onto the same room.  Recording it unmeasured rather than guessing a figure | **OPEN.**  The fix is a rule about what `mark_done` MEANS on an already-settled row -- "stamp the instant" or "settle it if it is not settled" -- which is the same question X-aj2 answers for what a row may be BORN as, so it is carried there rather than patched at two call sites | X-aj2 |
| N-152 (X-aj1 as-built, 2026-08-02) | **`transfer_service.py` lands at 987 of its 1000-line ceiling, so the size gate is answered for X-d and NOT solved.**  X-aj1 was ruled explicitly not to be a line-count step, and it is not -- but the as-built headroom is **13 lines** against X-d's ~9, and the next change after that hits the gate again.  **The root is that the module is still four lifecycle verbs in one file**: `create_transfer` (~200 lines), `update_transfer` (~140), `delete_transfer` (~100) and `restore_transfer` (~170), plus the shadow constructor.  Three private siblings already exist (`_transfer_validation`, `_transfer_ownership`, `_transfer_loan_posting`) and `_transfer_validation`'s own docstring records that it was "extracted from `transfer_service` so that module stays under the 1000-line module limit" -- so X-aj1's extraction is the FOURTH shave, which is the measurement that shaving is the pattern rather than the fix | `$0.00` -- a size gate, not a money defect, and the same class N-145 was.  The cost is that every future step touching a transfer mutation pays a shave first, and a shave under pressure is how X-aj1's own extraction came to be built before it was ruled | **OPEN.**  The structural answer traced at X-aj1 and not taken there (it would have been a fourth mechanism in one step): make `transfer_service` a PACKAGE, one private leaf per verb, mirroring the 12 service packages this codebase already has and the three this arc built.  Two properties were verified while tracing: the W9907 allowlist keeps working with ZERO edit, because `_module_in_allowlist` matches "exactly, or as a package prefix, so a module later split into a package keeps its submodules inside the set"; and it TIGHTENS W9910, since `app.services._transfer_*` is today importable by every service module while `app.services.transfer_service._x` would not be.  Sequenced after X-ak, which may change what a shadow stores and therefore what the verbs do | X-ak |
| N-153 (X-d resume trace, 2026-08-03) | **`transfer_service._reconcile_postings_after_update` re-syncs both endpoints' anchors after a settled `paid_at` edit, and ruling R-DK dissolved the reason it gives for existing.**  Its docstring justifies the six lines as insurance against *"the delta-keyed self-heal"* missing a future COMBINED edit -- and the self-heal was delta-keyed because of the SKIP predicate X-d deletes.  What remains is a plain gate on "were any entries emitted", and the only case that gate skips is one where the ledger did not change, so no correction can have staled.  Traced 2026-08-03: `update_transfer` mirrors `paid_at` to BOTH shadows (`transfer_service.py:660-661`), so a moved civil day always moves the date-keyed reconcile's key and always emits; a `paid_at` edit within one civil day moves nothing on either side | `$0.00` and no wrong figure -- both calls are idempotent and land on the same state.  The cost is a second statement of the rule X-d exists to state once, in a module at 997 of its 1000-line ceiling, where six redundant lines are half the remaining headroom | **OPEN.**  Deliberately NOT taken at X-d (ruling R-DT): reversing a documented deliberate decision is its own change and does not belong bundled into a writer swap.  Sequenced at **X-ai**, which moves the checked-projection assert to the commit boundary and therefore re-decides where every re-derive runs -- deciding this before it would decide half a design.  **Ruling R-DU (2026-08-03) answers it rather than re-deciding it**: with the re-derive triggered by the commit, a per-edit endpoint resync has nothing left to insure against and goes at **X-ai-c** | X-ai |
| N-154 (X-d build, 2026-08-03, found by MEASURING a disable rather than reading it) | **`useless-suppression` does not report a stale `duplicate-code` disable, so a `# pylint: disable=duplicate-code` that suppresses nothing survives every gate this repo runs.**  `.pylintrc` enables `useless-suppression` precisely so a disable that silences nothing is itself a finding.  Measured both directions on 2026-08-03: `_attribution.py`'s `duplicate-code` disable was held against a query in `account_posting_service._walk`, which X-d DELETES -- removing the two pragma lines leaves `pylint app/` at 10.00/10, and planting them back leaves it at 10.00/10 too, with no `I0021`.  The gate is blind in the one direction that matters | `$0.00`: a stale suppression writes no wrong figure.  The cost is that a `duplicate-code` disable is a permanent claim nothing can invalidate -- and this arc's own Section 8 rules a label weaker than a predicate.  **FIFTEEN more live `duplicate-code` disables exist in `app/` and not one has been re-measured** -- counted by AST-free grep 2026-08-03 across `models/` (4), `routes/` (5) and `services/` (6).  The first draft of this row said "two", which was a guess; recomputing it is what this arc's Section 8 rule about quoting numbers exists for | **OPEN.**  The instrument is undecided and that is why it is a step: `useless-suppression`'s blindness is upstream behaviour (R0801 is a close-time checker, so its suppression accounting is not per-line), so the fix is a gate of this repo's own -- most likely a pre-commit arm that strips each `duplicate-code` disable in turn and fails if the tree stays clean without it | X-al |
| N-155 (X-d's two adversarial reviews, 2026-08-03; the correctness lens found it and it was reproduced twice independently) | **The checked-projection assert grades a HALF-FINISHED operation in every batch loop, not only in the delete window ruling R-DM ordered around.**  The assert compares an account's WHOLE linked ledger against its WHOLE source-row walk, and it rides on the PER-ROW write path -- the self-heal at each sync's tail.  So any operation that settles N rows of one account and then posts them one at a time refuses at the FIRST one, because rows 2..N are settled and not yet posted.  **The contrast that points at the fix is the LOAN sync, and the reason is sharper than "it asserts last" (traced 2026-08-03): its RECONCILE scope equals its ASSERT scope.**  `sync_loan_postings` brings every leg on the ledger it grades to target first -- all payment splits, all anchor corrections, and the other writer's cash entries through `_reconcile_lineage_transfer_entries` -- while the cash side reconciles ONE ROW (`posting_service.py:774`) plus the correction legs, then grades source legs neither reconcile may write.  A grader whose scope exceeds its reconcile's scope MUST fail on a half-posted batch | **Three CONFIRMED production defects and one plausible.**  (a) `carry_forward_service/_execute.py:236` settles every envelope inside its `no_autoflush` block and posts them in a loop afterwards, so carrying forward TWO partly-spent envelopes on one account raises `PostingError: ... [walk -50.00 vs posted -30.00]` -- and `routes/transactions/carry_forward.py` catches only `NotFoundError` / `ValidationError`, so it is an UNHANDLED 500 and the whole carry-forward is lost.  Reproduced by the review and again independently.  Every carry-forward test used exactly ONE envelope, which is the case that cannot see it.  (b) `entry_service`'s three entry mutations retire the payback through `retire_transaction` -- which re-derives UNCONDITIONALLY -- before the parent's `actual_amount` is recomputed, so removing the last credit entry from a settled envelope 500s the same way.  (c) `posting_resync.resync_all_cash_postings` walks settled rows through the CHECKED sync, so the deploy hook whose whole job is repairing a multi-row stale-date state can repair only the first row per account before aborting the container under `set -eEuo pipefail`.  **(d) CONFIRMED 2026-08-03, upgraded from "plausible", with the trigger named**: `loan_posting_service._sync:233`'s stale-transfer repair loop calls the CHECKED cash wrapper per transfer, so TWO stale-dated transfers drawn on one checking account make iteration 1 grade that account while transfer B is still stale-dated -- the walk expects B at its settled day, the ledger holds the old one (the cash walk sees transfer shadows like any other settled row: `balance_predicates.balance_contributing_clause`, `cash_ledger/_events.py:13`).  **The loop is defeated by the assert in exactly the state the loop exists to repair**, and that residue is real (E1a measured `+$2,410.95` at 2026-07-02 against a reversal dated 2026-06-18 on the production Mortgage).  So the loan's OWN assert has no batch window, but the loan SYNC inherits this defect as a CALLER of the cash writer -- the row's earlier "the loan side does not have this defect" was too broad and is narrowed here.  Nothing is divergent on production today (verified read-only across all 9 accounts), so the cost is `$0.00` until the code ships | **OPEN, and it PARKS X-d** (developer ruling 2026-08-03: the placement is its own design step, not a patch at each loop -- batching at the four known sites would make the ordering an obligation four-plus callers must remember, which is the shape R-DM itself rejected).  **X-ai is WIDENED to own the whole placement question**, not only the commit-boundary hook | X-ai |
| N-156 (X-d's adversarial design review, 2026-08-03) | **The 1000-line ceiling has now split a SECOND module, and that split was recorded in a step's as-built rather than as a finding.**  `posting_service` crossed the gate at X-d, so the deploy sweep was evicted to a new PUBLIC flat module `posting_resync.py`; the module is left at 988/1000, TIGHTER than the 13 lines that made N-152 a finding for `transfer_service`.  **The stated forcing constraints do not survive the option that was not considered**: `posting_resync.py:14-16` names the established pattern -- both other posting packages keep their deploy-wide sweep in a `_sync` module beside the per-mutation writers -- and then departs from it.  Make `posting_service` a PACKAGE and W9910 is satisfied (the outside importer names the package) and the import-cycle objection dissolves | `$0.00`: a public module writes no wrong figure.  The cost is the pattern N-152 itself names -- every future step touching a posting writer pays a shave first -- now running in two modules instead of one | **OPEN.**  Same class and same answer as N-152, so it is owned beside it: make the over-ceiling module a package rather than shaving it again | X-ak |
| N-157 (X-d's adversarial design review, 2026-08-03) | **`resync_anchor_postings` is a NAME, not a chokepoint, and its docstring claims otherwise.**  It says it is *"the ONE name for 'an operation has finished; re-derive what it touched', and every caller that owns the end of an operation calls it here.  Three do."*  Counted: **five** entry points reach `_assert_checked_projection` -- `sync_account_anchor_postings` (direct, x2 from `transfer_service`), `sync_account_anchor_postings_all_scenarios` (x4), `resync_user_account_anchor_postings` (x2), `backfill_all_account_anchor_postings` (x1) and the named one -- so the ordering rule governs **9 call sites through 5 doors** and is stated in one docstring that 7 of them never touch.  `sync_account_anchor_postings`, the function that actually CONTAINS the assert, states no ordering rule at all | `$0.00` today.  The cost is that a future delete or revert path calling any of the other four doors mid-retirement raises in production with nothing in its docstring warning it -- and that whoever fixes N-155 must find five doors, not three | **OPEN, and ruling R-DU (2026-08-03) deletes the rule instead of relocating it.**  The remedy was to state the ordering rule on the function that contains the assert and shrink the overclaiming docstring.  Under the one verb there is no ordering rule to state -- a re-derive rebuilds from the facts and is therefore safe at any instant, so no door can call it "too early".  The docstring's claim still has to shrink to what is true, which is the only half that survives.  Dies at **X-ai-c** | X-ai |
| N-158 (X-d's adversarial design review, 2026-08-03) | **The shared checked-projection assert leaves its SIGN convention as an unnamed operator inside each caller's loop.**  `account_posting_service/_sync.py` folds `+ delta` and `loan_posting_service/_sync.py` folds `- delta` over the same four-line shape; `_posting_reconcile.py` justifies it as *"folding the sign in here would need a flag, and a flag is exactly the thing a caller can get wrong."*  An unnamed `-` inside a caller's loop is STRICTLY WEAKER than a flag: it is in no signature, no type checks it, and it does not appear as an argument in review | `$0.00` today, and the docstring names the cost if it is ever wrong: *"a sign flip still BALANCES every entry, so the trial balance closes and only the balance sheet is upside down"* -- which X-d measured again, a one-cent correction mutation growing the ledger by 71 entries with the trial balance still reading `$0.00`.  The one measurement pinning the cash sign (negating it fails all 7 pairs) was a hand-run probe, not a test | **OPEN.**  The option not considered: give each package a `posting_deltas(walk)` accessor stating the convention beside the ledger whose normal balance defines it, and let the shared assert take an iterable -- the sign becomes a property of the package and the loop is written once.  Sequenced at X-ai, which owns the assert | X-ai |
| N-159 (X-d's adversarial design review, 2026-08-03) | **Transfer retirement stays TWO halves a caller must remember to pair, which is the obligation `retire_transaction` was built to make structural.**  `delete_transfer` calls `reverse_transfer_postings_before_delete` and, ~70 lines and a soft/hard branch later, `account_posting_service.resync_anchor_postings`.  Ruling R-DN refuses the symmetric `posting_service.retire_transfer` because *"Transfer Invariant 4 reserves every shadow mutation to the transfer service"* -- while `retire_transaction` mutates a `Transaction`'s `is_deleted` from OUTSIDE `transaction_service`, so the two halves of one problem are justified on principles that contradict each other.  Section 8's own lesson applies verbatim: *"when two sides of ONE problem have different SHAPES, the loose side is where the next hole is"* | `$0.00` today: `delete_transfer` is the single transfer-delete path and it pairs them correctly.  The cost is that the pairing is a convention in one function rather than a structure, on the half of the problem that carries the CRITICAL transfer invariants | **OPEN, and ruling R-DU (2026-08-03) may dissolve the question before X-ak reaches it.**  Under the one verb, reverse-before-delete stops being a discipline a caller pairs: the row is deleted and the account is re-derived, so there are no two halves to keep together and no principle to rule between.  **Left owned by X-ak deliberately** -- the underlying question (whether a `retire_transfer` may mutate shadows from outside `transfer_service`) is a transfer-structure question that outlives the posting redesign, and re-homing another step's finding on a prediction is what this document's citation standard refuses.  Re-score it when X-ai-c lands | X-ak |
| N-160 (X-ai's loan-side trace, 2026-08-03) | **Two exported loan writers reconcile the ledger without ever grading it.**  `sync_loan_payment_postings` and `sync_loan_anchor_corrections` are public (`loan_posting_service/__init__.py:94,103`, both in `__all__`) and each reconciles ONE HALF of a loan's genesis ledger with no checked-projection assert -- the assert lives only in the unified `sync_loan_postings`.  Censused 2026-08-03 across `app/` and `scripts/`: **zero production callers**; the only callers are the split-value unit tests, which is what the payment one's docstring already says it survives for.  The step-E1a claim that "every one of those doors then runs the step-E1a assert" (`loan_posting_service/__init__.py:81-83`) is therefore true of the WIRED doors and not of the module's public surface | `$0.00` today, and it cannot fire from `app/` at all -- an unwired writer posts nothing.  The cost is that the ledger's public write surface is wider than the graded one, so a future caller reaching for the half it needs gets an ungraded write and nothing says so | **OPEN, and ruling R-DU (2026-08-03) decides it rather than leaving three options.**  Under the one verb a half-ledger writer cannot exist: the verb re-derives an account's WHOLE projection, so "reconcile only the payment splits" is not a smaller version of it but a different, ungraded thing.  Both go at **X-ai-b**, with the split-value unit tests re-pointed at the unified sync | X-ai |
| N-161 (X-ai-0's cost measurement, 2026-08-03; **RE-ROOTED the same day after an adversarial review showed the first diagnosis was the probe's key choice rather than the defect**) | **The anchor-correction reconcile violates the R2 attribution rule that the source reconcile obeys, and the same module implements R2 CORRECTLY in the one branch that does not run.**  R2 is stated in `posting_service`'s module docstring: a correction "carries the PAY PERIOD of the postings it reverses -- read back from the ledger per period, **never the source row's current period**".  The source reconcile obeys it structurally: `_posted_by_period` groups by `(pay_period_id, entry_date)`, so a delta lands in the period of what it corrects.  The correction reconcile does the forbidden thing at `account_posting_service/_anchors.py:187` -- `periods[key] = correction.anchor.pay_period_id`, the source row's current period -- because `posted_correction_legs` reads the posted side keyed `(source_kind_id, entry_date)` with NO period, so it cannot know which period it is correcting.  **The module knows the rule**: `_posted_only_key_period_id`'s docstring states it verbatim ("the R2-faithful period for its reversal is the period of the postings it reverses, read back from the LATEST posted correction entry") -- and that branch runs only for a key with no history row, which is the case the module itself calls defensive and unreachable.  **Measured on the clone**: Checking carries two assertions observed 2026-06-03 whose stored periods differ (5 and 6, history rows 49 and 50).  The walk's per-assertion corrections are `+$386.85` and `-$186.85`; the ledger holds `+$3,054.36` in period 5 (entry 148) and `-$2,854.36` in period 6 (entry 261).  The `-$2,854.36` is a REVERSAL of postings that live in period 5 and it was filed in period 6, which is exactly what R2 forbids.  **The period-blind key is also why the two entries' magnitudes are 7.9x and 15.3x the per-assertion corrections and cancel only in NET** -- one key cannot tell two same-day assertions apart, so the second correction reconciles as a delta against the first's whole posted amount.  A contributing but secondary cause for row 50's stored period: `get_current_period` defaulted `as_of` to `date.today()` while `stage_anchor_true_up` dated the row `display_today()`, fixed at `bee9b881` (2026-07-31) with the data unhealed.  **That cause does NOT explain history row 45** (Van Loan, observed 2026-05-21, stored against period 1 ending 2026-04-08 -- 43 days and four periods out); a one-day clock split cannot produce it and its cause is untraced | **`$0.00`, and no rendered figure moves** -- tested rather than asserted, and an adversarial review tried to refute it and could not.  The reconcile is at target under its OWN key (merged target `{8: +200.00, 30: -200.00}` against a posted net of `+$200.00`), so it emits nothing and cannot converge.  The confirmed income statement's pay-period arm filters `class_id IN (Income, Expense)` (`_income_statement.py:166`) and a correction's legs are Asset (linked, id 8) and Equity (anchor-equity, id 30); its calendar arm and the balance sheet fold by DATE; `balance_at/_cash_periods.py:461` buckets an assertion on `observed_on`, so the read side never reads the stored period at all; and `pay_period_admin._period_ids_with_unbalanced_ledger` (`:852`) locks a period whose entries do not net to zero PER LEDGER ACCOUNT, which both periods fail under either attribution by many accounts unrelated to the anchor.  **The cost is that a reversal is filed against a period it did not come from, permanently, in a ledger whose whole claim is that it is a faithful projection** | **CLOSED for the RULE half at X-ai-r (2026-08-03), and the shipped figures are NOT the ones this row first predicted.**  Adding `pay_period_id` to `posted_correction_legs`' `GROUP BY` and to the correction key makes the period come from the KEY -- the period of the postings being corrected -- which is R2.  **Shipped deltas: `-$2,854.36` in period 5 and `+$2,854.36` in period 6**, landing period 5 on `+$200.00` and period 6 on `$0.00`; a draft of this row predicted `±$3,054.36` under R-DZ's target key, which ruling **R-EA** superseded after an adversarial design review showed it files both assertions' corrections into a period neither was asserted for.  The shipped rule DERIVES the period from the assertion's day, which is what makes the ledger agree with the grid's "Book vs bank" row on 61 of 61 periods (R-DZ's key: 59 of 61).  **The per-ASSERTION split (`+$386.85` / `-$186.85`) needs an identity the ledger does not carry and waits for X-ai-s** -- both assertions share a containing period, so they still merge into one `+$200.00` entry.  A first draft of this row scheduled the whole thing behind the migration, which is the deferral the developer objected to.  The structural fix is to give a correction the source identity it lacks: `journal_entries` records WHICH source produced an entry by FK for a transaction and a transfer and carries no such column for an assertion, which is why `_anchors.py` synthesises one from `(kind, date)` -- and why `merge_target_legs` and `_posted_only_key_period_id` exist at all.  With the FK the correction reconcile becomes the SAME shape as the source reconcile, both helpers delete rather than being ported into the verb, and R2 holds by construction on all three source kinds.  It carries a MIGRATION and a backfill, so it wants its own step ahead of X-ai-a rather than riding a writer swap.  **Whichever key is chosen MOVES a figure** (`$2,854.36` between two columns under the read side's rule, `$2,667.51` under the stored-period rule) -- and the read side's rule is the one that shipped, at exactly the `$2,854.36` this sentence priced, so it is a ruled change rather than a refactor.  **The repair of the two stale history rows is NOT ahead of it and does not need to be** (N-168): the ledger now states the day's truth rather than inheriting the row's error, so a mis-filed row costs a stale `pay_period_id` on the row alone | X-ai |
| N-162 (X-ai-0's adversarial review of the measurement, 2026-08-03; reproduced twice independently) | **A walk-driven whole-account re-derive cannot see a source that LEFT the settled set, so X-ai-a's defining sentence has a hole in it.**  `walk_cash_ledger` names only settled rows (`settled_cash_facts` filters `status_id IN settled_status_ids()`), so a row that reverts, cancels, is soft-deleted, or is hard-deleted leaving SET-NULL residue is absent from the walk -- and a verb built literally from X-ai-a's sentence ("every source fact's entry reconciled to target") never hands it to `sync_transaction_postings(settled=False)` and never reverses its legs.  **Measured on the clone, twice, by two parties**: flip one settled Checking row (txn 2388, `$105.36`) to Projected and the walk drops from 139 facts to 138, the walk-driven re-derive emits **0** entries, and the row's legs `{ledger 8: -105.36, ledger 18: +105.36}` remain posted.  Today's per-row writer does not have this hole, because a revert calls the sync on the row itself; the hole is created BY moving to a walk-driven verb.  Ruling **R-DI** is adjacent but does not cover it: R-DI cedes the residue arm for postings whose source row is GONE, while this is a source row that still exists and is no longer settled | **`$0.00` today and unreachable from `app/`**, because no walk-driven cash verb is shipped -- the defect would be BORN with X-ai-a.  The cost if it ships uncaught is a reverted or cancelled row whose cash effect stays in the posted ledger forever, which every ledger reader then reports: the balance sheet, the statements, and the reconciliation oracle | **OPEN, and it is a REQUIREMENT on X-ai-a rather than a defect to fix afterwards**: the verb's source set must be the UNION of the walk's facts and the ledger's already-posted source links (the distinct `transaction_id` / `transfer_id` on entries touching the account's linked ledger), so a link the walk no longer names is reconciled to zero.  That union is also what makes the verb's assert scope equal its reconcile scope, which is N-155's root | X-ai |
| N-163 (X-ai's from-scratch design pass, 2026-08-03; the census is AST, not grep) | **A registry-scoped commit-boundary grader is BLIND to a bulk `UPDATE` or `DELETE` of a source table, and the door is 20 call sites wide.**  The grader R-DU ruled is a Python `before_commit` listener drained from a registry the posting writers populate.  A bulk statement calls no writer, so it registers nothing and the hook grades nothing -- while the rows it changed are exactly the facts the ledger is a projection of.  **This is finding N-65's class, already measured the expensive way**: the full suite came back with 41 failures, every one a bulk `query.update(...)` no session listener can see.  Censused by AST across `app/` and `scripts/` 2026-08-03: **20 bulk `Query.update` / `Query.delete` sites**, several on the cash fact table itself -- `routes/templates.py:234` bulk-updates `Transaction.name`, `carry_forward_service/_execute.py:156` and `:174` update projected rows, `routes/accounts/crud.py:749` bulk-DELETEs transactions (guarded upstream), and `entry_service.py:968` bulk-updates `TransactionEntry.settled_on` UNguarded.  **Ruling R-DX names this as the accepted limit rather than hiding it, and a first draft of this row overstated the DB tier's cover.**  `ck_account_postings_balanced` fires `AFTER INSERT OR UPDATE` and **NEVER on DELETE** -- deliberately, so a CASCADE disposal is not aborted mid-cascade (`posting_infrastructure.py:141-145`, `models/journal_entry.py:55-57`) -- and **9 of these 20 sites are `.delete()`**.  So the honest grounds are: the DB refuses an unbalanced entry on every WRITE; on DELETE the invariant is carried by an APPLICATION guard (`routes/accounts/crud.py:726`, whose own comment says "the balanced trigger does not fire on DELETE"); and a bypassed re-derive leaves the ledger STALE, which reconcile-to-target self-heals at the next re-derive of that account | **Unquantified today, and that is the finding.**  None of the 20 has been classified as to whether it can reach a POSTED row -- several are guarded by `is_projected_clause`, which makes them harmless.  **A first draft named `crud.py:749` as one that "deletes settled rows by construction"; that is REFUTED** -- two archive-instead-of-delete guards precede it (`:709` any non-deleted transaction, `:726` any ledger posting), so it is reachable only on an account with neither, and its own comment says it deletes soft-deleted ghosts.  The genuine unguarded example is **`entry_service.py:968`**, which bulk-updates `TransactionEntry.settled_on` with no projected guard -- and `settled_cash_leg` values a row by subtracting its credit entries, so that IS a bulk write to a cash fact.  The cost when it fires is a ledger that silently disagrees with its own source rows until something else touches that account | **OPEN, and owned by X-ai-g**, which classifies all 20: each is either proven unable to touch a posted row, or routed through a writer, or named in the one docstring that states what the grader cannot see.  **The remedy is NOT a checker forbidding bulk statements** -- several are legitimate and performance-load-bearing -- it is the classification plus one honest sentence | X-ai |
| N-164 (X-ai's from-scratch design pass, 2026-08-03; the mechanism was already named in the tree and its CONSEQUENCE was not) | **A transfer's posted effect is computed by TWO rules, and account-owned re-derivation would turn their disagreement into a write OSCILLATION.**  `cash_ledger.settled_cash_leg` values a shadow as `effective_amount - Sigma(credit entries)`; `posting_service._settle_effective` values the transfer as `COALESCE(actual_amount, estimated_amount)` on the INCOME shadow with no credit term.  `cash_ledger/_events.py:265-277` already states the mechanism and that the two "agree today only because Transfer Invariant 3 mirrors `actual_amount` onto both shadows and `entry_service` refuses entries on a shadow at all" -- calling it "exactly the 'two rules that happen to agree' shape this module claims to have ended, surviving on the rows that carry the largest cash movements".  **What was NOT named is what happens under a whole-account verb**: re-deriving the FROM account computes the entry from the expense shadow and re-deriving the TO account from the income shadow, so if the two ever differ each re-derive posts a delta back to its own target and every commit flips it.  Ruling **R-DU**'s idempotence argument -- "both endpoints compute the identical target from the identical rows" -- does not hold, because they read different rows | **`$0.00` today and no wrong figure**, because Transfer Invariant 3 holds and the credit term on a shadow is always zero, so the two rules are arithmetically identical on every existing row.  The cost is that the ledger's largest entries are correct only because a separate invariant enforced by discipline in `transfer_service` happens to hold, and the account-owned design would have made a breach oscillate rather than merely disagree | **OPEN, and ruled RESOLVED BY DESIGN at X-ai-a (R-DV / R-DW)**: under event-owned entries a transfer is ONE event with ONE valuation, so the second rule is deleted and the oscillation is unrepresentable.  The surviving READ-side asymmetry is deliberate -- the walk is per-account, so a broken Invariant 3 makes the walk disagree with the ledger and the checked-projection assert REFUSES the write, which is the fail-loud disposition rather than a silent correction.  X-ai-a must MEASURE that unifying on the leaf's rule moves no figure | X-ai |
| N-165 (X-ai's design review, 2026-08-03) | **A whole-account re-derive writes on OTHER accounts' ledgers, and the design gives no rule for grading them.**  A transfer entry has legs on BOTH endpoints' linked ledgers, and today `sync_transfer_postings` self-heals both explicitly (`posting_service.py:586-588` passes `(xfer.from_account_id, xfer.to_account_id)`).  Under a verb scoped to ONE account, re-deriving A emits a transfer delta onto B's ledger and nothing enqueues B.  **The loan side is sharper**: `_reconcile_lineage_transfer_entries` (`loan_posting_service/_sync.py:150-235`) already concedes that the loan sync "may RE-DATE a payment's cash entry, which touches the CHECKING side", and symmetrically a Checking re-derive that moves a loan-payment transfer moves the LOAN's linked-ledger per-date nets -- the exact quantity `_assert_checked_projection` (`_sync.py:237`) grades -- with nothing re-running the loan's assert | `$0.00` today and unreachable from `app/`: no whole-account cash verb is shipped, so the defect would be BORN with X-ai-a.  The cost if it ships uncaught is a ledger changed ungraded, which is the failure mode the whole restructure exists to eliminate | **OPEN, and a REQUIREMENT on X-ai-a rather than a defect to fix after**: the verb returns the `(account, scenario)` pairs its emitted legs touched and the caller re-enqueues them, with a stated termination argument -- a second re-derive of an account already at target emits nothing, so the fixpoint is one extra round | X-ai |
| N-166 (X-ai's design review, 2026-08-03) | **Two concurrent re-derives of one account double-post its correction, and no DB constraint can catch it.**  Under READ COMMITTED neither transaction sees the other's uncommitted legs, so both read the same posted state, both compute the same anchor-correction delta, and both INSERT it; the balanced trigger passes because each entry balances on its own.  Today's idempotency argument is stated as "the delta math PLUS the transaction's `version_id` optimistic lock" (`posting_service.py:640-642`) -- a lock on the SOURCE ROW, which does not cover account-scoped work driven by two different source rows on the same account.  **Moving the trigger to `before_commit` and widening the unit of work from one row to a whole account widens the window substantially**, and ruling R-DX's "identity invariants go to the DB tier" cannot help: reconcile deltas are legitimately many-per-key, so no uniqueness constraint is available | `$0.00` today -- the per-row writers are narrow enough that the window is a single row's `version_id`, which the optimistic lock does cover.  The cost when it fires is a double-posted correction: the trial balance still closes (both entries balance) and the account's ledger is wrong by the correction amount, which is exactly the class the checked-projection assert would then refuse on the NEXT write | **OPEN, and a REQUIREMENT on X-ai-a**: a per-`(account, scenario)` advisory lock taken inside the verb, on the pattern `pay_schedule_service.lock_schedule` already uses with a re-count under the lock (`pay_period_admin.py:598-603`) | X-ai |
| N-167 (X-ai's design review, 2026-08-03) | **The append-only ledger has no reversal linkage, so "which entry undoes which" is recoverable only by re-running the reconcile arithmetic.**  Corrections are made by posting reversing entries and `journal_entries` carries no `reverses_entry_id` / `correction_of_entry_id`.  **The cost is not hypothetical and this arc has paid it twice this week**: diagnosing N-161 required hand-tracing entries 148 and 261 against history rows 49 and 50, and X-ai-s's backfill has no resolvable structure for the 2026-04-15 group (3 events, 2 entries) precisely because the entries do not say what they undo.  A from-scratch double-entry design carries the column; R-DY adds five FKs and not this one | `$0.00` -- it moves no figure and never has.  The cost is diagnostic: every question of the form "why does this ledger hold these two entries" is answered by re-deriving rather than by reading, which is what made two findings this session cost hand-tracing | **OPEN, owned by X-ai-s**, which is the step that opens `journal_entries` for a migration anyway and is therefore the only cheap moment to add it.  It is NOT free: a reversal is emitted per `(key, ledger account)` delta and may net several prior entries, so the column is a nullable "the entry this most directly corrects" pointer, and the step must rule whether that is honest enough to be worth storing or whether the real answer is a reconcile-group id | X-ai |
| N-168 (X-ai's design review, 2026-08-03; the constraint a ruling proposed cannot exist as proposed) | **An anchor history row can be filed against a pay period that does not contain its own `observed_on`, BY DESIGN, and the invariant is not expressible as a CHECK.**  Ruling R-DX proposed it as a database CHECK; PostgreSQL refuses (`ERROR: cannot use subquery in check constraint` -- the predicate needs `pay_periods.start_date` / `end_date`), so it can only be a trigger.  **And live code produces the violation deliberately**: `account_service.resolve_anchor_period_id` (`:54-95`) rule 2 falls back to the user's EARLIEST period when none contains the date, and `_reject_undatable_observation`'s own docstring names the outcome verbatim -- "`resolve_anchor_period_id` silently falls back to the EARLIEST period, which files the row against a period its own `observed_on` falls outside".  A user whose periods are all in the future can legitimately assert today and land outside every one.  **Row 45's cause is therefore TRACED, and an earlier draft called it untraced**: it is the Commit-3 origination backfill (its own `notes` say so), which mirrors that same rule-2 fallback, while `observed_on` was added later and backfilled from `created_at` -- two migrations, two rules, one row | **`$0.00`, and row 45 is INERT.**  Account 8 is the Van Loan: `_load_non_amortizing_account` returns `None` for an amortizing account, so that history row produces no journal entry at all, and its `anchor_balance` is `$0.00`.  Row 50 (Checking) is the only one that reaches a correction, and X-ai-r fixes what it costs without touching the row.  The cost of the class is that a legitimate future-periods user files assertions outside their own periods and the grid's `spans.containing` buckets them nowhere.  **RE-SCORED at X-ai-r (ruling R-EA):** the LEDGER no longer inherits the error -- a correction is filed by the period containing its own day, so a mis-filed row costs a stale column on the row and nothing downstream.  What the class now costs instead is the CASCADE coupling R-EA cedes: a period wipe can dispose a mis-filed assertion while its correction lives in another period, where the two used to fall together.  That is bounded by the same 2 rows, and the resync every wipe path already runs re-derives what survives | **OPEN, and DELIBERATELY NOT bundled into X-ai-s** (a draft of that step demanded an F1-class human decision on both rows, one of which is inert).  The constraint is a TRIGGER paired with a fix to `resolve_anchor_period_id` rule 2, and ruling which of the two is the defect -- the fallback or the absence of a period -- is a data-model question that outlives the posting redesign  **RE-POINTED 2026-08-04 (R-EO): CLOSED STRUCTURALLY at X-f1c3b, and its two rows were re-measured on production the same day** (row 45 day `2026-05-21` in the `2026-03-26`..`04-08` period; row 50 day `2026-06-03` in the `06-04`..`06-17` period -- 2 of 78).  The column the invariant is about is DELETED, so there is no filing to be inconsistent with and no trigger to write.  The data-model question this row said outlives the posting redesign is answered by removing the data | X-f1c3b |
| N-169 (X-ai-r's adversarial design review, 2026-08-03) | **A chronology primitive both ledgers now depend on lives in the LOAN package, and the cash fact it replaced is now read by nothing.**  Ruling R-EA points BOTH anchor reconciles at `loan_ledger.resolve_anchor_pay_period` for "which period does this correction book in".  Nothing about that question is loan-specific -- it is `find_period_containing_date` plus a fallback -- and that helper's own docstring records it having ALREADY moved once for exactly this reason ("it moved from `account_projection` at plan step D1b: its only two callers are this module and the balance seam, so a kind CLASSIFIER was holding a chronology primitive").  It now has a third and fourth caller, one of them the CASH posting package, so the cohesion argument that moved it applies again.  **The other half: `CashAnchorFact.pay_period_id` has ZERO consumers in `app/`** (measured by grep across `app/` and `scripts/`; the three surviving `fact.pay_period_id` hits in `balance_at/_cash_periods.py` are `CashSourceFact`, a transaction's BUDGETED period, a different dataclass).  A field nothing reads is where the next writer re-introduces N-161 by mistaking a cache for a fact | **`$0.00`, and it moves no figure.**  Both are structural: the import is legal and fenced-clean (`resolve_anchor_pay_period` is an allowlisted non-producer in the balance-seam checker), and the dead field is documented in place rather than deleted.  The cost is that a future reader looking for the cash side's period rule finds it in a module named for loans, and that an unread column on a fact type still looks authoritative | **OPEN, owned by X-ai-s**, which is the step that decides what IDENTITY an assertion has on the ledger and therefore the only step that can rule whether `CashAnchorFact.pay_period_id` is deleted or becomes the FK's backfill input.  The primitive's HOME is decided there too: if the FK lands, both reconciles key on the event and the question is whether a shared chronology module is still earning its move  **RE-POINTED 2026-08-04 (R-EO): the cash half is ANSWERED and closes at X-f1c3b** -- `CashAnchorFact.pay_period_id` is DELETED rather than becoming a backfill input, so X-ai-s no longer owns that choice.  The primitive's HOME (a chronology helper living in the loan package) is untouched by this and stays this row's remainder | X-f1c3b |
| N-170 (X-ai-r's third adversarial design review, 2026-08-03; **a divergence the step ITSELF introduced, recorded rather than argued away**) | **The app now carries TWO day-to-period derivations that disagree, and X-ai-r is what split them.**  The WRITE side resolves an assertion's period with `account_service.resolve_anchor_period_id` (`:99-116`): the period CONTAINING the day, else the user's EARLIEST.  The LEDGER now resolves a correction's period with `loan_ledger.resolve_anchor_pay_period` (`_visible.py:191-192`): containing, else the LATEST period ENDING BEFORE the day, else the earliest.  They agree for a day inside the schedule and for a day before it; they diverge maximally for a day AFTER it.  **Measured on the production clone's own 61-period calendar (2026-03-26 .. 2028-07-26) for a day in 2029: the writer answers period index 0, the ledger answers index 60.**  Reachable without a clock bug: a user whose rolling window has lapsed (every period in the past) asserts a balance today -- `get_current_period` returns None, so the ROW is filed against the earliest period while its CORRECTION is filed against the latest.  Before X-ai-r the cash ledger COPIED the row's period, so the two agreed by construction; deriving is what made them two rules.  The same split reaches `pay_period_admin._reanchor_accounts` after a reset whose new schedule does not straddle today | **`$0.00`, and NOT reachable on today's production data**: the clone's schedule runs to 2028-07-26, so no assertion falls after it, and the re-derive on the prod clone moved 0 of 12,636 rendered figures.  The cost when it fires is that a row and its correction name different periods, which is the CASCADE decoupling ruling R-EA already cedes (N-168): a period wipe can dispose the assertion while the correction survives elsewhere.  Nothing RENDERS the divergence -- the grid's assertion row is built from the WALK, and the income statement's pay-period arm filters to Income/Expense classes which a correction's Asset+Equity legs never enter | **OPEN, owned by X-ak**, the step that already owns N-168 -- the two are one question ("is the fallback the defect, or the absence of a period?"), and answering it once fixes both.  The candidate resolution is to give BOTH sides one resolver, which means ruling on the fallback rather than the caller: the ledger's `latest ending before` is the more defensible answer for a day past the schedule, and `resolve_anchor_period_id`'s `earliest` is a Commit-3 backfill rule that outlived its migration  **RE-POINTED 2026-08-04 (R-EO): CLOSED STRUCTURALLY at X-f1c3b.**  The candidate resolution this row records -- give both sides ONE resolver -- is reached by deleting the writer's: `resolve_anchor_period_id` is callerless once neither the assertion row nor the account column needs a period, so the ledger's `containing, else latest ending before` is the only day-to-period rule left | X-f1c3b |
| N-171 (the from-scratch anchor investigation, 2026-08-03; measured read-only on the PRODUCTION database, not a clone) | **The true-up residual is booked to EQUITY, so real economic activity is structurally invisible on the income statement.**  `_anchors.py` books the correction's counter-leg to the per-account `anchor_equity` ledger, which is Equity class; `ledger_report_service/_income_statement.py:166` filters `class_id IN (Income, Expense)`.  On Checking over 129 days that is **$15,065.08 gross / -$1,495.10 net** that no spending report can see -- against **$22,735.97** of everything the statement DOES call expense, so the hidden gross is **66%** of the visible total.  Every source consulted (standard bank reconciliation, Beancount `pad`, QuickBooks Opening Balance Equity, GnuCash, hledger) treats an equity plug as exceptional and explicit; here it is automatic, silent, and fired on **49 of 51** assertion days | **-$1,495.10 of unclassified spending over four months, and $15,065.08 of gross churn through an account with no economic meaning.**  Net worth is unaffected (the plug balances), so this is an income-statement and classification defect, not a balance defect.  The trial balance still closes at `$0.00` over all 643 postings | **OPEN.**  Resolved by R-EB (d) -- the residual posts to Uncategorized Expense / Income -- and only correctly AFTER the date work, because reclassifying timing churn would put $15,065 of phantom spend in the report | X-f3 |
| N-172 (same investigation) | **The book-vs-bank gap at an assertion IS the rendered balance error immediately before it, and it is not small.**  The correction the engine plugs equals `asserted - what the recorded facts produced`, which is exactly what the app WOULD have shown had the user not asserted.  Measured per assertion day on Checking: **average $321.52, worst $1,853.92**, non-zero on 96% of days.  Gross is **10x** net and the series' lag-1 autocorrelation is **-0.33** with 22 of 42 consecutive non-zero pairs reversing sign, which is the signature of TIMING rather than missing money -- confirming the arc's existing "the amounts are right, the DATES are guesses" record and putting a number on it | **$321.52 typical, $1,853.92 worst**, on the account the developer budgets from | **OPEN.**  The churn half is X-f1/X-f2's; the residue half is X-f3's.  Recorded separately from N-171 because the two halves have different fixes and neither alone closes the row | X-f3 |
| N-173 (same investigation) | **For two thirds of the settled Checking rows the settle date is a bookkeeping-session artifact, not a fact about money.**  `paid_at` is `db.func.now()` at the click, and **88 of 135 settled rows (65.2%) share a click-minute with at least one other row** (80 distinct click-minutes, largest batch 6).  This is the GENERATOR of N-172's churn, and it is why `transactions.settled_on` (S2-b) is the first step rather than a follow-up | **65.2% of the account**, and it is the input every partition rule in this arc was built to compensate for | **OPEN**, owned by **X-f1**, which is the step that gives a settle its own posting day | X-f1 |
| N-174 (same investigation, on the developer's own correction of the question put to them) | **The PROJECTED END BALANCE -- the figure the developer actually budgets against -- inherits the whole gap, and the invariant protecting it is unbuilt.**  The developer stated it plainly: *"I work off the projected end balance as my way of knowing if I can afford a purchase or if I need to move expenses to a different pay period."*  R-DH (c) states the invariant for exactly that figure and its own note records it as **"NOT YET TRUE, and NOT YET TESTED"** -- `grep` finds those sentences only in this document, and record-then-anchor clears an entry while anchor-then-record does not.  Under R-EB (a) the invariant is algebraic rather than a rule two tests hold in step: recording a $150.27 purchase takes book to $1,157.39 and the envelope remainder to $349.73, and `1157.39 - 349.73 - 827.61 = -19.95` unchanged -- landing on the very figure R-DH (c) says the user must true up to BY HAND | **the affordability decision itself**, which is the highest-stakes read in the app for this user | **OPEN.**  This is the finding that ranks R-EB above the current scope: better dates shrink the noise, but only removing the reset makes the invariant unbreakable.  **X-f3's ship gate is this invariant passing as a TEST without a true-up, in both orders** | X-f3 |
| N-175 (same investigation, a PROCESS finding) | **`anchor_settle_partition.md` is a second live planning document for this arc, which Section 9 rule 1 prohibits.**  Its steps 1, 2, 3 and 4 and S1-c have ALL shipped; the only unshipped work in it is **S2-b**.  Rule 1 says new standalone plans for this arc are prohibited and rule 5 says a completed half is archived WHOLE rather than trimmed piecemeal, so the disposition is an archive move, not a deletion or a trim | **`$0.00`**, and the cost is orientation: the developer stated *"I have spent so much time and effort on this that I'm losing track of everything"*, and two live plan documents for one root is part of why | **OPEN.**  Archive it as the THIRD as-built record alongside the loan and cash arc records, carrying S2-b forward.  **RULED 2026-08-03 (R-EB): the archive move ships WITH X-f1**, so the superseded plan and the plan that supersedes it land together rather than leaving a window where neither is authoritative | X-f1 |
| N-176 (same investigation; a POSITIVE CONTROL, recorded because it is the only production exercise of the path) | **Five posted correction days on Checking carry no surviving anchor history row, and all five self-healed to `$0.00`.**  Entry dates 2026-04-29, 2026-05-06, 2026-05-15, 2026-06-16 and 2026-07-07 appear in the posted corrections but not in `account_anchor_history` (55 rows on 51 distinct days; the posted side carries 56 days).  Each nets to exactly zero, which means `_posted_only_key_period_id`'s defensive branch -- the one its own docstring calls unreachable through the linear lifecycle -- has in fact run in production and reversed correctly | **`$0.00`**, by construction and by measurement | **OPEN as a RECORD, not a defect.**  It matters to R-EB because X-f4 deletes that branch with the rest of the correction machinery, and deleting a path that has demonstrably fired in production should be a stated deletion rather than an unnoticed one | X-f4 |
| N-177 (X-f1's trace 2026-08-03, on the developer's own question -- *"I don't manually change a status from paid to settled so how does a transaction receive the status settled now?"*; measured read-only on a fresh PRODUCTION clone) | **The `Settled` status has no writer, no reader that distinguishes it, and zero rows.**  Measured: **0 of 897 non-deleted transactions and 0 of 120 non-deleted transfers** carry it (transactions are Projected 710 / Paid 134 / Received 22 / Credit 10 / Cancelled 21; transfers Projected 98 / Paid 17 / Cancelled 5), and **0 of 1,110 transaction + transfer audit rows have ever written `status_id = 6`** across the audit table's whole retention window (2026-05-06 to 2026-08-03, 2,712 rows -- so this is "never in three months", not "never ever").  `StatusEnum.SETTLED` has exactly four `app/` references and NONE assigns it to a row; the only door is the status `<select>` in the two full-edit popovers.  The balance engine cannot tell it from `Paid`: every consumer reads the SET `settled_status_ids()`, never the member, and `is_immutable` is already true for `Paid` and `Received`.  Its entire distinct behaviour is one transition-map line -- `settled: {settled}`, terminal | **`$0.00` today, and the cost is a THIRD member in the predicate every balance rule in this arc is written against.**  It is also a live trap for X-f1: `Paid -> Settled` is a RE-ENTRY into the settled band, and the seam preserves the settle instant on re-entry (`status_seam.py:157`) precisely so archiving does not re-date the money -- the N-146 defect class.  A stored `settled_on` must inherit that rule verbatim, so the dead status still constrains the new column's design | **OPEN.**  Developer ruled 2026-08-03 that it *"probably doesn't need to exist"* and directed it be recorded for later rather than taken inside X-f1 -- which is rule 7's own remedy (its OWN step, not a deferral).  **X-f1 does NOT wait on it**: the preserve-on-re-entry rule is required whether or not the third member survives, and X-f1 pins it with a test | X-am |
| N-178 (X-f1's conversion trace 2026-08-03; REPRODUCED against real requests before it was written down, with a positive control) | **Re-POSTing mark-done on an already-settled TRANSFER moves its money to today -- finding N-146 through a door N-146's fix did not close.**  X-aj1 fixed the re-dating at the SEAM (`status_seam.py:157` preserves an existing instant on re-entry into the settled band), but `transfer_service.update_transfer` carries an explicit `paid_at` kwarg whose branch (`:658-661`) writes both shadows VERBATIM after `_apply_pair_status` has already preserved -- and **both mark-done doors pass `paid_at=db.func.now()` unconditionally** (`routes/transfers/mutations.py:386`, `routes/transactions/mutations.py:594`).  `done -> done` is a legal transition, and neither route gates on status.  **Reproduced**: a transfer settled 7 days earlier, back-dated THROUGH the service so the ledger followed, then re-POSTed -- `paid_at` moved 7.00 days and the ledger gained a reversal at `2026-07-27` plus a fresh posting at `2026-08-03`.  **Positive control: the ORDINARY transaction path does NOT re-date** (it passes no explicit instant, so the seam preserves), which localises the defect to the kwarg rather than to the route pattern | **the whole amount of any re-POSTed settled transfer, moved by however long ago it really settled** -- on the rows this arc records as carrying the largest cash movements.  Not reachable by a normal click (both buttons render only for Projected), so the doors are a stale page, a second tab, a replayed POST or a back-button resubmit -- which is the same reachability class N-146 had and was ruled a live defect on | **OPEN.**  Root fix, not a route guard: the explicit kwarg exists so a caller can say "the user TYPED this day", and neither mark-done door is that caller -- the seam already stamps on first entry and preserves on re-entry, so both passes are redundant and one is harmful.  The revert-side `svc_kwargs["paid_at"] = None` (`routes/transactions/mutations.py:169`) is redundant too: the seam clears on leaving the settled band.  The kwarg SURVIVES for X-f1c's edit door, which is its only legitimate caller | X-f1b0 |
| N-184 (X-f1b's test-integrity review, 2026-08-03; PROVEN by deleting the branch and watching the suite stay green) | **`_normalize_empty_inputs`'s `dump_only` arm has no production instance left, and it is INVISIBLE downstream of `load()`.**  `TransactionUpdateSchema.paid_at` was the only `dump_only` field in the whole validation package and ruling R-EC deleted it with the column, so `app/schemas/validation/_helpers.py:95`'s `and not field.dump_only` guarded a shape nothing declares (that line number is HEAD's; the arm is deleted in the working tree, and `dump_only` now survives in `app/` only as prose in that module's docstring).  Worse for the test: marshmallow discards a `dump_only` key on load either way, so a case routed through `load()` passes identically with the arm deleted -- which the FIRST repair of this row did, and a mutation caught | **`$0.00`** -- the branch is correct and inert.  The cost is a live line of code whose only test could not fail, in the package the arc reads most | **ANSWERED and BUILT, closes when X-f1c1 ships** (2026-08-03).  The settle-day field LOADS -- it is an edit door -- and an AST census finds **no `dump_only` field anywhere in `app/`**, so the arm has had no instance for two steps.  Both the arm and its test go; the surviving case asserts the `allow_none` arm against the helper (falsifiable on its own) plus the new load-bearing rule that an EMPTY settle-day input DROPS rather than clearing, on both update schemas | X-f1c1 |
| N-186 (X-f1c's own build, 2026-08-03; surfaced by ruling R-EJ's guard, not by a review) | **The R1 regression lock for "early settle, then time passes" encodes a FUTURE-DATED settle, not an early one -- so what it locks is not what it claims.**  `test_posting_ledger_loan_reconciliation.py::test_early_settled_payment_keeps_the_parallel_run_exact` freezes today at `2026-02-10` and settles the P3 payment on P3's own period start (`2026-03-13`), then asserts `seed_periods[_P3].start_date > frozen` and calls that an EARLY settle.  It is a settle dated three weeks in its own future; ruling **R-EJ** now refuses it at the write door.  **And the fixture cannot simply be re-dated**: the parallel run compares `posted_loan_balance_map` (selected by `entry_date <= period.end_date`) against a resolver that replays the SCHEDULED payment from the anchor and discards the cash, so the two agree only while a payment's entry lands in the same period its schedule slot does.  A genuinely early settle puts the cash in an EARLIER period than the schedule, which makes the two producers disagree over the intervening periods BY CONSTRUCTION.  Measured: settling on the frozen today, and again on the start of the period containing it, both give `map 97997.25 != resolver 99001.12` at period 2 -- a difference of one payment's principal | **`$0.00` in production** -- the fixture is the only thing that ever produced this shape, and R-EJ now makes it unreachable.  The cost is that a regression lock the 2026-07-02 review's H2 asked for may be pinning a state the app cannot reach, and the real "early settle, then time passes" case may be unlocked | **ANSWERED and CLOSED at X-f1c** (ruling **R-EK**, 2026-08-04).  The question it forbade re-dating until was answered by tracing the two producers rather than by picking a date: they key on DIFFERENT FACTS -- the ledger on the day the money moved, the resolver on the pay-period start -- and the second is a proxy, so the divergence is a real defect and NOT an expected property.  That defect is **N-187**, owned by the new step **X-an**.  The fixture is re-pointed to the early settle the app actually produces and both producers can see (cash 2026-01-30, booked in the period containing it, paying the 2026-03-01 installment: early against the INSTALLMENT), and its non-vacuity is PROVEN by mutation rather than asserted -- dating the split correction at the due date fails it loud at `walk 1003.87 vs posted 1498.88`.  The case the re-point gives up is carried by N-187, not dropped | X-f1c1 |
| N-187 (X-f1c's resume trace 2026-08-04, on N-186's own question; REPRODUCED with a control before it was written down) | **The loan resolver decides "has this payment happened yet?" from the pay period, and the posted ledger from the day the money moved -- so an installment paid before its pay period begins is counted TWICE.**  `is_confirmed_payment_eligible` (`rate_period_engine.py:395`) caps the replay on `period_start <= as_of` and `_build_monthly_override:174` excludes on the same field, while `posting_service._entry_date:256` dates the journal entry from the shadow's `settled_on`.  A pay period is a BUDGETING fact the lender never sees.  **Reproduced** at $100,000 @ 6%, today 2026-02-10, the 2026-04-01 installment paid 2026-02-10: ledger `97,997.25` against resolver `99,001.12` -- exactly one payment's principal, `$1,003.87` -- and the rendered schedule carries `2026-04-01` as BOTH a confirmed row and a projected row, with its last row at `2032-10-01` against the control's `2032-12-01`.  **The control moves ONLY the pay period the payment is filed in** (same amount, same cash day, same installment, same posted split), which is what makes this a producer defect and not a fixture artifact.  It PREDATES X-f1: `git show fac90200:.../posting_service.py` already derived the entry date from `paid_at` | **`$0.00` on today's production data and one ordinary click away.**  Measured read-only on `shekel-prod-db` 2026-08-04, over settled rows that CARRY an instant (`paid_at IS NOT NULL`): **0 of 7** settled loan payments carry a settle day outside their pay period, but **19 of 135** settled Checking rows do (9 early, 10 late) -- and **2 of 5** Money Market rows do, so the habit is not Checking's alone.  The excluded rows are N-181's undated ones (1 Auto Loan, 1 Mortgage, 4 Checking, 2 Money Market), which the X-f1b backfill dates to their own period's `start_date` and so lands INSIDE the period by construction -- it is 0 of 9 on the loan side either way.  Only the EARLY direction bites -- a late settle's divergence window is already past at any render; an early settle's CONTAINS today until its pay period begins.  Blast radius: `loan_figures.payoff_date` is MEASURED equal in both columns (`2032-11-01`); the grid's per-period map is a STRUCTURAL read (`positions()`'s future half folds `memoized_plan`, built from PROJECTED shadows only) and the recurrence end date is an INFERENCE from the payoff date -- all three unaffected, but only the first is a number.  The damage is `LoanState.schedule` and the payoff scenarios sharing `_build_forward_inputs` -- the loan detail page's amortization table, payoff / interest summary and band chart | **OPEN, and RULED (R-EK).**  The cut moves onto `settled_on` for a CONFIRMED payment, so both producers share one definition of "already happened"; a PROJECTED payment keeps the pay-period start, because its cash has not moved.  Its own step rather than a leaf of X-f1: the developer declined mixing a loan-resolver change into a step already carrying a destructive migration and two edit doors.  The step owes the complement move, an explicit decision on the shared RATE key (N-36), and the regression lock X-f1c's re-point gave up | X-an |
| N-189 (X-f1c's app-code review, 2026-08-04; the input REPRODUCED against the schema before it was written down) | **The settle-day correction box was bounded above and not below, and the unbounded end moves money by the mirror of the mechanism the bound end refuses.**  `reject_future_settle_day` refuses a future day because the walk lets it ride on top of every assertion; a day at or BELOW an assertion is ABSORBED into it by `cash_ledger/_walk.py:291-294` and the next line executes `running = anchor.anchor_balance`, discarding the row's delta.  `fields.Date().deserialize("0202-08-04")` returns a real `date`, so a mistyped YEAR reaches `journal_entries.entry_date`.  Worked on a `$1,000` anchor observed three days ago with a `$100` settled expense: the grid reads `$900`, and after the typo `$1,000`, with the `$100` becoming an unexplained plug against Uncategorized at the next re-derive -- the row still reading Paid throughout | **the full amount of any corrected row**, on the account the developer budgets from, by an ordinary typo in a box whose own tooltip invites correction.  `$0.00` on today's data only because the door did not exist until this step | **CLOSED at X-f1c1** (ruling **R-EL**, 2026-08-04, RE-RULED once on evidence).  The bound is `pay_period_service.earliest_recordable_day` -- the SAME floor an anchor's `observed_on` has used since N-133, moved down to a leaf module -- plus `min` on both inputs.  **It lives at the DOOR (`settle_day_for_status`, which all three HTTP doors call), not at the seam**, and the first placement was wrong: at the seam it broke six tests whose scenario is a payment budgeted to a 2026 period whose cash moved in December 2025.  That shape is legitimate -- the absorb is CORRECT for a genuine pre-schedule settle under R-EB, and X-f6's bank import produces it in bulk -- so the floor was never protecting an invariant, only catching a typo.  The ceiling stays at the seam because no caller may record money that has not moved.  Non-vacuity proven by mutation at both levels, and `test_the_service_itself_still_accepts_a_pre_schedule_day` fails if the bound drifts back to the seam | X-f1c1 |
| N-190 (X-f1c3c's app-code review 2026-08-04, then REPRODUCED INDEPENDENTLY with the interleave forced at the reconcile's read; a positive control ran first) | **Every posting-ledger reconcile is an unserialised read-modify-write, and ruling R-EN removed the accident that had been covering the cash half.** `reconcile_account_anchor_corrections` reads `posted_correction_legs`, subtracts it from the walked target and INSERTs the difference; repeated deltas under one key are the DESIGN, so no unique index can tell a racing duplicate from a legitimate adjustment, and there are no rows to `SELECT FOR UPDATE` when nothing is posted yet. The deleted `version_id` UPDATE had serialised it only because it autoflushed and took a row lock before the walk. **The LOAN half never had even that**: `apply_loan_anchor_true_up` appends to an append-only event table and UPDATEs no row, so `sync_loan_postings` has carried the identical race since Commit 16 -- and R-EN cited that append-only contract as its precedent for deleting the cash lock | On an account reconciled at `$4,000.00`, two concurrent true-ups both answer **200**, both assertions survive, the resolver returns one of them, and the linked ledger settles at **`$1,000.00`** against a resolved **`$2,000.00`**. Trial balance still `$0.00` -- the anchor-equity leg carries the mirror-image error -- so nothing fails loudly until the account's next anchor sync, which may be never | **CLOSED at X-f1c3c.** One per-USER advisory lock, taken INSIDE the reconcile at all four entry points (cash per-scenario + all-scenarios, loan per-scenario + all-scenarios) rather than at any door, because the true-up is one of four callers reaching that window. Per user rather than per account on a CORRECTNESS argument, not a convenience one: the reconcile derives each correction's period from the OWNER'S calendar and `journal_entries.pay_period_id` is `ON DELETE CASCADE`, so a concurrent truncate can delete the period it is filing under -- the consistency boundary is the ledger AND the calendar. One key per user also makes deadlock structurally impossible on every request path; the two deploy-wide backfills, the only multi-owner transactions, pre-take every key ascending by user id. The pay-schedule lock MOVED into `app/services/user_write_lock.py` as that one lock, namespace value unchanged so a rolling deploy cannot split it. Sufficiency verified rather than assumed: READ COMMITTED measured as the default on dev, test AND production, with no override anywhere, so the waiter re-reads and reconciles to the true merged target. Three mutants planted (lock removed / lock after the read / loan lock removed), all three killed, and the ordering mutant was killed ONLY by the ordering assertion -- which is why presence and ordering are graded separately | X-f1c3c |
| N-191 (X-f1c3c's residue pass 2026-08-04, found while re-justifying a comment ruling R-EO had falsified; census by AST over `app/`, after a neutral claims audit REFUTED the grep count this row first carried) | **The app's civil day rests on a compose environment variable rather than on a rule, at 78 call sites.** `app/utils/dates.display_today()` is the user's civil day; `date.today()` is the PROCESS-local day, and `app/` calls it at **78 sites** -- `ast.Call` nodes over all 299 `app/**/*.py`, 0 parse errors. **A first version of this row said 113, which was a `grep` line count**: 35 of those occurrences are the name written in docstring or comment PROSE, `display_today`'s own included. A census stated as a census must be counted as one. Production pins `TZ: America/New_York`, so the two clocks are equal there and none of the 78 is a live defect -- which is exactly the problem: their correctness is a property of the deployment, not of the code, and finding N-138 already records that neither clock gate can detect a process-vs-display SPLIT. Two sites found in this pass decide something against the user's CALENDAR and so look like the wrong side of the line: `pay_period_admin.top_up_rolling_window` (`as_of` for "keep N periods ahead of today") and `classify_periods_bulk` (`as_of` for whether a period is HISTORICAL, which is a truncate LOCK decision). `display_today`'s own docstring calls `date.today()` the UTC day, which is false in production, where the process clock is Eastern | `$0.00` today and on any TZ-pinned deployment. The exposure is a one-day boundary error in whether a period is historical (a truncate refusal) or whether the rolling window is short, for the hours the two clocks disagree -- reachable the moment a container runs unpinned, which CI already does on purpose | **OPEN.** Not fixed here: 78 sites is a systemic surface far outside a leaf that deletes two columns, and each needs a per-site ruling on which day it means -- the same shape N-142 has for query-string ids. Recorded with the census rather than the two anecdotes | X-ak |
| N-192 (X-f1c3c's residue pass 2026-08-04) | **A `PostingError` that was referentially unreachable is now held out of reach by code alone, and its comment still cited the deleted constraint.** `reconcile_account_anchor_corrections` raises when an account has anchor corrections to post while its owner has no pay periods. That was impossible under the FK: an assertion carried a NOT NULL `pay_period_id` pointing at one of those periods. Ruling R-EO deleted the column, so what keeps the state out of reach is now three separate code paths -- registration opens a bootstrap period before creating the default account, `truncate_pay_periods` only deletes indices ABOVE the one kept so index 0 survives, and `reset_pay_periods` regenerates before returning | `$0.00`; unreachable through every UI path traced. A 500 on account create or on any true-up for the affected user if it is ever reached | **OPEN, deliberately as a loud failure.** The comment is corrected to state what actually holds it out of reach; the raise STAYS, because this reconcile derives each correction's period from its day and an empty calendar is the one state that could silently mis-file every correction an account has. Worth a schema-level guarantee if one exists that does not re-file a bank fact under a budgeting artifact -- which is what R-EO deleted | X-ak |
| N-193 (X-f1c3c's concurrency review 2026-08-04; the deadlock REPRODUCED against a real PostgreSQL with its own `DeadlockDetected` DETAIL line, the statement ordering CAPTURED from a real loan-payment settle) | **The per-user write lock closes the reconcile race and opens an advisory-vs-ROW-lock cycle, because it is not the FIRST lock its transaction takes.**  A settle takes row locks first -- `update_transfer` UPDATEs the transfer and both shadows, they flush, and the posting sync reaches `lock_user_writes` only afterwards (measured: statements 2-4 against statement 19) -- while `truncate_pay_periods` / `regenerate` / `reset` take the lock first and then bulk-DELETE pay periods, which CASCADEs to `budget.transactions` and locks exactly the rows the settle may hold.  Same user, opposite orders.  **A first version of this step's docstrings and record called deadlock "structurally impossible on every request path"**, an argument that considered only advisory-vs-advisory ordering | PostgreSQL detects it and aborts one transaction: an unhandled 500 on a money route, **no money corrupted** (the loser rolls back atomically).  Needs a settle and a schedule rebuild for one user to overlap -- two browser tabs.  `$0.00` of ledger divergence, against the silent permanent divergence the lock replaces, which is why the lock ships anyway | **OPEN.**  The fix is the invariant stated in `user_write_lock`'s docstring -- *this lock must be the FIRST lock a transaction takes* -- which means acquiring at the write-SERVICE entry (the status seam, `update_transfer`, the delete/restore paths) rather than inside the reconcile.  Deliberately NOT done in this leaf: it is a different change with its own blast radius and needs its own review, and the decompose-at-the-leaf-boundary lesson is this step's own.  What ships here is the correct claim, not the correct lock placement | X-ak |
| N-188 (X-f1c's app-code review, 2026-08-04) | **The W9907 checker still describes the seam as the thing that maintains `paid_at`, in the message it shows the developer.**  `tools/pylint/shekel_checkers/status_bypass.py` names `paid_at` at lines 6, 179, 200 and 221, the last inside the user-visible `shekel-transaction-status-bypass` text -- but ruling **R-EC** deleted that column at X-f1b and the seam maintains `settled_on`.  Introduced by `6a06d4c6`, untouched by X-f1c, found by a neutral review of X-f1c's diff | **`$0.00`** -- the checker's BEHAVIOUR is correct and unaffected; the cost is that the fence for one half of the `status_id` / `settled_on` pair tells a developer to protect a column that no longer exists, which is the same invented-citation class this document treats as a defect | **OPEN.**  Not fixed inside X-f1c: X-aj2 DELETES this checker (ruling R-DP replaces its write door with a read-only attribute), so correcting its prose now writes text with a scheduled deletion date.  It is recorded rather than fixed so the stale text cannot be read as current in the window before then, and N-185 already binds the pair to that step | X-aj2 |
| N-185 (X-f1b's correctness review, 2026-08-03) | **`settled_on` is now exactly as load-bearing as `status_id`, and only one of the two has a fence.**  W9907 (`shekel-transaction-status-bypass`) refuses a `status_id` write outside the seam; nothing refuses a `settled_on` write.  The invariant binds them -- a row is settled iff it carries a day, and the seam writes both in one statement -- so a second writer of EITHER breaks it, which is precisely what N-183 was.  The only thing keeping `routes/transactions/mutations.py`'s `setattr(txn, field, value)` loop (`:340` at HEAD, `:253` in the working tree after the module split) from becoming that second writer is the field's absence from `TransactionUpdateSchema`, **and plan step X-f1c adds it** | **`$0.00` today** and a re-run of N-183 the moment the edit door lands | **OPEN, and X-f1c1 met its condition.**  Do NOT grow W9907 an arm: the developer's standing ruling is that fences become structurally unnecessary, and the scheduled mechanism already exists -- **X-aj2 replaces W9907's write door with a read-only attribute**.  `settled_on` joins `status_id` under THAT mechanism, so the pair gets one structural answer rather than two checkers.  Until X-aj2, the edit doors route through the seam: X-f1c1 names the pair `_SEAM_OWNED_FIELDS` and EXCLUDES both from the PATCH handler's `setattr` loop, and X-f1c2's `Transfer.settled_on` has no setter, so assignment raises.  A test dates a PROJECTED row through the PATCH and asserts it stays undated -- removing `settled_on` from that set kills it | X-aj2 |
| N-180 (X-f1b's design review, 2026-08-03) | **A de-duplication rationale in `balance_at/_loan_interest.py` was falsified by ruling R-DH and the X-f1 conversion then edited an INVENTED CITATION into it.**  The paragraph argued that `confirmed_shadows_through` is a UTC-visibility subset while the tax `as_of` is a display date, so an evening settle is counted twice.  `payment_visible_on` became display-tz at R-DH (b), and since X-f1 the day is STORED in the user's zone and converted by nothing -- so the premise is doubly false.  The conversion rewrote the citation to `to_utc_civil_date(settled_on)`, **a function that has never existed in `app/`** | **`$0.00`** -- the code was never wrong (the de-dup runs off `_due_slot` over the walk, not off this claim); only the reason written beside it was.  The cost is that the stated rationale for a de-duplication design is false, and a future reader would rely on it | **OPEN.**  The falsified paragraph is REPLACED in place at X-f1b and the invented citation deleted, but **whether the two sets can still differ for any other reason is UNVERIFIED**, so the de-dup stays and this row owns the question.  X-e re-reads this package | X-e |
| N-181 (X-f1b's app-code review, 2026-08-03; MEASURED on the production clone) | **The backfill moves one figure, and `verify_balance_baseline.py` structurally cannot see it because it is not a balance.**  `Transaction.days_paid_before_due` gated on "was an instant recorded" and now gates on "is the row settled".  The 8 legacy settled rows whose `paid_at` was NULL take their pay period's `start_date` -- exactly what every reader already derived, so no BALANCE moves -- but they now enter `spending_analysis.payment_timeliness_from_txns` for the first time, dated by a day nothing observed.  All 8 carry a `due_date` and `spending_report_service` applies no transfer-shadow filter, so they reach the metric: the four expense legs report **8 days early, on time, on time, 1 day late** | **a soft metric on `/analytics`**, not a balance.  The claim "no figure moves" was FALSE as stated and is corrected in the migration docstring, the model docstring and this document | **OPEN.**  Narrowing the backfill to `paid_at IS NOT NULL` was REJECTED -- it leaves 8 settled rows undated, which the balance walk now REFUSES, trading a soft metric for a 500 on the grid.  The resolution is the edit door -- **and it is the TRANSFER one, not the transaction one** (ruling **R-EF**, 2026-08-03).  Measured on the production database: the eight rows are transactions **1457, 1458, 1823, 1824, 1826, 1827, 2161, 2162**, ALL EIGHT transfer shadows (four pairs, transfers 54 / 154 / 155 / 322), and `routes/transactions/forms.get_full_edit` redirects a shadow to the TRANSFER popover -- so the transaction door corrects **0 of 8** and the plan's own claim that X-f1c is this row's door was false as scoped | X-f1c2 |
| N-139 (X-ae's build 2026-08-02; REFRAMED the same day when two adversarial reviews refuted its first statement) | **Nothing prevents a submitted digit string being parsed laxly again, and a checker on the method NAME does not prevent it -- which is what the first version of this row proposed.**  X-ae removed every Unicode-wide digit predicate from `app/` and `scripts/` (AST: exactly ONE call site remains, `digit_strings.py:91`, the implementation of the replacement), converted the URL converter and 73 schema declarations, and the reviews then showed the proposed gate would still report clean over the very defect it was written for: **a future author writing a bare `try: int(raw) / except ValueError` passes a `isdigit`/`isdecimal`/`isnumeric` matcher and reintroduces the many-spellings defect** -- and this step's own record proves that form insufficient, because it is what the 2026-08-01 ruling specified and what measurement rejected.  `re.fullmatch(r"\\d+", "١٠٦")` matches for the same reason.  **The signal is not a method name; it is Unicode-wide digit ACCEPTANCE, and it has at least four spellings** | `$0.00` today and no reachable crash: every surface X-ae covers is fixed and measured.  The exposure is the NEXT id parse written, which on this arc's history is a matter of when rather than whether -- four `isdigit()` sites accumulated with nothing watching, and the reviews found two more surfaces this step's own census had missed | **OPEN, and the INSTRUMENT is undecided, which is why this is a step and not a line.**  A checker is the only available shape -- the receiver is `str`, a builtin nobody can give a narrower type, so there is no `ReconciledThrough` to write and the developer's *structural-over-detector* ruling has nothing to prefer; `anchor_settle_partition.md` 14.5 is the precedent (a type and a checker fence COMPLEMENTARY holes).  But X-ag's trace must first answer what the checker MATCHES, given that the method name is measurably the wrong signal.  **A second draft of this row argued that step 3 proved a checker would be blind at a bare-local site; that claim was REFUTED by step 3's own review and withdrawn in 14.1**, and restating it was the stale-citation class this arc keeps paying for | X-ag |

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
   **IT IS BLIND ABOVE THE SEAM, and that needed a SECOND instrument**
   (`tests/manual/verify_savings_producers.py`, added 2026-07-28 at X-t). It reads `balance_at`
   directly, so for a step whose whole surface is a producer package, a serializer or a template
   it is byte-identical whatever the step did -- a free pass that reads as proof. The second one
   dumps every per-account projection field, the debt summary, the tracks section, all four narrow
   producers and the serialized `data-chart` payload, NORMALIZED across an intended shape change so
   a deliberate diff cannot hide an accidental one, and it was shown firing on a planted one-cent
   defect before X-t trusted it. **Ask of every harness: can it SEE the code under test?**
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

* **A mutation-planting reviewer and an editor cannot share a worktree** (X-f1c, 2026-08-03).  The
  adversarial test-integrity review works by writing a defect into `app/`, running the suite, and
  restoring from its own backup.  Editing the same tree meanwhile is not merely racy -- it is
  SILENTLY LOSSY in both directions: a restore reverted four lines of this step's own settle-day
  mapping in `_shadow_mutations.py` (caught only because the Stop hook flagged the now-unused import
  it left behind), and the OTHER review found a live `# MUTANT` still sitting in
  `transfer_service.py` -- a revert that leaves the settled effect posted, i.e. the balance keeping
  money the user just said was never spent -- which `git add -A` would have committed.  The rule:
  while a mutation review is running, edit DOCS only; before any commit, `grep -rn "MUTANT" app/
  tests/` and stage with `git add -p`, never `-A`.
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
* **A REPAIR for "a control that cannot fail" is itself a control, and needs the same mutation.**
  X-f1b's whole ship gate was N-182 -- two rules whose pins had gone vacuous -- and the repair pass
  shipped THREE more of the same shape before a review caught them: the `@validates` hook's pin
  (which the seam's own pre-check made unreachable), the dump-only normalization case (re-pointed
  once and STILL unfalsifiable, because the branch is invisible downstream of `load()`), and the
  preserve-on-notes-edit case (which asserted `is not None` where its name promised "that day").  The
  author of a fix for an untestable claim is in exactly the frame of mind that writes another one.
  **Plant the mutation the fix is FOR, not a mutation nearby**: deleting the decorator, deleting the
  branch, moving the day.
* **When a conversion is mechanical, the DIRECTION of the type change has a mirror, and the mirror
  is silent.**  X-f1 turned settle instants into civil days; the same pass turned five ASSERTION
  instants into days, where a `date` reaching a `timestamptz` becomes midnight UTC -- the PREVIOUS
  Eastern evening, i.e. the previous business day.  Four of the five failed loudly by accident (a
  helper called `.tzinfo` on them); nothing structural caught them.  Both directions now refuse:
  the column refuses a `datetime`, and the assertion builders refuse a `date`.
* **An ORACLE that states a different rule than the engine lets both be wrong together while the
  sweep reports clean.**  The anchor-reconciliation oracle ordered "the latest assertion" on
  `(created_at, id)` while `resolve_anchor` orders on `(observed_on, created_at, id)` -- a
  divergence dating from plan step 2, invisible for three steps because no fixture separated the two
  keys.  Found only by re-deriving the oracle instead of renaming it.  **When a rule moves, re-read
  the oracle's restatement of it, not just its vocabulary.**
* **Ask what a test's failure would have COST before deleting it, and write the answer down.**
  X-f1b deleted 24 tests whose code path is provably unreachable, and the first draft of that
  record implied nothing was lost.  Three things were: an executed downgrade, one exclusion rule,
  and the only comparison of an INDEPENDENT implementation against the go-forward builder.  The
  deletion was still right; the silence about it was not.  A deletion that cannot name its cost has
  not been measured.
* **Review a FROZEN tree.** X-s ran two adversarial reviews in parallel and applied the first's
  fixes while the second was still reading; the second reported the artifact changing underneath it
  and its gate results graded a tree that no longer existed. Both gates and both real-data harnesses
  had to be re-run at the end anyway, so the concurrency bought nothing and cost a review's
  confidence. Either freeze the tree for the review or expect every result to be provisional.
* **A correction can carry the defect it corrects.** X-s2's docstring wrongly claimed an invariant
  was stated in one place; the fix naming the other three places invented TWO of their function
  names. Fixing a false citation is exactly when the next false citation gets written, because the
  author is reasoning about structure rather than reading it. Walk the AST for the enclosing
  function; do not type the name you remember.
* **COUNT THE CALL GRAPH, NOT THE CALL SITES.** X-t2 measured "how many places state the
  no-baseline rule" by searching for the predicate's spelling, hoisted what it found, and shipped a
  docstring saying the package had exactly two seam doors. It had three: the third reaches the seam
  through ANOTHER SERVICE (`compute_property_equity` -> `home_equity_service` -> `loan_figures`),
  where no spelling of the predicate appears at all, and it raised a `ValueError` on a live page
  for a borrower with a Property securing a mortgage. The same search also missed a copy written as
  a truthiness test (`if not (current_period and scenario)`) in the very package it was counting.
  A grep answers "who writes this line"; the question was "who reaches that raise".
* **A NEW FIXTURE IS A NEW CONTROL, AND IT CAN BE BORN DEAD.** The Property fixture written to
  catch the door above set `mortgage.secured_by_account_id`, which is not a field -- SQLAlchemy
  takes the assignment as a plain Python attribute and says nothing, so the loan was never secured
  and the test passed while exercising none of the code it named. Two of X-t's controls had to be
  run against the DEFECT (guard removed) before they could be believed. Assert the fixture's own
  precondition, or run the control against the failure it claims to catch.
* **A STATIC GATE MUST BE EXERCISED, NOT READ.** X-t3's band gate said in its docstring that a name
  in a COMMENT could not satisfy it. Three of its five arms scanned raw source, so
  `var ASSET_BANDS = ["asset", "retirement", "investment"  // "other" dropped` passed the arm whose
  whole purpose is that case. The hole was found by calling the helper on a planted string --
  something the gate's own author could have done in a minute and did not.
* **A RULING ID IS A CITATION, SO THE RULING SHIPS FIRST.** X-u's code cited `R-BS` six times
  across five files for the four forks the developer had just ruled, while Section 4 still ended at
  R-BR -- both adversarial reviewers found it independently and neither could resolve it. The arc
  had no practice here and both available precedents were wrong in different directions: X-s's code
  commit cited R-BD one commit before the ledger recorded it, and X-t's code commits cited no
  ruling at all, so the forks it was built on were unfindable from the code. The fix is ordering,
  not wording: **the rulings land in their own `docs(balance):` commit BEFORE the code**, and the
  tick-and-close commit follows the code as rule 2 requires. A citation that resolves nowhere is
  worse than a stale line number, because the reader looking it up is the one about to re-decide
  the thing it ruled.
* **A GATE'S PATTERN MUST BE EXERCISED AGAINST THE ARTIFACT IT GRADES, not against a synthetic
  twin.** X-u made Section 6's "**The ledger stands at N rows**" sentence a gate arm, because it had
  drifted to 38 against a 40-row table. The first draft required `rows**`; the live document writes
  `rows.**`, with the period inside the emphasis. So the pattern matched the ledger NOWHERE, the arm
  read that as "no count is claimed", and a planted 38-against-41 passed clean -- while the
  synthetic control, written without the period, passed too and proved nothing. Found only by
  planting the defect in the REAL file. This is X-t3's lesson (`A STATIC GATE MUST BE EXERCISED,
  NOT READ`) one step later and one axis over: there the arm read the wrong THING, here it read the
  right thing in the wrong SPELLING. Every gate over prose now carries an arm asserting its pattern
  still MATCHES the live document, or it can go vacuous in silence.
* **AN AST CENSUS IS A GREP WITH BETTER MANNERS UNLESS IT FOLLOWS THE DATA.** Finding N-112 said
  its own count was "a floor until an AST pass replaces the grep", and named the exact site the
  grep could not see. X-v built the AST pass, found a 13th site the grep had missed -- **and still
  missed the site N-112 named**, because that predicate arrives as a FUNCTION PARAMETER
  (`_recent_settled_expenses_monthly(..., scenario)`) rather than as an attribute or a local alias.
  A second pass that walks parameters found it plus a whole family one tier down. The instrument
  that replaces a discredited one inherits its burden of proof: ask what SHAPE of the thing you are
  counting your new instrument cannot represent, and answer it before quoting the number.
* **A CENSUS AND A GATE CAN BE BLIND THE SAME WAY, AND THEN THEY CONFIRM EACH OTHER.** X-v's two
  instruments were an AST pass that followed `BalanceContext` and a route sweep that failed only on
  5xx. A surface that resolves the baseline ITSELF and answers with a plausible 200 is invisible to
  both -- so the balance sheet reporting `in_balance = True` over an unreadable ledger passed the
  census (it names no context) and passed the sweep (it returns 200), and the step shipped a
  docstring saying no caller decides this any more. Two instruments are not independent evidence
  when they share a blind spot; ask what a defect would have to look like to pass BOTH.
* **A COUNT IN A DOCSTRING IS A CLAIM, AND THIS ARC KEEPS WRITING IT WRONG.** X-v's own reviews
  found "exactly ONE caller reads the nullable" (four did, one of them named 130 lines below in the
  same file), "five callers scope a query" (seventeen), "seventeen guards deleted" (eighteen), and
  one enumeration copied into five files so a wrong count needed five edits. Where a claim is a
  number, either recount it from the AST at writing time or cite the ONE place that carries it --
  never restate it.
* **WIDENING AN INSTRUMENT IS A SHAPE CHANGE, AND IT NEEDS THE SAME NORMALIZATION THE CODE DOES.**
  X-w's reviews found the classic blind spot -- the narrow producers carry a field nothing reads and
  NEITHER real-data harness dumped it -- and ruling R-CM added it. The first attempt dumped `null`
  on the pre-X-w tree, because the projection had no such field there, so every account's map read
  as a diff and the cross-tree comparison the previous commit had just established was destroyed.
  Normalizing the new key to the SEAM's own map on the old tree turned the same edit into the
  strongest evidence the step has: the diff now proves the projection's map equals the seam's map
  account by account, which is the step's central claim, instead of leaving it inferred from the
  figures downstream. A harness that grows a key must answer what the OLD tree puts there before it
  is trusted, exactly as a producer that grows a field must.
* **A GUARD WRITTEN AGAINST THE WRONG FAILURE MODE CAN STILL BE A GOOD GUARD, AND THE REASON BESIDE
  IT IS WHAT ROTS.** X-w added two template guards on the premise that Jinja renders a missing
  attribute as an empty string, so a renamed producer field would ship a blank figure with the suite
  green. Half true: a bare `{{ value }}` renders empty, but the `money` macro's first statement is
  `{% if value < 0 %}` and `Undefined.__lt__` raises -- so those reads 500 and the pre-existing
  status assertion already caught them. One of the two guards was ALSO unable to fire on anything
  (a `count(...) >= 3` satisfiable three times over from outside the region it named). The guards
  are right and now discriminate; what had to be corrected in four places is why they exist. Ask of
  a new guard: what does the failure it names actually LOOK like, and does this arm see that and not
  something else?
* **`hasattr` ON A DATACLASS IS NOT A TEST, AND NEITHER IS `is not None` AFTER `isinstance`.**
  X-aa's new contract control looped `assert hasattr(traj, field)` over four field names to prove a
  producer "fills every field". A frozen dataclass with four required fields gives all four to
  anything that passes the `isinstance` on the line above, so the loop was structurally
  unfalsifiable -- and its docstring promised it would catch "a future edit that omits a field",
  which is precisely the mutant that survived it. **Seven mutants, five survivors**, including a
  `required_monthly` inflated tenfold and an inverted pace. Comparing `dataclasses.astuple` per
  branch killed all seven. The general form: when a test asserts a SHAPE the type system already
  guarantees, it asserts nothing; assert the VALUES, and run the mutants before believing the test.
  This is Section 7.3 with a new disguise -- the control looked like a control, passed, and was
  committed inside the step whose entire thesis is that a guard which cannot fail is not a guard.
* **A LIST RETURNED FOR ITS COUNT MUST HAVE ITS COUNT ASSERTED.** X-z3's Jinja gate arm was built
  on a helper whose docstring said "a list, not a set: the count is part of what the arm asserts,
  so a site silently disappearing is a failure" -- and the arm asserted non-empty, subset and
  membership, never the length. Deleting the guard on the entire debt-summary footer leaves one
  liability comparison and passes all three. Both adversarial reviewers found it independently, by
  mutating the real template rather than reading the assertions. The step's own committed control
  renamed BOTH sites to a NON-band, which only the subset arm catches -- so the control was
  strictly weaker than the failure it advertised. When a helper's return TYPE is justified by a
  property, assert that property, and pick mutants that survive the arm rather than ones that
  obviously do not.
* **A BASELINE IS ONLY A BASELINE AGAINST THE DATABASE IT WAS TAKEN FROM.** X-z's render harness
  authenticated and hit `/savings`, and the app's rolling window CREATED a pay period in both dev
  databases (timestamps `02:19:52` and `02:20:20` UTC, found by reading `created_at`). Every stored
  harness blob taken before that then diffed against every blob taken after, showing a new period
  key in every account's dense map -- which reads exactly like a producer regression. It was
  diagnosed only because the extra "data point" was chased instead of reported. Re-capture the
  reference from a `git worktree` at the last shipped commit on the CURRENT database, and treat any
  instrument that WRITES as invalidating every baseline it straddles.
* **AN INSTRUMENT THAT CANNOT AUTHENTICATE REPORTS NO DIFFERENCES, LOUDLY AND WRONGLY.** Two
  successive drafts of that same harness rendered the LOGIN page for all five URLs -- first from
  wrong paths (404, `follow_redirects` swallowing it), then from a hand-built session Flask-Login's
  strong protection discarded -- and both reported five identical files across both trees. "No diff"
  from an instrument that graded nothing is the most dangerous result an instrument can give,
  because it is indistinguishable from success. The assertions that caught it (`status == 200`, and
  `"<title>Login" not in body`) are cheap, belong in every render harness, and were added only after
  the second failure.
* **A REFUSAL IS ONLY AS GOOD AS THE REPAIR IT NAMES, AND NOBODY HAD PRESSED THE BUTTON.** Plan
  step X-x replaced a dozen fabricated figures with one repair card, on ruling R-CY's premise that
  every reachable form of the state has a one-click repair. Its adversarial review pressed the
  button: for an interior hole `/pay-periods/generate` REFUSES every date inside the hole and
  silently creates nothing past the far end, because the overlap guard bounds on `max(end_date)`
  over all periods. So the step turned "four pages show wrong numbers" into "every page refuses and
  the button does not work" -- strictly worse for that user. **The step's own gate could not catch
  it: it asserted the card NAMES a repair.** When a guard's answer is "go here and fix it", assert
  that going there fixes it, on the data that produced the refusal.
* **A CENSUS THAT IS NOT COMMITTED IS AN UNCITED CLAIM.** X-x quoted "96 branches in 49 files" and
  "about 50 distinct answers" as the measurement that re-scoped the whole step, from an AST script
  living in a scratch directory. Neither reviewer could re-derive either number, and Section 7.6
  forbids exactly that. A count is evidence only while its instrument is re-runnable; ship the
  instrument in the same commit as the number, or write the number as an estimate.
* **CONVERTING A SURFACE TO "RAISE" BLINDS EVERY TEST WHOSE FIXTURE CANNOT REACH IT.** X-x made a
  dozen producers refuse, and ~80 tests began grading the repair card instead of the page they
  name -- passing, because their assertions were satisfiable from `base.html` or were negative.
  Among them one XSS control (satisfied by the flash toast), three cross-user isolation controls
  (satisfied by a nav link) and nineteen CSP scans. The author fixed the four that went RED and
  never looked for the ones that stayed GREEN, which is the whole trap: a converted surface turns
  failing tests into a to-do list and passing tests into a silence. Instrument the event and fail
  any test that touches it off-allowlist -- reading the diff cannot find these.
* **A SUITE THAT PASSES ON 353 DAYS A YEAR IS NOT A GATE, AND THE DAY IT FAILS IT WILL LOOK LIKE
  YOUR BUG.** X-x1's first full run came back with seven failures. One was the step's own; the other
  six were red at `HEAD` and at all 25 preceding commits, while CI had been green three days
  earlier. `test_cross_page_balance_equality` builds CALENDAR-MONTH periods on the wall clock and
  hand-computes each `expected_balance` for a day inside the anchor period, so on the last day of
  any month `date.today() == anchor.end_date` and ruling R-G correctly rolls the whole remaining
  plan into the next period. It fires about 12 days a year and blocks the merge gate on each.
  **The sibling directory already knew**: `tests/test_services/conftest.py` freezes to
  `2026-03-20`, and its docstring says "mid-period 5". The integration suite never got one.
  **And the first fix was wrong**: relaxing R-G's floor from `as_of + 1` to `as_of` made all 22 go
  green and moved the real Checking balance TODAY from `$2,824.26` to `$2,978.28` -- counting
  income merely expected today as money in hand, against the figure this arc verified to the cent
  against the persisted ledger. A green suite is not evidence that the change producing it was
  correct; measure the change on real data before believing the tests it satisfied.
* **THE STATE A GUARD DEFENDS AND THE STATE THE APP IS IN CAN BE OPPOSITES.** X-x's census found 96
  branches answering "this user has no pay periods" -- a state no owner can reach, because
  registration writes one and neither delete path can remove the last -- while the state that IS
  reachable, "no period contains today", moved `/savings` net worth by `$3,228.55` and put an anchor
  CACHE column on screen as a current balance. The defensive code was not merely useless; it was
  read as coverage for the neighbouring question nobody had asked. Before writing a guard's answer,
  measure whether its condition can occur AND whether the condition beside it can -- a census that
  counts branches without asking which are reachable will always over-report the safe one.
* **AN INSTRUMENT THAT SILENTLY GRADES ONE SUBJECT FIVE TIMES REPORTS FIVE RESULTS.** X-x's first
  onboarding probe registered five users, logged in as each, and printed a clean five-row table of
  accept/refuse outcomes. Every request had run as the FIRST user: the later logins did not take,
  and the table was one user's schedule growing under five different labels. It was caught only by
  querying the database directly and finding 53 periods on one user and 1 on the other four. This is
  the "cannot authenticate" lesson with the failure INVERTED -- there, the instrument reached nothing
  and reported no differences; here it reached the wrong subject and reported differences that were
  real, consistent, and about someone else. Assert the identity the result is attributed to, not
  just that a result came back.
* **SCORE THE RULE YOU SHIPPED, NOT THE RULE YOU DESIGNED.** R-DH picked the day partition by
  scoring four candidate rules on real data, and the winner's numbers went into the ruling, the
  commit message and this document. A developer amendment made LATER THE SAME DAY, during the build,
  changed the rule -- and the table was never re-run. It stayed as the amended rule's evidence for
  four hours and one commit, advertising a net plug of `-$940.06` for code that produces
  `-$2,997.48` (N-133). Nothing was hidden and no test could have caught it: the measurement was
  honest when taken, and the rule moved out from under it. **Any change to a rule after it was
  scored re-opens the score.** The cheap form is to keep the measuring script and re-run it as the
  last act of the build, not the first act of the design -- this arc already keeps
  `verify_balance_baseline.py` for figures and had no equivalent for RULES.
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
