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

*The Reconcile rebuild -- one page on four verbs (rulings R-HP through R-HX), nineteen shipped steps
from the page itself to the Skipped tab's Undo -- was ARCHIVED at `bank_import:X-gq`'s registry pass
under conventions rule 5, this document's cap being binding and rule 5's escape being the sanctioned
one. Its as-built record is `historical/bank_import_x_gj_as_built_2026-09-04.md`, which governs
nothing and says so on its own first line. The code as committed is what those steps did.*

- [ ] **X-f6** `feat(import): the bank says when money moved` -- the DECOMPOSED parent of the
      statement importer (**R-FP**), carrying **N-173**.
      **It is no longer the sequenced follow-on ruling R-EB made it** (developer, 2026-08-13): what
      the cash cutover needed was the CLEARING FACTS, not the import surface. When it ticks is
      `steps.md`'s to say and is not restated here (conventions rule 16).
  - [x] **X-f6c** `321bf2e4` -- one step with `balance:X-bi-7b` (**R-BAL24**), shipped at its leaf
        `X-bi-7b-3`: a NEW-ENVELOPE merchant answer mints ONE rule-less definition the first time
        and names it thereafter, so a merchant rule's container carries an identity across pay
        periods instead of a name; closed **N-328** (**R-GA**'s argument, ruled 2026-08-20).
  - [ ] **X-f6g** `refactor(reconcile): a statement-covered account reconciles from statements` --
        the reconcile panel stops offering an account whose statements the owner imports
        (**R-GD(d)**). **Its specification pass FOLDED into `balance:X-bj-1` on 2026-09-03**
        (**R-JN**): coverage is defined ONCE, by the level relation, as a level row's own span, and
        **N-343** went with it. What remains here is the panel change, reading that coverage; ranked
        below the card arc with the other enhancements (**R-JL**).
  - [ ] **X-f6b** `feat(import): the statement arrives without being fetched` -- the automated
        SOURCE ADAPTER (**R-FP**), the DECOMPOSED parent of four leaves;
        **DESIGN LOOP CLOSED 2026-09-18** (**R-BI10**..**R-BI14**, **R-BAL71**; the argument and
        every option space are `HANDOFF-X-f6b.md` s.1 until the coordinator names the in-repo design
        home). R-FU stands; N-372 and N-381 are -2's own design rounds first; the Capital One CSV
        adapter stays the card's manual fallback.
    - [x] **X-f6b-1** `d4eb2752` -- the sighting relation, coverage the declared window, the solve's
          candidates the line days + the stated day (**R-BI10**, **R-BAL71**, **R-BAL74**); no
          money. Closed **N-303**, **N-313**, **N-331**, **N-434**.
    - [x] **X-f6b-1b** `5daa1393` -- the sighting carries the merchant key; the line's merchant is a
          READ over its sightings, one correlated SQL producer wrapped read-only (**R-BI16**); the
          word column and the line's key dropped by migration (**R-BI17**; the UP sweeps merchants
          nothing names); `orphan_merchants_by_import` reads the sightings' keys. No money (306 of
          306 equal on the restore). Closed **BI-504**.
    - [ ] **X-f6b-2** `feat(import): the SimpleFIN feed` -- `bank_feeds` under the renamed field key
          (**R-BI12**, **BI-503**), the claim and mapping, one sync function (**R-BI13**), a match
          carrying the reviewed day (**R-BI14**). Closes **N-338**, **BI-499**, **BI-503**.
    - [ ] **X-f6b-3** `feat(import): the nightly door and the feed panel` -- `task.sh`,
          `scripts/feed_sync.py`, the `deploy/systemd/` pair (**R-BI11**), the runbook's rows, the
          statements page's feed panel. Closes **N-326**, **N-330**, **BI-502**.

**The release is cut after `X-gj-4b` merges and before `X-gi`** (`bank_import:R-JK`, 2026-09-03).

**The shipped `X-gi` leaves and `X-gm` are condensed below and their as-built records archived**
under rule 5 to `historical/bank_import_x_gi_leaves_as_built_2026-09-12.md`, on `X-gi-4`'s tick.
**The X-ga..X-gj leaves are the standing-consent REDESIGN the developer approved 2026-08-24**
(**R-GH**..**R-GL**; argument: `docs/audits/bank_import_redesign/README.md`), and
**the shipped X-gb..X-gf-3b-2 span and X-gk are ARCHIVED under rule 5** to their four as-built
records under `historical/` (`bank_import_x_gb_x_gc`, `_x_gd`, `_x_ge`, `_x_gf`; X-gk has none) and
their index rows to `historical/bank_import_shipped_steps_archived_2026-09-18.md` (on `X-f6b-1b`'s
tick): every finding they did not close is a live `ledger.md` row, and what they leave a LATER step
is on that step's own entry.

