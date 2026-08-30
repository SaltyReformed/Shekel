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
`balance:X-ao-1` with the balance arc's, which also REPAIRED `R-FW` (`0d6f8c09`). How that lift came
about, and what it caught, is that registry's own header to tell.

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
        (`credit_card:CC1a`..`CC1c`); no step for it exists yet, deliberately.

**The X-ga..X-gh leaves are the standing-consent REDESIGN the developer approved 2026-08-24**
(**R-GH**..**R-GL**; argument: `docs/audits/bank_import_redesign/README.md`), and
**the shipped X-gb..X-gf-3b-2 span is ARCHIVED under rule 5** to the five
`historical/bank_import_x_*` files: every finding it did not close is a live `ledger.md` row, and
what it leaves a LATER step is on that step's own entry.

- [x] **X-gb** `ec346c46` -- the delete door (**R-GM**), P-6. Closed **N-344**; opened **N-348**.
- [x] **X-gc** `0452eef3` -- three surfaces stopped stating what is false (**R-GN**..**R-GP**).
- [x] **X-gd** `d1910c95` -- a merchant answer became a standing RULE: its identity and its store.
- [x] **X-gd-1** `395b14f7` -- a merchant is a ROW (**R-GR**).
- [x] **X-gd-2** `154cfcec` -- the rule STORE (**R-GS**, **R-GT**); **N-353** shut, **N-358** open.
- [x] **X-ge** `6d3e3ca1` -- the auto-apply door (**R-GH**, **R-GU**); MONEY, no press. **N-359**.
- [x] **X-ge-1** `6d3e3ca1` -- each tier publishes the refusals it used to swallow.
- [x] **X-gf** `ff744d79` -- the review is an exception queue; minted **X-gi**.
- [x] **X-gf-1** `a4db019f` -- an unmatched inflow becomes income (`bank_import:R-GW`).
- [x] **X-gf-2** `64cfca05` -- the register is not the queue (**R-GX**, **R-GY**). Shut **N-358**,
      **N-349**.
- [x] **X-gf-3** `ff744d79` -- decomposed parent of the queue proper; ticked with its two.
- [x] **X-gf-3a** `44f1cc7b` -- one rule VERDICT, one SENTENCE. Shut **N-359**, **N-371**.
- [x] **X-gf-3b** `ff744d79` -- decomposed parent of the queue's second leaf; ticked with two.
- [x] **X-gf-3b-1** `d2248fe6` -- the workbench is not the queue (**R-HC**). Closed **N-374**.
- [x] **X-gf-3b-2** `ff744d79` -- one list by the decision (**R-HB**, **R-HD**). **N-380** shut,
      **N-381** open.
