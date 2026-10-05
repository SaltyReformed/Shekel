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
      the books; the popovers' captions; `R-CC51`), 4a-4 (a row holding a movement is history;
      neither member subject key cascades; `R-CC55`) and, on 2026-09-23, 4a-5 (the reconcile panel
      and carry-forward say what they free first; `R-CC76`); ticks with its last leaf. Must land
      before any card import exists.
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
  - [x] **CC-5-4a-4** `1d7a1174` -- a row holding a payment or purchase is history (**R-CC54** parts
        2-3): its delete stops cascading, the template, account, pay-period and transfer doors keep
        it (**R-CC63**..**R-CC66**, **R-CC75**), the database refuses a hidden non-transfer row
        holding one (**R-CC89**, **R-CC92**), and a match's key to its payment or purchase stops
        cascading, the leftover-match check deleted. Migration `c4a4e7d1b9f2`; ships in one release
        with `balance:X-bn` (**R-CC109**, **R-CC120**). Closed **CC-358**, **CC-363**, **CC-376**.
- [ ] **CC-5-4a-5** `fix(cards): the panel and carry-forward say what they free first` -- `R-CC76`:
      the two doors that reach the status seam's `$0.00` / purchases withdrawal and undo a statement
      match with no caption (**CC-364**) name the bank lines they would free BEFORE the press, from
      the same read they act on, as the popovers do (`match_withdrawal.pending_for_movements`;
      `R-CC56`, `R-CC59`). The reconcile panel (`accounts/_reconcile_panel.html`) says it on a row
      whose kept payment an accepted match names: its per-row `settled_amount-<id>` box at `$0.00`,
      a reverted envelope's tick (the seam's purchases arm) and a transfer row's box at `$0.00`,
      which takes both legs' payments off as the transfer popover's Actual box does. Carry-forward's
      confirmation (`grid/_carry_forward_preview_modal.html`) says it for each envelope
      `settle_from_entries` would settle. Of the seam's doors only the grid's one-click Mark Paid
      stays silent (`R-CC56`); the doors OUTSIDE the seam that withdraw a match with no caption (the
      purchase delete, Undo CC, the popover's Status leaving Credit, the last credit purchase's
      delete or un-credit) were **CC-367**; the account and recurring-transfer permanent deletes
      archive on a held leg since **R-CC65** and free nothing. **R-CC80** puts CC-367's doors in
      this step: each says it first, and the one removal act asks what the owner was shown
      (**R-CC81**). Its `$0.00` captions were its own question at its start, under **R-BAL155**
      (ruled below). A row's own account's list, ticking a reopened bill whose kept payment is on
      another account (a card, or Checking under `R-CC117`), re-pointed that payment
      (`status_seam._covering._re_point`) and withdrew any match naming it through
      `match_withdrawal.withdraw_for_moved_movement` with no caption (**CC-378**): its builder asked
      the developer whether that list captions such a row (`paid from <account>`) or omits it (ruled
      below). After 4a-4 (`R-CC76`). Closes **CC-364**, **CC-367**, **CC-378**, **CC-384**,
      **CC-385**, **CC-386**. **Ruled at its start** (2026-09-30): the panel refuses a `$0.00`
      payment (**R-CC125**), a reopened bill paid from another account is offered only on that
      account's list (**R-CC126**, so the panel never re-points and **CC-378** closes unreachable),
      and the removal act checks the page (**R-CC127**).
      **Built in seven leaves, `Ships:` on the last one** (the coordinator, 2026-10-04 and
      2026-10-05). **4a-5a** `ff86849b` (done, no `Ships:`) -- the removal act takes the lines a
      page showed or a named silence (**R-CC127**); every popover press posts the lines its captions
      named (`shown_lines`), and a stale press redraws the whole card (**R-CC128**); Estimated's
      caption on a reverted matched row lets one Save record `$0.00` (**R-CC129**); a companion's
      Mark Paid that would free a line is refused (**R-CC130**); the delete dialog posts the
      purchases it named (`shown_purchases`; **R-CC131**, fulfilling **R-CC96**'s clause);
      `ShownIds` joins the row-id census; a hand-built hard DELETE of a pair whose leg is matched,
      which withdrew the match silently, is refused (a designed 400; **R-CC127**). Applications, no
      ruling of their own: `designed_error(retarget=)` for the redraw (**salary:R-SAL33**) and the
      owner's one-click Mark Paid silent (**R-CC56**); a transfer `$0.00` Paid caption withdrawn (a
      transfer prices `$0.00` only where a derived payment rounds to it, and there the popover's
      Paid is refused as out of date). **4a-5b** `b3ff5bec` (done, no `Ships:`) -- **CC-367**'s
      doors name what they free first and post it back (**R-CC80**, **R-CC127**): the purchase X's
      confirmation asks in R-CC80's words and posts its lines as the DELETE's query string, a list
      out of date refused and drawn again as it is now; on the envelope's last card purchase the X
      names and takes the CC payback's line too, purchase and payback in ONE removal act
      (`delete_entry`: two acts on one account refused the ordinary X as out of date); the edit
      form's CC un-tick on that purchase names the payback's line in a caption and posts it; Undo CC
      and Status leaving Credit share one caption beside Undo CC
      (`credit_workflow.pending_for_credit_revert`), posted by Undo CC and the Save; one read serves
      the purchase lists (`match_withdrawal.pending_for_each`, `entry_service._removals`), who is
      looking taken from the session, never the posted `can_edit`. A companion is shown no line
      (**R-CC132**): a purchase whose X would free its line has no X, the ruled note in its place, a
      press that would free one anyway is refused with the ruled sentence, and one over the CC
      payback's line (the last card purchase's X or un-tick, an add or an edit bringing the card
      total to `$0.00`) with one sentence, an application: "Groceries's card payback is matched to a
      line on the bank statement, so only the account owner can make this change." The grid's
      statement-count test folds an `IN (...)` id list (**R-CC133**, rule 5). Closed **CC-367**.
      **4a-5c**, split in two: **4a-5c-1** `bc0b02b3` (done, no `Ships:`) -- the reconcile panel. A
      ticked `$0.00` box is refused before its arm settles anything, in the ruled sentence naming
      the row or the leg (**R-CC125**, `_rows._refuse_a_zero_payment`, both arms). A row's own list
      leaves out a row holding no purchase whose covering movement is on another account, of any
      date (**R-CC126**, `_transactions._own_clauses`; any date is an application of the picked
      description), so neither list re-points a payment (`status_seam._covering._re_point` changes
      nothing), and its tick names no tender (**R-CC15**'s tender at the panel deleted as changing
      nothing a tick books, an application; the booked account pinned per list). The one tick left
      that takes a payment out of its matches, a row settling from its purchases over the payment a
      revert kept, says so under its row ("Closing it from its purchases withdraws ...", the shared
      `frees_lines` clause, an application of **R-CC56** and **R-CC80**) from ONE read
      (`match_withdrawal.pending_for_each`), and posts those lines per row (`shown_lines-<row id>`)
      to its own settle, since the act compares per account; a stale press redraws the panel with
      the refusal's facts and "Here it is again -- tick what your statement shows." (**R-CC127**).
      The panel's two `Silent` sites are deleted. Closed **CC-378**. Left open: a `$0.00`-figure row
      ticked with its box cleared, uncaptioned (**balance:BAL-596**); two rows matched to one line
      ticked together, refused (**CC-384**); a captioned tick whose last purchase another tab
      deleted, saved uncompared (**balance:BAL-597**). **4a-5c-2**, split in three (the coordinator,
      2026-10-04): **4a-5c-2a** `f6de7772` (done, no `Ships:`) -- a statement screen never moves a
      payment between accounts (**R-CC137**, amending **R-CC43**): the reconcile panel and the
      statement matcher ask ONE predicate, `status_seam.payment_recorded_elsewhere_clause`, so a
      Projected row holding no purchase whose payment is recorded on another account is not offered
      on its own account's screen, and the matcher names no tender (`_moving._apply_day`), so 'Paid
      from' is the one door that moves a bill's payment. The screen names each such row in one
      sentence (**R-CC140**, replacing R-CC137's; `_subjects.HeldElsewhere.said`) at what pressing
      Paid records now (**R-CC139**, `_valuation.held_elsewhere_of`); applications under
      **balance:R-BAL207**: a `$0.00` row is not named, an unpriceable one is counted among the rows
      that could not be priced, a figure the owner typed is the figure named, only Projected rows
      are named, and "is not listed here" speaks of the match list. The panel's `$0.00` refusal
      reads as a deposit on money coming in (**R-CC136**, `_rows.ZERO_DEPOSIT_REFUSAL`). Three
      **R-CC43** tests rewritten under rule 5 (**R-CC138**), and a fourth deleted, the lane's
      reading of R-CC138's "Make the tests match the code" (only the declined ask named the
      deletion). Filing a purchase into a lump-paid envelope stays allowed (**R-CC141**), except
      where the owner-wide claims scan refuses it (**CC-385**). **4a-5c-2b** `2c7bb775` (done, no
      `Ships:`) -- **R-CC135**'s one check per save: a door whose page names lines, or that is
      silent by a ruling, opens ONE `match_press.Press` around its save, and a door whose page names
      nothing has each call open one of its own over `NOTHING_SHOWN`; each call refuses at once a
      line the page did not name, and the close compares what the whole save freed with what the
      page named (a `Silent` press compares nothing) and logs the withdrawal events. A save that
      reached no match step is graded only where its page promised what it named, the reconcile
      panel (`promised`; closed **balance:BAL-597**). The statement matcher keeps the default press:
      its accept frees nothing, and its undo frees no line another act holds, since an act that only
      matched creates nothing to remove and a purchase an act created becomes its envelope's last
      card purchase only by a CC tick, an edit the undo refuses. Its claims are the account's own
      acts and a destination is re-asked of its row as it stands, so filing into, and matching a
      purchase under, a reverted lump-paid envelope go ahead (**R-CC141**, **R-CC143**; closed
      **CC-385**). Applications under **balance:R-BAL207**: the `promised` rule, a door's own
      refusal abandoning its press, and carry-forward as one `Silent("CC-364")` press. Left open:
      **CC-384**, **balance:BAL-599** (events logged before the commit, `balance:X-dc`) and
      **balance:BAL-600** (BAL-597 at the single-row doors, `balance:X-da`). **4a-5c-2c**, split in
      two (the coordinator, 2026-10-05): **4a-5c-2c-1** (no `Ships:`) -- **CC-386** first, a writer
      enumeration and a deletion mutation (a deletion may need a developer ruling, as
      **bank_import:R-FY** names `_accept._reject_parent_and_its_own_purchase`); **R-CC135**'s
      per-match warnings on the reconcile panel, a shared act's line counting as named only when all
      its rows are ticked, closing **CC-384**, pinned by the ruling's own example. **4a-5c-2c-2**
      carries the `Ships:`: carry-forward's Confirm with its caption, post-back and redraw, its
      `Silent("CC-364")` press a promised `Shown` one, closing **CC-364**; the `Silent` sweep and
      the docstring rewrite.
