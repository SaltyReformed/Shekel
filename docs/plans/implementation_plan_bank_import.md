# The statement importer: the bank says when money moved

**The arc that gives the app FACTS instead of guesses about when money moved and what a statement
showed.** It was `balance:X-f6`, a single sequenced follow-on to the cash cutover, until 2026-08-13:
measurement against the developer's own bank exports showed the cutover depends on this arc's output
rather than the other way round, and the balance README's Section 5.0 had already named a document
for it. The rules this document is held to are `conventions.md`, its open findings are rows in
`ledger.md`, what "done" means is `verification.md`, and the ORDER is `steps.md`.

## Context

**What it is for, in one sentence: a bank statement is the only source of two facts the app
currently guesses** -- the day money actually moved, and which lines a statement showed.

**Both guesses are measured, on the developer's own YTD exports** (SECU checking, OFX/QFX/QBO plus
six CSVs, 2026-01-02.. 2026-08-03, 342 lines and 342 distinct `FITID`s; Capital One card,
OFX/QFX/QBO/QIF/CSV, 105 lines carrying BOTH `Transaction Date` and `Posted Date` plus `LEDGERBAL`).
The parser was validated first: it reproduces the bank's own `2026_ytd_daily_balances.csv` on
**112 of 112 days, 0 mismatches**, and every figure below rests on that.

| what was measured | result |
|---|---|
| app rows whose recorded `settled_on` is the day the bank posted them | **33 of 110** matched movements (30%) |
| assertions equal to the bank's closing balance for their own day | **17 of 55** |
| the app's book-vs-bank gross, against the bank's actual closing balances | `$4,513.89`, against the `$15,413.71` the app's own instrument reports |
| matched movements that are individual PURCHASES rather than transaction rows | **58 of 110** |

The last row is the arc's shape in one number: **the bank speaks in purchases**, so an envelope row
(`Groceries $505.91`) has no bank counterpart and the matcher works at two grains.

**This arc does NOT replace manual entry, and that is the developer's own bound** (2026-08-13).
Marking a row paid, ticking the reconcile panel and typing a balance all stay; the import CONFIRMS
and CORRECTS. The two facts are separable by design -- `settled_on` is the user's record that money
moved, the clearing link is the statement's record that it was seen -- so an owner who never imports
anything loses nothing but the corrections.

## The rulings

