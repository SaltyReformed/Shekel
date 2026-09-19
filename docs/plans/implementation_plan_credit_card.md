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
- [ ] **CC-3** `feat(cards): the statement cycle, pure, and the card's APR` -- design 3.4:
      `card_statement.py` (cycle window, statement sequence, due date, minimum payment, grace kept)
      with no Flask and no session, and the APR on `rate_history` behind a card-gated loader and
      write route the loan loaders never see.
- [ ] **CC-3i** `feat(cards): the finance charge under the one interest producer` -- design 3.6:
      `recurrence:R16-d`'s `accrued_interest` under the `DAILY_ACTUAL_365` member that step gains,
      summed per constant-balance segment of the closed-open cycle, purchases joining the path on
      grace loss (`R-CC19`: after `R16-d`; the card ships no convention table of its own).
- [ ] **CC-4** `feat(grid): the paycheck's plan items across checking and its cards` -- design 3.3
      (`R-CC16`): the DECOMPOSED parent, split 2026-09-18 (developer) into three leaves; ticks with
      4-3.
  - [x] **CC-4-1** `b4e35783` -- the money: `cash_flow_set.CashFlowSet` (ONE row clause with
        R-CC23's far leg), `resolve_cash_flow_set`, `active_accounts_query(revolving=)`,
        `GridColumn.elsewhere` and the seam taking the SET, "On other accounts" on three templates;
        no migration; byte-identical on production's shape (no card exists); suite 14603/0.
  - [ ] **CC-4-2** the affordances: an account chip on a cell or mobile card whose row is on another
        account than the balance line's; the account picker over the set's members on the
        full-create popover and the Add Transaction modal (default the balance line's; the hidden
        input for a set of one). Method: `HANDOFF-credit-card-CC-4.md` s.7.
  - [ ] **CC-4-3** the other three readers: the dashboard's upcoming bills
        (`_query_unpaid_expense_rows`), the spending report (`query_settled_expenses`, `_in_span`)
        and the calendar (`_query_transactions_for_range`) through the one predicate and clause, the
        report's and the calendar's default adopting the set's primary. Method: s.8.
- [ ] **CC-5** `feat(cards): a purchase is a movement on the card` -- design 3.2 (`R-CC15`): the
      settle-with-tender door (the movement's day, default the act's, basis `entered`, refused on or
      before the card's opening) and the card purchase entry; the parent-account key re-cut to a
      plain `transaction_id` FK (**CC-353**, renamed from BAL-506, closes with it); every reader in
      3.2's census re-pointed onto the movement's account, the entry reduction gaining its
      account-keyed arm BESIDE the flag's (`_amounts.py`, `_cash_leg.py`: the balance and
      bank-import lanes' files, coordinated at build); the ownership gate against the ROW's owner;
      the movement chip. The flag survives to CC-7.
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
- [ ] **CC-10** `feat(cards): the refusals` -- design 3.8: a transfer OUT of a card refused at both
      transfer doors (the sibling of the loan guard); `active_accounts_query`'s `revolving` filter
      shipped with CC-4-1.
- [ ] **CC-11** `feat(cards): the cockpit and the grid affordances` -- design 3.9, through the
      design loop: each screen held to `docs/design/fable5-design-language.md` and verified on the
      dev clone.

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
