# Archive: superseded balance-architecture documents

**Read-only history. Nothing in this folder governs work.** The single live document is
`../README.md`, the CASH plan of record since 2026-07-26 -- and since 2026-08-04 "single" is
literally true again: `anchor_settle_partition.md` had become a second one, which Section 9 rule 1
prohibits, and it is now in this folder. Every file here is either a completed as-built record, a
superseded plan, or an audit whose surviving findings were absorbed into the plan of record's
findings ledger. Archived 2026-07-16, extended 2026-07-26 and 2026-08-04.

**The 2026-08-04 extraction changed what an as-built record IS, and the two earlier ones do not
follow the new rule.** `phase_x_as_built_2026-08-04.md` is CONDENSED -- one line per step, its
verified commit hash, and what it closed -- because this arc repeatedly carried a claim into an
as-built that the code later contradicted. The two records before it are verbatim moves of the plan
document's prose (211 KB and 252 KB), and the two standing warnings at the foot of this file apply
to them with full force. Section 9 rule 5 of the live document now requires the condensed form.

**Start here for the CASH half:** `phase_x_as_built_2026-08-04.md` for anything from X-c2c4
onward, and `cash_arc_as_built_2026-07-27.md` for X-a through X-g3b before it. **"Shipped" here
does not mean "deployed", and the split is per section**: everything through Section 1 is in
production (PR #65, merge `69a527cd`, and the per-step PRs that record names), while **Section 1a's
X-f1 cluster is COMMITTED ON `feat/xf1-settle-day` AND NOT DEPLOYED** -- production still runs merge
`e5f27154` at migration head `d7c1f4a9e603`. **Phase X is still IN FLIGHT**, so these are
shipped-so-far records; the remaining steps are live in `../README.md`. The earlier record also holds the plan of record's whole running-state narrative
from that span -- read it as history, and re-verify any figure in it against the code.

**Start here for the loan half:** `loan_arc_as_built_2026-07-26.md`. It is the as-built record of
Phases A-F -- the whole loan arc, shipped to production in PR #64 -- extracted verbatim from the
plan of record when that document was trimmed to the work that remains. It also holds the register
of every finding the arc CLOSED. The loan questions that are still OPEN are NOT there: they stayed
in the live findings ledger, because unfinished work belongs where work is planned.

| file | what it was | why archived |
|---|---|---|
| `anchor_settle_partition.md` | The anchor/settle partition arc, and for a while a SECOND live planning document for this arc (finding N-175, which rule 1 prohibits): ruling R-DH's six parts, steps 1-4 and S1-c as built, the F1-F12 adversarial-review register, F11's measurement, and the from-scratch redesign that was measured and REJECTED because it re-opens `-$4,001.42` by another route | Superseded 2026-08-04. Everything in it had shipped (PRs #67, #75, #76), been absorbed (S2-b into X-f1), or was already a row in the live ledger -- **except three obligations with no home there, which were carried into `../README.md` FIRST**: the uniqueness-index re-key at X-f1c4, and X-d's already-measured ship gate plus the `_attribution.py` loaders it inherits. Read it for how the day partition was decided; do not read it as instructions |
| `phase_x_as_built_2026-08-04.md` | Phase X from **X-c2c4** to **X-f1e1**, CONDENSED: one row per step (id, verified commit hash, the commit's own subject, findings closed), the six findings closed outside a step, and the 79 rulings whose work has shipped | Extracted 2026-08-04, when the live document stood at **6,688 lines**. Section 9 rule 4 became a hard 1,000-line cap with a gate behind it, and rule 5 became "archived whole and CONDENSED, not moved verbatim". Read it to find WHICH COMMIT shipped a behaviour; read the commit for why. **Extended 2026-08-05** with Section 1a, the X-f1 cluster's eleven shipped leaves, when the live document hit 1,195 of its 1,200-line cap -- the second time rule 4 bound |
| `cash_arc_as_built_2026-07-27.md` | The CASH half as far as it has shipped: the plan of record's entire preamble (its running state narrative), Phase X steps X-a .. X-c2c3 and X-g1 .. X-g3b as built, and the 10-row closed-findings register | Extracted 2026-07-27 on the developer's instruction, at 2,982 lines, so the plan of record carries the work that REMAINS rather than the log of work that is done. **Phase X is NOT complete and none of it is in production** -- read this for how a shipped cash behaviour was decided and which commit shipped it; the remaining steps are live in `../README.md` |
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
