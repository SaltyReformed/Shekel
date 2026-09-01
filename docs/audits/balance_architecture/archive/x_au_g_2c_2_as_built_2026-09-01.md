> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is `docs/plans/steps.md`; the code as
> committed is the source of truth for what the app does.

# `balance:X-au-g-2c-2` as built -- a transfer shadow's amount is its parent's

Commit `1f2b98a4`. Rulings **R-IN** and **R-IO** (developer, 2026-09-01).

## What shipped

Every transfer SHADOW declares `parent_transfer` and stores no figure, so Transfer Invariant 3 is
STRUCTURAL. Deleted with it: `update_transfer`'s amount copy, `restore_transfer`'s drift corrector,
`LoanPricing.live_cash` and its scenario-wide config map, `_loan_installment._manual_shadow_amount`,
and `_settle.frozen_amount`. `LoanPricing` takes neither a clock nor a scenario now, and deleting
its config map removed the cash-ledger package's ONLY `budget.transfers` query.

## Measured, on a restored copy of production (stamp `a4c6f1d92b73`, 2026-09-01)

| measurement | value |
|---|---|
| transfer shadows declared by the migration | 350 |
| non-shadow rows touched | 0 |
| shadows whose figure differed from their parent's, before | **0** |
| rows in `budget.loan_payment_settings` | 0 |
| downgrade round trip over all 1,028 transactions | **byte-identical** |

So it moved `$0.00`, and the loan arm is graded on a seeded mortgage rather than on live data.

## What two adversarial reviews found, and what each cost

* **A money defect this step was ADDING.** `routes/transfers/mutations.py` raises `is_override`
  when the amount changed OR THE PERIOD DID, and that form posts every input it renders -- so a
  period-only save arrives with the amount echoed unchanged beside the flag. Taking ownership on
  the flag alone froze a derive-mode loan payment's legs at the stale `transfers.amount` snapshot:
  finding **N-238**'s own exposure, added by the step whose docstring claimed to remove it.
  Ownership now requires the figure to have MOVED. **No test could see it** -- both cases written
  for period moves sent the carry-forward payload -- so a case now PATCHes the real form.
  Confirmed by mutation: with ownership on the flag alone, that case is the ONLY failure and its
  six siblings pass.
* **The freeze log event was WIDER than the producer it replaced.** A first replacement asked the
  amount model for the RULE, which is true of any loan payment; the deleted producer's candidate
  map admitted only derive-mode payments and manual ones with a standing extra. It compares the
  booked figure against the transfer's own now, reproducing the old extension exactly. Its missing
  `held` arm -- a re-settle honouring a retained correction logged a freeze that had not happened,
  reporting the derivation's figure as the booked one -- is fixed and tested.
* **Two claims written about this step's own fixes were FALSE**, each naming a mutation that
  survives; corrected in place rather than deleted.
* **Four assertions could not fail**: an equality whose two sides came from one producer, an
  entailed inequality, two bare `IntegrityError` raises, and a `match=str(id)` that the revision
  id's own digits satisfied.
* **`pricing_load_options` named SEVEN relationships; the rules walk EIGHT.**
  `Transaction.pay_period` was found by this step's query-count control, not by the census -- and
  the same fix removed a duplicate loader strategy in `query_shadow_income`.
* **Two FALSE statements of current behaviour in `app/`**, one of them a reason paragraph a later
  step would have cited (`row_valuation` naming a survivor that `X-au-g-2c-1` had already routed).
* `Transaction.__repr__` renders `$None` for a derived row. Cosmetic; reported, untouched.

## Reported, not fixed

`tests/` is outside the lint gate and a SQLAlchemy declarative class has no `__slots__`, so
`obj.typo = value` followed by `commit()` succeeds silently. That is how two escrow probes in this
step's own tests wrote `EscrowLine.annual_amount` -- a column that lives on the VERSION -- and one
assertion passed because nothing had changed. A fixture helper asserting the name is mapped would
close the class; it touches every fixture and was left for a developer decision.