- [x] **X-gi** `cd78499a` `refactor(import): the queue's replaced model leaves orphans` -- the
      DECOMPOSED parent of the exception queue's retirement, split 2026-09-05 at its own census into
      five leaves. **The census may delete nothing it has not shown orphaned**, because a route that
      reads dead is not one no door reaches (**N-112**'s shape). Its link and page counts are
      SPENT -- `X-gi-1` repointed and `X-gi-2` deleted -- and its headline was VOIDED by **R-KC**,
      so what survives is the rule, not the numbers; `X-gi-3`..`X-gi-5` carry their own.
  - [x] **X-gi-1** `8543c80f` -- four links repointed; the MATCH pane unscripted behind `?open=`
        (**R-KA**, **R-BI1**). Both obligations it left `X-gi-2` are discharged.
  - [x] **X-gi-2** `b462af07` -- **R-HU**'s deletion. Closed **N-404**; filed **BI-479**,
        **BI-480**.
  - [x] **X-go** `20f96d90` -- `_MAX_MATCH_MEMBERS` deleted (**R-BI2**); two bounds already held it.
  - [x] **X-gq** `86f8620d` + `be59238e` -- the LANDING moved out of `_variance.py`.
        **A LATER SPLIT OBEYS**: a byte-pure move breaks REFERENCES across the boundary, and no gate
        sees it (**BI-484**).
  - [x] **X-gp** `40354328` -- ONE control whose options ARE the acts (**R-BI2**). Closes
        **BI-481**. **A LATER STEP OBEYS**: a pre-X-gp page FAILS CLOSED (`residual-` and
        `difference_on-` are unread).
  - [x] **X-gi-2a** `8fa4d0bc` -- the `?open=` pane priced from the SUBMITTED form (**R-BI3**);
        closes **BI-478**. The SCRIPTED page without `?open=` still loses a refused press's ticks,
        unfiled.
  - [x] **X-gi-3** `43ce313b` -- the queue, the register and the dead readers deleted; closes
        **BI-479**, **BI-480**; filed **BI-482**..**BI-485**.
  - [x] **X-gi-4** `ba5ae344` -- **N-470** rendered and logged; **N-405** at `af6f8a3f`; **N-402**
        by **R-BI4**, ONE default-deny login gate. **A LATER LANE OBEYS**: write no decorator; a
        public route is named in `PUBLIC_ENDPOINTS`. Filed **BI-486**..**BI-490** (`X-gr`, `X-gs`).
  - [x] **X-gi-5** `cd78499a` -- every line lock FIRST, in one read ordered by `id` (**R-BI5**: the
        mode is `FOR NO KEY UPDATE`, one helper). Closed **N-471**, REPRODUCED first by a forced
        interleave. **A LATER STEP OBEYS**: `balance:X-bn`'s advisory lock goes ABOVE `lock_lines`;
        what remains is the transactions-plus-advisory cycle, X-bn's.
- [x] **X-gm** `1b722d52` -- the badge and the inbox are ONE producer (**R-KB**, **R-KD**); closed
      **N-476**. **A LATER STEP OBEYS TWO**: removing a line reprices another's row (**R-GD(a)**),
      and `to_explain` counts CARDS where the badge counts LINES -- `X-gn` keeps those equal.
- [ ] **X-gn** `feat(import): a match may name a second bank line` -- **R-KC**, which carries the
      argument and the three facts keeping the AXIS. `X-gi-2` deleted the workbench, the only door
      to a multi-line group, and the developer accepted that gap: this restores it card-side.
- [x] **X-gr** `303a907f` -- the import doors say what they do. Closed **BI-487**, **BI-488**,
      **BI-490**; **BI-489** ruled **R-BI6** (the badge and the receipt assert no cause; the
      released-placement fact lives nowhere until the level relation) and re-homed to
      `balance:X-bj-1` as **BAL-485**. **A LATER STEP OBEYS**: the delete confirmation and the act
      read ONE producer per figure (`resting_on`, `lines_by_import`, `orphan_merchants_by_import`);
      a second spelling of any of them is the defect BI-490 named.
- [x] **X-gs** `eec1a2be` -- `require_owner` reads `current_user.role_id` outright; closed
      **BI-486** (the fixture half was EMPTY, measured twice; the commit carries the census).
- [x] **X-gt** `89e39a18` -- the receipt value (`AppliedItem`, `RefusedItem`, `BatchOutcome`,
      `Tally`) left `_batch.py` (998 -> 679) for `_outcome.py` (374) as a PURE move, AST-graded
      (**R-BI7**); **N-474**'s wording fixed at `74673532`, two more exclusive sentences in the
      rider. Closed **BI-491**, **N-474**; **BI-494** filed under `X-gw`.
- [ ] **X-gu** `fix(import): the delete door locks its lines in the shared order` -- **BI-492**.
      `lock_lines` over the import's lines before `delete_import` deletes the row, so the cascade
      cannot cross a press; the cross-resource half is `balance:X-bn`'s. Minted 2026-09-12.
- [x] **X-gv** `88f38feb` -- a door reads the row its lock holds (`populate_existing()` beside the
      mode). Closed **BI-493**.
- [x] **X-gx** `c2e22790` -- the receipt offers rules from the applied items, not `review_set`.
      Closed **BI-495**.
- [x] **X-gz** `08a66901` -- the match pane shows the dates a human verifies by (**R-BI9**); outcome
      O0 delivered 2026-09-16. Closed **BI-498**. The three entries in full:
      `historical/bank_import_shipped_entries_condensed_2026-09-22.md`.
- [ ] **X-hb** `fix(import): the delete confirmation names the placement that leaves` -- **BI-501**
      (was balance:BAL-489, re-homed 2026-09-18): the confirmation and the receipt read the level
      relation `balance:X-bj-1` built and say this import's own placement leaves and which import
      becomes the balance-of-record; `ImportRemoval` gains the field. `$0.00`; the door's copy only.
- [ ] **X-ha** `perf(import): the reconcile screen's per-request cost` -- **BI-500**. Render 650-850
      ms, Apply of 22 cards 1.65 s / 974 KB, preview 560-650 ms x9 (2026-09-15, `slow_request` +
      nginx); the step names the query or payload each pays for. Performance only; upkeep tier.
- [ ] **X-gy** `chore(ci): the suite's CI clock is measured, then split` -- **BI-496**, WIDENED
      2026-09-22 by **R-BI38** from a fix to the job's shape. It starts with its measurement: a
      matched A/B on the hosted runner names where its 5-13x per-test ratio comes from (the 3x
      oversubscription of `-n 12` on 4 cores, the per-item drop-and-reclone, the service container's
      disk). Then ONE change: the suite sharded across ~4-6 parallel jobs by an in-repo splitter,
      the shards' node-id UNION graded equal to today's collected set (the `docker`-marked tests and
      the serial audit-trigger benchmarks included); `-n` sized to the runner; lint (the plan gate,
      pylint, the custom checkers, duplicate-code) its own parallel job; and an aggregate job named
      `lint-and-test`, the context branch protection requires, failing on any failed, skipped or
      cancelled shard. No check is weakened, and `ci_scope`'s registry-only scope, `fetch-depth: 0`,
      the clock-skew `TZ` and the pinned postgres survive; `pytest.ini`'s per-test cap is re-sized
      from CI's own `--durations`. Expected ~12-15 minutes a PR against 41-61 over four runs on
      2026-09-22; merged alone, since every PR runs `ci.yml` and `pytest.ini`.
- [ ] **X-gw** `refactor(import): the tally freezes itself` -- **BI-494**. `Tally.frozen()` beside
      both classes in `_outcome.py`, built from the field names so a counter on one side and not the
      other refuses loudly; `apply_reviewed` returns it (`X-gx`, queued ahead, edits the same file).
      Minted by the developer 2026-09-12 at `X-gt`'s ruling.
- [ ] **X-gg** `docs(plans): the envelope-semantics design loop` -- **R-GK**'s owed loop, run WITH
      the developer: filling, closure on coverage, carry-forward and the grid's row identity (whose
      same-name double-render the review measured); it mints the build steps rather than building.
      **It waits on `credit_card:CC-5`** (developer ruling 2026-08-24, on `CC3c`; re-pointed at the
      2026-09-18 re-mint): envelope filling is two-source -- debit swipes from SECU lines, card
      swipes from card-side charges -- and the card-tender entry shape the loop must design over is
      the movement on the card CC-5 builds. Designing over the payback shape the card arc deletes is
      the mistake that withdrew `balance:X-au-i`.
