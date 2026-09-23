# Credit card arc: the plan of record

**Status: RE-MINTED FROM SCRATCH 2026-09-18 (`credit_card:R-CC14`..`R-CC22`). The design of record
is `docs/design/credit_card_from_scratch.md`; the twelve leaves below are its section 5, and its
section 5 also holds the map from the seventeen 2026-07-19 leaves to these.** The 2026-07-19 plan
(its architecture, its phases 0-6 and its consumer inventory) is archived whole to
`historical/credit_card_plan_2026-07-19.md`.

**The 2026-07-19 "Ratified sequencing" was DISCHARGED and is ARCHIVED** to
`historical/credit_card_sequencing_2026-07-19.md`. It stayed here, live-looking, until 2026-08-11,
warned about only by a paragraph in ANOTHER document -- which is the same failure rule 15 fixed for
archived files: the warning must be on the artifact. Two of the bare ids it ordered now name LIVE
steps in other arcs (`pay_calendar:C8`, `recurrence:D1`), so it could send a reader at the wrong
work by grep alone.

**The 2026-07-19 gate (ruling R-EB) was DISCHARGED on 2026-09-18** by the per-leaf trace
`credit_card:R-CC13` asked for (**R-CC14**, **R-CC15**), and the same day's design loop re-minted
every leaf from scratch (**R-CC16**..**R-CC22**); no leaf waits on `balance:X-f4`, and the waits
that are real are `steps.md`'s cells. **When each leaf may start is `steps.md`'s answer**, never
restated here. Every card test builds rows through the suite's builders (`one_off_row_of`,
`generate_row_of`, `payback_row_of`), never `Transaction(`: `tests/manual/census_hand_built_rows.py`
(`X-bi-7c`'s instrument) counts the latter.

**This arc's findings live in `ledger.md`, its steps are indexed in `steps.md`, its rules are
`conventions.md` and what "done" means is `verification.md`** -- the shared registries for every
arc. What stays HERE is the argument: the context, the locked rulings, the architecture and each
step's specification.

## Context

Shekel's "Credit" status is a spreadsheet-era placeholder: marking an expense Credit flips it to a
balance-excluded status (contributes $0 to its period forever, never settles, never posts to the
ledger) and auto-creates a phantom "CC Payback" Projected expense in `period_index + 1`
(`app/services/credit_workflow.py`). The developer pays those bills with a real credit card that has
interest, due dates, minimum payments, and flat-rate cash back.

The current model is financially wrong in five ways: the debt exists nowhere (net worth overstated
by the outstanding card balance); spending attribution is destroyed (the source never posts; the
payback posts under a generic category in the wrong period); cash back is manual; the payback lands
in `period_index + 1` while real cash leaves on the statement due date (up to ~6 weeks later); and
interest/grace is unrepresentable.

A seeded "Credit Card" account type existed from the start (`app/ref_seeds.py:59`,
`AcctTypeEnum.CREDIT_CARD`) and classified `PLAIN` with every behavioural boolean false, while the
seam held a non-loan liability flat at one reserved hook in `balance_at/_liability.py`; **CC-1**
(`6f7cb619`) flagged the type `has_revolving_credit` and made that band read the fold forward for
every non-loan liability, so the hook is gone.

## The rulings

**This arc's rulings are in `rulings.md`, rows whose `arc` is `credit_card`.** The key is
`(arc, id)` and no arc document states a ruling; cite one as `credit_card:R-CCnn` wherever the bare
id could be another arc's (`conventions.md` rules 9 and 10).
**The eight LOCKED rulings of 2026-07-19 are `R-CC1`-`R-CC8`**, in the order that list numbered
them, and the four `Grid / companion hard requirements` taken the same day are `R-CC9`-`R-CC12`.

## The design

**`docs/design/credit_card_from_scratch.md` is the argument, ruled 2026-09-18**: a card is a FLAG on
its account type read by the one kind-blind seam (3.1); a purchase is a MOVEMENT on the card, read
on the card, and the plan item never moves (3.2, `R-CC15`); a plan item's `account_id` is where its
money is EXPECTED to move through and the grid reads the owner's cash-flow accounts as one set (3.3,
`R-CC16`); the statement is a derived cycle and its closing a LEVEL (3.4); the payment is ONE
recurring transfer per card with a mode, priced by the card from the fold (3.5, `R-CC18` as amended
by `R-CC22`); interest is a row of a card-owned definition priced by `recurrence:R16-d`'s one
producer (3.6, `R-CC19`); the cutover of the cheat converts every frozen pair by a total rule and
the live ones by the developer's own act at a coordinated release (3.10, `R-CC17` as amended by
`R-CC20` and `R-CC21`); the card is one more account on the SimpleFIN feed (3.11). What the design
deletes is its section 4; what it measured on production is its section 7; what the neutral reviews
refuted is its section 8.

