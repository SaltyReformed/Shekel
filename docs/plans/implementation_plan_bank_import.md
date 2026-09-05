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
        (**R-GD(d)**). **Its specification pass FOLDED into `balance:X-bj-1` on 2026-09-03**
        (**R-JN**): coverage is defined ONCE, by the level relation, as a level row's own span, and
        **N-343** went with it. What remains here is the panel change, reading that coverage; ranked
        below the card arc with the other enhancements (**R-JL**).
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

**The release is cut after `X-gj-4b` merges and before `X-gi`** (`bank_import:R-JK`, 2026-09-03).

**The X-ga..X-gj leaves are the standing-consent REDESIGN the developer approved 2026-08-24**
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
- [x] **X-gj** `f119ec0a` -- the Reconcile rebuild, one page on four verbs (**R-HP**..**R-HX**),
      ticked with `X-gj-4b`, which closed `X-gj-4` and this. Detail:
      `historical/bank_import_x_gj_as_built_2026-09-04.md`.
      **What it binds on later steps is on `X-gl-1` and `X-gl-5`**, R-JY's container having been
      minted in the commit that archived this span; it is not restated here, because a constraint
      with two homes has a maintenance contract and rule 14 is that the remedy is to delete a home.
- [x] **X-gj-1** `a43e8e2f` -- the page, split three ways on the services boundary.
- [x] **X-gj-1a** `bc851df9` -- the pass becomes CARDS (**R-HP**, **R-HQ**, **R-HW**).
- [x] **X-gj-1b** `cfcfcac9` -- the page and the three bank-line tabs; minted `X-gk`.
- [x] **X-gj-1c** `a43e8e2f` -- the two settled tabs; the register RETIRED (**R-HU**).
- [x] **X-gj-2** `a23315dc` -- **R-HT(a)**'s inflow answer, deposit half and refund half.
- [x] **X-gj-2a** `751eba5d` -- a standing rule answers a DEPOSIT: the fifth `RuleAnswer`.
- [x] **X-gj-2b** `a23315dc` -- the refund filing. Ruled **R-IK**, **R-IL**, **R-IM**.
- [x] **X-gj-2b-1** `9920bed7` -- the entry positivity check becomes `amount <> 0` (**R-II**).
- [x] **X-gj-2b-2** `1bfeff07` -- a rule FILES a refund; a PARTITION correction, not a new arm.
- [x] **X-gj-2b-3** `a23315dc` -- the reader census; a purchase's sign is PICKED, not derived.
- [x] **X-gj-3** `e42dcd6b` -- **R-HT(b)**'s group answer; its second leaf WITHDRAWN (**R-JJ**).
- [x] **X-gj-3a** `e42dcd6b` -- a group's difference lands on a member the OWNER names (**R-IU**).
- [x] **X-gj-4** `f119ec0a` -- the SKIP verb, split at its gate; **R-JG** took the act ROW.
- [x] **X-gj-4a** `758bbe55` -- the STORE and its two doors: `budget.statement_line_skips`.
- [x] **X-gj-4b** `f119ec0a` -- the VERB LIT (**R-HW**, **R-JI**): `offers_for` stops shutting SKIP,
      a `skips` list on the batch schema, `reconcile_payload` reads `verb=skip`, `apply_reviewed`
      grows a fourth arm, the SKIP pane renders. Shut for a line a source files as paying an account
      the owner holds, read off the PASS's merchant set so a PROPOSED line is shut too.
- [x] **X-gj-4c** `56f97b98` -- the SKIPPED TAB (**R-JH**); its ORDER argument is spent.
- [x] **X-gj-4c-1** `456d6bd2` -- a *never a purchase* answer is not a disposition (**R-JH**).
- [x] **X-gj-4c-2** `56f97b98` -- the TAB, its Undo, and a `CardKind` the building arm states.
- [x] **X-gk** `8569e5ec` -- the MERCHANTS surface (**R-IC**): every merchant an account has seen,
      its standing answer or *You have not said*, edited ONE at a time through
      `record_submitted_rules`, so four surfaces keep one grader and one writer. On a 2026-08-31
      clone **32 of 62 were on NO surface**. Opened **N-402** (the statements route family is absent
      from the auth sweep) and **N-403** (the new-envelope answer needs scripting, on all three
      controls) -- `X-gi` and `X-gj-1c` own them, and the register's retirement is now unblocked.
