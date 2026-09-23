> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is `docs/plans/steps.md`; the code as
> committed is the source of truth for what the app does.

# CC-5-1 and CC-5-2 as built: a movement's account is its own, and the first door that writes one elsewhere (2026-09-21)

Archived at the tick of the two leaves (5-1 `4045a9b1` the key; 5-2 `3ca97070` + `af3b9f5b` the purchase door,
its readers and the grid half) that opened the CC-5 family, shipped in ONE pull request under **R-CC33**. The
commits are the record; the build logs, the mutation harnesses and the adversarial reviews are
`~/projects/shekel-handoffs/HANDOFF-credit-card-CC-5.md` and `credit_card-2026-09-21/` ((1/2)'s nine
mutations are listed in `3ca97070`'s body). 5-1 carries
migration `9900b309f0b0` (on `45f10b870c8b`); 5-2 carries none; neither moves money. Below: the CC-5 entry as it
stood in `implementation_plan_credit_card.md` before the split (verbatim), the ledger row 5-1 closed, and the
developer's picked option texts for the seven rulings the leaves built to, verbatim -- the `rulings.md` rows
quote them in full; this record holds the old entries beside them.

## The CC-5 entry as it stood before the split, verbatim

- [ ] **CC-5** `feat(cards): a purchase is a movement on the card` -- design 3.2 (`R-CC15`): the
      settle-with-tender door (the movement's day, default the act's, basis `entered`, refused on or
      before the card's opening) and the card purchase entry; the parent-account key re-cut to a
      plain `transaction_id` FK (**CC-353**, renamed from BAL-506, closes with it); every reader in
      3.2's census re-pointed onto the movement's account, the entry reduction gaining its
      account-keyed arm BESIDE the flag's (`_amounts.py`, `_cash_leg.py`: the balance and
      bank-import lanes' files, coordinated at build); the ownership gate against the ROW's owner;
      the movement chip. The flag survives to CC-7.

## The `steps.md` row as it stood, verbatim

| arc | id | also | what this step does | order | commit | starts |
|---|---|---|---|---|---|---|
| credit_card | CC-5 | -- | Add the movement-on-card doors (settle-with-tender taking the movement's day, basis `entered`, refused on or before the card's opening; the card purchase entry), re-cut the parent-account key to a plain FK, re-point every reader in design 3.2's census onto the movement's account, and gate ownership on the ROW's owner (**R-CC15**). Closes **CC-353**. | #1 | -- | NOW / balance:X-bi-4 (shipped; both halves of the fork ruled on its entry: the key relaxed AND the fold's movement predicate) |

## Ledger row CLOSED at this tick (moved here under the same id)

**CC-353** CLOSED at `credit_card:CC-5-1` `4045a9b1`: `fk_transaction_entries_parent_account` and its
`ON UPDATE CASCADE` are dropped; a movement is held to its own account by `fk_transaction_entries_account_id`
and to its row's owner by `fk_transaction_entries_owner_transaction` / `fk_transaction_entries_owner_account`
(**R-BAL76**, **R-CC32**). The row as it stood:

| arc | id | also | finding (one line) | worst measured | status | owner |
|---|---|---|---|---|---|---|
| credit_card | CC-353 (`balance:X-bi-3c`'s adversarial review 2026-09-16) | renamed from BAL-506 2026-09-18: filed under balance while `X-bi-6` owned it, re-homed with its owner `CC-5` (conventions.md rule 10) | **THE `ON UPDATE CASCADE` ON `fk_transaction_entries_parent_account` HAS ONE BENEFICIARY, THE TRANSFER SHADOW** (migration `c4e8a2d7f1b3`, **R-BAL46**): it admits a parent-account move carrying ordinary purchases too, which the `NO ACTION` key refused and no writer performs (the maintain pass retains such a row, N-292); once `X-bi-6` deletes the shadow rows no parent's account can move and the cascade fences nothing | `$0.00`; a key stating a rule about an act the schema can no longer express | **OPEN, born with an owner.** Was `X-bi-6` restoring `NO ACTION`; since the 2026-09-18 re-mint `CC-5` re-cuts the key to a plain `transaction_id` FK (design 3.2), so the update rule goes with the composite key and the restore has no object | CC-5 |

## The rulings' picked texts, verbatim (the developer's words; `rulings.md` holds the condensed rows)

R-CC32 and R-CC33 were asked at CC-5-1 (2026-09-20), R-CC34 to R-CC37 at CC-5-2 (1/2) (2026-09-20), R-CC39 at
CC-5-2 (2/2) (2026-09-21). The rule-5 confirmation at 5-1 (*"Yes, re-express to R-BAL76"*: two cases of
`test_clearing_link_schema.py::TestAPurchasesAccountIsItsParents` re-expressed) took no id.

**R-CC32** -- picked *"Follow the row's own shape (Recommended)"*: "The movement-to-row owner key CASCADEs exactly like the existing `transaction_id` key beside it (the model's stated principle: two keys over one column never delete differently, or a delete's outcome depends on which PostgreSQL evaluates). Both account keys RESTRICT like `fk_transactions_owner_account`: an account holding any movement -- a card with purchases from checking envelopes -- cannot be hard-deleted, and the account delete door archives it instead (leaf 5-2 teaches that door to count movements on the account, not only rows). ON UPDATE stays plain NO ACTION on all three; the old cascade's one beneficiary, the transfer shadow, is already moved by hand (ledger row CC-353)." Rejected: the account keys CASCADE too (deleting a card would silently destroy checking envelopes' swipes).

**R-CC33** -- picked *"Stack 5-1 and 5-2, one PR (Recommended)"*: "5-1 is committed and pushed now as the leaf boundary (a fresh session builds 5-2 on the same branch), and the branch opens ONE pull request only when 5-2 is on it -- so the key never reaches dev ahead of the readers and the writer, which is R-BAL76's letter. Cost: 5-1's migration sits unmerged until 5-2 lands, so if another lane merges a migration first, this one is re-parented (the d4a92f6b13c8 precedent); the coordinator holds that." Rejected: merge 5-1 alone with a window-accepted ruling; fold 5-1 into 5-2.

**R-CC34** -- picked *"Hidden when one choice"*: "the add-purchase Account dropdown renders only when the owner's set (the row's account plus their active cards, CC-4-2's set) holds more than one account; with one, the form carries no account control and the door defaults to the row's account -- CC-4-2's `accounts|length <= 1` rule applied to the purchase picker, so every production form (0 cards) is byte-identical." Rejected: always show; show for any other cash account.

**R-CC35** -- picked *"Keep the checkbox as is"*: "the CC checkbox stays beside the dropdown until CC-7 deletes it; ticking CC and picking the card is refused by the service with a banner naming which to use; no interim show/hide logic." Rejected: hide on the add form when a card exists; hide on both forms.

**R-CC36** -- picked *"Retire it and re-point on re-settle"*: "for transaction definitions the account-move refusal goes -- a row holding records moves with its definition and its movements stay on their own accounts; the three tests re-expressed; the seam's re-settle branch sets a kept payment record's account to the row's (the line CC-5-3 writes for "no tender named")." Rejected: retire except for a kept record; keep until CC-5-3.

**R-CC37** -- picked *"The picker's set, plus the row's own"*: "a purchase may name its row's own account, or any member of the owner's cash-flow set (the primary grid account plus active cards) -- the SAME predicate the picker renders from, so the door and the form agree by one rule and a swipe on a 401(k), IRA, house or savings account is unwritable, not merely unoffered; a row that LIVES on such an account keeps its purchases there." Rejected: any account whose balance folds movements (PLAIN/INTEREST); keep as built and fix the sentence.

**R-CC39** -- picked *"Union (Recommended)"*: "A purchase may name its row's own account (preselected) or any member of the owner's cash-flow set (primary checking plus active cards), in that order. One rule, no branch on where the row lives. The dropdown appears only when there is more than one option. This is R-CC37 as written and the door as built at (1/2)." Rejected: "Own + cards" (own account or an active card, never primary checking unless it is the row's own; a Savings envelope with no card shows no dropdown; the door narrows); "Own only outside the set" (a row on checking or a card offers the set; a row on any other account offers only its own and shows no dropdown; the door narrows).

## The two rulings asked at CC-5-3's entry (2026-09-21 08:2x, the card lane), verbatim

**R-CC40** -- picked *"Card's screen only, two halves (Recommended)"*: "The most robust and future-proof: ONE rule, money is read where it moved, for the fold, the ledger, the panel and the matcher alike; no cross-account link, no allocation table, one bank line to one movement. HALF 1 lands in 5-3 (~10 lines: the price producer in status_seam and the matcher's three readers): on an account's screen a settled row is worth what its payment moved ON THAT ACCOUNT, zero when it moved elsewhere, so the grocery bill leaves checking's screen and a stray $120 there stays unexplained until you record it. HALF 2 is its own leaf right after 5-3 (bank-import's files: candidates, moving, accept, the accepted register, undo; the card's reconcile panel gets the same admission): the payment movement is the CARD screen's candidate for GROCERIES -$120, matched as a movement member, dated through the bill's own door because the movement follows the row. Worked: accept dates the bill by the card's line; the card's actual equals the bank; nothing counted twice. No card import exists yet, so nothing is lost provided half 2 lands before the card import does." Refused: *"Half 1 now; half 2 rides the card import"* (half 2 stays with the design's card-on-the-feed leaf, minted when the card import is built); *"Nothing in 5-3; both with the card import"* (the matcher untouched: the bill offered on checking's screen at -$120, a stray-$120 pairing hides a real outflow, the card's line has no candidate; CC-5's steps.md sentence false at the tick); *"Both screens"* (matching on checking books money against a statement that never showed it, and the card-side row member is unwritable under `fk_statement_match_members_transaction_account`).

**R-CC41** -- picked *"Its own leaf under CC-5 (Recommended)"*: "A sign fix across five sites with a stated invariant between them (the trend's index 0 reconciles to the hero 'by construction', so one site alone breaks it): one subject, one review, $0.00 on production. Minted after 5-3 (and after the matcher's half 2 if that is minted too); the coordinator ranks it and mints its id." Refused: *"CC-5-3 takes it"*; *"CC-11, the card cockpit"*; *"Another step"*.

The developer's context, in his words at the restatement he asked for: "All the charges to my checking account SECU to my credit card Capital One are held until the credit card account is built and ready." "My expectation is that the app could track payments from checking to the credit card as a transfer to pay off the credit card. I would hope that the app could handle applying payments to the oldest purchases first which I believe is how my real credit card works. The wrinkle in this is I don't always pay off the card in amounts that exactly match the purchases. For example, I make $120 and $450 purchases with the credit card, then I pay $300 towards the credit card in one paycheck and the remaining $270 out of the next paycheck. I have only built an import for checking and don't have one for the credit card yet but I plan to do so." The lane answered from the ruled design (no per-purchase allocation, R-CC18 / R-CC22) and he picked the recommended option with that stated.

## CC-5-3 `9a74658f`, ticked 2026-09-21 (its specification as it stood, then R-CC42 verbatim)

- [ ] **CC-5-3** `feat(cards): the settle-with-tender door` -- design 3.2: `Settlement` gains the
      tender account (default the row's expectation, else its own; a statement-driven settle forces
      the statement's account); `_cover` / `_record_onto` write the covering movement onto the
      tender on the movement's day, basis `entered`, refused on or before the card's opening;
      `_mirror_assertion` and `record_clearing` stop copying the row's clearing link onto a movement
      on another account; the full-edit popover's settle section and `mark_done`'s form gain the
      "Paid from" picker, both reading `purchase_accounts` (`R-CC39`: one function, three doors);
      undo is the ordinary revert. HALF 1 of `R-CC40` (ruled at this leaf's entry):
      `status_seam.covered_cash_leg` takes the account and the matcher's three readers
      (`_candidates`, `_accepted_view`, `_release`) pass the statement's, so on an account's screen
      a settled row is worth what its payment moved ON THAT ACCOUNT, zero when it moved elsewhere.

| credit_card | CC-5-3 | -- | Add the settle-with-tender door: `Settlement` gains the tender account, the seam writes the covering movement onto it on the movement's day, basis `entered`, refused on or before the card's opening; the popover's settle section and `mark_done` gain a "Paid from" picker reading `purchase_accounts` (**R-CC39**); on a screen a settled row is worth what its payment moved there (**R-CC40**, half 1). | #3 | -- | NOW / credit_card:CC-5-2 (shipped; the door and the picker it reuses) |

**R-CC42** -- picked *"Retained across a revert (Recommended)"*: "From scratch: where the money moved is part of the record and survives a revert exactly as a stated figure does. A re-settle that names no account keeps the kept payment's account; naming one re-points it; every door reads the record: the popover's 'Paid from' shows the kept account selected, and the one-click checkmark and mobile Mark Paid pass nothing, so the seam keeps it. One default, spelled once in the seam. AMENDS R-CC36's re-point clause: a Projected row whose definition moved keeps its kept record where the money moved, and only a named tender moves it (that test is re-expressed to say so). Worked: the hotel's $120 stays on the card after revert, move and checkmark." Refused: *"R-CC36's letter: none named = the row's"* (the seam re-points a kept record onto the row's account whenever no tender is named -- the line 5-2 wrote; the one-click re-settle of the reverted hotel bill books $120 on Checking: card $120 low, Checking $120 high, the card's line unmatched -- unless the popover is opened and the card re-picked; the definition-move case keeps booking on the new plan account); *"Hybrid: seam per R-CC36, doors name the kept account"* (every door posts the kept record's account; the default spelled at the doors AND the seam, rule 14's two homes); *"Store whether the tender was picked"* (a column beside the movement's figure source; a migration and a second echo rule at the door). Worked example given: a $120 hotel bill on Checking, marked Paid from the card on 9/22 (the $120 movement on the card); revert to move it to next paycheck, move it, click the cell's checkmark (one click, no picker). Retained: still on the card. R-CC36's letter: silently onto Checking. The wrinkle stated: R-CC36 was ruled for a definition move (Checking -> Second Checking) over a reverted row holding a kept record; nothing stored says whether an account was PICKED or merely defaulted, so retention flips that case. CONSEQUENCE (rule 5, confirmed by his option text): `tests/test_services/test_covering_movement.py::test_a_re_settle_re_points_the_kept_movement_onto_the_rows_account` (R-CC36's control) is RE-EXPRESSED as `test_a_re_settle_keeps_the_kept_movement_where_the_money_moved`, his text quoted in its docstring: after the definition move the re-settle keeps the movement on Checking; a re-settle NAMING Second Checking moves it. R-CC36's row takes an "AMENDED 2026-09-21 BY R-CC42" head on its re-point clause; its retire-the-retention clause stands.

## CC-5-4a-1 `079524b0` (+ `e8ba69d6`, the rule-5 docstrings), ticked 2026-09-21: CC-5-4's specification as it stood, then the six rulings and the confirmation VERBATIM (the card lane's record, `rulings_cc5_4.md`)

- [ ] **CC-5-4** `feat(cards): the card's line meets the bill it paid` -- design 3.2 and `R-CC40`'s
      HALF 2: the payment movement is the CARD screen's candidate for the bill (`statement_match`
      candidates, moving, accept, the accepted register, undo -- bank-import's files,
      announce-first; the card's reconcile panel's purchases arm gets the same admission), matched
      as a MOVEMENT member and dated through the bill's own door because the movement follows the
      row; accept dates the bill by the card's line, the card's actual equals the bank, nothing is
      counted twice. Must land before any card import exists.

| credit_card | CC-5-4 | -- | Offer the payment MOVEMENT on the CARD's screen as the candidate for the bill it settled, matched as a movement member and dated through the bill's own door (`statement_match` candidates, moving, accept, the accepted register, undo; the card's reconcile panel's purchases arm), so a card-settled bill reconciles on the card's statement, never checking's (**R-CC40**, half 2); before any card import. | #3 | -- | NOW / credit_card:CC-5-3 (shipped; the movement it offers and the readers it re-keys) |

## Ruling 1 (Q1, "Subject shape") -- what the matcher's subject IS

Picked: **"B now, in this leaf"**

> The universal subject in CC-5-4: the kind, the migration re-keying every row member onto its payment,
> the census, the register/undo/claims/proposer rewrite. Its own PR and release (it rewrites accepted
> acts). Collides with bank-import's held branch across 18 files.

The option B it names, as stated in the question: "the MOVEMENT is the subject of EVERY settled match on
every screen; a row is a candidate only while Projected. It deletes A's fence, the settled arm of the
row-pricer and three readers of covered_cash_leg. It needs a migration re-keying every accepted match's
row members onto their payments (221 acts on production at the 08-27 count) plus a census for rows
holding none, and it rewrites the register, the undo, the claims and the proposer's whole-row set:
bank-import's files, 18 of which its held branch edits; it also survives X-bi-6-4's re-parenting (a
payment member outlives a shadow-row member)."

Refused: *"A now, B minted later (Recommended)"* (the SETTLEMENT kind admitted only where the movement is
and the row is NOT; B minted as a bank-import follow-up that deletes that fence); *"A only; B is not the
destination"* (the two-armed rule -- row on its own screen, payment elsewhere -- as the design);
*"Refuse: re-open the design"*. Rejected in the question's text: reuse PURCHASE with a covers_settlement
flag (a proxy beside a kind); a TRANSACTION member on the card (R-CC40 refused "Both screens"; the key
refuses it).

Facts stated in the question, verified at 739288bb: the matcher's two kinds conflate two facts (which
table the member names; whose record the figure / clock / door are); a covering movement has the ENTRY's
identity and the ROW's record; fifteen kind branches (`_offers`, `_landing`, `_dating`, `_pairing`,
`_propose`, `_verdict`, `_variance`, `_moving`, `_accept`, `_resolve`, the review template's token); a
third kind dropped in blind makes `_moving._apply_day`'s else-arm `db.session.get(Transaction, <entry
id>)`.

Worked (given for all options): GROCERIES $120 on Checking, paycheck 9/12-9/25, marked Paid from the
card on 9/22; the card's feed posts GROCERIES -$120 on 9/24. On the card's screen the payment is offered
at -$120 with window 9/12-9/25, so 9/24 pairs; accept moves the bill's day to 9/24 (observed) and the
payment follows; the card's fold reads -$120 on 9/24, Checking reads $0, nothing twice. REVERTED CASE,
also admitted: revert the bill to move it to the 9/26 paycheck; its $120 payment is KEPT on the card,
un-dated (R-CC42). The card's screen offers that kept payment at what re-settling would book ($120),
window = the new paycheck; accept re-settles the bill on the card at 9/24. Checking's screen ALSO offers
the reverted bill (Projected, on Checking); whichever is accepted first wins, the other re-prices to $0
and is refused.

### Ruling 2 (Q2, "Panel") -- the card's reconcile panel

Picked: **"P1: a fourth panel arm, own leaf (Recommended)"**

> CC-5-4b: settlements arm in reconcile_service, ticked through the bill's door on the asserted day with
> this account as tender; the payment takes the statement's link; record_clearing takes the statement's
> account. Built after 5-4a, a separate session.

The P1 it names, as stated: "a FOURTH arm of the panel, settlements: un-dated payments on this account
whose bill is on another; listed under the bill's name in its paycheck block; ticked through the bill's
door (settle on the asserted day, tender = this account); the PAYMENT takes the statement's link and the
bill does not (a bill's link is held to the bill's own account), which means the seam's clearing writer
learns which account's statement it is recording. Its own leaf (CC-5-4b): the panel is
balance/bank-import's package and the template and POST gain a field."

Refused: *"P1, but inside CC-5-4 as one leaf"*; *"P3: no panel admission; amend R-CC40"* (the card's
panel never lists a payment; Checking's panel keeps offering the reverted bill; R-CC40's "same admission"
clause struck); *"Refuse: re-open the design"*. Rejected in the text: P2, widen the purchases arm and
split its writer (two writers inside the arm whose whole character is one bulk UPDATE).

Facts stated: the panel lists only UN-DATED things (purchases `settled_on IS NULL`; rows Projected) on
THIS account; a card-paid bill is dated at the settle, so the ONE state the card's panel can meet is the
REVERTED card bill (kept, un-dated payment on the card); the purchases arm's writer is a bulk UPDATE of
the day pair + link (`_purchases.record_settled_days`), which on a payment would date the seam's mirror
while its bill stays Projected; `fk_transactions_reconciled_by` is composite over the row's account.

Worked: the reverted GROCERIES bill, $120 kept on the card; the card's balance asserted for 9/30. P1: the
panel lists "Groceries (Checking's plan, paid from this card) $120"; tick it and the bill settles on the
card by 9/30 (a bound), the payment carries the 9/30 statement's link, and the card's books-vs-bank
difference closes by $120. P3: the panel lists nothing; the card's difference stays $120 off until the
bill is marked Paid on the grid, and then the panel still never asks about it.

### Ruling 3 (Q3, "Leaf split") -- B's two halves, and the member table's bill column

Picked: **"Two leaves, two PRs (Recommended)"**

> CC-5-4a-1 = the writer, this session, no migration, rides the next dev-to-main. CC-5-4a-2 = the re-key
> migration + the bill column dropped, next session, its own PR and release, graded on production's
> shape first. CC-5-4b (the panel arm) after both.

The two halves as stated: HALF 1, the WRITER: a settled bill is offered as its payment on whichever
account the payment is on; a Projected bill is offered as itself; when a match settles a Projected bill,
the member recorded names the payment the settle just wrote, never the bill; every new match names
movements only; the register, the undo, the claims and the proposer read BOTH member shapes until half 2;
no migration. HALF 2, the STORED PAST: a migration re-keys every existing bill member (221 accepted
matches on production at the 08-27 count) onto that bill's payment, after a census on the newest
production dump; a member with no payment to re-key onto (a bill closed from its purchases, a $0.00
close, a Credit or Cancelled bill matched before today's refusals existed) REFUSES the migration rather
than being guessed at, and the developer rules what happens to it if the census finds one; then the bill
column on the member table goes, with its key and its unique index (the member is a bank line or a
movement, nothing else) and the six readers of a bill member (the register, the undo, the claims, the
withdrawal on delete, the bank agreement) lose that arm; its own PR and release.

Refused: *"One leaf, one PR"*; *"Two leaves, but keep the bill column"* (half 2 leaves
`statement_match_members.transaction_id` in place, nullable and unwritten -- a stored home with no
writer); *"Refuse: re-open the design"*.

Stated for the record (the coordinator's ask): a 4a-only release, before 4b: the card's statement screen
offers the $120 payment and matches it; the card's reconcile panel does not yet list the ONE state 4b
covers (a reverted card bill's kept payment); Checking's panel keeps listing that reverted bill as a
Projected row. Coherent, with a stated gap.

### Ruling 4 (Q4, "Finding") -- the card screen's create arm

Picked: **"File it, owner = the card import leaf"**

> A ledger row owned by the step that builds the card import (the bank-side leaf design 3.2 names);
> nothing built now.

The finding as stated: on the CARD's statement screen today, a card line cannot be recorded into a
Checking envelope: the destinations offered are only envelopes ON the card (`_destinations.py:182`
scopes by `Transaction.account_id`; none exist), and the door that records a purchase from a line names
no account (`_create._born_purchase` -> `entry_service.create_entry` with no `account_id`), so the
purchase would take the envelope's account (Checking) while the member key
(`fk_statement_match_members_entry_account`) holds it to the card, a refusal. A NEW envelope minted from
the card's screen is placed ON THE CARD (`_container.py:416`, `account_id=scope.account_id`), so its
budget would sit on the card rather than on Checking's paycheck grid. Design 3.2's bank-side row names
this; the card import (not yet built) is what would hit it.

Refused: *"File it, owner = CC-5-4b"*; *"File it, owner = a new CC-5 leaf"*; *"Decline: not a finding"*.

### Ruling 5 (asked during the build after 12:49 EDT, recorded 13:42 EDT, "Re-point vs match") -- a matched payment's account change

Picked: **"W: withdraw and disclose (Recommended)"**

> A payment's account change withdraws any match naming it, through match_withdrawal, disclosed at the
> door as a delete is. Applied to the tender re-point and the transfer endpoint move in this leaf; the
> row/definition doors reported, not fixed.

Refused: *"R: refuse while matched"* (the seam refuses a re-point of a matched payment with a sentence
naming the match and its Undo; the endpoint move refuses the same way; nothing withdrawn without his
hand); *"W here; the other doors too"* (as W, and the leaf also wires the withdrawal into the
definition-move and any other door that changes a matched subject's account, after a census);
*"Refuse: re-open the design"*. Listed as impossible in the text: *"K: keep half 1's register reading"*
(a payment member cannot survive the account change; the member key holds it, NO ACTION on update).

Facts stated in the question: under R-CC43 every accepted match names the PAYMENT; the member table
holds a payment member to its account by `fk_statement_match_members_entry_account` and the database
refuses an UPDATE of the payment's account while the member exists; under half 1 (CC-5-3) the member
named the BILL, which stayed on Checking, so the re-point went through and the register showed the match
"no longer holding" at $0.00 with its Undo -- structurally unreachable for a payment member. The same
key shape holds a bill member to the bill's account and a purchase member to its account, so a matched
Projected bill whose definition moves accounts (the R-CC36 case) and a matched transfer leg whose
endpoint moves already hit the database the same way before this leaf (the endpoint move's own docstring
names `fk_transaction_entries_reconciled_by` refusing a cleared leg, ledger row BAL-503).

Worked: GROCERIES $120 on Checking, marked Paid from Checking on 9/22; Checking's feed shows GROCERIES
-$120 on 9/24; matched (the match names the $120 payment on Checking, dated 9/24); later "Paid from: the
card" is picked on the popover. W: the correction goes through; the match is WITHDRAWN and disclosed
("this frees 1 bank line: 9/24 GROCERIES -$120"); Checking's 9/24 line is unexplained again and the
payment is on the card, offerable on the card's screen. R: refused with a sentence naming the match and
its Undo.

### Ruling 6 (asked and recorded 13:42-13:44 EDT, "Endpoint move") -- R-CC46's second door

Picked: **"BAL-503 clause; X-bi-6-4 owes it (Recommended)"**

> R-CC46's endpoint-move half is recorded as a clause on ledger row BAL-503 (the member key beside the
> link), owned by X-bi-6-4; this leaf wires the tender re-point only and reports the endpoint move.

Refused: *"This leaf edits transfer_service now"* (CC-5-4a-1 wires the withdrawal into
`_apply_endpoint_move` today, announced to balance first; a merge conflict with X-bi-6-3 likely);
*"Refuse: re-open the design"*.

Facts stated: `transfer_service._endpoints._apply_endpoint_move` already refuses at the database when a
payment carries a statement link (`fk_transaction_entries_reconciled_by`, its own docstring; ledger row
BAL-503, owned by balance:X-bi-6-4); under R-CC43 a MATCHED leg's payment refuses the same way
(`fk_statement_match_members_entry_account`); transfer_service is the balance lane's live file this hour.

Worked: the mortgage transfer settled, its Checking leg matched to Checking's 9/1 line; the source
re-pointed to Second Checking. Before this leaf: refused at the database if the leg was cleared through
the panel (BAL-503); under this leaf also refused when matched. Under the clause: the refusal stays
and X-bi-6-4 owes the designed treatment (withdraw and disclose) when it re-parents the record.

### Confirmation 7 (asked and recorded 14:5x EDT, "Rule 5") -- the re-expressed tests

Picked: **"Confirm A, B and C as rule-5 re-expressions under R-CC43 (Recommended)"**

> The member names the payment; a day moves through the seam that mirrors it; a settled row is
> ticked as its payment. Each docstring quotes this confirmation; the commit stands.

Refused: *"Confirm B and C; keep A as written"* (the four member-shape tests back to a ROW member,
failing against the ruled code -> stop and re-open R-CC43); *"Confirm A and C; keep B as written"*
(the two raw row-column writes back, failing and pinning a state no door produces); *"Refuse:
re-open"*.

The groups as stated: A (4 tests: test_accept "the match is recorded with one member per subject",
test_income "it records a match naming both", test_residual "it is recorded as a member" and "the
members sum to the lines after an attributed match": the app-side member names the bill's PAYMENT,
not the row); B (2 tests: test_accept "a hand-moved day stops it agreeing", test_release "an act that
no longer holds is shown however old": the day moves through the row's own door, which mirrors it
onto the payment the act names); C (the builder `a_submission` resolving a settled row to its
payment; four window cases in test_candidates asking the payment; the token round-trip staging a
settled row; two pane ids in test_statement_reconcile; three CC-5-3 cases). Stated in the question:
"a row whose REVISION moved is refused" keeps its raw write (the payment's token carries the row's
counter since the review's L2), nothing to confirm.

## CC-5-5a `aa29d977`, ticked 2026-09-22: CC-5-5's specification, its `steps.md` row and ledger row CC-354 as they stood, then Blocks 1-6 VERBATIM (the card lane's record, `rulings_cc5_5.md`)

- [ ] **CC-5-5** `fix(cards): a card in credit is the issuer owing` -- `R-CC41`: the five `abs()`
      sites (`balance_at/_liability.py` `_spliced_owed_series` and `liability_owed_at_dates`,
      `savings_dashboard_service/_net_worth.py`'s hero and trend series, `_debt_line.py`'s
      no-payoff-model debt total) as ONE subject, the trend's index 0 still reconciling to the hero
      by construction; `$0.00` on production (no card). Closes **CC-354**.

| credit_card | CC-5-5 | -- | Fix the five net-worth sign sites (`_liability.py`'s spliced owed series and `liability_owed_at_dates`, `_net_worth.py`'s hero and trend, `_debt_line.py`'s debt total) as ONE subject so a card in credit reads as the issuer owing and the trend's index 0 still reconciles to the hero (**R-CC41**); `$0.00` on production. Closes **CC-354**. | #5 | -- | NOW |

| credit_card | CC-354 (`credit_card:CC-3`'s trace 2026-09-18; the cut-b review's census the same night) | -- | **THE NET-WORTH SURFACES REPORT A CARD IN CREDIT AS OWED.** Five sites take `abs()` of a plain liability's fold: `balance_at/_liability.py:80` (`_spliced_owed_series`) and `:218` (`liability_owed_at_dates`), `savings_dashboard_service/_net_worth.py:291` (`compute_net_worth_today`, the hero) and `:370` (the trend series), `_debt_line.py:261` (the no-payoff-model debt total) -- and `_liability.py:137-140` states the band's index 0 reconciles to the hero "by construction", so a fix at one site alone breaks that. A card the issuer owes `$50.00` (a credit balance; the fold reads +50.00) reads `$50.00` OWED where `-$50.00` is true; CC-3's statement module keeps the ONE sign flip **R-CC29** rules (owed = minus the fold) and reads none of these | `$0.00` on production (no card exists); on a card in credit every surface reads net worth low by twice the credit | **OPEN, born with an owner** (developer 2026-09-18, ruled in the coordinator session) | CC-5-5 (developer, 2026-09-21: its own leaf under CC-5, **R-CC41**) |

### Block 1 -- R-CC47 (Q1, the design)

Question as asked (verbatim):

> CC-5-5 (the card-in-credit sign fix): the net-worth screens take abs() of every liability because
> the balance code reports in two signs. A loan with its terms set up reports what you OWE as
> positive (Mortgage +$176,719.77); every other account, cards included, reports what it HOLDS (a
> card owing $1,000 is -$1,000; a card holding a $50 credit is +$50), so abs() counts that $50
> credit as $50 owed. Measured on your data plus a hypothetical Visa owing $1,000, Amex with a $50
> credit, auto loan with no terms owing $5,000 and family loan owing $2,000: the hero says
> liabilities $199,515.28 and net worth $227,242.71; the truth is $199,415.28 and $227,342.71 (low
> by $100). Production is $0.00 either way (your two liabilities are set-up loans, never negative).
> Every option gives the true figures; which design?

PICKED: **"A: one sign, re-scoped now (Recommended)"**:

> Every balance is what the account holds, negative when owed; a set-up loan reports -$176,719.77
> like a card. 'Owed' is minus the balance, R-CC29's one flip moved into the balance seam. Net
> worth is the plain sum of all balances with no sign branch left; the five abs() and the subtotal
> defect go. Every loan screen and liability tile shows what is owed, so production's screens do
> not change (a hypothetical card tile would read $1,000 where it reads -$1,000 today). CC-5-5 is
> re-scoped to this: more than one leaf, the loan path re-signed, about 5 loan-balance readers
> moved onto 'owed', and every loan test figure that flips sign is a rule-5 re-expression you
> confirm. I propose the leaf cut before building.

REFUSED:

- "C now, A as its own later step": "CC-5-5 ships C now: the seam publishes one 'owed' figure whose
  rule knows which calculation reports which sign (a set-up loan as is, everything else minus). The
  five sites and the subtotal read it, and tiles are unchanged. A is minted as its own later step
  that re-signs the loan path and deletes C's rule. Faster to ship, but it builds scaffolding that is
  planned for deletion."
- "B: one sign in the net-worth view": "Cards and liabilities without loan terms flip once inside the
  seam's net-worth view, so every liability reads as owed there; loans are untouched. Same screens
  as A and a smaller change. But a card's seam balance (+$1,000) then disagrees in sign with its own
  grid (-$1,000), and every future sum across account types must remember to treat liabilities
  differently."
- "C only, no later step": "The seam's one 'owed' rule that knows which calculation reports which
  sign, with no plan to unify the two signs. Smallest change. The two signs stay for good, so the
  next screen that adds raw balances repeats the subtotal bug."

Specifics in the picked text that the reader census must CONFIRM or CORRECT before the cut is
approved (coordinator, 2026-09-22): "about 5 loan-balance readers" and "production's screens do
not change".

### Block 2 -- R-CC48 (Q2, the sixth site)

Question as asked (verbatim):

> CC-5-5 found a sixth site the ruling did not name: on /savings, the liability group subtotal
> (which the chart legend prints beside the liability band) adds liability balances raw, with loans
> positive and cards negative. With a hypothetical Visa owing $1,000, Amex with a $50 credit, auto
> loan with no terms owing $5,000 and family loan owing $2,000 beside your two loans, it reads
> $183,515.28 while the band beside it reads $199,515.28 (the truth is $199,415.28). It is $0.00 on
> production, where both liabilities are loans. Fix it in CC-5-5?

PICKED: **"Yes, same subject (Recommended)"**:

> It is the same two-sign defect on the same screen, and the legend labels the band the hero
> reconciles to. Designs A and B fix it with the same change; C needs one line reading 'owed'.

REFUSED: "No, file it separately": "CC-5-5 stays at the five named sites. The subtotal becomes its
own ledger row with an owner, and the coordinator grants the id."

The site: `app/services/savings_dashboard_service/_display.py` `_compute_group_subtotals` (sums
`ad.current_balance` per category group; its docstring claims "Liability groups sum the loan
resolver's positive owed balances", false for any non-loan liability).

### Block 3 -- R-CC49 (Q3, the revolving footer)

Question as asked (verbatim):

> The /savings footer 'Loans paid off <month>' adds 'excludes $X revolving' for liabilities with no
> payoff date (cards, and liabilities without loan terms). With a Visa owing $1,000, an Amex
> holding a $50 credit, an auto loan with no terms owing $5,000 and a family loan owing $2,000, it
> says $8,050 today, counting the Amex credit as debt. Once the sign is fixed, what should X be?

PICKED: **"$8,000: floored per account (Recommended)"**:

> Each account's owed amount, floored at zero, then summed. A credit on one card is not debt and
> does not pay down another card; the footer counts the debt a payoff date leaves out. This is the
> same zero floor R-CC29 puts on the statement minimum.

REFUSED: "$7,950: net of all of them": "The Amex credit offsets the debts, the way net worth counts
it. Whenever a card holds a credit, the footer then understates the debt that has no payoff date."

### The measurements the questions quoted (HEAD 4d97749e, clone shekel_cc55)

- Production: 9 accounts; 2 liabilities, both CONFIGURED loans (Mortgage id 3, Van Loan id 8).
  Current 176,719.77 / 14,745.51; dense map over 64 periods min 169,499.28 / 3,026.43; the forward
  fold at every horizon sample (25, to 2049-12-31) and on EVERY day for 40 years: min 0.00, zero
  negative days. So every option is $0.00 on production's net-worth figures.
- Production as is: hero liabilities 191,465.28, net 235,292.71; 2y band and Horizon index 0 equal
  the hero; legend subtotal 191,465.28; revolving footer 0.00.
- Plus the four hypothetical liabilities (rolled back): hero 199,515.28 / 227,242.71; 2y band and
  Horizon index 0 199,515.28; legend subtotal 183,515.28; footer 8,050.00; tiles Visa -1,000.00,
  Amex 50.00, auto loan -5,000.00, family loan -2,000.00, Mortgage 176,719.77, Van Loan 14,745.51.
- Baseline blob for the byte-identical grade: `verify_savings_producers.py` on HEAD 4d97749e
  against shekel_cc55 -> `credit_card-2026-09-22/cc55/before_head.json` (77,009 bytes, 2 users).

### Block 4 -- R-CC50, the LEAF CUT (asked ~17:5x 2026-09-22, after the reader census)

Question as asked (verbatim):

> CC-5-5 leaf cut (your ruling R-CC47: one sign, every balance is what the account holds, 'owed'
> is minus it). The census CORRECTS two specifics you ruled: the loan-balance readers moving onto
> 'owed' are 7, not 'about 5' (debt strategy; the loan pages' one balance property feeding the
> dashboard and payoff/refinance calculators; home equity; on /savings a liability tile's figure,
> its sparkline, and two debt-summary figures). 'Production's screens do not change' holds by
> census, and a committed before/after screen diff will grade it. Order: CC-5-5 goes BEFORE
> recurrence R16-c-2 (#5 vs #23; it moves no money and does not touch the loan stream R16-c-2
> rewrites). The sign flip must be ONE commit: flipping loans before /savings moves breaks the
> subtotal, tiles and debt figures, and moving /savings first needs the per-account-type rule you
> refused. Which cut?

PICKED: **"5a groundwork, then 5b flip (Recommended)"**:

> CC-5-5a: R-CC29's one flip moves from card_statement into the balance seam, and the statement
> reads it (byte-identical); the footer becomes each non-loan liability's owed amount floored at
> zero, summed (R-CC49; those accounts already report held, so no loan re-sign; $0.00 on
> production); a screen-diff instrument over /savings, the loan dashboard and its calculators, and
> /debt-strategy on the clone, baselined on HEAD. CC-5-5b, one commit: the loan path reports held,
> the hero, trend, band and subtotal read the sum of balances and 'owed', the 7 readers move onto
> 'owed', liability tiles show owed, tests are re-signed after you confirm the classes with counts,
> and 5a's diff must read byte-identical on production's screens.

REFUSED: "One leaf, no container": "All of CC-5-5a and CC-5-5b in one commit and one PR: the flip's
move into the seam, the footer floor, the screen-diff instrument, and the sign flip with every
reader. Fewer ticks, but the grading instrument is not committed and baselined ahead of the change
it grades, and the single session carries both."

### Block 5 -- ledger row CC-357 (owned by credit_card:CC-11), the card tile's editor (asked with Block 4)

Question as asked (verbatim):

> A side effect of your ruling that liability tiles show what is owed: on /savings a card or
> custom-liability tile would read $1,000 (owed) while its click-to-edit anchor editor still
> pre-fills and takes the held figure, -$1,000.00. Loan tiles are unaffected, since the editor
> refuses every loan type and they stay read-only. Production has no such account, so this is
> latent. How should CC-5-5 handle it?

PICKED: **"Report it for CC-11 (Recommended)"**:

> CC-5-5 leaves the anchor editor alone and files a ledger row (id from the coordinator) owned by
> CC-11, the card cockpit and grid affordances step, which designs that editor. The editor is a
> WRITE door; changing the sign it takes is a behaviour change that belongs with the step designing
> it.

REFUSED: "Fix it inside CC-5-5b": "The anchor editor on a liability shows and takes what is owed,
flipping once at the door through the same 'owed' function. This widens CC-5-5b into a write door
(the true-up), so a sign mistake there would record a wrong balance assertion."

Facts behind Block 5 (measured on HEAD 4d97749e): `routes/accounts/anchor.py` `anchor_form` refuses
every AMORTIZING account (`_anchor_kind_refusal`), so loan tiles, configured or not, are read-only;
it pre-fills `cash_ledger.resolve_anchor(account).balance`, the HELD figure, for a card or a custom
(non-amortizing) liability.

### Block 6 -- R-CC52, THE TYPED SIGN (asked ~18:3x 2026-09-22 after 5a's adversarial review, finding F1). AMENDS R-CC50 (the coordinator files R-CC50 with an "AMENDED 2026-09-22 BY R-CC52" head); CC-357's owner moves CC-11 -> CC-5-5b; names confirmed CC-5-5a / 5b (the doors) / 5c (the flip); next free card R-CC53 / CC-360

Evidence (measured on clone shekel_cc55 / HEAD 4d97749e before asking): `templates/accounts/form.html:60-68`
"Opening Balance" help text "The account's real-world balance." (no sign guidance);
`budget.account_openings`: Mortgage (id 3, configured) +174,281.51, Van Loan (id 8) -531.94;
`budget.account_anchor_history`: Mortgage +178,103.41 (2026-05-01), Van Loan 0.00;
`tests/test_services/test_balance_at.py` builds a "Terms Never Entered" Mortgage at +150,000.00 and
one test says the cash producer "owes exactly the anchor".

Question as asked (verbatim):

> CC-5-5's review found that the premise of your ruling R-CC47 ('every balance is what the account
> holds, negative when owed') is not enforced where balances are TYPED. The account create form asks
> 'Opening Balance: the account's real-world balance' with no sign guidance, and your own Mortgage
> was typed positive (+$174,281.51 opening, +$178,103.41 anchor) while the Van Loan's opening is
> -$531.94; the suite's 'terms never entered' Mortgage is +$150,000 and a test calls that owed. Cards
> are ruled negative-when-owed (R-CC14/R-CC29), but a loan without terms or a custom liability
> carries whatever sign the owner typed; abs() was hiding that. Worked case: an owner adds 'Car
> loan' (terms not entered) and types 5,000.00. Today it counts as $5,000 owed; once R-CC47's flip
> lands it counts as a $5,000 ASSET, net worth off by $10,000. Production has no such account. How
> should the sign be fixed?

PICKED: **"Doors ask 'owed' (Recommended)"**:

> From scratch: every door that takes a balance for a LIABILITY account (the create form's opening
> balance, the anchor editor, the books-opening door) asks for the amount OWED (positive = you owe;
> a card's credit entered as a negative amount owed) and stores the held sign through the one
> owed() flip, so the premise is true by construction. Typing 5,000.00 on the car loan stores
> -5,000.00 and reads $5,000 owed. It is a write-door change, so it becomes its OWN leaf between 5a
> and the flip (CC-5-5b the doors, CC-5-5c the flip), re-opening your cut (R-CC50) and folding
> CC-357 (the tile editor you routed to CC-11) into it. 5a commits as built.

REFUSED:

- "Refuse a positive entry": "Keep 'held' as the stored convention and make the doors fail closed:
  a positive balance typed on a liability that cannot hold a credit (a loan, a custom liability) is
  REFUSED with 'enter what you owe as a negative amount'; a card may still be positive (a credit).
  Typing 5,000.00 on the car loan is refused, and the owner re-types -5,000.00. Also a write-door
  change, so its own leaf before the flip, same re-cut. An overpaid loan without terms could then not
  be entered."
- "Document 'held', add guidance": "Keep 'held' as the convention and only add sign guidance to the
  doors' help text ('negative for something you owe', as the books-opening door already says); no
  refusal and no flip at entry. The smallest change, but an owner who types 5,000.00 anyway, as the
  Mortgage was typed, gets a $5,000 asset, silently. 5a commits as built and the cut stands."
- "Sign by kind, not held": "Withdraw R-CC47's premise for liabilities other than cards: a card is
  held (negative = owed); every other liability is read as a MAGNITUDE owed whatever sign was typed.
  The car loan reads $5,000 owed either way. It brings back a per-account-type sign rule (the shape
  you refused as 'C'), cannot represent an overpaid loan without terms, and 5a's footer must be
  rebuilt for non-card liabilities."

Consequences as ruled: the cut is now CC-5-5a (built) -> CC-5-5b (the liability doors ask owed;
CC-357 folded in) -> CC-5-5c (the one-commit flip, formerly "5b"). R-CC50's "5b" text now describes
CC-5-5c. OPEN for CC-5-5b's first question (not asked here): the EXISTING positive-typed liability
entries (production: only the configured Mortgage's own cash-ledger opening and anchor, which feed no
net-worth surface because the loan reads its schedule) -- re-sign in a migration, or leave.

## CC-5-5a's tick review (2026-09-22, FIX-THEN-SHIP): the developer's two answers VERBATIM, as the coordinator relayed them

The questions were asked by the coordinator after the fresh review of the tick and `aa29d977`; their text is the coordinator's record. The picked option's description is the ruling; the options' labels are as asked.

### M1 -- the positive-typed liability and 5a's floored footer (R-CC49)

The finding: 5a's floored footer TRUSTS the typed sign, so until CC-5-5b makes the doors enforce it, a non-loan liability typed POSITIVE (e.g. an 'Auto Loan' with no terms typed +$5,000) reads owed -5,000, floors to $0 and HIDES the caveat; at HEAD `abs()` would have counted it. The coordinator measured the 2026-09-22 17:06 pre-deploy dump: 9 accounts; the only 2 liabilities, Mortgage and Van Loan, both have LoanParams.

PICKED: **"Ship 5a now, disclose (Recommended)"**:

> Merge 5a now; the PR and the R-CC49 ruling row name the positive-typed case. The window is real only if you
> create a card, a loan without terms, or a custom liability before 5b ships; I'll warn you until it does. No
> production figure changes today.

REFUSED: "Hold 5a until 5b"; "Revert the footer to abs until 5b".

### L1 -- the recurrence:R16-c-2 wait over both new leaves (R-CC50, R-CC52)

PICKED: **"Keep: after 5b and 5c (Recommended)"**:

> R16-c-2 starts only after the flip ships, so its loan tests are written once, against the final sign. It's
> unstaffed, so nothing waits today; I'll raise it again if a session frees up before 5c lands.

REFUSED: "Only after 5c's design"; "Drop the wait".

## CC-5-4a-2 `550cc9ce` and CC-5-4a-3 `175b192d`, ticked 2026-09-22: 4a-2's specification and `steps.md` row as they stood, the ledger rows closed, then both leaves' rulings VERBATIM (the card lane's records `rulings_cc5_4a2.md` and `rulings_cc5_4a3.md`)

4a-2 carries migration `2eabfa596ee0`, cut on `9900b309f0b0` and RE-PARENTED onto `balance:X-bi-6-3`'s `c7d1e9a4b2f8` at `62bf0e35` when 6-3 merged to dev first (#448; the two touch disjoint tables). 4a-3 carries none. Under **R-CC51** and **R-CC55** the two ship in ONE release, and CC-5-4a-4 (**R-CC54** parts 2 and 3) follows in its own. The commits are the record; the build logs, probes and adversarial reviews are `~/projects/shekel-handoffs/HANDOFF-credit-card-CC-5.md` and `credit_card-2026-09-22/`.

### CC-5-4a-2's specification as it stood, verbatim

- [ ] **CC-5-4a-2** `feat(cards): a member is a bank line or a movement` -- `R-CC45`'s second half:
      a migration re-keys every accepted act's row member (103 row members on the 2026-09-21 dump;
      221 acts at the 08-27 count) onto that bill's payment after a census on the newest production
      dump and an ASSERT of one act per row; a member with no payment to re-key onto (a bill closed
      from its purchases, a `$0.00` close, a Credit or Cancelled bill matched before today's
      refusals) REFUSES the migration and the developer rules it; then
      `statement_match_members.transaction_id` goes with its key and unique index, and the six
      bill-member readers (`_candidates.matched_subjects`, `_candidates._is_claimed`,
      `_accepted_view._accepted_row`, `_acts.named_rows`, `match_withdrawal`,
      `bank_agreement._rows_on`) lose that arm; with the row-member shape gone a definition's
      account move touches no member's subject (closes **CC-356**), and
      `status_seam/_covering.py:36-40`'s stale module docstring (it names `_candidates._price`,
      gone) is this leaf's to correct (announce-first, balance's region). Its own PR and release,
      graded byte-identical on production's shape first; rehearsal base the clone `shekel_cc54` (the
      2026-09-21 10:33 dump at `9900b309f0b0`).

### The `steps.md` row as it stood, verbatim

| arc | id | also | what this step does | order | commit | starts |
|---|---|---|---|---|---|---|
| credit_card | CC-5-4a-2 | -- | Re-key every accepted act's row member onto its payment by migration after a census on the newest dump (103 row members on 2026-09-21; one with no payment to re-key onto REFUSES the migration), assert one act per row first, then drop `statement_match_members.transaction_id` with its key and unique index and the six bill-member readers' arm (**R-CC45**); OWN PR, OWN RELEASE. Closes **CC-356**. | #2 | -- | NOW / credit_card:CC-5-4a-1 (shipped; the writer whose stored past it re-keys) |

### Ledger rows CLOSED at this tick

**CC-356** CLOSED at `credit_card:CC-5-4a-2` `550cc9ce`: with the row-member shape gone a definition's account move touches no member's subject. Pinned through its own door by `tests/test_models/test_cc5_4a2_member_rekey.py::TestADefinitionsAccountMoveNoLongerMeetsTheMemberKey::test_the_definition_door_moves_the_row_once_the_act_names_its_payment` (on the old-shape member the door raises on `fk_statement_match_members_transaction_account`; re-keyed, the same door commits and the act is unchanged). The row as it stood:

| arc | id | also | finding (one line) | worst measured | status | owner |
|---|---|---|---|---|---|---|
| credit_card | CC-356 (`credit_card:CC-5-4a-1`'s tick review 2026-09-21, M3; the developer's R-CC46 text "the row/definition doors reported, not fixed") | -- | **A DEFINITION'S ACCOUNT MOVE RE-ATTRIBUTES A PROJECTED ROW THAT A PRE-4a-1 ROW MEMBER STILL NAMES, AND THE MEMBER KEY 500s.** Since **R-CC36** `recurrence_engine/_maintain.py` (`propagate_to_unruled_definition`, :404-447; :225-240) moves every Projected row's `account_id` with its definition; a ROW member recorded before CC-5-4a-1 (103 on the 2026-09-21 10:33 dump, the reverted acts shown as "no longer holding" among them) is held to the row's account by `fk_statement_match_members_transaction_account` (NO ACTION on update, `app/models/statement_match.py:281-286`), so the move raises `IntegrityError` where R-CC46 owes a withdrawal and a disclosure | `$0.00`; a 500 on a definition move over a matched Projected row (0 cards; 103 row members on production's shape) | **OPEN, born with an owner**: the re-key deletes the row-member shape whole, after which a definition move touches no member's subject (the payment stays where the money moved, **R-CC42**); sequenced there by the developer's R-CC46 text | CC-5-4a-2 |

**CC-359** CLOSED at `credit_card:CC-5-4a-3` `175b192d` as NOT A DEFECT; it was never a row. Granted at CC-5-4a-2's review (L6) for `_release._remove`'s claim that the shared delete verb's withdrawal is a no-op, unverified for a row an act CREATED that later gained a purchase another act matched. Measured at 4a-3: adding a purchase does NOT move the row's `version_id` (2 -> 2 on a minted envelope, 3 -> 3 on a reverted residual), so the revision is not what holds it; `_release._container_survives`' content arm keeps a container that still holds a purchase, so releasing the first act never reaches the second. Pinned for the CONTAINER case by `tests/test_services/test_statement_match/test_withdrawal.py::TestReleasingAnActDoesNotWithdrawTwice::test_a_purchase_ANOTHER_act_matched_keeps_the_container_and_that_act`, which failed when that content arm was deleted (4a-3's mutation log); the residual and income cases are held by the refusals `_release._remove` names (`entry_service._refusals._reject_settled_addition`; after a revert the undo's revision check and `_accept._reject_parent_and_its_own_purchase`; `create_entry`'s expense-only guard), with no pin of their own.

### CC-5-4a-2's rulings and confirmations, VERBATIM as picked (AskUserQuestion in the CC-5-4a-2 session shekel-0d, 2026-09-22)

For the CC-5-4a-2 tick (the coordinator's). Each block: the option label + description as picked (his option text
IS the ruling), the refused options, and what the question stated. Ids are the coordinator's: CC-358 (the finding),
CC-5-4a-3 (the fix leaf), R-CC51 (the disposition, NOT FILED until the sequencing question below resolves).

#### Confirmation 8 (asked 17:3x-17:5x EDT 2026-09-22, recorded 17:56, header "Rule 5") -- the re-expressed tests, under R-CC45 (no id)

Picked: **"Confirm all four groups (Recommended)"**

> All four are rule-5 re-expressions under R-CC45. Each changed docstring quotes this confirmation, and the leaf
> commits.

The groups as stated in the question: Group 1, DELETE: the 2 tests of the old row-member shape
(`test_cc5_4a1_settlement_subject.py::TestAnActRecordedBeforeThisStepStillClaimsItsRow`) and 2 parametrize rows for
the dropped relationship (`test_statement_match_schema.py::TestASubjectIsReachedThroughTheAccountToo`, the
`StatementMatchMember.transaction` rows); their behaviour stays graded on the payment shape by existing tests and the
new migration test. Group 2, DELETE ONE LINE in 6 tests: the assertion "no member names the row", which the schema now
guarantees; each keeps its "the member names the payment" assertions (test_accept, test_income, test_residual,
test_cc5_4a1's card-member case, test_cc5_3's two register cases). Group 3, RE-POINT 8 tests from a row member to the
row's payment member, same assertions (schema: two subjects refused; one movement in two matches, now asserting
`uq_statement_match_members_entry`; the partial index; the three `e4a7c0f13b92` repair cases; bank_agreement: the
matched case and the covered bill and paycheck case). Group 4: the schema test helper stops naming the dropped column.
"No money assertion changes in any group."

Refused: *"Confirm 2-4; re-express group 1"* (rewrite the two old-shape tests onto an act naming the payment);
*"Refuse: re-open R-CC45"*.

NOT asked, reported instead (a harness change, no assertion moved): `tests/test_models/test_settle_day_basis.py`
`TestTheBackfillArmsAreExactOverTheirOwnPredicates._unpair` now also re-adds `statement_match_members.transaction_id`
for the duration of each (rolled-back) case, so migration `c7d31f9a45e8`'s frozen `classify_settle_days` runs against
the schema of its own revision; the four cases stage no member.

#### Ruling (asked 17:3x-17:5x EDT 2026-09-22, recorded 17:56, header "$0 re-record") -- the finding CC-358's disposition (R-CC51 when filed)

Picked: **"Own leaf next; 4a-2 not held (Recommended)"**

> New card leaf right after CC-5-4a-2, before CC-5-4b. It designs the fix with you (withdraw and disclose at the
> popover, as R-CC46 does, plus a structural end for the leftover match). CC-5-4a-2 ships as ruled. The defect is live
> for every new match either way; the register's Undo repairs any case.

Refused: *"Own leaf; hold 4a-2's release"* ("Same new leaf, but CC-5-4a-2's release waits until the fix ships, so the
103 older matches never meet the defect. CC-356 stays open on them meanwhile."); *"Fold the fix into CC-5-4a-2"*
("CC-5-4a-2 grows: the fix's design questions come first, then the build, in one PR and one release.").

The question as stated: "A defect found while tracing this leaf. It's live since today's 17:06 release, which shipped
CC-5-4a-1 (acts name the payment). Example: a Hotel bill, $120 on Checking, marked Paid 9/22; Checking's 9/24 bank
line "HOTEL -$120" matched to it. Later you type $0.00 as the paid amount on its popover (or switch it to 'from its
purchases'). NOW: the $120 payment is deleted and the match silently keeps only the bank line. The register shows it
as no longer holding, with 0 rows (its Undo still works). The 9/24 line shows as unexplained again, and matching it
to any other row gives an error page, because the old match still holds the line. BEFORE today's release, and still
today for the 103 older matches: the match stays, reads amber ($0 vs -$120) with its Undo, and the line stays
explained. CC-5-4a-2's migration moves those 103 older matches onto the new behaviour. CC-5-4a-2 also closes CC-356,
another error page on the same 103."

#### The SEQUENCING conflict -- RESOLVED (the coordinator's final question, quoting all four answers, 2026-09-22)

**RESOLVED.** Josh picked **"Wait for the fix (Recommended)"**, verbatim: "CC-5-4a-2 commits and its PR merges to dev
now, but it deploys only together with or after CC-5-4a-3 (the CC-358 fix). The 103 older matches keep CC-356's
milder exposure (an error, nothing changed) until then and never meet CC-358's silent withdrawal. I cut other
releases around 4a-2's unreleased migration on dev. Answers (2) and (3) are withdrawn." Refused: "Release 4a-2 when
ready". **R-CC51** = the disposition as it now stands: CC-5-4a-3 its own leaf right after CC-5-4a-2, and CC-5-4a-2's
RELEASE waits for it (with or after). The history below is kept for the record.

The disposition above conflicted with the coordinator-session ruling on production ("Keep it, fix forward": "... The
fix goes out before or with CC-5-4a-2's release, never after."). Both sessions then asked Josh to reconcile, in
parallel, each tagging the OPPOSITE option "(Recommended)", and he took the recommended one both times:

- In THIS session (header "Sequencing"), picked **"Fix before or with 4a-2 (Recommended)"**: "The coordinator-session
  answer stands. CC-358's own leaf is built next, and CC-5-4a-2's release goes out after it or together with it.
  CC-5-4a-2 still commits and opens its PR now. This session's "not held" is withdrawn." Refused: *"4a-2 not held"*
  ("This session's answer stands. CC-5-4a-2 releases when ready, and the fix follows as the next leaf. "Never after"
  is withdrawn; you avoid a $0 re-record of a matched bill until the fix ships.")
- In the COORDINATOR's session, per the coordinator: picked "4a-2 not held (Recommended)".

The coordinator took the ONE final question (quoting all four answers; the lane's recommendation, which the
coordinator shares: fix before or with 4a-2 -- CC-358 silently withdraws a match AND 500s on re-match, where CC-356,
which 4a-2 closes, only refuses). Whatever it returns is the sequencing; CC-5-4a-3 as minted and CC-358's owner
= CC-5-4a-3 hold either way.

#### Measured for CC-358's row (2026-09-22, scratch probes on the CC-5-4a-2 tree, deleted after)

1. THE SEAM PATH (new since CC-5-4a-1): settle a bill, accept a match naming its payment, re-record it at $0.00 on the
   popover door (`apply_requested_status(..., submitted=typed(0.00))`): `status_seam._covering._withdraw` deleted the
   payment; the member cascaded; the act stood with its line member only; `matched_subjects` no longer claimed the
   line; the register listed the act `agrees=False` with 0 rows; accepting the line against another row raised
   `IntegrityError` on `uq_statement_match_members_line`.
2. A BULK DOOR (pre-existing since the purchase member): `test_withdrawal`'s template hard-delete shape (a matched
   PURCHASE's envelope removed by one bulk statement) leaves the same act; re-matching its line raised the same
   `IntegrityError` on `uq_statement_match_members_line`. So the re-accept 500 predates CC-5-4a-1 at the bulk doors
   `match_withdrawal`'s docstring names; CC-5-4a-1 added the seam's `$0.00` / purchases re-record as a new door into
   it, and CC-5-4a-2's re-key extends that door to the 103 older acts.

#### Confirmation 8a (asked after the review's L7, 2026-09-22, header "Rule 5 fix") -- a correction to Confirmation 8

The review found Confirmation 8's group-2 description exact for 4 of its 6 tests only. Asked: "... In the other 2
(test_cc5_3_settle_tender: "the matcher's price asks the screen's account" and "a member whose tender moved reads
zero") I deleted a different kind of line: 4 lines that valued a ROW member on the register, e.g. register(txn, day,
card) == -$120.00 and register(txn, day, checking) == $0.00. Each sits beside an identical line valuing the PAYMENT
member (register(movement, ...)) with the same expected figure, and those stay. ... (Group 3 was also tightened to
match its description: the schema tests now stage the row's PAYMENT, not a purchase.) Confirm deleting those 4
row-valuation lines under R-CC45?"

Picked: **"Confirm the 4 row-valuation lines (Recommended)"**

> Deleting the 4 register(txn, ...) lines is a rule-5 re-expression under R-CC45. The paired register(movement, ...)
> lines, with the same -$120.00 / $0.00 figures, carry the assertion. The docstrings quote this.

Refused: *"Refuse: keep an equivalent"* (add a test that the register still values a row's payment the same as the
old row arm did, asserted through covered_cash_leg on the row).

#### CC-358's clause (the coordinator, 2026-09-22): the day drill-down

`bank_agreement._lines_on` reads a line's claim off the member table WITHOUT `act_still_names_a_row()`, so an act left
holding only its line (CC-358's end state; the bulk doors' leftovers) shows that line "matched" on the day drill-down
while the review screen lists it unexplained. Pre-existing; rides CC-358 as a clause; CC-5-4a-3 fixes it with the rest.
CC-359 (granted): `_release._remove`'s "the withdrawal is a no-op" claim is unverified for a created row that later
gained a purchase another act matched; CC-5-4a-3 probes it first (does adding a purchase bump the row's version_id so
`planned_removals` refuses it?) and closes it as not-a-defect with the measurement if so.

### CC-5-4a-3's rulings, VERBATIM as picked (AskUserQuestion in the CC-5-4a-3 session shekel-0d, 2026-09-22)

For the CC-5-4a-3 tick (the coordinator's). Each block: the option label + description as picked (his option
text IS the ruling), the refused options, and what the question stated. **IDS GRANTED by the coordinator
(shekel-d1) 2026-09-22 ~19:5x: R-CC54 = Round 2 Q1 (the design), R-CC55 = Round 2 Q2 (the sequencing), R-CC56 =
Round 1 Q2 (the disclosure); step credit_card:CC-5-4a-4 minted (parts (2)+(3), the migration, the check
deleted; after CC-5-4a-3, blocked by it; migration-bearing, its own release after the 4a-2 + 4a-3 release);
ledger CC-363 (MONEY) = the side-effect destruction, owner CC-5-4a-4; CC-358's `_lines_on` clause moves to
CC-5-4a-4. Next free card R-CC57 / CC-364.**

#### Round 1 (asked 19:28 EDT, answered ~19:3x EDT 2026-09-22)

##### Q1 "Leftover" -- REFUSED, the premise rejected

The question offered four ways to make a match that outlives its payment impossible: B (a database rule
deletes the match when its last payment or purchase goes, R-BI10's shape), A (the database refuses the delete),
C (refuse at commit), D (keep today's shape and wire the doors). Josh's answer, verbatim: **"Are these options
the best from scratch design? They seem like fences trying to avoid the root cause."** No option picked; the
design was re-opened (see Round 2).

##### Q2 "Disclosure" -- PICKED (**R-CC56**): **"Both popover paths (Recommended)"**

> A caption under the Actual box ('Recording $0.00 withdraws 1 accepted match, so 1 bank line is unexplained
> again: 9/24 HOTEL -$120.00'). A second caption beside Paid/Received on a reverted row whose purchases would
> replace its payment. Both captions come from the one read the door acts on. The grid's one-click Mark Paid
> withdraws and logs it without a caption, as the Paid-from change does today.

Refused: *"Actual box only"* (only the $0.00 caption; the purchases path withdraws and logs without saying so
first); *"A confirm step"* (a second click, 'Save and withdraw the match', a control shape the Paid-from picker
does not use).

The question as stated: "R-CC51 already rules that the popover withdraws the match and says so first, as the
'Paid from' picker does ('Picking another account withdraws 1 accepted match, so 1 bank line is unexplained
again: 9/24 HOTEL -$120.00'). Two popover paths remove a matched payment: typing $0.00 in Actual on the Hotel
bill, and pressing Paid on a reverted bill that now has purchases (its purchases replace the payment). Grid
quick-actions have no popover in either case. Where does the popover say it?"

##### Q3 "Bulk doors" -- REFUSED, the premise rejected

The question (conditional on Q1's B) asked what the template / account / pay-period doors say when a match
goes with them. Josh's answer, verbatim: **"I want root cause solutions and the best from scratch design that
avoids this problem"**.

#### Round 2 (asked 19:4x EDT, answered ~19:4x EDT 2026-09-22) -- the re-opened design

Measured before asking (scratch probes on 550cc9ce, deleted after; outputs in the session scratchpad):
P5 -- a Projected one-off envelope 'Home Improvement' holding a $25.00 purchase recorded from Checking's 1/5
bank line (`create_purchase_from_line` into the existing envelope): `template_has_paid_history` = False;
`definition_delete.permanently_delete_definition` erased the purchase; Checking's `settled_cash_facts` sum
moved -25.00 -> 0 (the balance $25.00 higher than the bank); the act was left holding its line alone.
P6 -- a matched Paid $120 Hotel bill reverted to Projected, then its template permanently deleted: the kept
payment destroyed, the act left holding its line alone. Production census (17:06 dump `shekel_cc54a2_base`):
284 acts, 0 stranded, 0 lineless; 1 non-settled row holds a movement (an un-dated purchase under template 19
'Clothes', already refused permanent delete by its merchant rule) [FALSE, measured at CC-5-4a-4's entry: template 19 has no merchant rule and its permanent delete is permitted; see CC-363]; 0 future periods hold one; 0 matched
movements under a non-settled row.

##### Q1 "Root cause" -- PICKED (**R-CC54**): **"Root-cause design (Recommended)"**

> (1) ONE act takes a payment or purchase off the books: it reverses its ledger, takes it out of any match
> (withdrawing a match left with nothing and saying which bank line that freed), then deletes it. Every door
> calls it, including the popover's two paths. (2) The database stops cascading a row's delete to its payments
> and purchases. A row holding one is history, so the template and account permanent deletes archive instead,
> and truncate/regenerate lock its period. Home Improvement: archived, the $25 stays spent, the match stands.
> (3) A match's key to its payment or purchase stops cascading, like its key to the bank line. The screens'
> leftover-match check is deleted.

Refused: *"Same, bulk doors disclose"* ((1) and (3), but the template, account and pay-period doors take the
payments and purchases off through the one act and say so in their confirmation; the bank-confirmed purchase
destroyed on his say-so, Checking $25.00 higher than the bank until the line is recorded again); *"(1)+(2),
keep the check"* (the match's key keeps cascading; the screens' leftover-match check stays as the guard for a
door written later).

The question's stem: "Root causes found for CC-358. (1) Taking a payment or purchase off the books is written
separately in several doors, and the popover's $0.00 path forgot the match. (2) The template, account and
pay-period deletes destroy payments and purchases as a side effect, judging safety by 'never Paid' instead of
by what a row holds. Measured: permanently deleting the one-off 'Home Improvement', whose $25.00 purchase was
recorded from Checking's 1/5 bank line, erases the purchase, so Checking reads $25.00 higher than the bank, and
the match is stranded. A matched $120 Hotel bill, reverted then template-deleted, loses its kept payment the
same way. On production's 17:06 copy the rule changes nothing deletable today. [FALSE, measured at CC-5-4a-4's entry: template 19 'Clothes' (no merchant rule, no settled row) holds movement 343, $107.57, under a row its permanent delete may erase, and its archive drops that purchase from the fold; see CC-363] Which design?"

##### Q2 "Sequencing" -- PICKED (**R-CC55**): **"Two leaves (Recommended)"**

> CC-5-4a-3 builds part (1). CC-5-4a-2 then deploys with it, as R-CC51 requires, because the popover path is
> the one R-CC51 is about. A next leaf (id from the coordinator) builds parts (2)+(3) with their migration and
> deletes the check. Until that leaf ships, the bulk doors behave as today (the Home Improvement case stays
> live, with 0 instances on production). [FALSE, measured at CC-5-4a-4's entry: template 19 'Clothes' is one live instance; see CC-363]

Refused: *"One leaf, all three"* (CC-5-4a-3 builds (1), (2) and (3) with the migration, announced to the
template, account and pay-period owners; CC-5-4a-2's release waits for all of it).

#### Round 3 (asked ~20:2x EDT 2026-09-22, after the neutral review's M1) -- the transfer popover (**R-CC59**, granted by the coordinator; R-CC56 gains "extended to the transfer popover by R-CC59" at the tick)

Measured before asking (scratch probe on the 4a-3 tree, deleted after): a $500 Checking-to-Savings transfer
marked Paid, its Checking leg's payment matched to a -$500 line; `transfer_service.update_transfer(...,
figure=typed(0.00))` (the transfer popover's Actual box) -> acts 1 -> 0, the line unclaimed, both legs'
covering movements gone. The transfer popover rendered no caption. Census: only two templates post
`settled_amount` (grid/_transaction_full_edit.html, transfers/_transfer_full_edit.html). [INCOMPLETE as a census of the doors that take $0.00, read at the CC-5-4a-2 / 4a-3 tick: `accounts/_reconcile_panel.html:71` posts a per-row `settled_amount-<id>` (`min="0"`) that settles through `transaction_service.settle_transaction`, a door neither R-CC56 nor R-CC59 names]

##### Q1 "Transfer $0" -- PICKED: **"Same caption, this leaf (Recommended)"**

> The transfer popover's Actual box gets the bill popover's caption: 'Recording $0.00 withdraws 1 accepted
> match, so 1 bank line is unexplained again on your statement screen: 1/6 TRANSFER -$500.00'. It reads what
> taking BOTH legs' payments off frees, from the same read the door acts on. Built in CC-5-4a-3 (the transfer
> popover's route and template, cleared with the coordinator; balance's 6-3 does not touch them).

Refused: *"No caption; file it"* (this leaf states the transfer popover as a door that withdraws and logs
without a caption, like the grid's one-click Mark Paid; a ledger row files the transfer caption for a later
step).

#### Measured for BAL-530 (granted by the coordinator 2026-09-22 ~21:3x, owner balance:X-bi-6-4; filed at the 4a-3 tick)

The transfer full-edit popover (`GET /transfers/<id>/full-edit`, `app/routes/transfers/forms.py:get_full_edit`) calls
`transfer_legs.covering_movements_by_leg([xfer.id])` TWICE per render, on the CC-5-4a-3 tree:
(1) its figures -- `forms.py:108` `transfer_settlement_amounts(xfer, current_user.id)` -> `app/routes/_render_helpers.py:347`
`grid_transfer_leg(xfer, xfer.from_account_id)` -> `app/services/transfer_legs.py:631` `covering_movements_by_leg([transfer.id])`;
(2) R-CC59's caption -- `forms.py:131` `_payment_withdrawal(xfer)` -> `forms.py:160`
`transfer_legs.covering_movements_by_leg([xfer.id])`. One indexed query each (`_covering_movements_query` narrowed to
one transfer). Left in 4a-3 because resolving it once and threading it through both reads changes the signatures of
`transfer_settlement_amounts` (`_render_helpers.py`, where balance:X-bi-6-3 carries a docstring hunk) and
`grid_transfer_leg` (X-bi-6's leg producers, the join X-bi-6-4 moves onto the link); `_payment_withdrawal`'s docstring
states the second call honestly (round-3 review L5).

#### Consequences for the registries (the coordinator's to cut at the tick; ids not yet granted)

- A ruling for the disclosure (Round 1 Q2), one for the root-cause design (Round 2 Q1), one for the sequencing
  (Round 2 Q2) -- or fewer if the coordinator folds them.
- A NEW STEP: the leaf that builds parts (2)+(3) + migration + deletes the check (`act_still_names_a_row`),
  ranked after CC-5-4a-3.
- A NEW LEDGER ROW (money): the template / account / pay-period doors destroy payments and purchases as a side
  effect (P5's +$25.00; P6's kept payment), owned by that new step. CC-358's `_lines_on` clause moves to the new
  step (it dies with the check); CC-358 itself (the popover path) closes at CC-5-4a-3.
- CC-359 closes at CC-5-4a-3 as NOT A DEFECT (measured 19:21; the coordinator acked).
