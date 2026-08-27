> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is `docs/plans/steps.md`; the code as
> committed is the source of truth for what the app does.

# Three shipped recurrence steps, archived out of the plan of record (2026-08-27)

**What this is.** The three `- [x]` entries that left
`../implementation_plan_recurrence_redesign.md` on 2026-08-27 under conventions.md rule 5, with
their `steps.md` rows, to buy the room `recurrence:R16-b`'s decomposition and ruling **R-R37**
needed. The document stood at **828 of its 850-line cap against a 20-line headroom floor -- two
lines of room** -- and rule 4 forbids raising a cap when it binds. **The COMMIT is the record for
all three**; read the code each shipped, not this table.

**Why these three.** `recurrence_completed_findings_span_as_built_2026-08-19.md` named the next
candidate outright -- "`R7a-1` is the cheapest remaining candidate once its three live citations are
re-pointed" -- and the one reason `R-F1` was held has since lapsed, as its own entry said: it stayed
for a `tools/plan_gate` control that NAMED it as the worked example of the PREFIX trap, and
`pay_calendar:C2-f3e` made that control DERIVE its specimen (`_staging.a_prefix_trap`), closing
**D42**. None of the three blocks anything: no `starts` cell in `steps.md` names any of them, which for
`R7a-2a` is a RE-GREP rather than an inherited claim -- the 2026-08-19 record called it
`recurrence:R-F17`'s blocker, and `R-F17` has since shipped with a `--` `starts` cell.

| step | commit | what it shipped |
|---|---|---|
| `recurrence:R7a-1` | `6fed14af` | The Recurrence cell became ONE function over `(interval, unit)`, replacing eight hand-written template branches keyed on the closed pattern set, so a cadence nothing authors yet already reads correctly. Closed **D17**. |
| `recurrence:R7a-2a` | `003e3657` | The paycheck count became DERIVED per owner (`pay_calendar.PayCadence`) rather than a `Decimal("26")` constant nine files read, so every monthly-equivalent figure stopped being wrong for an owner who is not paid biweekly. Opened **F-16**, **F-17**. |
| `recurrence:R-F1` | `44b25ad3` (migration `c7f3a9d1e864`) | The lagging `ref` identity sequences were put back in step. Closed **F-1**. Its fuller account was already archived to `recurrence_as_built_2026-08-15.md`. |

## Where their live citations now point

**Rule 5's second condition is that no live sentence may depend on an archived one.** Both
surviving citations of `R7a-1` are PROVENANCE -- "this is where that came from" -- which rule 15
permits against an archived record, and each was re-pointed at this file rather than left naming a
row that no longer exists:

| the citation | what it says |
|---|---|
| The recurrence plan's Half-A / Half-B split ("Half A = R1-R4, R7a-1 through R7c") | Names the span the parallel run covered. Re-pointed here. |
| `ledger.md` row `D29` | Cites `R7a-1` as where `describe`'s coordinate dispatch came from; `R8-c` owns the row and the citation is history, not a dependency. Re-pointed here. |

`conventions.md` rule 13's own prose still names `R-F1`, `R-F10`, `R-F12` and `R-F13` as the worked
example of the prefix trap that made the gate DERIVE its specimen instead of naming one. That
sentence is an argument about how the gate came to be designed and does not depend on `R-F1` being
a live row -- which is the very point it makes.

## What the next session should expect

The document stands at 830 of 850 after this pass, which is **AT the 20-line headroom floor and not
under it** -- `test_the_cap_still_has_headroom` asserts `<= 830`, so the room is zero. The very next
recurrence step to touch this document must archive again before it adds a line.

**Each candidate's predicate was RE-GREPPED here rather than carried from the 2026-08-19 record**,
and one of that record's claims had already lapsed:

| candidate | measured 2026-08-27 |
|---|---|
| `R7a-2a` | **No longer blocked-by anything.** The 2026-08-19 record said it was `recurrence:R-F17`'s blocker; `R-F17` is SHIPPED with a `--` `starts` cell, and `R7a-2a` now appears in `steps.md` exactly once, in its own row. It is the cheapest remaining candidate. |
| `R7c` | Still `pay_calendar:C6`'s blocker (`steps.md`, `after #17 / pay_calendar:C4 / recurrence:R7c (shipped)`), so rule 13 still grades that key. |
| `R7c-c` | Still carries a LIVE obligation -- `R5` deletes `compute_due_date` and the WEEK unit `R8-b` frees is where that migration's SQL and its Python twin part -- so rule 5 forbids it. |
| `R-F10` | Identity-paired with `pay_calendar:C5a` (`ticks it` / `ticked by it`); the pairing has to be handled first or rule 11's class loses a member. |
| `R-F17` | Two live `ledger.md` citations (`F-21`, `P73`) would need the re-pointing this pass gave `R7a-1`. |