- [ ] **X-gi** `refactor(import): the queue's replaced model leaves orphans` -- the DECOMPOSED
      parent of the exception queue's retirement, split 2026-09-05 at its own census into five
      leaves. **The census may delete nothing it has not shown orphaned**, because a route that
      reads dead is not one no door reaches (**N-112**'s shape). Measured on `cef75a42`: three route
      modules over nine endpoints, seven templates, `statement_review.js`, `.stmt-pick-list`,
      `_queue.py`, `_register.py`, four test modules -- and SIX live inbound links to repoint FIRST.
      *It VOIDED its own headline*: 287 matches, none naming two bank lines, measures an import
      feature the developer had abandoned, so no step may cite it.
  - [ ] **X-gi-1** `feat(import): the links point at Reconcile, and MATCH works unscripted` -- the
        six repoints, plus **R-KA**: `?open=<line_id>` renders ONE card's candidate rows
        server-side. Rendering ALL of them is 143,298 bytes of a 238,645-byte page, which is
        **N-374** re-opened; one card is at most 15 rows. **Waits on `X-gm`** (developer
        2026-09-05).
  - [ ] **X-gi-2** `refactor(import): review, register and workbench retire as pages` -- **R-HU**'s
        deletion, once nothing links in. Closes **N-404**, whose remedy was always this.
  - [ ] **X-gi-3** `refactor(import): the queue's model has no reader` -- `_queue.py` whole
        (`_SAID`, `_notes_for`, the evidence grouping), `_register.py`, four `ReviewSet` members,
        their re-exports, `test_queue.py`, and `test_rules.py`'s queue half.
        **A LATER READER must obey**: `TestNoSweptRowCarriesASentence` goes WITH the queue, never
        before it.
  - [ ] **X-gi-4** `fix(import): three findings the deletion does not close` -- **N-470**'s two
        unrendered receipt figures, **N-405**'s CSP-dead inline style, **N-402**'s 12 uncovered
        route decorators. **N-405's ledger diagnosis was WRONG**: djlint H021 runs in pre-commit and
        CI and refuses an inline style UNLESS a Jinja expression sits between `style=` and the tag's
        `>`; that one blind spot passed all 159 templates. `SHK01` closes it. No X-gm dependency.
  - [ ] **X-gi-5** `fix(import): one ordered read takes every lock a pass needs` -- **N-471**.
        `apply_reviewed` locks in submission order across THREE loops, so two presses naming the
        same lines in opposite order deadlock. **MOVES MONEY, OWN PR.** No X-gm dependency.
- [ ] **X-gm** `refactor(import): the badge and the inbox are one producer` -- **R-KB**. Measured
      2026-09-05 at head: the grid badge reads **27** and the Reconcile inbox **18**, the delta
      being 9 parked card payments at `$7,412.94` on the Transfers tab, and 18 + 9 = 27 exactly.
      Today they AGREE because the badge opens the queue, so `X-gi-1` is what would MINT the
      disagreement -- which is why it ranks first and no interim sentence is built. Move the parked
      and impossible-day split off the proposer into `_undisposed`, which **R-HQ** already implies:
      a holding state is not a task, so nothing should be proposing a match for one.
- [ ] **X-gn** `feat(import): a match may name a second bank line` -- **R-KC**. The workbench is the
      only door to a multi-line group and `X-gi-2` deletes it; the developer accepted that gap. The
      AXIS stays: `MatchSides.of` sums over lines, `MatchDays.posted_first` exists only for it,
      `test_accept.py` covers it, **R-JY**'s model keeps `group_id`, and
      `uq_statement_match_members_line` forbids faking it as two matches.
- [ ] **X-gg** `docs(plans): the envelope-semantics design loop` -- **R-GK**'s owed loop, run WITH
      the developer: filling, closure on coverage, carry-forward and the grid's row identity (whose
      same-name double-render the review measured); it mints the build steps rather than building.
      **It waits on `credit_card:CC3c`** (developer ruling 2026-08-24): envelope filling is
      two-source -- debit swipes from SECU lines, card swipes from card-side charges -- and the
      card-tender entry shape the loop must design over is what CC3c rewrites. Designing over the
      payback shape the card arc deletes is the mistake that withdrew `balance:X-au-i`.
