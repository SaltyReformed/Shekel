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

**The manual-entry bound MOVED on 2026-08-24** (it was "this arc does NOT replace manual entry",
2026-08-13): a standing rule the owner stated is manual entry's consent given ONCE, so a
rule-covered NEW swipe files itself (**R-GH**), while any row the owner made by hand still changes
only under a tick. `settled_on` stays the record that money moved and the clearing link the record
that it was seen; an owner who states no rules keeps exactly the old workflow.

## The rulings

**This arc's rulings are in `rulings.md`, rows whose `arc` is `bank_import`.** They moved there at
`balance:X-ao-1` with the balance arc's. `R-FW` was REPAIRED by that lift: an unescaped `|` inside a
backticked string split it into four cells of a three-column table, so Markdown truncated the rule
at the pipe and a header-anchored reader would have dropped the row -- unreported for nine days (it
landed at `0d6f8c09`, 2026-08-18) because nothing parsed this table.

**The match predicate is RULED (`R-FS`) and the measurement that forced its shape is worth
carrying.** A naive exact-amount matcher plateaus: 36 of 227 bank lines at a same-day tolerance, 119
at plus-or-minus fourteen days, and it never reaches further. The 108 it never explains are four
structural classes rather than noise: 156 card-swipe lines an envelope aggregates, 9 payroll
deposits the app splits into two or three rows each, 9 card payments against 20 payback rows, and a
handful of lines the app models not at all (dividends `$0.66`, a `-$4.00` foundation donation, ATM
cash). **Two of those classes carry a defect of their own**, and both are rows in `ledger.md` rather
than a second copy here (rule 16): **N-239** and **N-323**.

## The steps

