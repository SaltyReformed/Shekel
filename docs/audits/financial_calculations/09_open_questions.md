# Open Questions

Questions surfaced during the audit that the developer must answer before the corresponding finding can be classified. Phases write into this file as questions arise; later sessions consult it.

## Candidate behavioral expectations needing developer confirmation

(P0-b populates this section.)

Q-01: Is the canonical rounding rule for monetary calculations `Decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)`?

Why it seemed natural to assume: the audit plan's inventory step (section 1.1) and required-grep list (section 13) both name `ROUND_HALF_UP` and `quantize` as expected patterns to find in the code, and the schema rule `NUMERIC(12,2)` fixes storage precision at two decimal places, so a single rounding mode is strongly implied for any computation that produces a stored or displayed money value.

Why no explicit source could be found: `grep -inE "round_half|quantize|two decimal|2 decimal|cent" CLAUDE.md docs/coding-standards.md docs/testing-standards.md` returns no matches; the standards documents fix the storage type but not the rounding mode used by calculation code.

Question for the developer: should every monetary calculation that produces a stored or displayed value quantize to two decimal places using `ROUND_HALF_UP`, and if so, do you want this added to section 0.3 as a behavioral expectation (with citation to your answer) before P0-c starts the plan-vs-code watchlist?

## Cross-plan contradictions to adjudicate

Questions surfaced by P0-c when comparing watchlist entries across plans. Each maps to a `C-NN` entry in section 0.5 of `00_priors.md`. The developer answers; Phase 3 then has a single source of truth to compare the code against.

Q-02 (maps to C-01): Carry-forward envelope semantics. The plans `carry_fwd_design` (Option F) and `carry_fwd_impl` (its execution) settle the envelope source row in place (status DONE/RECEIVED, `actual_amount = entries_sum`, `pay_period_id` unchanged) and bump the target canonical's estimate; `envelope_view` keeps the source row moving (post-33cd21e behavior) and groups canonical plus carried members at display time via a new `carried_from_period_id` column. Which architectural shape is the current direction for envelope items: data-layer settle (Option F), display-layer envelope view, or both layered together?

Q-03 (maps to C-02): Recurrence skip rule for `is_override` rows. `carry_fwd_impl` leans on the existing rule that any `is_override=True` row blocks regeneration of its canonical; `envelope_view` (sections 4.4 and 12) narrows the rule so carried-only overrides do NOT block generation, only non-carried (manually-edited) overrides do. Should the recurrence and transfer-recurrence engines treat carried overrides as non-blocking (envelope_view's narrowing) or continue blocking on any override (carry_fwd_impl's assumption)?

Q-04 (maps to C-03): ARM `current_principal` source for projection. `section5` (5.1-2) says current principal must be derived from confirmed payments via engine replay and replace the stored `LoanParams.current_principal` for projection purposes; `arm_anchor` (3F) says for ARM loans `_compute_real_principal()` must return `current_principal` directly without replaying payments because forward-from-origination is mathematically wrong without complete rate history. For ARM loans specifically, should the audit verify that the code uses the stored `current_principal` (per `arm_anchor`) or the engine-replayed value (per `section5`), and is the answer different for fixed-rate loans?

Q-05 (maps to C-04): ARM monthly payment computation. `section5` describes the engine replaying actual payments from origination and re-amortizing at every rate change. `arm_anchor` (1A, 1D) introduces an anchor reset at today using `current_principal` and `current_rate`, and recomputes the monthly payment from the anchor forward; pre-anchor rows are approximate. Inside an ARM's fixed-rate window, these two methods can produce different monthly payment values when rate history is incomplete. Which method is current, and is the developer's reported "fluctuating monthly payment" symptom a manifestation of the methods being mixed across entry points?

Q-06 (maps to C-05): Year-end mortgage interest source. `section8` (13.D) defines mortgage interest total as the sum of interest portions from amortization schedule rows whose `payment_date` falls in the calendar year. `year_end_fixes` (1) requires escrow subtraction from shadow transaction amounts and biweekly-month redistribution before amortization. Should the `section8` definition stand on its own, or is it superseded by `year_end_fixes`'s preprocessing requirements such that `section8`'s rule is incomplete on its own?

Q-07 (maps to C-06): Envelope source row `pay_period_id` after carry-forward. `carry_fwd_impl` (Phase 4 step 7) says the envelope source row stays in its original period as a settled record; `prod_readiness_v1`'s description of `carry_forward_unpaid` (WU-10) implies template-linked transactions move to the target period and are flagged as overrides. These describe different generations of carry-forward behavior. Which behavior does the current code embody for envelope vs non-envelope rows, and is `prod_readiness_v1`'s description superseded by `carry_fwd_impl`?
