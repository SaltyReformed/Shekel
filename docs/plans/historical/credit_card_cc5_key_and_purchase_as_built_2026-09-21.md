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