- [ ] **X-gl** `feat(import): a bank line's disposition is one row` -- the DECOMPOSED parent of the
      ACT-MODEL rebuild the developer ruled from scratch on 2026-09-04 (**R-JY**). The argument,
      what it deletes, the limit it does NOT reach and the forks still open are
      `docs/design/statement_disposition_model.md`. ONE row per bank line names the VERB it ended on
      and WHO decided, replacing the two act stores; **R-HP**'s *exactly one verb per line* becomes
      a UNIQUE key rather than an invariant two doors maintain under a row lock, and
      `applied_by_rule` (**R-GT**) becomes one column over all four verbs. Ranked beside the card
      arc after the release **R-JK** requires: TRANSFER is the verb the disposition exists to admit,
      and `credit_card` is what makes TRANSFER real.
  - [ ] **X-gl-1** `feat(import): the disposition row` -- `budget.line_dispositions`, its migration,
        its backfill from `budget.statement_matches` and `budget.statement_line_skips`, and every
        reader repointed; `undisposed()` becomes one anti-join. It MOVES NO MONEY: a disposition
        records what already happened. **What the archived X-gj span binds on it** (rule 5, moved
        here because this step rewrites it): `_resolve.load_lines` takes a row lock on a
        keyword-only `for_write` with NO default, `_preview` passing `False` because the lock is
        refused in the `REPEATABLE READ, READ ONLY` transaction a GET runs in -- the new store must
        keep that refusal or state why it no longer needs it.
  - [ ] **X-gl-2** `feat(import): a disposition that claims nothing is deleted` -- the
        `AFTER DELETE` trigger that removes a disposition whose act no longer names an app row, and
        the deletion of `_candidates.act_still_names_a_row` and most of
        `app/services/match_withdrawal`. **Without it X-gl-1's UNIQUE key is not earned**: a stale
        MATCH disposition would block a later SKIP on a genuinely unanswered line. FORK, unruled --
        keeping the filter instead gives up the key, which is most of the reason to do the work.
  - [ ] **X-gl-3** `feat(import): a rule states a disposition and a refusal` -- `merchant_rules`
        carries a nullable DISPOSITION beside a separate REFUSAL flag; *never a purchase* leaves the
        answer set and `RuleAnswer` is replaced by the verb set. Makes **R-JH** structural rather
        than a rule a reader must remember, and makes the card arc's arrival a DATA change. The
        disposition a rule may state INCLUDES SKIP (**R-JZ**), so this step owes the `filed_total`
        exclusion -- a skip names no created subject -- while `X-gl-5` owes the
        *Skipped by your rule* surface those filings render on; a skip rule without that surface is
        the harm the ruling names, not the rule. FORK 3 stays unruled: whether *never a purchase*
        migrates to the refusal flag or is re-stated per merchant.
  - [ ] **X-gl-4** `refactor(import): consent is a type, not a check` -- two batch values, a ticked
        pass carrying all four act lists and a rule pass carrying only the classes **R-GH** consents
        to. `ReviewedBatch.__post_init__` deletes whole, taking both its existing refusals and
        `X-gj-4b`'s third with it.
  - [ ] **X-gl-5** `feat(import): one vocabulary on every surface` -- the merchant control rebuilt
        on the four verbs, the settled tabs' authorship split extended to SKIP, and `AddAct` deleted
        as `LinePipeline`'s second spelling. **What the archived X-gj span binds on it**: `parked`
        is account-payments ONLY, so its chip's MAGNITUDE is theirs alone.

*`X-gh` (**R-GL**, *the bank's balance asserts the anchor*) was WITHDRAWN on 2026-09-03 as
superseded by `balance:R-IS` and **R-JN**: under the level relation `balance:X-bj-1` builds, the
bank's closing is an OBSERVATION and neither asserts nor restates. The record is
`historical/decision_sweep_2026-09-03.md`; **N-470** went to `X-gi` and **N-434** to `X-bj-1`.*
