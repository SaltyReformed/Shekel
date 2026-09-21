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