## The steps (each commit independently green + revertable; additive-first)

Every leaf is specified by the design document's section named on its line; the sentence here is the
index's. Money movers own their PR. When each leaf may start is `steps.md`'s answer.

- [x] **CC-1** `6f7cb619` -- the flag, its CHECK, `is_revolving`, the seed and migration
      `a16516c8ec05`; the liability band reads the fold through `balance_at_dates` (`R-CC14`).
      Byte-identical on the dev clone (two liabilities, both loans); on a planted card every moved
      figure is one sum, and the horizon consequence is **BAL-520** (`balance:X-cq`). The migration
      was hand-written past the guard's prompt and accepted as built (developer 2026-09-18).
- [x] **CC-2** `c69bdbfe` -- `budget.credit_card_params` (close day, due day, the minimum rule, the
      cashback rate and auto-redeem threshold, the limit; no `grace` column, its keeping is CC-3's
      derivation) under C13-c's ownership key, migration `97f92340fffc` (the table empty); ONE door
      and ONE schema, the five NOT NULL columns required (`R-CC24`); the "Card terms" card on cash
      detail gated by `is_revolving` (`R-CC25`). Moved no money; suite 14652/0 on its tree.
- [x] **CC-3** `e4c0b5ec` -- `card_statement.py` pure (leaf `8cec3531`: the closed-open window, the
      due date the first due day strictly after the close `R-CC26`, owed = minus the fold `R-CC29`);
      `card_apr.py` + the set-by-date and remove doors (`R-CC27`) inside the Card terms card
      (`R-CC28`); ONE effective-dated walk `utils/effective_dated.in_effect_on` (escrow re-pointed;
      `BAL-524` folds the six left). A card BORN a card is what the loan loaders never see (the
      re-typed loan is the held candidate).
- [ ] **CC-3i** `feat(cards): the finance charge under the one interest producer` -- design 3.6:
      `recurrence:R16-d`'s `accrued_interest` under the `DAILY_ACTUAL_365` member that step gains,
      summed per constant-balance segment of the closed-open cycle, purchases joining the path on
      grace loss (`R-CC19`: after `R16-d`; the card ships no convention table of its own).
- [x] **CC-4** `66409bc4` -- the paycheck's plan items across checking and its cards (design 3.3,
      `R-CC16`): the DECOMPOSED parent, split 2026-09-18 (developer) into 4-1 (the money), 4-2 (the
      affordances) and 4-3 (the other three readers); ticked with 4-3.
  - [x] **CC-4-1** `b4e35783` -- the money: `cash_flow_set.CashFlowSet` (ONE row clause with
        R-CC23's far leg), `resolve_cash_flow_set`, `active_accounts_query(revolving=)`,
        `GridColumn.elsewhere` and the seam taking the SET, "On other accounts" on three templates;
        no migration; byte-identical on production's shape (no card exists); suite 14603/0.
  - [x] **CC-4-2** `07063a13` -- the account chip on a cell or mobile card whose row is on another
        account than the balance line's (a transfer between members never chips); a fragment's
        balance line resolved ONCE by the page's call, the override off `HX-Current-URL` (`R-CC30`);
        the pickers over the set's members on the full-create popover and the Add Transaction modal,
        a write carrying its account as a set of one (`R-CC31`); 22 of 23 surfaces byte-identical on
        production's shape (the 23rd a loan-side shadow's cell, chipped as ruled).
  - [x] **CC-4-3** `66409bc4` -- the dashboard's upcoming bills (`_query_unpaid_expense_rows`), the
        spending report (`query_settled_expenses`, `_in_span`) and the calendar
        (`_query_transactions_for_range`) read the set through the one clause behind ONE member's
        balance line; both defaults are the set's primary (`resolve_analytics_account` deleted; the
        calendar's twin is `resolve_analytics_cash_flow_set`); no migration; 21 surfaces
        byte-identical on production's shape; suite 14680/0.
