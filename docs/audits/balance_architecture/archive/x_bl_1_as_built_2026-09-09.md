> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is `docs/plans/steps.md`; the code as
> committed is the source of truth for what the app does.

# `balance:X-bl-1` as built: a cutover's control can fail (2026-09-09)

**What shipped** (`e0e257a7`). `tests/manual/verify_amount_resolver.py`'s
invariance pass is SEVEN perturbations of the SOURCES a row's rule names, each
asserting a disjoint pair: exactly the rows declared to that source move by
exactly it, and every other row holds to the cent. Ruling **R-JF**; `X-bl` was
SPLIT into `X-bl-1` and `X-bl-2` at this step (developer, 2026-09-09).

## `N-445` as closed

The pass this replaced perturbed each row's OWN stored column and asserted a
DERIVED row did not move. Its loop opened
`if txn.estimated_amount is None: continue`, and
`ck_transactions_amount_ownership` is the biconditional
`(amount_source_id IS NULL) = (estimated_amount IS NOT NULL)`, which makes that
column NULL on every derived row -- so it skipped every one of them BY
CONSTRUCTION and re-resolved an unperturbed row, counting the unchanged answer
as a pass. **The tell is that its two numbers are the same number**: *934
derived rows held* against *934 rows storing no figure at all*.

`archive/x_au_d_as_built_2026-09-03.md` and `archive/x_au_e_as_built_2026-09-03.md`
both quote that count and **both already carry the correction** (added
2026-09-03), so R-JF's third deliverable -- *"`X-au-d`'s as-built takes the
correction `X-au-e`'s already carries"* -- was discharged before this step began.
The ledger row and the ruling both still said otherwise; that clause was stale.

## What the seven perturbations grade

Measured on a production clone at `a1c7e5d20f43`: 1,028 rows, 0 refusals.
Census 525 TEMPLATE, 350 TRANSFER, 94 OWN, 59 SALARY, 0 LOAN_PAYMENT; 934 store
no figure at all; 7 of the 525 TEMPLATE rows price from a superseded version.

| arm | moved | held |
|---|---|---|
| `own_column` (a verified rival in every row's own column) | 94 | 934 |
| `transaction_series` | 525 | 503 |
| `newest_transaction_version` (the time dimension) | 518 | 510 |
| `transfer_series` | **0 declared -- grades nothing here** | 1,028 |
| `parent_figure` | 350 | 678 |
| `salary_deduction` (by exactly `-$1,000.00`) | 59 | 969 |
| `salary_raise` (partitioned on the pay period) | 30 | 998 |

## The negative control, shown to fire

Eight mutations of `cash_ledger._amount_source`, each applied to disk and
printed before the run. All eight exit **1**; all eight report *refusals: 0*, so
pass 1 sees none of them.

| mutation | fires | rows |
|---|---|---|
| `m7_column_fallback` -- rule 3 prefers the stored column | `own_column` | 525 |
| `m2_scalar_not_series` -- reads `default_amount` | both template arms | 525 / 518 |
| `m1_fixed_template` -- every row from ONE template | both template arms | 518 / 518 |
| `m3_newest_version` -- supersession ignored | `newest_...` | 7 |
| `m4_constant_shadow` | `parent_figure` | 350 |
| `m5_fixed_transfer` -- every shadow from ONE parent | `parent_figure` | 348 |
| `m9_constant_salary` | both salary arms | 59 / 30 |
| `m6_fixed_period` -- every paycheck from ONE period | `salary_raise` | 30 |

## Three adversarial reviews, and what each cost

**The first build was KIND-UNIFORM.** Every instance of a source kind moved by
the same amount, so the control could see WHICH KIND of source a row reads and
never WHICH INSTANCE. `m1`, `m5` and `m6` -- every recurring row priced from the
wrong definition, every shadow from the wrong parent, every paycheck from the
wrong period -- all passed it and exited 0. Five arms took the SOURCE ROW's id
as their scale; rule 2 has no such scale, so `salary_raise` partitions the
paychecks on their own pay period instead, and `m6` fires on that arm and on no
other.

**The absolute rival collided.** The own-column arm first wrote `$1000.00` into
every derived row; the clone states exactly that price on one version, so the 2
rows priced by it were graded by coincidence. The rival is now each row's own
answer displaced by `$1,000 x its id`, signed on the id's parity.

**The supersession partition was wrong for a shape this database lacks.** It
used `not _superseded(txn)`, but `amount_as_of` back-projects flat below the
earliest version (ruling **R-I**), so on a single-version template such a row
resolves to the NEWEST one. 22 rows are in that shape here and the arm passed
only because every one of them is an OWN row.

**Prose that overstated the control** was corrected in four places, including
one claim that the expectation shares no producer with the resolver -- it shares
`amount_rule`, and a MANUAL loan payment misread as a plain shadow is silent to
both sides. That is dormant only because `budget.loan_payment_settings` is empty.

## What it opened

**`N-549`**: pass 1's agreement arm compares a value with itself in both
directions. `resolved` and `today` are the same expression one line apart, and
`stored` is non-NULL only on an OWN row whose rule answers by returning that
column. Both arms were removed from the exit gate here and labelled structural;
whether an agreement pass should exist at all once `X-au-d` deleted its second
producer is a design question this step did not answer.
