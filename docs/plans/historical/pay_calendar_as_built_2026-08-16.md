> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is `docs/plans/steps.md`; the code as
> committed is the source of truth for what the app does.

# The pay calendar, as built: the C3 writer span (2026-08-16)

**Read-only history. Nothing here governs anything.** The live document is
`docs/plans/implementation_plan_pay_calendar.md`; this record exists so that document's shipped
entries can be pointers rather than accounts of themselves (`conventions.md` rule 5). Archived to
make room for `C2-f2d`'s three leaf specifications, under the same rule that says to shrink the
record of what is DONE and never the specification of what remains.

**`C3` is a COMPLETED span**: both leaves shipped, its container ticked with the last of them, and
no live step depends on a sentence that lived only here. What the code does now is stated by
`pay_period_write`'s own module docstring, which declares itself the one place in `app/` that
constructs or deletes a pay period.

## The three steps

- **C3 -- the writer writes paydays, forward-only.** `7e3fb33b`. The DECOMPOSED parent; ticked with
  `C3-b`, its last leaf.

- **C3-a -- the destructive form stops keying on an ordinal.** `5f1e2bd6`.
  `keep_through_period_id`, a `RowId` resolved against the owner's own periods; anything else is
  `PayPeriodUnresolved`, with both F-144 branches logged. The tail is selected by PAYDAY, so no part
  of the operation reads a column `C4` drops. The lock classifier moved to `pay_period_locks`
  (developer ruling). Closed **P13**; opened **P29**, **P30**.

- **C3-b -- the writer materialises the derivation.** `7e3fb33b`. `pay_period_write` is the one
  place in `app/` that constructs or deletes a pay period, writing `derive_periods` over the WHOLE
  payday list every time. R-PC1's floor is one full CADENCE; its coverage half is DELETED (developer
  2026-08-11: a stranded settle day cancels on both sides of R-K, and `integrity_check` BA-06 asks
  it as a query). Closed **P2**'s writer half, **P12**, **P29**, **N-127**; opened **P31**, **P32**;
  found **P33**.

## What a LATER step must still obey

Two of these are cited by live work and are repeated in the live document's rulings table, so this
archive is not load-bearing for either:

- **The forward-only FLOOR is one full cadence after the latest payday**, and its whole job is
  keeping `C6` closed; `C6` deletes it. The COVERAGE half of R-PC1 is deleted and must not be
  reinstated -- the claim it rested on (that stranding a settled day reproduces `balance:N-128`) was
  measured false.
- **`pay_period_write` is the ONE writer.** `C2-f3` moves `pay_period_admin`'s three write-path
  ORM-row reads into it rather than beside it, and `C4` changes that one file plus the readers.

**P33** remains OPEN and is carried on `ledger.md`, not here.
