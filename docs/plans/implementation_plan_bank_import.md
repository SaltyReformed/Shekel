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

**The ruling this arc still owes** is the match predicate itself, and `X-f6a` states it: a naive
exact-amount prototype matched 110 of 178 movements, and the 68 it missed each need a stated answer.

## The steps

- [ ] **X-f6** `feat(import): the bank says when money moved` -- the DECOMPOSED parent of the
      statement importer (**R-FP**), carrying **N-173**.
      **It is no longer the sequenced follow-on the balance arc's ruling R-EB made it**: what the
      cash cutover needed was never the import surface but the CLEARING FACTS, so its first leaf
      moved AHEAD of that cutover on the developer's ruling 2026-08-13. It ticks with X-f6b.
  - [ ] **X-f6a** `feat(import): a statement is matched, reviewed, and recorded` -- ruling **R-FP**,
        closing **N-173**. **MOVES MONEY** (it corrects `settled_on`), and its first act is the
        matcher's measurement rather than code. One normalized line shape
        `(posted_on, transaction_on, amount, fitid, description)` behind a SOURCE ADAPTER, matched
        against transaction rows AND purchases, then a review screen that commits nothing until it
        is accepted. **It must RULE the match predicate**: the prototype's 68 misses are envelope
        aggregates, CC paybacks and inter-account transfers, and each owes a stated answer rather
        than a silent drop. It writes two facts, both corrections and both audited: the bank's
        posted day onto `settled_on`, and the clearing link `balance:X-f3a` defines.
        **It needs a one-time account mapping** -- the export's `ACCTID` (`40943820`) to the Shekel
        account -- and that mapping is a fact, not a guess.
  - [ ] **X-f6b** `feat(import): the statement arrives without being fetched` -- the automated
        SOURCE ADAPTER (**R-FP**), additive to X-f6a's core and touching no correctness path.
        SimpleFIN is the recommended target, on the security ground R-FP states.
        **It needs infrastructure this app does not have**: no scheduler, no task queue and no CLI
        entry point exist in `app/` or `scripts/` today, so the trace decides where a scheduled
        fetch runs before an adapter is written.