- [ ] **CC-5** `feat(cards): a purchase is a movement on the card` -- design 3.2 (`R-CC15`): the
      DECOMPOSED parent, split 2026-09-20 (the card lane's trace) into 5-1 (the key), 5-2 (the
      purchase door, its readers and the picker) and 5-3 (the settle-with-tender door), 5-1 and 5-2
      in ONE PR (`R-CC33`), and 2026-09-21 (at 5-3's entry) into 5-4 (the matcher's card-screen
      half, `R-CC40`) and 5-5 (the net-worth sign fix, `R-CC41`); ticks with its last leaf. The flag
      survives to CC-7.
  - [x] **CC-5-1** `4045a9b1` -- the key: `fk_transaction_entries_parent_account` and its
        `ON UPDATE CASCADE` dropped, `owner_id` NOT NULL backfilled, three keys holding a movement
        to its own account and to its row's owner (`R-BAL76`'s letter; `R-CC32`: the owner key
        CASCADEs, the account keys RESTRICT); migration `9900b309f0b0`; no door wrote cross-account
        yet, every balance byte-identical; suite 14919/0. Closed **CC-353**. Record (the rulings'
        verbatim texts too): `historical/credit_card_cc5_key_and_purchase_as_built_2026-09-21.md`.
  - [x] **CC-5-2** `af3b9f5b` -- the first cross-account writer (two commits, `3ca97070` first): the
        purchase door takes an account gated on the ROW's owner (`R-CC37`, `R-CC39`: its own or any
        member of the owner's set); the reconcile scope re-keyed to the movement's account,
        `off_statement_sum` and guard 4 given a movement arm; the account-move retention retired
        (`R-CC36`); the picker from the door's tuple, hidden with one choice (`R-CC34`), the CC
        checkbox kept (`R-CC35`); ONE chip macro names the movement's account; suite 15014/0.
  - [x] **CC-5-3** `9a74658f` -- the settle-with-tender door: `Settlement.account_id` (None = the
        seam's ONE default `tender_account_id_of`, the kept movement's account else the row's;
        `R-CC42`); ONE gate `movement_account.admitted_movement_account_id` for all three writers;
        `_cover` books on the tender on the movement's day, refused on or before the card's opening;
        `_re_point` moves a kept movement only for a NAMED tender; the "Paid from" picker
        (`R-CC39`); `covered_cash_leg` takes the account (`R-CC40` half 1).
