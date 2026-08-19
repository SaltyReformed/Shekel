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
| **R-FV** | 2026-08-17 (developer) | **A match is stored as IDENTITY, and the CLEARING LINK is not written from one.** The two answer different questions: a match says *which real movement this app row is*, and `transactions.reconciled_by_id` (ruling **R-FL**) says *which declared balance already contains it*. The second is DERIVABLE from the first once a statement carries the line -- anything matched to a line on a statement covering day D is inside any balance declared for D or later -- while the first is not derivable from the second, so the app stores the fact and derives the rest. Two measurements make it concrete rather than tidy: `reconciled_by_id` names an `account_anchor_history` row, a balance the owner typed, and a bank line names none; and R-FQ's theorem bounds a link to assertions SHARING the date rule's day, of which production has three on Checking, so a bank import could not choose among them anyway. What an accepted match writes is the bank's posted day, which makes the date rule's INPUT an observation instead of a guess. **It does not make that rule correct, and a first draft of this ruling said it did** -- R-FL rejects *"sharpening the date rule with the bank's posted day"* by name, because a civil day still cannot tell a mid-day reading from a late-posting item, and 9 of 55 Checking assertions are measured to be exactly that shape. The honest claim is narrower: the same rule now runs on a true day, which is strictly better and is still a guess until `X-f3c`. **The settle doors RELEASE any prior link as they move the day**, which this ruling inherits from `X-f3a-1` rather than justifies: the bank contradicts the DAY, not the STATEMENT, and an assertion observed weeks later still contains the row. The release is safe (`_recorded_anchor_id` falls back rather than raising, so a stale link is inert) and it is a LOSS -- a row the owner had ticked goes CLEARED back to UNKNOWN. `0` of 1,012 production rows carry a link today, so nothing is destroyed now; **N-307** owns the class. **Also ruled, on the same principle:** an unbalanced group is REFUSED and its difference NAMED, never apportioned (which needs a rule about which member is wrong -- a decision about a paycheck, not a matcher's) and never absorbed by a tolerance (which would silence finding **N-239**, the `$0.05`-`$0.06` gap on 6 of 16 payroll deposits that this matcher is the first instrument to see from OUTSIDE the app's own arithmetic -- recorded as N-312 and merged into N-239 on 2026-08-18, the row whose mechanism it measures); and a match MAY settle a still-Projected row, because a statement is evidence that money moved and 11 rows inside the developer's own statement span had never been marked as having happened -- which the GROUP path must reach too, and a first implementation excluded undated rows from it outright, so a split payroll deposit with one unsettled member was unproposable. **Rejected:** writing the link to whatever assertion the date rule already picks, which stores a copy of a derivation; and recording the BANK's own daily closing balances as assertions, which is the best data available -- only 17 of 55 hand assertions equal the bank's closing balance -- but would move Checking's OPENING assertion from 2026-03-27 back to 2026-01-02 and redefine the account's opening equity, which is the cutover's decision. That is recorded as **N-304**, owned by `balance:X-f3c` |
| **R-FW** | 2026-08-18 (developer, on measurement) | **A match corrects a purchase's PURCHASE day as well as its posting day, and it corrects only the day the bank CONTRADICTS.** A purchase carries two clocks -- `purchased_on`, the day it was made, and `settled_on`, the day the bank took the money -- and R-FV ruled only the second. **What forced the first is that the step below it was measured to be building a duplicate machine**: of the 121 lines the matcher leaves unexplained on the developer's own 2026-08-16 statement against a 2026-08-18 production clone, **14 worth `$1,028.66` are an exact amount at the same merchant as an app purchase the screen ALSO lists as unexplained** -- six of them typed in one bookkeeping session on 2026-04-29 for swipes the bank posted on 04-24 and 04-27. They are not offered because `entry_service` refuses a posting day earlier than the purchase day, correctly; so `X-f6a-3`'s create-a-purchase door would have invited the owner to record every one of them a SECOND time. The app HAS these rows, and what is wrong is the day. **The day it writes is the one the bank STATED, not the one it cleared on**, which is a fact this app was throwing away: SECU states the swipe day inside a card line's description (`DATE 08-13`) on **182 of 361** lines, 1-4 days before posting, and the adapter copied `posted_on` into `transaction_on` on all 361 -- so the column could not distinguish an observation from a restatement. That column is now NULLABLE and the NULL means *this source states none*, because the alternative source states none at all (the OFX's `DTUSER` equals `DTPOSTED` on 359 of 361 and is one day LATER on the other two). **Rejected: taking the bank's day unconditionally**, which is the symmetric-sounding rule and is measurably worse -- it moves 27 of the 44 purchases in today's proposals, 18 of them onto a CLEARING day because the source states no swipe day at all, replacing 27 dates the owner got right to fix 3 they got wrong. Correcting only what is refuted moves exactly those 3, and each is an impossibility rather than a disagreement. **Also rejected: relaxing `ck_transaction_entries_settled_not_before_purchase`** -- money cannot leave an account before it is spent, and the constraint is what makes the correction necessary rather than optional. **AMENDED the same day by three adversarial reviews, and two of the amendments are the ruling's own consequences rather than implementation slips.** (1) Making the pairing LEGAL removed the only bound an undated purchase had: `DAY_WINDOW` is measured from `settled_on` and such a row has none, so the app offered to re-date a purchase by **59 days** on an exact-amount coincidence, overwriting the one fact that would have exposed the mis-pairing. A purchase is never truly undated -- it is now anchored on the day it was MADE, which cut the worst re-dating to 6 days and refused 2 of 17. (2) What REFUTES a purchase day is the EARLIEST line of a match, not the latest: money cannot leave before it is spent, so a purchase explained by lines posted 06-01 and 06-10 was made on or before 06-01, and testing against 06-10 left an impossibility standing that `update_entry` could not catch either. (3) The day is read from the bank's DESCRIPTION cell rather than the `Description | Memo` text the row stores, so a user's own memo cannot state it |
| **R-FX** | 2026-08-19 (developer) | **A bank line the app has no row for becomes a PURCHASE, never a bare transaction, and the app may CREATE the budget line that holds it.** R-FS's third shape said "against an envelope the owner picks", and measurement made that insufficient: of the 91 unmatched outflows on the developer's own statement, 10 fall in a period whose every envelope closed at a stored figure, and several merchants (a hardware store, a parks fee, two subscriptions) have no envelope in any period. A purchase is the app's record of one payment and a transaction is a budget line that reserves, so recording a payment as a budget line would collapse two facts a purchase keeps apart and leave the next statement line for that merchant nowhere to go -- which is why the created container is always an ENVELOPE, born Projected budgeting `0.00` and closed from its own entries. **Also ruled, and it is the money half:** a settled row admits a NEW purchase exactly when its recorded figure IS its purchases AND that purchase states the day the bank took it. On a stored-figure row the gross cannot rise and `settled_cash_leg` subtracts money it never held (measured `-163.95` to **`+203.67`**, an expense row publishing an inflow, while the anchor true-up moved `$0.00`); on ANY settled row an undated purchase moves the row's own leg on the day the row CLOSED, a past day with no evidence (measured `-50.00` to `-80.00`). `EntryDetails` therefore carries `settled_on`, which the create door had deliberately excluded on a premise true only of the hand-typed form. **It does NOT answer the carry-forward double count**: every carried-forward source settles on the admitted basis, and finding **N-249** already records that with the developer's 2026-08-12 remedy (reconcile the rollover) which explicitly REJECTED refusing late purchases -- so this ruling applies a decision already taken rather than making a new one. **Rejected:** refusing the add and sending the owner through revert / add / re-close, which rewrites the envelope's settle day and its statement link to record the bank's own evidence |
| **R-FY** | 2026-08-19 (developer, on measurement) | **A row the app has never marked as paid is bounded by its OWN PAY PERIOD, widened by `DAY_WINDOW` at each end -- not by the statement's covered span, and not by nothing.** Such a row carries no observation of when its money moved, and that was read as *no bound at all*: any bank line sharing its amount could claim it, from any date whatever. Measured on a production clone: the account holds 610 unsettled transactions, 600 of them projections budgeted past the statement's last day, up to 92 sharing one amount (24 identical `$1,910.95` mortgage transfers spanning 2026-08-27 to 2028-07-27). It never fired on the first import only because a settled row won every amount race; remove the settled partner from an amount group and **44 of the statement's own lines** pair with a projection budgeted 48 to 148 days later, the worst a 2026-04-01 line taking a row budgeted 2026-08-27. That is the SECOND import's ordinary state, and settling next month's projection against this month's line files real money under the wrong paycheck. **Rejected: the statement's covered span**, which finding N-312 proposed and which X-f6a-3b's own measurement pointed at -- it is a property of how much history the owner happened to export rather than of the money, so the same two rows match or do not depending on the file, and on this data it still admits a 2026-04-17 line claiming a row budgeted 2026-08-13, 118 days out. The pay period is what the app itself asserts about when that money moves, and it is the WHOLE of what it asserts, so both ends travel. **Also rejected: the earlier reasoning that a bill must stay unbounded** because bounding it would refuse the arm that settles a row nobody has marked as having happened -- re-measured, every one of the 51 rows that arm settles is a PURCHASE, already bounded by the day it was made, and 0 proposals name an unsettled transaction on either the first pass or the second. **Also ruled: the clause that hides an already-matched envelope from the create door's destination list STAYS WHOLE**, and finding N-317 STAYS OPEN beside it. N-317 argues it is wider than the money needs because a purchase born carrying its posting day moves its parent's cash leg by zero; measured, that holds for an envelope settled on a purchases basis (`-88.01` unchanged) and for a projected one already holding purchases (`0.00` unchanged), and fails for a projected envelope holding none -- `settles_from_entries` flips it from its own figure onto `sum(entries)` while the new purchase's posting day is subtracted again, measured `-111.02` to **`0.00`** on `Gas` 2232. **X-f6a-3c-1 read that third shape as this clause's own and closed the finding as a misdiagnosis; an adversarial review of the step measured it unreachable HERE** -- a match SETTLES the envelope it names, and a zero-entry settle records a stored figure, which the money clause beside it already refuses. So every destination this clause uniquely removes is a safe one, N-317 is right, and what this ruling decides is only that a `$0.00` benefit does not buy a change to a money guard. **What must NOT be narrowed with it** is `_accept._reject_parent_and_its_own_purchase`: stamping an EXISTING unposted purchase whose parent is matched does move that parent's leg (`-265.69` to `-247.05`), a different act. **And the bound is on what the matcher OFFERS, never on what the accept door WRITES**: the hand-build form exists so an owner may assert a grouping the proposer would not guess, and a date bound there would refuse the one act R-FP reserves to them |

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
        into three leaves 2026-08-16 and into FOUR on 2026-08-18. Its one-step form was specified to
        RULE the match predicate as its first act; the measurement came back many-to-many in BOTH
        directions (**R-FS**), which is three separable kinds of write rather than one -- and the
        third leaf split again when its own premise was measured false (**R-FW**).
        **When it ticks is `steps.md`'s to say and is not restated here** -- the sentence that used
        to name a leaf disagreed with the order table's own answer, which is what rule 16 exists to
        stop.
    - The importer's CORE shipped 2026-08-16 - 2026-08-19 in four steps -- `40a490c3`, `267cb75e`,
      `140f1f24` + `feb2ea91`, `1f214712` -- and is CONDENSED under `conventions.md` rule 5 into
      `historical/bank_import_x_f6a_core_as_built_2026-08-19.md`, which names each id, its commit
      and what it closed. Every finding they left open is live in `ledger.md`; every ruling they
      established is in the table above.

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

    - [ ] **X-f6a-3c** the DECOMPOSED parent of the review screen's THROUGHPUT, split in two
          2026-08-19 (developer). What the proposer may OFFER and how many acts one request performs
          are different subjects: the first is a matching rule over money already in the app, the
          second is a batch write door, and grading both in one diff is the review surface that let
          a defect ship at X-f6a-3a. **When it ticks is `steps.md`'s to say.**

    - [x] **X-f6a-3c-1** `c25edd96` every candidate row carries the WINDOW the app believes its
          money moved in (**R-FY**) -- a settle day, a purchase day, or a bill's whole pay period --
          so no row the matcher OFFERS is unbounded and the global undated pool is DELETED rather
          than reported. Closed **N-312**, **N-315**, **N-316**; opened **N-322**; RESTORED
          **N-317**. **What a LATER leaf must obey**: a new candidate KIND owes a window, and the
          pair test and the group bucket apply the SAME slack or they disagree silently.

    - [ ] **X-f6a-3c-2** `feat(import): review a statement in one pass` -- the reviewed MULTI-SELECT
          finding **N-306** says the volume owes.
          **READ `balance:X-au-j` (#6) FIRST, or measure before building.** It is legal to start
          today, which is why `steps.md` says `NOW`, and it inherits a per-ACT cost that X-f6a-3b
          made worse: `create_purchase_from_line` calls `accept_match`, which re-derives through
          `candidates_for` -- measured at **3.67 s** per accept on the developer's own clone, of
          which 3.6 s is that one call (finding **N-309**), and the existing-envelope arm pays a
          second full scan through `destinations_for`. **124 proposals looped through the
          single-line door is 7.4 minutes of derivation alone, and all 215 acts is 12.9**, so this
          leaf takes a BATCH at ONE derivation rather than looping.
          **It must not become *accept everything*** (**R-FP**): the shape is tick-the-ones-you-
          agree-with, over BOTH acts the screen now offers -- accepting a proposal and recording a
          line as a purchase. The developer's own first import produces 124 proposals and 91
          creatable lines, and each is currently its own round trip through a money door.
          **The failure policy is RULED** (developer, 2026-08-19): a refused item leaves nothing
          behind and the rest still land, each refusal quoted with its own sentence -- so one
          `$0.05` payroll gap (finding **N-239**) cannot block 123 good corrections. Each item is
          its own SAVEPOINT; the request is still the transaction, and a `PostingError` still fails
          the whole thing loud. **What the batch may assume, and what it may not**: `propose`
          partitions its lines and its rows, so two ticked proposals cannot name the same subject --
          but a once-derived PRICE is only safe across items because
          `_reject_parent_and_its_own_purchase` refuses the one interaction that moves a row another
          item names, and that guard reads the database, so each item must flush before the next is
          validated.

    - [ ] **X-f6a-3d** `feat(import): the import remembers where a merchant goes` -- a destination
          stated ONCE PER MERCHANT and pre-filled thereafter, still reviewed before it commits.
          **The same shape ruling R-FP already gives the account identity**: recorded by the user's
          own choice and then CHECKED, never inferred. Measured on the developer's own statement:
          the 74 unexplained swipes are **18 distinct merchants** and the whole 361-line export is
          59, so the first import teaches it and the second is nearly automatic.
          **What a merchant KEY is, is the decision it opens with.** X-f6a-3b reads it as the
          parenthesised trailing token of the description, present on 361 of 361 SECU CSV lines and
          used only as a form DEFAULT; a key a rule matches on is a stronger claim and may want the
          adapter to record it as a column of its own.
  - [ ] **X-f6b** `feat(import): the statement arrives without being fetched` -- the automated
        SOURCE ADAPTER (**R-FP**), additive to X-f6a's core and touching no correctness path.
        SimpleFIN is the recommended target, on the security ground R-FP states.
        **The identity rule does not re-open for it** (R-FU): a positional key serves a JSON feed
        exactly as it serves a CSV, and SimpleFIN's own id joins as corroboration.
        **It needs infrastructure this app does not have**: no scheduler, no task queue and no CLI
        entry point exist in `app/` or `scripts/` today, so the trace decides where a scheduled
        fetch runs before an adapter is written.