- [ ] **X-hc** `refactor(import): a row an accepted act names is the bank's` -- **R-BI18** (N-372's
      round, X-f6b-2's design loop): its settle day and figure are observed facts, so every door
      that would move them, its status or its existence REFUSES while the act stands ("undo the
      match first"); the members' row-side keys become NO ACTION like the line-side key;
      `_still_holds`, the fold and R-GX(c)'s whatever-its-age clause delete (R-GX(c) AMENDED when
      this ships); its first act is the census of the doors it refuses at. After `balance:X-bi-4`
      (shipped `32c65cf1`; the figure's home is the covering movement), beside `X-gl-2`. Closes
      **N-372**.
- [ ] **X-hf** `feat(import): the card on the feed` -- design 3.11 of `credit_card_from_scratch.md`:
      map the card account once `X-f6b-2` confirms SimpleFIN serves it (the mapping form must admit
      a Credit Card, as `serves_cash_detail` does), land its lines in the one inbox on the card
      (purchase lines matching card movements or minting them, the closing balance a level; payment
      lines wait for `X-gl`'s TRANSFER door), the Capital One CSV adapter the fallback. After
      `X-f6b-2` and `credit_card:CC-5-4a-2`; `credit_card:CC-7o` waits on it. Closes **BI-506**.
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
        keeping the filter instead gives up the key, which is most of the reason to do the work; a
        THIRD option since **R-BI18** (2026-09-18): neither, because `X-hc` makes the state
        unrepresentable (row-side keys NO ACTION, the doors refuse), so this waits on `X-hc`.
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