- [ ] **CC-5-4** `feat(cards): the card's line meets the bill it paid` -- design 3.2 and `R-CC40`'s
      HALF 2: the DECOMPOSED parent, split 2026-09-21 by the developer (`R-CC45`) into 4a-1 (the
      writer), 4a-2 (the re-key migration; the member table's bill column dropped) and 4b (the card
      panel's settlements arm, `R-CC44`), and on 2026-09-22 given 4a-3 (one act takes a movement off
      the books; the popovers' captions; `R-CC51`) and 4a-4 (a row holding a movement is history;
      neither match key cascades; `R-CC55`); ticks with its last leaf. Must land before any card
      import exists.
  - [x] **CC-5-4a-1** `079524b0` -- the payment MOVEMENT is the matcher's subject on every screen
        (`R-CC43`): `RowKind.SETTLEMENT` (the movement's identity, the row's record: priced as a
        movement when dated, the row's paycheck as its window, dated through the row's own door with
        the screen's account as tender); a settled row offered as its payment; a settling match
        records the payment its settle wrote; a re-pointed payment withdraws the matches naming it,
        disclosed (`R-CC46`); both member shapes still read until 4a-2.
  - [x] **CC-5-4a-2** `550cc9ce` -- migration `2eabfa596ee0` (re-parented onto `c7d1e9a4b2f8` at
        `62bf0e35`) re-keyed the 103 row members onto their payments, refusing a member with no
        payment, one on another account or one another member names, and dropped
        `statement_match_members.transaction_id` with its key and unique index; its readers lost
        that arm (`R-CC45`), closing **CC-356**. Deploys with 4a-3 (`R-CC51`); suite 15144/0.
  - [x] **CC-5-4a-3** `175b192d` -- `movement_removal.remove_movements`, ONE act taking a payment or
        purchase off the books (reversed, out of every match, deleted; `R-CC54` part 1), called by
        the six doors that removed a movement one by one, the status seam's `$0.00` / purchases
        record among them (**CC-358**'s popover path); the bill popover's two captions (`R-CC56`)
        and the transfer popover's (`R-CC59`) read `match_withdrawal.pending_for_movements`.
        **CC-359** measured not a defect. No migration; suite 15167/0.
- [ ] **CC-5-4a-4** `fix(cards): a row holding a payment or purchase is history` -- `R-CC54` parts
      (2) and (3), its own leaf by `R-CC55`: a row's delete stops cascading to its payments and
      purchases, so a row holding one is history -- the template and account permanent deletes
      archive instead, the archive keeps such a row where today it hides it and its purchase leaves
      the fold (**CC-363**: template 19 'Clothes' holds movement 343, `$107.57`, which either door
      drops on production's copy), and truncate / regenerate lock its period; a match's key to its
      payment or purchase stops cascading, like its key to the bank line, and the leftover-match
      check `_candidates.act_still_names_a_row` (read by `matched_subjects` and `_undisposed`) is
      deleted, which makes `bank_agreement._lines_on`'s membership read exact (**CC-358**). A
      migration on `2eabfa596ee0`, its own release after the 4a-2 + 4a-3 release; its entry's own
      design questions (2026-09-22, extending part (2) to the archive, the object layer and the
      reset and transfer doors) are filed at its tick. Closes **CC-358**, **CC-363**.
- [ ] **CC-5-4b** `feat(cards): the card's panel lists the bill it paid` -- `R-CC44`: a FOURTH arm
      of the reconcile panel, settlements -- un-dated payments on this account whose bill is on
      another, listed under the bill's name in its paycheck block, ticked through the bill's own
      door (settle on the asserted day, tender = this account); the PAYMENT takes the statement's
      link and the bill does not, so `status_seam.record_clearing` learns which account's statement
      it records; the panel template and POST gain a field. After 4a-2; balance / bank-import's
      package, announce-first.
- [ ] **CC-5-5** `fix(cards): one sign for every balance` -- `R-CC47` (re-scoping `R-CC41`; the
      sixth site `R-CC48`): every balance is what the account HOLDS, negative when owed, and owed is
      minus it; the DECOMPOSED parent, split 2026-09-22 by the developer (`R-CC50` as amended by
      `R-CC52`) into 5a (the groundwork), 5b (the liability doors ask owed) and 5c (the one-commit
      flip); ticks with its last leaf. `$0.00` on production's net-worth figures (both liabilities
      are configured loans).
  - [x] **CC-5-5a** `aa29d977` -- `balance_at.owed(balance) = -balance`, R-CC29's one flip moved
        into the seam (`card_statement.owed` deleted); the /savings revolving footer is each
        non-loan liability's owed amount floored at zero, summed (`R-CC49`);
        `tests/manual/verify_liability_screens.py`, the screen-diff instrument (178 responses plus a
        `tree.json` app digest) that grades 5b and 5c; 178 screens byte-identical on production's
        shape.
- [ ] **CC-5-5b** `fix(accounts): a liability's balance is asked as owed` -- `R-CC52`: every door
      that takes a LIABILITY's balance asks for the amount OWED (positive = you owe; a card's credit
      a negative amount owed) and stores the held sign through `balance_at.owed`, one crossing per
      door (the form speaks owed, the service stores held; the schema tier may not branch on account
      class, as `schemas/validation/accounts.py` states): the account create form's opening balance
      (`templates/accounts/form.html`, its route, `AccountCreateSchema`,
      `account_service.create_account`), the anchor editor (`routes/accounts/anchor.py`
      `anchor_form` and its PATCH, `grid/_anchor_edit.html`; amortizing accounts stay refused) and
      the books-opening door (`routes/accounts/opening.py`, `opening_service`,
      `accounts/_books_opening.html`), each entered and shown as owed, the pre-fill and a 422
      redisplay included; the tile editor's held pre-fill (**CC-357**) closes with it. FIRST, a
      census of every STORED liability balance on the newest production dump
      (`account_openings.opening_equity`, `account_anchor_history.anchor_balance`, any other
      books-opening or assertion row), each read under `R-CC47`, and of what reads them: the
      Mortgage's `+174,281.51` opening and `+178,103.41` anchor were typed as owed, so wrong-signed
      (both a configured loan's, feeding no net-worth figure; its cash fold and the posted ledger's
      Balance Sheet are what to trace). A wrong-signed stored value is a MONEY question to the
      developer, with worked dollars, BEFORE 5c; any backfill is an Alembic migration, which makes
      this leaf migration-bearing and its own release. Before 5b grades with it, 5a's instrument
      gains `/grid?account_id=<id>` for each non-amortizing liability (the balance line the anchor
      door opens from). Graded by 5a's instrument: every ASSET screen byte-identical, a liability
      form's label change a declared change. Closes **CC-357**.
- [ ] **CC-5-5c** `fix(cards): a card in credit is the issuer owing` -- `R-CC47` and `R-CC48`, ONE
      commit: the seam's two configured-loan arms report the HELD sign (`positions()` and the loan
      domain's producers stay owed), each arm converting through the one flip and never an inline
      `-positions(...)` (CLAUDE.md rule 14) -- so FIRST `owed` MOVES out of `_liability.py`, which
      imports `_inputs` and `_kind_correct` (the two arms), to a module with no seam imports (e.g.
      `balance_at/_sign.py`; the public name `balance_at.owed` stays), and its docstring names the
      seam's other NON-held outputs (`liability_owed_at_dates`' magnitudes for every liability,
      `positions`, `secured_loan_series`), none of which may be passed to it; the hero and trend
      read the plain sum of balances with liabilities as `owed`, the band drops its `abs` and the
      liability subtotal (`_display._compute_group_subtotals`, `R-CC48`) reads `owed`; the seven
      loan-balance readers move onto `owed` (debt strategy; the loan pages' `current_balance`
      property feeding the dashboard, the payoff and refinance calculators and the true-up pre-fill;
      home equity; on /savings the liability tile's figure, its sparkline and two debt-summary
      figures) and liability tiles show owed. The flip also decides what a savings goal backed by a
      LIABILITY reads (**CC-360**: `_goal_form_context` offers any active account, a loan included,
      and `_goals._goal_account_balance` reads a configured loan's OWED figure as progress today and
      its held balance once the loan arm is re-signed): 5c either gains that reader, the goal
      reading `owed()`, or refuses a liability as a goal's account, a design question the lane puts
      to the developer with worked dollars before building; and the footer's "revolving" caption,
      whose sum also counts an amortizing account with no loan terms and a custom liability, is
      corrected (**CC-361**). Every loan test figure that flips sign is a rule-5 re-expression the
      developer confirms, its classes with counts, before the commit. Graded by 5a's
      `tests/manual/verify_liability_screens.py` per its docstring's procedure: production's screens
      byte-identical. Closes **CC-354**, **CC-360**, **CC-361**.