| ruling | date | what was ruled |
|---|---|---|
| **R-FP** | 2026-08-13 (developer) | **A statement importer is a SOURCE ADAPTER over one normalized line shape**; matching, review and fact-writing are source-independent, so the file path ships first and an automated source is additive. `FITID` is the idempotency key -- 342 of 342 distinct in the developer's own OFX -- so re-importing a file cannot duplicate. **A match is a PROPOSAL, never a silent apply**: it is reviewed before commit, every corrected `settled_on` goes through `system.audit_log`, and unmatched lines on BOTH sides are shown rather than guessed at. Rejected for the automated source: OFX Direct Connect, which needs no third party but stores the credentials that grant FULL account access, against SimpleFIN / Plaid, which store a revocable READ-ONLY token -- the strict option is the revocable one even though it adds a vendor. Also recorded, because it is what makes the file path first: the developer's exports already cover both accounts, so the matcher can be GRADED before an adapter exists |
| **R-FS** | 2026-08-16 (developer) | **A MATCH has three shapes, because the grain mismatch runs in BOTH directions.** Measured on the developer's own accounts 2026-08-16: the bank shows 156 individual card-swipe lines where the app holds 34 envelope rows, and shows ONE payroll deposit where the app holds three rows (`$2,473.38` + `$100.00` + `$39.54`). So a match is (1) one line to one row, (2) a GROUP -- N app rows summing to one line, or N lines summing to one row -- with the bank's day and the clearing link written to every member, or (3) a bank line the app has no row for BECOMING a purchase against an envelope the owner picks. The third is what makes the app's records reach the bank's grain permanently, and `balance:X-f3b` is what makes it work: a purchase carrying a posting day is already a cash movement of its own whose envelope books only the remainder. Rejected: one-to-one only, which explains 119 of 227 lines and leaves half the account with no clearing fact at all |
| **R-FT** | 2026-08-16 (developer) | **An import PERSISTS a normalized line table**, not nothing and not an id ledger. `budget.bank_statement_lines` under a `budget.statement_imports` batch, with accepted matches recorded separately and PROPOSALS never stored. Uniqueness on the line's identity makes re-import idempotent STRUCTURALLY rather than by the importer remembering to look -- and the same table is the fact `balance:X-f3a-2` needs (a statement walked line by line, so a line it did not show is NOT CLEARED) and the provenance `balance:X-f3c` needs to re-open a recorded difference. One table serves three steps; storing nothing would have each invent its own |
| **R-FU** | 2026-08-16 (developer, on measurement) | **The source is SECU's CSV with running balances, and a line's identity is POSITIONAL rather than the bank's own id.** QBO and QFX are the same file as the OFX -- 342 identical `STMTTRN` blocks plus two Intuit routing tags -- so the real choice was CSV or OFX. The OFX truncates 326 of 361 descriptions to 32 characters and carries no per-line balance; the CSV carries the merchant, the bank's category and a running balance, and its description STARTS WITH the OFX name on 306 of 306 shared lines. What the CSV lacks is `FITID`, and **R-FP's idempotency key is superseded on measurement**: the positional key `(account, posted_on, amount, sequence within that group)` reproduced the `FITID` key EXACTLY across two exports twelve days apart -- 0 keys in one export only, 0 disagreeing ids, over 342 shared lines -- so identity is one rule for every adapter and an external id is stored as corroboration. The ordinal is what makes it total: two real charges may share a day and an amount. **AMENDED 2026-08-16 by the step's own adversarial review, because the measurement is blind on the key's only novel component**: 0 groups needed an ordinal in either export, so `sequence_in_group` was 0 on all 361 lines and the comparison tested `(day, amount)` against `FITID` -- never the ordinal, which is the one term that is derived rather than observed and the one that can be unstable. The honest statement of the ruling is therefore: identity is positional, and the positional key is proven equivalent to the bank's own id ONLY on data where no group exceeded one member. What holds the key total in the meantime is the description compare in the write door, not the ordinal -- which is finding **N-303**'s subject |
| **R-FV** | 2026-08-17 (developer) | **A match is stored as IDENTITY, and the CLEARING LINK is not written from one.** The two answer different questions: a match says *which real movement this app row is*, and `transactions.reconciled_by_id` (ruling **R-FL**) says *which declared balance already contains it*. The second is DERIVABLE from the first once a statement carries the line -- anything matched to a line on a statement covering day D is inside any balance declared for D or later -- while the first is not derivable from the second, so the app stores the fact and derives the rest. Two measurements make it concrete rather than tidy: `reconciled_by_id` names an `account_anchor_history` row, a balance the owner typed, and a bank line names none; and R-FQ's theorem bounds a link to assertions SHARING the date rule's day, of which production has three on Checking, so a bank import could not choose among them anyway. What an accepted match writes is the bank's posted day, which makes the date rule's INPUT an observation instead of a guess. **It does not make that rule correct, and a first draft of this ruling said it did** -- R-FL rejects *"sharpening the date rule with the bank's posted day"* by name, because a civil day still cannot tell a mid-day reading from a late-posting item, and 9 of 55 Checking assertions are measured to be exactly that shape. The honest claim is narrower: the same rule now runs on a true day, which is strictly better and is still a guess until `X-f3c`. **The settle doors RELEASE any prior link as they move the day**, which this ruling inherits from `X-f3a-1` rather than justifies: the bank contradicts the DAY, not the STATEMENT, and an assertion observed weeks later still contains the row. The release is safe (`_recorded_anchor_id` falls back rather than raising, so a stale link is inert) and it is a LOSS -- a row the owner had ticked goes CLEARED back to UNKNOWN. `0` of 1,012 production rows carry a link today, so nothing is destroyed now; **N-307** owns the class. **Also ruled, on the same principle:** an unbalanced group is REFUSED and its difference NAMED, never apportioned (which needs a rule about which member is wrong -- a decision about a paycheck, not a matcher's) and never absorbed by a tolerance (which would silence finding **N-299**, the `$0.05`-`$0.06` gap on 6 of 16 payroll deposits that this matcher is the first instrument able to see); and a match MAY settle a still-Projected row, because a statement is evidence that money moved and 11 rows inside the developer's own statement span had never been marked as having happened -- which the GROUP path must reach too, and a first implementation excluded undated rows from it outright, so a split payroll deposit with one unsettled member was unproposable. **Rejected:** writing the link to whatever assertion the date rule already picks, which stores a copy of a derivation; and recording the BANK's own daily closing balances as assertions, which is the best data available -- only 17 of 55 hand assertions equal the bank's closing balance -- but would move Checking's OPENING assertion from 2026-03-27 back to 2026-01-02 and redefine the account's opening equity, which is the cutover's decision. That is recorded as **N-304**, owned by `balance:X-f3c` |

**The match predicate is RULED (R-FS) and the measurement that forced its shape is worth carrying.**
A naive exact-amount matcher plateaus: 36 of 227 bank lines at a same-day tolerance, 119 at
plus-or-minus fourteen days, and it never reaches further. The 108 it never explains are four
structural classes rather than noise -- 156 card-swipe lines an envelope aggregates, 9 payroll
deposits the app splits into two or three rows each, 9 card payments against 20 payback rows, and a
handful of lines the app models not at all (dividends `$0.66`, a `-$4.00` foundation donation, ATM
cash).
**Two of those classes carry a defect of their own, recorded as findings rather than fixed here**:
the app's projected paycheck runs `$0.05`-`$0.06` BELOW the actual deposit on 6 of 9 deposits, and
the payback rows do not reconcile to what was actually paid to the card.

## The steps

- [ ] **X-f6** `feat(import): the bank says when money moved` -- the DECOMPOSED parent of the
      statement importer (**R-FP**), carrying **N-173**.
      **It is no longer the sequenced follow-on the balance arc's ruling R-EB made it**: what the
      cash cutover needed was never the import surface but the CLEARING FACTS, so its first leaf
      moved AHEAD of that cutover on the developer's ruling 2026-08-13. It ticks with X-f6b.
  - [ ] **X-f6a** the DECOMPOSED parent of the importer's CORE (**R-FP**), carrying **N-173**, split
        into three leaves 2026-08-16. Its one-step form was specified to RULE the match predicate as
        its first act; the measurement came back many-to-many in BOTH directions (**R-FS**), which
        is three separable kinds of write rather than one. It ticks with X-f6a-3.
    - [x] **X-f6a-1** `40a490c3` a statement is RECORDED: four tables, one normalized line shape
          behind a source adapter, the SECU CSV reader, and the import page. Balance-neutral on a
          production clone (9 accounts, 434 grid cells, 6,076 daily points byte-identical with 306
          real lines). **What a LATER leaf must obey**: identity is positional and its ordinal is
          UNMEASURED (see R-FU's amendment), the restatement guard compares only the description,
          and a refusal has no repair door -- **N-301**, **N-302**, **N-303**.
    - [x] **X-f6a-2** `267cb75e` a bank line IS these rows: `budget.statement_matches` and its
          members hold the correspondence over an exclusive arc, the proposer offers it, and an
          accepted match writes the bank's posted day onto every member -- settling a Projected row
          and correcting a wrongly-dated one. Closed **N-173**; opened **N-304**-**N-306**.
          **What a LATER step must obey**: no clearing link is written from a match (**R-FV**), an
          unbalanced group is refused rather than apportioned, and a group's day is its LATEST.

    - [ ] **X-f6a-4** `feat(import): an import can be undone` -- the repair door finding **N-302**
          says a refusal owes. A `StatementLineConflict` is TERMINAL today: no door in `app/`
          deletes an import, a line or a recorded account identity, so once one fires for an
          account, importing into that account is dead until someone runs SQL -- while the message
          tells the user it "needs a human before anything overwrites it", a promise the app cannot
          keep. **The refusal POLICY is right and stays** (refusing beats overwriting an
          observation); what is missing is the remediation beside it. Two shapes, and the second is
          now the cheaper one because `X-f6a-2` built the screen: a delete-this-import door,
          audited, or the review screen resolving a divergence rather than the write door
          hard-stopping. Balance-neutral either way -- deleting an import deletes what the bank
          SAID, and the days an accepted match wrote are the app's own record and stay.

    - [ ] **X-f6a-3** `feat(import): a bank line becomes a purchase` -- ruling **R-FS**'s third
          shape. **MOVES MONEY.** The 156 card-swipe lines an envelope aggregates have no app row to
          match, so the import offers to CREATE one: a `transaction_entry` against an envelope the
          owner picks, dated the bank's posted day. `balance:X-f3b` already made such a purchase a
          cash movement of its own whose envelope books only the remainder, so this changes no rule
          -- it supplies the records the rule was built for.
          **It is ranked before the cutover deliberately**: the cutover classifies what the records
          do not explain, and this is the step that stops 156 lines being unexplainable.
  - [ ] **X-f6b** `feat(import): the statement arrives without being fetched` -- the automated
        SOURCE ADAPTER (**R-FP**), additive to X-f6a's core and touching no correctness path.
        SimpleFIN is the recommended target, on the security ground R-FP states.
        **The identity rule does not re-open for it** (R-FU): a positional key serves a JSON feed
        exactly as it serves a CSV, and SimpleFIN's own id joins as corroboration.
        **It needs infrastructure this app does not have**: no scheduler, no task queue and no CLI
        entry point exist in `app/` or `scripts/` today, so the trace decides where a scheduled
        fetch runs before an adapter is written.