- [ ] **X-gj** `feat(import): reconcile is one page on four verbs` -- the DECOMPOSED parent of the
      Reconcile rebuild the developer LOCKED at Loop A round 4 on 2026-08-29, on rulings
      `bank_import:R-HP`..`R-HX`, stated in `rulings.md`. The assessment, the research, the four
      rounds and the locked direction are `docs/design/bank_import_audit.md`. `X-gi` follows it.
  - [ ] **X-gj-1** `feat(import): the Reconcile page` -- the DECOMPOSED parent of the page, split
        three ways 2026-08-29 on the two boundaries this package draws: the SERVICES boundary, and
        **R-GX**'s -- a line to explain is `_reads`', an applied act is `_accepted_view`'s.
        **It posts only to doors that exist** (`apply_reviewed`, `release_match`, `state_rules`) and
        adds no money door. Each leaf ships a WHOLE page: none renders a tab or control a later one
        completes, which **R-HW** forbids. The balance arc's reconcile PANEL keeps its name, so the
        module is `statement_match/_reconcile.py` beside `routes/accounts/reconcile.py`.
    - [ ] **X-gj-1a** `feat(import): the reconcile view model` -- the service turning a pass into
          CARDS: one per line with its verb (**R-HP**), what suggested it, the sentence's PARTS,
          which verbs are OPEN and why a shut one is (**R-HW**), the settled act's card one tense
          over, the tab counts, the chips (**R-HQ**), and `bank_agreement`'s HEADLINE DAY -- the
          latest COMPARED day the bank's record can PRICE, not `span.last_day`.
    - [ ] **X-gj-1b** `feat(import): the page, and the lines still to explain` -- the whole page
          over that model and the three tabs whose cards are BANK LINES: To explain, Transfers,
          Skipped. The route pair, hero, chips, legend, tab bar, the card, its opened four-tab panel
          (**R-HR**, **R-HW**) with Find and Match and an always-for-this-merchant checkbox, and the
          footer's sweeps and Apply (**R-FZ(c)**, **R-HD**). An unmatched inflow is not pre-filled
          (**R-HX**), so Choose opens the panel this same leaf builds. Both themes and viewports
          through the visual loop; its route test posts what the card and footer emit, and its
          ownership 404 is paired with a case asserting the URL still routes.
    - [ ] **X-gj-1c** `feat(import): what has already been decided` -- the two tabs whose cards are
          ACTS: Explained and Filed by rules, newest first with an act that has stopped holding on
          top, Undo carrying **R-GY**'s confirm where it destroys a row, and the Explained chip's
          link; it retires the register as a page (**R-HU**), in both themes and viewports through
          the visual loop, with its own route test and ownership 404 pairing.
  - [ ] **X-gj-2** `feat(import): a rule answers an inflow` -- **R-HT(a)**: a deposit signature
        files as an income category, a merchant credit as a NEGATIVE purchase in that merchant's
        envelope; it CREATES, so it auto-applies at import under **R-GH** with `X-ge`'s receipt and
        undo. **MOVES MONEY, OWN PR.** Measured 2026-08-29 by running `review_set` on account 1:
        nine of its sixteen unmatched deposits dissolve (five dividends, three refunds, one answer).
  - [ ] **X-gj-3** `feat(import): a rule names a row set` -- **R-HT(b)**: a payroll signature
        pre-builds the group match (the period's payroll rows, the residue onto the named row per
        **R-GD** or its own row per **R-FN**) as a solid suggestion, with the
        *always, this signature is this period's payroll rows* checkbox the MATCH panel renders; it
        MODIFIES rows, so it applies only on the owner's OK. **MOVES MONEY, OWN PR.** Interim for
        **N-239**.
  - [ ] **X-gj-4** `feat(import): skip, and the holding state` -- a SKIPPED line's disposition is
        recorded and undoable, which LIGHTS the SKIP verb the panel renders shut and fills the
        Skipped tab `X-gj-1b` draws for lines a standing *never a purchase* answer already disposes
        of. **It opens with a fork the developer decides at the gate**: a nullable disposition
        column on `bank_statement_lines` or a `statement_line_dispositions` table (append-only,
        which is what undo and the audit trail want -- the recommendation).
- [ ] **X-gi** `refactor(import): the queue's replaced model leaves orphans` -- the retirement leaf
      **X-gf** owed on shipping, and since 2026-08-29 the LAST leaf of the Reconcile rebuild: it
      waits on `X-gj` and deletes what that page orphans -- the review, register and workbench
      routes and templates, the evidence-group rendering (`_queue.py`'s `_SAID`, the per-row
      sentence composers `_notes_for`), and whatever else the census finds unreachable.
      **It is a CENSUS before it is a deletion**: it may delete nothing it has not first shown to be
      orphaned, because a route that looks dead to a reading is not the same as one no door reaches
      (**N-112**'s shape). What the archived X-gf span leaves it: `apply=hand` is already DELETED
      (never re-add an index over a form), and `TestNoSweptRowCarriesASentence` guards a queue this
      step removes, so the test goes with the queue, not before it.
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
