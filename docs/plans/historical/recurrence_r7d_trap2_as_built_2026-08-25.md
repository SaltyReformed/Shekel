> **ARCHIVED. Historical record only -- this document governs nothing and
> is not a plan of record.** It records how two claims about `recurrence:R7d`
> were measured and settled. Cite it for how a decision came to be; never for
> what is true now. The code as committed is the source of truth.

# R7d TRAP 2, and the scope of R7d-a's invariance

Both paragraphs lived in `implementation_plan_recurrence_redesign.md`'s R7d container until
`recurrence:R7d-b`. TRAP 2 is CLOSED by a shipped step and the invariance residue is carried by
`ledger.md` row `balance:N-352` with an owner, so neither is a live constraint on any remaining
leaf.

## TRAP 2 -- it was this step's open problem, and R7d-a closed it

R7d's second design argued that a bound resolved before the rows exist is always LATER than the true
one, so it could only over-generate. Measured FALSE, and re-measured 2026-08-25 through the door
that presents the state: `regenerate_pay_periods` deletes the rebuildable tail of pay periods and
repopulates, `budget.transfers.pay_period_id` CASCADEs, so generation resolves against a loan with
no future payments. With the Van's definition PLANTED at `$300.00` against its `$531.94` contractual
installment, the bound read **`2029-02-22` at that moment against `2030-02-22` before and after --
twelve payments the owner owes.**

The cause: the plan answered "what will this loan be paid in month M" two ways, a materialised row's
cash or the CONTRACT. **R10-b did NOT close it** -- that fixed the template-edit sweep, and this is
another door. Those two answers also switched the projection at the materialised HORIZON
(`2030-02-22` with rows to 2028-07, `2030-04-22` with rows to 2029-01); both closed in one commit
(`89cb0c1d`, ruling R-R33), which took no ledger id and is its own record.

## What R7d-a's invariance actually is, and it is SCOPED

The payoff stops depending on materialisation whenever the rows AGREE with their definition, which
is the state the app's own amount door leaves behind. The residue is the window between a
restatement and the regeneration that applies it, and it closes at `balance:X-au-f` rather than in
the recurrence arc: row **N-352** carries the measurement.

## The READER census that forced the seven-leaf split (R-R34)

The census, taken (2026-08-25, by setting `end_date` NULL on both live
rules, then corrected by an adversarial review that found it wrong in both directions). SEVEN
surfaces read a loan payment's closing bound and this specification named one: generation stops at
the payoff; `recurring_view` / `describe` print "until Jan 22, 2029";
`obligations_aggregator.has_ended` takes a RETIRED loan's payment out of `/obligations` and the
`/savings` emergency-fund baseline (`$2,442.89` a month across the two live loans); the locked
"Ends" control shows the payoff; `_recurrence_form_refusals` reads the column for its
inverted-window check; **`update_recurrence_rule_from_form` READS THE BOUND AND WRITES IT BACK** on
every unrelated edit of the template (`_recurrence_form_helpers.py:617` into `reauthor_rule`), which
no NULL-the-column census can see because the difference is in what the next save persists; and
`_sync_loan_cadence` does the same round-trip inside the module R7d-g rewrites, so it OUTLIVES that
leaf unless named. `_recurrence_preview` is NOT one -- it composes from `request.args` and its
control is `disabled` when locked, so it renders "never ends" already. Ruling **R-R34**.