- [x] **CC-5-4b** `30e7ddbb0` -- the "Paid from this account" list (`R-CC44`), the bill arm's second
      scope (`SETTLEMENT_ARM`) ticked through its settle: a tick posts `transaction_ids` /
      `settled_amount-<row id>` and neither template nor POST gained a field (`R-CC116`); "this
      card" is `account_projection.is_revolving`, the type's `has_revolving_credit` flag and the one
      card predicate, where an id compare would be a second (`R-CC117`, rule 14). The row's own list
      offered it, by analogy to `R-CC43`'s matcher, until **R-CC126** (**CC-378**).
- [x] **CC-5-5** `8f8b056d` -- `R-CC47` (re-scoping `R-CC41`; the sixth site `R-CC48`): every
      balance is what the account HOLDS, negative when owed, and owed is minus it; the DECOMPOSED
      parent, split 2026-09-22 (`R-CC50` as amended by `R-CC52`) into 5a, 5b and 5c, and given
      2026-09-23 5d (`R-CC69`..`R-CC73`); ticked with 5d.
  - [x] **CC-5-5a** `aa29d977` -- `balance_at.owed(balance) = -balance`, R-CC29's one flip moved
        into the seam (`card_statement.owed` deleted); the /savings revolving footer is each
        non-loan liability's owed amount floored at zero, summed (`R-CC49`);
        `tests/manual/verify_liability_screens.py`, the screen-diff instrument (178 responses plus a
        `tree.json` app digest) that grades 5b and 5c; 178 screens byte-identical on production's
        shape.
  - [x] **CC-5-5b** `3d9d0c1a` -- every liability balance door asks OWED and stores held through
        `liability_sign.held_balance` (`owed()` moved out of the seam; `R-CC52`): the create form
        (`R-CC58`), the anchor editor on every surface (`R-CC57` as amended by `R-CC60`), the
        books-opening card; a stale form refused and re-opened as a fresh click (`R-CC61`,
        `R-CC62`); no stored row re-signed, a loan's anchor cell a link (`R-CC53`). Two commits,
        `ef4f6782` first; closed **CC-357**; suite 15186/0 at `ef4f6782`.
  - [x] **CC-5-5c** `6daa3048` -- ONE commit (`R-CC50`): the configured-loan arms report HELD
        through `liability_sign.owed`; net worth the plain sum; band, trend, subtotal (`R-CC48`),
        tiles, debt summary, loan readers and archived drawer (`R-CC67`) read `owed`; the footer's
        words (`R-CC68`); an anchor save answers its opener's display (`R-CC74`, `R-CC77`, `R-CC78`)
        from the door's report (`R-CC79`, `R-CC85`). Rule-5 re-signs confirmed; closed **CC-354**,
        **CC-361**, **CC-362**, **CC-365**; suite 15323/0.
  - [x] **CC-5-5d** `8f8b056d` -- a goal on a DEBT is a milestone to get under (`R-CC69`..`R-CC73`):
        ONE goal door, `savings_goal_door.judge_goal_save` (`R-CC87`, `R-CC90`, `R-CC93`); every
        debt figure by the tile's one rule, `_tile.tile_balance_on` (`R-CC88`, `R-CC95`, `R-CC97`),
        a card-style goal's start recorded as `start_owed` (`R-CC91`); a Delete goal button
        (`R-CC94`); migration `764461215480`. Rule-5 re-expressions confirmed; closed **CC-360**,
        **CC-371**; suite 15368/0.
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