- [ ] **X-f6** `feat(import): the bank says when money moved` -- the DECOMPOSED parent of the
      statement importer (**R-FP**), carrying **N-173**.
      **It is no longer the sequenced follow-on ruling R-EB made it** (developer, 2026-08-13): what
      the cash cutover needed was the CLEARING FACTS, not the import surface. When it ticks is
      `steps.md`'s to say and is not restated here (conventions rule 16).
  - [ ] **X-f6c** `feat(import): a merchant answer names a template` -- a NEW-ENVELOPE answer
        creates a recurring TEMPLATE once and names that template thereafter, so the container a
        merchant rule files into carries an identity ACROSS pay periods instead of a NAME. Finding
        **N-328**, ruled by the developer 2026-08-20 on the argument **R-GA** already makes: a
        budget line either has a period-independent identity or it does not, so the answer set is
        really {existing template, NEW template, never} and *a new envelope* is
        *a new template, first time*. X-f6a-4's convergence made the fragmentation stop; it did not
        give the row an identity, so the reuse it performs is a string compare on a name the owner
        can rename. **Verified before the ruling rather than assumed**: a template carrying no
        recurrence rule generates nothing (`recurrence_engine._generate.resolve_generation_plan`
        returns `None` for a rule-less template), so this adds no unwanted future rows.
        **It waits on `balance:X-au-e`** and the reason is a constraint rather than a preference: a
        row that HAS a template has a derivation to read (`ck_transactions_amount_ownership`), and
        X-au-e is the step that rebuilds what a templated row's amount is. Building against today's
        shape would mean building it twice.
  - [ ] **X-f6g** `refactor(reconcile): a statement-covered account reconciles from statements` --
        the reconcile panel stops offering an account whose statements the owner imports
        (**R-GD(d)**). **It owes a specification pass before it is picked up, and that pass IS the
        first half of the step**: the panel is the balance arc's surface, and what "statement
        coverage" means for a day no import spans is undecided. Nothing here starts by deleting it.
  - [ ] **X-f6b** `feat(import): the statement arrives without being fetched` -- the automated
        SOURCE ADAPTER (**R-FP**), RE-SCOPED 2026-08-24: the daily fetch lands on standing rules
        (**R-GH**), never a review queue, and its per-sync balance is the corroboration source the
        evidence ladder lost when SECU dropped running balances (it carries **N-338**'s ruling
        question too). The identity rule does not re-open (R-FU): a positional key serves a JSON
        feed as it serves a CSV, and SimpleFIN's own id joins as corroboration. The scheduler
        decision is ruled toward host cron through a CLI door, matching the no-scheduler,
        no-exposed-ports deployment posture. A CARD-statement adapter (Capital One -- its exports
        are already measured in R-FP's context) is worth minting once the card ledger exists
        (`credit_card:CC1`); no step for it exists yet, deliberately.

**The X-ga..X-gh leaves are the standing-consent REDESIGN the developer approved 2026-08-24**
(**R-GH**..**R-GL**); the argument and worked examples are
`docs/audits/bank_import_redesign/README.md`. The retirement leaf -- deleting what the exception
queue orphans -- is MINTED when `X-gf` ships. **The X-gb..X-ge-1 span is ARCHIVED under rule 5**
(condensed 2026-08-26, cut to one line each 2026-08-27): the as-built records are the four
`historical/bank_import_x_*` files, every finding it did not close is a live `ledger.md` row, and
the constraints it leaves a LATER step are on that step's own entry.

- [x] **X-gb** `ec346c46` -- the delete door (**R-GM**) and the P-6 repair. Closed **N-344**; opened
      **N-348**.
- [x] **X-gc** `0452eef3` -- three surfaces stopped stating what is false (**R-GN**, **R-GO**,
      **R-GP**). **N-345** is half open, at `operator`.
- [x] **X-gd** `d1910c95` -- a merchant answer became a standing RULE: its IDENTITY and its STORE.
- [x] **X-gd-1** `395b14f7` -- a merchant is a ROW (**R-GR**).
- [x] **X-gd-2** `154cfcec` -- the rule STORE (**R-GS**, **R-GT**). Closed **N-353**; opened
      **N-358**.
- [x] **X-ge** `6d3e3ca1` -- the auto-apply door (**R-GH**, **R-GU**), MOVING MONEY with no press.
      Opens **N-359**.
- [x] **X-ge-1** `6d3e3ca1` -- each matcher tier publishes the refusals it used to SWALLOW, so a
      pass reports three verdicts rather than two.
- [ ] **X-gf** `feat(import): the review is an exception queue` -- **the DECOMPOSED parent**, split
      2026-08-27 on measurement: the review body renders **554,122 bytes** against the developer's
      own dev data, of which the merchant control is 225,474 and the accepted-matches panel
      212,576 -- **79% of the page is two registers of decisions already made** -- while the work a
      routine import leaves is **two creatable lines** at 15,842 bytes, starting at byte 227,166.
      224 `<form>` elements, 64 selects, 96 checkboxes. It ticks with the last of its three leaves.
- [x] **X-gf-1** `a4db019f` -- an inflow the books hold no row for becomes an uncategorized INCOME
      row (**R-GW**), **MOVING MONEY**: two correct refusals pointed at each other and left every
      unmatched DEPOSIT with no act at all -- 8 lines, `$58.87`, on his own data.
      **The 9 parked Capital One lines still have only a group match**: `$7,412.94` of card payments
      against `$5,819.99` of unmatched `CC Payback` rows, so `$1,592.95` of card spending the books
      never recorded can be matched by nothing, which `credit_card:CC3b` dissolves (**N-337**).
- [x] **X-gf-2** `64cfca05` -- the register is not the queue (**R-GX**, **R-GY**): 29 answered
      merchants and 221 accepted acts left the review screen for their own surface, taking the
      review body from 578,523 bytes to 149,103 and `review_set` from 146 SQL statements to 7, a
      cost the IMPORT path paid too. Closed **N-358** -- three by-id reads of RENDERED user data
      became composite-FK relationships, so the account travels in the JOIN and `accepted_groups`
      fell from 139 statements to 7 -- and **N-349**. Opened **bank_import:N-371**, **N-372**.
- [ ] **X-gf-3** `feat(import): the review is an exception queue` -- **the DECOMPOSED parent**,
      split 2026-08-27 on the developer's ruling into the rule verdict both readers share and the
      queue's own shape. It ticks with the last of its two leaves.
- [x] **X-gf-3a** `44f1cc7b` -- one rule VERDICT and one screen SENTENCE, derived where the decision
      is and read by ruling **R-GH**'s door and by the review screen. Closed **N-359** and
      **N-371**. **What a LATER step must obey**: the withholding sentence is composed in
      `_verdict.ruled` and printed unbranched, because the partition behind it is the service's; and
      a parked line names the register only where a different answer would open the create door,
      which was 0 of 9 on the developer's own data.
- [ ] **X-gf-3b** `feat(import): the review is an exception queue` -- what remains once the register
      and the workbench leave: contested lines, group residuals and every proposal touching a
      hand-made row, as ONE list of unexplained bank lines grouped by the DECISION each poses rather
      than the three cards that partition them by MECHANISM -- outflow-that-may-be-recorded,
      outflow-that-is-barred, inflow-that-may-be-recorded -- when the question all three pose is the
      same: *is this money my books already hold, or is it new?* (**bank_import:R-HB**).
      **The hand-build match form moves to a surface of its own** (**bank_import:R-HC**), on the
      argument **R-GX** made about the register: it is not an exception, it is the TOOL three
      exceptions send the owner to, and each of them links to it. Closes **N-374**.
      **Measured through the real route on the developer's own data 2026-08-27**, after `X-gf-3a`:
      the review body renders **146,907 bytes** of which that form's two pick lists are
      **89,247, or 61%** -- 22,830 for 27 bank lines and 66,417 for 67 rows -- against 1 unanswered
      merchant, 2 creatable lines, 16 deposits and 9 parked payments of actual work.
      *The 149,103 figure `R-GX` states is not contradicted*: it was measured at `X-gf-2`, before
      `X-gf-1`'s deposit card and this step's per-line sentence were both in the page and before the
      developer worked lines off it.
- [ ] **X-gg** `docs(plans): the envelope-semantics design loop` -- **R-GK**'s owed loop, run WITH
      the developer: filling, closure on coverage, carry-forward and the grid's row identity (whose
      same-name double-render the review measured); it mints the build steps rather than building.
      **It waits on `credit_card:CC3c`** (developer ruling 2026-08-24): envelope filling is
      two-source -- debit swipes from SECU lines, card swipes from card-side charges -- and the
      card-tender entry shape the loop must design over is what CC3c rewrites. Designing over the
      payback shape the card arc deletes is the mistake that withdrew `balance:X-au-i`.
- [ ] **X-gh** `feat(balance): the bank's balance asserts the anchor` -- **R-GL**, designed against
      the post-cutover assertion (after `balance:X-f3c`), with the residue surfaced as an exception
      and the hand true-up demoted to a correction. **The residue is not hypothetical**: `X-gb`
      reversed `$7,769.58` of double-booked spending and the balance did not move, because 60 hand
      true-ups were absorbing it -- which is what this step stops happening silently.