- [ ] **CC-6** `feat(cards): the payment is one recurring transfer with a mode` -- design 3.5
      (`R-CC18` as amended by `R-CC22`): `card_payment_settings` with the four modes and a unique
      key over the card; the transfer setup flow, seated under `recurrence:R7f` once ruled (else
      this leaf's loop rules the seat); the CARD amount rule pricing at the end of the day before
      the row's day less what is not yet inside that balance, floor zero, with its injected deriver;
      a mode switch as a recurrence rewrite plus regeneration; the typed-over row as the partial
      payment; the placed one-off as an extra payment; the payable on checking's grid; the
      underpayment notice. Owns **N-311**.
- [ ] **CC-7** `feat(cards): the cutover of the cheat` -- design 3.10 (`R-CC17` as amended by
      `R-CC20` and `R-CC21`): every frozen transaction-level pair by the total rule copying day,
      figure, both bases and the clearing link from the payback's covering movement, with the
      journal reversal and the match-member re-point; every envelope by the envelope clause with its
      correction lines; a still-live pair reported, its payback deleted, its bill settled at
      `$0.00`; `is_credit`, its reducer arm and
      `ck_transaction_entries_card_purchase_clears_nowhere` deleted once no line carries the flag;
      the mark/unmark doors, the `Credit` transition and the `Credit` status VALUE retired; routes,
      templates and JS; `credit_workflow.py` and `entry_credit_workflow.py` deleted whole. Graded on
      checking's cash fold by equality (`tests/manual/verify_balance_baseline.py`), on the budget
      clock by a stated per-period diff (`verify_grid_cutover.py`) and on the posted ledger for the
      reversed legs. **MOVES MONEY on the budget clock, `$0.00` on checking's cash fold.** Closes
      **CC-352**, **N-337**, **N-350**, **N-351**.
- [ ] **CC-7o** `docs(runbook): the card's release` -- design 3.10 (`R-CC21`; `salary:R18-d`'s shape
      under `R-HJ`): the prerequisite act (the card paid in full, every payback marked paid, no new
      charges), the release, the card created through the account door with its books opened on the
      payment day at that day's balance verbatim, the payment definition set up, the first cycle on
      the feed reconciled; no per-row door act.
- [ ] **CC-8** `feat(cards): the finance-charge definition` -- design 3.6: the definition the card
      owns (`credit_card_params.finance_charge_template_id`) and its rule, monthly on the statement
      close, `$0.00` when grace held; `template_id` is the pricing link `balance:R-IY` names, so no
      fourth FK and no `system_origin_id`. Closes **N-264**.
- [ ] **CC-9** `feat(cards): rewards` -- design 3.7: accrual as a derived figure over settled
      purchases minus redemptions; manual redemptions and the auto-redeem threshold as rows; no
      `system_origin_id`. Ruled the tail of the path by the 2026-09-15 order.
- [x] **CC-10** `32528a7c` -- a transfer OUT of a card refused at both transfer doors through ONE
      composed loan-or-card set (`_validation._reject_unmodeled_source`, leaf `c5fffc55`; the
      investment contribution door translates the refusal instead of a 500); a card kept out of a
      paycheck's deposit picker (`active_accounts_query(revolving=False)`, design 3.8); no
      migration; data unchanged on production (0 revolving accounts, read-only 21:4x 2026-09-18); a
      LOAN source at the investment door now flashes where it was a 500.
- [ ] **CC-11** `feat(cards): the cockpit and the grid affordances` -- design 3.9, through the
      design loop: each screen held to `docs/design/fable5-design-language.md` and verified on the
      dev clone. A payment between two non-balance members of the set is drawn on a third member's
      grid from its FROM side and counted once (**R-CC38**, amending R-CC23; the fourth arm of
      `cash_flow_set.leg_accounts_shown` and `far_legs_of`). Closes **CC-355**.

**The card on the feed is `bank_import:X-f6b`'s** (design 3.11): SimpleFIN serves a card's lines and
balance (an external property nothing on the tree asserts, which `X-f6b` CONFIRMS); card lines are
disposed under `X-gl`'s verbs; the Capital One CSV adapter stays a manual fallback.

## Verification standard

**The standard is `verification.md`**, one copy for every arc; what every commit owes is
`CLAUDE.md`'s Definition of Done. This section states only what is SPECIFIC to this arc.

- **`CC-1`'s band and `CC-7`'s cutover are live-render gates**: on the dev clone, before landing,
  render the grid on the card, the savings cockpit, obligations, the net-worth trend and the balance
  sheet. Every moved number is individually explained and signed off.
- **End-to-end acceptance on the dev clone**, one walk: create card + params -> settle a projected
  bill with the card as tender (a movement on the card) -> statement closes -> the payment row
  prices from the fold -> settle the payment -> grace holds (interest `$0.00`) -> force an
  underpayment -> the notice, and the finance-charge row once `CC-3i` / `CC-8` ship -> rewards
  accrue -> a redemption row -> the next payment shrinks.
