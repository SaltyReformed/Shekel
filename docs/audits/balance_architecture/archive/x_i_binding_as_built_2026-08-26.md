> **ARCHIVED. Historical record only -- this document governs nothing and
> may never be read as a live plan or as a statement of the current state.**
> The live plan is `../README.md`; the code as committed is the source of
> truth for what the app does.

# The one-read-pass BOUNDARY and BINDING, as built

Cite this for how these two steps came to be, never for what is true now. The code as committed is
the source of truth for what the app does, and the live plan is
`docs/audits/balance_architecture/README.md`. Written 2026-08-26 under `conventions.md` rule 5,
because the live document had no headroom and its record of what is DONE is what shrinks.

Both steps are leaves of `balance:X-i`, whose remaining leaves (`X-i1`, `X-i2`, `X-i3-b`, `X-i5`,
`X-i6`) are LIVE and specified there. Every obligation these two leave a live step is restated at
that step; nothing below is load-bearing.

## `balance:X-i3-a` -- the boundary (`765daebd`)

A request is a QUERY or a COMMAND and its transaction says which (ruling **R-GU**). GET and HEAD run
at `REPEATABLE READ, READ ONLY`, so a render is ONE snapshot and cannot write; every other method
keeps `READ COMMITTED`, because the reconciles' lock-then-reread depends on it. Bound at the
session's `after_begin` rather than in a before-request hook, which is how the audit actor had been
lost and was recovered here. `write_transaction()` is the one door a render has when it must write,
and the GET handlers that wrote either declared it or were deleted.

Measured before the ruling: the mechanism proven against this application's own session (two counts
across one concurrent committed append answer `(1, 2)` today and `(1, 1)` under the query mode),
2,302 of 2,978 suite GETs arriving on an inherited transaction, and 36 arriving on one that had
already written.

Closed **N-353**; opened **N-358** and **N-359**.

## `balance:X-i4` -- the binding (`79a1730c`)

A read pass BINDS the account it values (ruling **R-GV**). `_context._memoize_once` -- the one
primitive that creates `account.id`-keyed state on a `BalanceContext` -- takes the `Account` and
refuses one whose owner is not the pass's, BEFORE the membership test, so a warm cache cannot serve
a foreign hit. All five funnels inherit it and nine public seam entries inherit it transitively with
no check of their own; `loan_walk` stopped open-coding a fourth copy of the store-once lines.

`_cash_fold.assemble` and its four siblings went as a mis-pairable shape: one door
(`assembled_fold`) and four readings that take the assembled record and carry no account and no
clock. `_cash_periods.cash_period_view`, which had no `app/` caller at all, was deleted rather than
re-signatured.

**What a mis-pairing would have published**, measured on the parent commit: a first owner's pass
handed the second owner's Checking answered `$2,000.00` -- that owner's real asserted balance --
across all ten of the FIRST owner's period columns. The transactions are scenario-scoped so none of
them folded; `cash_ledger.cash_anchor_facts(account_id)` takes no scenario and no owner, so the
assertions replayed.

**The memo**, measured on the dev database over the six cash entries a single-account screen asks:
42 `walk_cash_ledger` calls across 8 accounts before, 8 after. `records_balance_at` alone walked one
account TWICE inside a single call -- once for its assertion lookup, once through its
`cash_balance_at` fall-through.

**Two bindings the adversarial reviews forced**, each finding **N-354**'s own sentence one field
over on the same object: `BalanceContext.__post_init__` refuses a scenario belonging to another
owner (measured answering a real figure), and `AssembledCashFold.account_id` binds the record
`_asset_fold.resolve` folds an account onto -- that reader memoizes nothing, so the pass's refusal
never reached it, and the step's first build had edited its docstring to claim otherwise.

**The first build made the fold's cache PUBLIC** and both reviews measured the same bypass
independently: an `AssembledCashFold` carries `seed` and `steps`, so a prefix sum over it reproduces
the seam's own scalar, and a consumer importing nothing private could read a balance the W9910 fence
exists to make unreachable. W9910 sees imports and `protected-access` sees underscores, so neither
saw a public dataclass field; W9909, which does scope public methods on that class, refused the
public method form outright. The cache is `_cash_folds` now, filled through one named
`protected-access` crossing in `_cash_fold.assembled_fold`.

**Four tests** reused one pass across a write and were corrected to build a fresh one -- the
contract `BalanceContext` already documented, and what every write path in `app/` already does. No
asserted value changed. The fourth was found by a session-level write probe after the first three.

Closed **N-354**; opened **N-360**, **N-361**, **N-362** and **N-363**.
