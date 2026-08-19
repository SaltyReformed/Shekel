> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is `docs/plans/steps.md`; the code as
> committed is the source of truth for what the app does.

# The statement importer's core, as built: the X-f6a-1 - X-f6a-3b span (2026-08-19)

**Four shipped steps, condensed out of `docs/plans/steps.md` and
`implementation_plan_bank_import.md` under `conventions.md` rule 5** when
`steps.md` reached 244 lines against the 240 its cap leaves after headroom,
which is where two arcs landing in one day and a fifth bank_import leaf met.

Rule 5's three conditions hold. **Every finding these steps opened and did not
close is still live in `ledger.md`** -- N-302, N-303, N-306, N-313, N-317,
N-318, N-319, N-320, N-321 -- each naming a live owner there. **No live
sentence depends on a sentence here**: the rulings these steps established
(**R-FP**, **R-FS**, **R-FT**, **R-FU**, **R-FV**, **R-FW**, **R-FX**) stay in
the arc document's own rulings table, which is where a later step reads them.
**Nothing below was re-verified in the move** -- these are the entries as they
stood, and the commits they name are the record.

| step | commit | what it shipped, and what it closed |
|---|---|---|
| **X-f6a-1** | `40a490c3` | A statement is RECORDED: four tables, one normalized line shape behind a source adapter, the SECU CSV reader, and the import page. Balance-neutral on a production clone (9 accounts, 434 grid cells, 6,076 daily points byte-identical with 306 real lines). Opened **N-313**, **N-302**, **N-303**. What a later leaf must obey: identity is positional and its ordinal is UNMEASURED (R-FU's amendment), and the restatement guard compares only the description |
| **X-f6a-2** | `267cb75e` | A bank line IS these rows: `budget.statement_matches` and its members hold the correspondence over an exclusive arc, the proposer offers it, and an accepted match writes the bank's posted day onto every member. Closed **N-173**; opened **N-305**, **N-306**, **N-314**. What a later step must obey: no clearing link is written from a match (**R-FV**), an unbalanced group is refused rather than apportioned, and a group's day is its LATEST |
| **X-f6a-3a** | `140f1f24` + `feb2ea91` | The bank owns BOTH of a purchase's days (**R-FW**): `transaction_on` went NULLABLE, the adapter reads the swipe day SECU states in a card line's description, and a match corrects a purchase's `purchased_on` where the bank refutes it. Forced by X-f6a-3b's premise being measured FALSE. Opened **N-312**. What a later leaf must obey: a purchase day moves only where the bank REFUTES it, and only a line within `DAY_WINDOW` of the day it was MADE may refute it |
| **X-f6a-3b** | `1f214712` | A bank line BECOMES a purchase (**R-FS**, **R-FX**), against an envelope the owner picks or a NEW one it creates in the period the purchase was MADE in. It also REPAIRED X-f6a-3a, whose `_COST_BEARING_FIELDS` refused `purchased_on` and so killed 13 of the 15 purchase-date corrections the screen offered. Opened **N-315**-**N-321**; **N-306 split off**. What a later leaf must obey: R-FX's two conditions on adding to a settled row, and that a line becomes a PURCHASE, never a row |
