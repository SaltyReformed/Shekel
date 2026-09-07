# Findings of 2026-09-06/07 that no registry can yet hold

> **HOLDING PLACE, NOT A PLAN OF RECORD.** It governs NOTHING. Where it disagrees with `docs/plans/`
> or with the code as committed, they win. **Most of what this file held has been CONSUMED** by the
> registry pass of 2026-09-07 and is deleted here rather than duplicated, because conventions rule
> 16 says a registry's content is stated ONCE. What remains is the residue that `ledger.md`'s own
> grammar cannot express yet.

## What was consumed, so nobody looks for it here

Filed into `docs/plans/rulings.md`: **R-PC63** (C14-f is the door fix, superseding R-PC55, and the
door CONTINUES rather than refuses), **R-PC64** (regenerate is C17's era-mint;
**P80 does NOT close**), **R-PC65** (the gates split), and **R-BAL6** (the leftover's date and the
two-term CHECK, with the four rejected alternatives).

Filed into `docs/plans/steps.md` and the arc documents: the ticks for `S3-e-1`, `X-bv`, `C14-e-3`,
`C14-f` and the container `C14`; new rows for `X-bz`, `X-ca` and `X-bv-2`.

Corrected in `docs/plans/ledger.md`: **BAL-463**'s remedy, which aimed at migrations while the
strand producer was application code; **P80**, **N-493**, **N-494**, **N-495**, **N-496**,
**PC-497** and **PC-498** re-pointed off shipped steps; **N-398** closed and its pin removed from
the citation gate, exactly as `N-390` left it at `balance:X-bh-2`.

## SIX FINDINGS THAT CANNOT BE FILED: they have no owner the grammar accepts

`ledger.md`'s owner column admits only a LIVE step id, `operator`, or `developer-decision` -- and
`developer-decision` is refused without the DATE the fork was taken, `operator` without the QUESTION
stated. None of these six has a remediation step, and minting one is a scoping decision about work
nobody has scoped. **They are real, they are measured, and they are owed a decision.**

- **`BI-482`** -- five further orphans of the X-gi-2 deletion: `ReviewBounds.calendar_opens`
  (write-only) plus `MatchProposal.bank_amount`, `.app_amount`, `.confirms` and `.redate_gap`. Four
  sit behind live tests. BI-480's exact shape.
- **`BI-483`** -- the sweep guard's staging regressed from three withholding arms to one, where the
  three-arm version existed because an adversarial review ruled two insufficient.
- **`BI-484`** -- `_variance.py`'s module docstring describes code that now lives in `_landing.py`.
  **A DELIBERATE ACCEPTANCE**: the argument is genuinely one argument and its own first line says
  so, and cutting it to serve a line count is N-365 pointing the other way.
- **`BI-485`** -- `_reject_opposed_movements` cites
  *"this module's own docstring says why: mixed signs WITHIN a group stay legal"*, and that sentence
  is in no module docstring. Pre-existing, confirmed absent on dev at `20b75178`.
- **`N-549`** -- **a hardcoded future date decays into the past**, and nothing re-checks it because
  nothing fails until the day it does.
  **The population of the shape that FIRED is ONE and it is fixed** (`test_templates.py`,
  `dfadcac7`). The MIRROR shape -- a calendar-derived value asserted equal to a literal -- is 15
  sites, 2 verified safe, **13 needing per-site reading**. Scope is the UNFROZEN trees only;
  `tests/test_services/` pins today to 2026-03-20 and is out.
  **The remedy is a SIMULATION, not a grep**: run the unfrozen trees with the clock advanced six and
  twelve months. A static sweep only finds shapes someone thought to describe.
- **`N-548`** -- `migrations/` is linted by nothing: 181 files, all nine custom checkers blind,
  including the two that exist because getting them wrong mismanages real money.
  **Ruled: gate NEW migrations and triage the existing ref-name comparisons.** Part 1 is built and
  measured but UNCOMMITTED. **Part 2's scope is not 24**: defined as a SQL literal naming a known
  `ref.*` table and comparing a `name` column, the census is **117 across 46 files**. Most are
  unavoidable -- a migration that seeds a ref table must name its rows and cannot import
  `app.enums` -- so the triage should narrow to sites where an id was genuinely available.

## `REC-516` -- issued, and correctly NOT filed

After `X-bz`, no code can compute `occurs_on` for an existing row, and both automated production
backups on disk are pre-stamp -- so restoring one yields a permanently un-backfillable database
whose next maintain pass HARD DELETES (`_maintain` -> `db.session.delete`; `transfer_recurrence` ->
`delete_transfer(soft=False)`). Ruling `recurrence:R-R46` becomes FALSE at that merge, and
`migrations/versions/95e7938240e4` is left as the only surviving description of the mechanism.

**It is a real design fork, not an operator note**, which is why no owner spelling fits: the
remedies run from folding the occurrence walk into the maintain pass (so no backfill ever exists) to
making a stale restore unrepresentable. Measured: the developer's DEV database is already in that
state -- 622 of 624 template-linked transactions and 175 of 175 transfers NULL, behind a sentinel
dated Aug 28, with 503 rows mutable and reachable by the retire branch.
**Production is unaffected**: 6 NULL of 626, all immutable.
