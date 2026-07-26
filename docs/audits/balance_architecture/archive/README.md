# Archive: superseded balance-architecture documents

**Read-only history. Nothing in this folder governs work.** The single live document is
`../README.md` -- which since 2026-07-26 is the CASH plan of record. Every file here is either a
completed as-built record, a superseded plan, or an audit whose surviving findings were absorbed
into the plan of record's findings ledger. Archived 2026-07-16, extended 2026-07-26.

**Start here for the loan half:** `loan_arc_as_built_2026-07-26.md`. It is the as-built record of
Phases A-F -- the whole loan arc, shipped to production in PR #64 -- extracted verbatim from the
plan of record when that document was trimmed to the work that remains. It also holds the register
of every finding the arc CLOSED. The loan questions that are still OPEN are NOT there: they stayed
in the live findings ledger, because unfinished work belongs where work is planned.

| file | what it was | why archived |
|---|---|---|
| `loan_arc_as_built_2026-07-26.md` | The LOAN half of the plan of record: its running state narrative, the loan problem and root causes, the loan fold's target shape, rulings D1-D5 / R-A / R-C / R-D / R-E, Phases A-E and F2/F3 as built, and the 75-row closed-findings register | The loan arc is COMPLETE and in production (PR #64, merge `88c79857`, 2026-07-25). Extracted 2026-07-26 so the live document could shrink from 2,713 lines to the cash work that remains. Read it for how a shipped loan behaviour was decided and which commit shipped it; do NOT read it as instructions |
| `recurring_loan_balance_root_cause.md` | The original 2026-06-26 diagnosis | Superseded by the deeper root cause (partial balance function) |
| `implementation_plan_level1_balance_seam.md` | Level-1 `balance_at` seam plan | SHIPPED to prod 2026-06-27 (PR #45) |
| `implementation_plan_kind_correct_grid_interest.md` + `followup_kind_correct_grid_interest.md` | INTEREST-kind grid balance | SHIPPED to prod 2026-06-28 (PR #47) |
| `implementation_plan_posting_ledger_transfers.md` | Posting ledger Step 2 (transfers) | SHIPPED to prod 2026-06-28 (PR #48) |
| `implementation_plan_posting_ledger_cash_envelopes.md` | Posting ledger Step 3 (cash) | SHIPPED to prod 2026-06-29 |
| `implementation_plan_posting_ledger_loan_payments.md` + `adversarial_review_posting_ledger_loan_payments.md` + `implementation_plan_temporal_escrow.md` | Posting ledger Step 4 (loan splits) | SHIPPED to prod 2026-07-01 (PR #51) |
| `implementation_plan_loan_read_switch.md` | Ledger-authoritative loan reads | SHIPPED to prod 2026-07-02 (PR #52) |
| `implementation_plan_actuals_reporting.md` | Build-order Step 5 (actuals reporting) | SHIPPED (PR #58 merged) |
| `adversarial_review_balance_architecture_2026-07-02.md` + `adversarial_review_bb567e9_oracle_teeth.md` | Full-arc review + oracle-teeth review | Their R-items shipped; process lessons carried into the plan of record |
| `level1_level2_scope_and_fitness.md` | Level-1/Level-2 scoping | Superseded by the one-fold model |
| `followup_fence_loan_owed_at_dates.md`, `followup_debt_schedule_attribute_fence.md`, `followup_horizon_loan_predicate_split.md`, `followup_redundant_loan_resolution.md` | W9906 fence follow-ups + the 11x-resolution finding | All CLOSED on dev 2026-07-12/13 |
| `implementation_plan_loan_resolution_context.md` | `BalanceContext` (read-pass memo) plan | SHIPPED on dev 2026-07-13 (`b61aee9c`..`7b7c909b`) |
| `implementation_plan_fail_loud_ledger_authority.md` | Fail-loud arc; AS-BUILT record of C1/C1b/C2/C2b/C3 | C1-C3 shipped on dev; its "next up" superseded; kept as the authoritative as-built record of those five commits |
| `audit_loan_balance_producers.md` | The 2026-07-14 producer audit (findings B-1..B-22) | Register absorbed into the plan of record's findings ledger; its S0-S9 arc superseded |
| `adversarial_review_arc_and_direction_2026-07-14.md` | "Am I chasing my tail?" review; proved the fold exists and is total | Its recommendation became the from-scratch plan; evidence record |
| `implementation_plan_loan_balance_from_scratch.md` | The one-fold plan, rulings D1-D5 | ABSORBED (with amendments) into the plan of record |
| `audit_cash_balance.md` | The cash-side audit (D1-D4, X0-X4) | Absorbed with corrections: X0's premise was measured false; D4's root cause corrected (see plan of record) |
| `adversarial_review_reevaluation_2026-07-16.md` | Independent re-verification of the whole arc | Verified the fold day-by-day (212 days, 0 mismatches); corrections and amendments absorbed into the plan of record; evidence record |

Two standing warnings for anyone reading these for history:

* **Dollar figures and line numbers in archived documents were true on their write date only.**
  Several were already stale within days (the Checking figures moved with every re-anchor).
* **Three of these documents contain at least one load-bearing claim later proven false or
  uncited** (X0's premise, the cash "anchor has no date" root cause, M5's severity, one plan
  citation). The corrections live in `adversarial_review_reevaluation_2026-07-16.md` Section 3
  and are reflected in the plan of record. Do not act on an archived claim without re-verifying
  it against the code.
