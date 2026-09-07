# Developer rulings of 2026-09-06/07 awaiting a registry pass

> **HOLDING PLACE, NOT A PLAN OF RECORD.** This file exists because the findings and developer
> rulings of 2026-09-06/07 were made while the registry lane was occupied, and lived only in
> untracked files and session transcripts. It governs NOTHING. Each arc's registry pass moves its
> content into `docs/plans/` (`ledger.md`, `steps.md`, `rulings.md`), and
> **this file is deleted once consumed**. Where it disagrees with `docs/plans/` or with the code as
> committed, they win.

## Developer rulings of 2026-09-06 that are NOT YET in docs/plans/rulings.md

**These live only in session transcripts and this file until each arc's registry pass lands.** A
transcript is one context-clear from gone. Each lane owes its own rulings a registry commit; this
file is the coordinator's backstop, not the home.

Recorded by the coordinator session (shekel-7a) as they were made. Every one was answered by the
developer directly, either in the owning lane's session or in the coordinator's.

---

### pay_calendar

**`R-PC63` -- C14-f IS THE DOOR FIX, not a check.** Answered in the coordinator session. An owner
who already holds a rhythm is asked only how many more paychecks; the days come from the producer
Extend already uses. This makes P80's worked example UNWRITABLE rather than detectable. Accepted
costs, both put to him explicitly: **it SUPERSEDES `R-PC55`** (that supersession rides with this
ruling and is not a separate question), and it overlaps C8 at rank #46. The residue it does not
close is `regenerate` and `reset`, already C17's via **N-492**. Rejected in the same pass: shipping
a narrowed check (its predicate is unestablished -- the lane built its test record with the same
shift function the replacement calls, making five of eight comparison cells vacuous), and
withdrawing C14-f into C17.

**THE SUB-FORK -- `CONTINUE` (dispatch), not refuse.** Answered in the coordinator session. When an
owner who already holds paydays reaches `POST /pay-periods/generate`, the route DISPATCHES: they get
`extend_pay_periods(user_id, num_periods)`, the days come from `nominal_payday_after`, and the
submitted `start_date` / `cadence_days` / `shift` are simply not consulted. The reason he accepted
is precedent, not effort: `C3-b` deleted `cadence_days` from the Extend door at **P29**, and
`PayPeriodExtendSchema`'s docstring states the disposition verbatim -- an old client that still
posts one is not refused, the value is ignored, which is now what it means. Same arc, same weld,
opposite door. It also preserves the `D58` main-nav path, which was FIXED by adding populate rather
than closed. **CONSEQUENCE: `PC-498` is NOT on C14-f's critical path.** No `pay_period_admin` split
is needed, no home for a "does this owner hold paydays" predicate, and no placement ruling. PC-498
stays OPEN and UNOWNED.

**FORK 4 -- PULL C8's DOOR HALF INTO C14-f.** C14-f absorbs the door work; C8 keeps whatever of
P30/P47 is not the door, narrowed, at rank #46. **CAUTION, unresolved:** he ruled this BEFORE the
lane reported that under `CONTINUE` it never touches `cadence_days` on any form -- the Generate card
simply stops rendering for owners with a rhythm. So there may be less to absorb than the option
described. The coordinator's reading, flagged to the lane AS the coordinator's and not his: C14-f
owns the generate door's cadence question (discharged by the conditional render), C8 keeps the
standalone forecast-cadence control plus `regenerate` and `reset` (`routes/pay_periods.py:297`,
`:370`). **If the build disagrees, the corrected premise goes back to the developer -- the ruling
must not drift under a reinterpretation.**

### balance

**The leftover row's date.** A carried-forward leftover is created with the date its definition's
own rule gives for that paycheck: `recurrence_engine._plan.compute_due_date(rule, target_period)`,
the app's single producer of that date. Where the rule has been CLEARED
(`_recurrence_form_helpers._clear_recurrence_rule`, a live door; 0 of 39 templates on the prod
clone) it takes the paycheck's start date, which is `compute_due_date`'s OWN answer for a cadence
naming no day of the month -- so the two arms are ONE rule, not two. Rejected in the same pass, so
none is re-proposed: the paycheck's start date unconditionally (dates a bill due on the 22nd as due
on payday and prices it pre-raise -- **$180.00 against $210.00** on the worked example, making
ruling D5's objection permanent); the paycheck's last covered day (a fourth spelling of a row's
date); dropping the template link (breaks carry-forward's own next pass, which finds the leftover by
`Transaction.template_id` at `_context.py:241`); and guarding the chooser (the fence).

**The CHECK is `template_id IS NULL OR due_date IS NOT NULL`**, replacing the three-term predicate
staged in `b4d9e1c7a052`. The staged one admits the undated leftover and refuses only the
TRANSITION, converting the chooser's declare into an IntegrityError -- a 500 -- and would then need
a guard in `resolve_conflicts`. The two-term form is invariant under that declare (it touches
neither column), and no writer in `app/` sets `template_id` on an existing row, so once the producer
is fixed nothing can reach the state and NO GUARD EXISTS ANYWHERE. It also survives `X-au-l` without
a rewrite. Both predicates refuse ZERO rows on dev and on the prod clone.

Standing direction he restated unprompted to that lane: **"I will only accept root cause solutions
and the best from scratch design. NO fences. NO checkers."**

### bank_import

**`R-BI2`** -- the X-gi-2a fork: `X-go` -> `X-gp` -> `X-gi-2a`, superseding `R-IV`.
**Already durable** at `1e932188` on `origin/feat/x-gi-2`, in
`docs/design/bank_import_consent_and_member_cap.md`, outside `docs/plans/`. This one does not need
the backstop.

### salary

`R-SAL15`, `R-SAL16`, `R-SAL17` -- minted by the lane, which holds the registry lane and is landing
them now. See ID-REGISTER.md.

### shared tooling -- `N-548`, RULED BUT UNOWNED

**Ruled: gate NEW migrations AND triage the existing 24.** Answered in the coordinator session. Two
parts:

1. A scoped rcfile neutralising ONLY the two Alembic false positives -- `C0103` on `revision`,
   `down_revision`, `branch_labels`, `depends_on` (Alembic's loader reads them by name, so the
   lowercase names are mandatory) and `E1101` on `alembic.op` (a runtime proxy) -- with all nine
   custom checkers ARMED, plus `files: ^migrations/.*\.py$` in pre-commit. Pre-commit passes only
   CHANGED files, so this gates every NEW migration and touches ZERO of the 181 existing ones.
2. A triage pass reading each of the 24 existing ref-table name comparisons in migration SQL,
   recording which were reasoned tradeoffs and which were never looked at.
Measured basis: `migrations/` is linted by nothing -- pre-commit scopes pylint to `app/`,
`scripts/`, `tests/`, `tools/`; CI's six lint steps never target it; the Stop hook runs
`pylint app/`. 181 files, all nine custom checkers blind, 24 ref-table name comparisons, 0
`Decimal(<float>)`. Rejected: linting the whole tree (measured UNBUILDABLE -- a correct shipped
migration rates 0.00/10 and all 181 would hard-fail the `E` gate, each needing disables) and closing
the finding unbuilt.

**NO OWNER AND NO STEP ID YET.** All four lanes are on other work. Needs both.

## Corrections OWED to `docs/plans/`, which still carries the wrong text today

These are not new findings. They are places where the plan of record, as committed, says something
the code refutes -- so a session that opens `ledger.md` or `steps.md` and works from it is
misdirected. Each was verified against dev at the line cited.

### `BAL-463`'s REMEDY IS AIMED AT THE WRONG TIER (`ledger.md:34`)

The row's status column says *"the remedy makes the guard STRUCTURAL rather than conventional, so a
migration cannot ship a bare declare"*.
**The strand producer is APPLICATION code, not a migration.**

`carry_forward_service._execute._create_target_override_row` creates a leftover row copying
`template_id` with `due_date=None`; `recurrence_engine._conflicts.resolve_conflicts:200` ("use the
template's amount") then calls `declare_derived` on it, emptying its figure. The row is then priced
by amount rule 3, which resolves on the row's OWN due date -- and it has none, so `_stated_amount`
raises `AmountUnresolvable`, which has five handlers in `app/` and none on the grid, dashboard or
companion path.

**A step built to the row AS WRITTEN would have hardened migrations and left that live.** The
predicate stands and the row stays OPEN; only the remedy sentence is wrong. Fixed in code by `X-bv`
(PR #290, commit `66ff070b`), and `ledger.md:34` still carries the wrong sentence.

This is the one correction in this file that is a TRAP rather than a fact a reader can re-derive: it
cost the balance lane its first hour, and the document that would have corrected it was untracked.

Also refuted, so it is not re-proposed: the claimed live path at
`app/schemas/validation/transactions.py:105/:154` is NOT it.
`_gates._reject_generated_due_date_edit` already refuses `due_date` on any template-linked row keyed
on presence, wired at `mutations.py:338`; the `:154` schema is the ad-hoc create, which cannot set
`template_id`.

### `N-365` calls a fork open that `R-IR` answered (`ledger.md:49`)

The row still says whether the remedy is a SPLIT per module or a different ceiling is a fork `X-be`
owes. Ruling `R-IR` answered it on 2026-09-01. Pre-existing; caused by none of today's steps.

### `steps.md:70`'s `X-bv` row is the REFUTED specification

It still specifies the migration-guard remedy above. It needs re-writing to the producer fix, not
merely re-ranking.

### `steps.md` owes rows for `X-bz` and `X-ca`

Both are issued, both have merged or open PRs, and neither appears in any planning document.

## pay_calendar, 2026-09-07 -- FIVE rulings, none yet in `rulings.md`

Recorded verbatim from the lane as it closed. `grep -rl "R-PC63" docs/` returned nothing before this
file; these existed only in code comments and session transcripts.

1. **`R-PC63` -- `C14-f` is the DOOR FIX, not the check.** An owner who already holds a rhythm is
   asked only how many more paychecks; the days come from the producer Extend already uses.
   **It SUPERSEDES `R-PC55`, and that supersession rides WITH the ruling** rather than being a
   separate question to re-ask. Two costs accepted: the `C8` overlap, and that the residue is
   `regenerate`.
2. **The sub-fork is CONTINUE, not refuse.** A submitted `start_date` / `cadence_days` / `shift` is
   IGNORED, not refused, on `PayPeriodExtendSchema`'s own **P29** precedent -- *"an old client that
   still posts one is not refused; the value is simply ignored, which is now what it means."*
3. **Fork 4: pull `C8`'s door half into `C14-f`.**
4. **`regenerate` is `C17`'s era-mint in disguise; guard it meanwhile** with a confirmation when a
   rebuild skips a whole paycheck. **`P80` is RE-POINTED at `C17` and explicitly NOT CLOSED** -- a
   confirmed gap is still a gap. **DO NOT TICK P80.**
5. **Split the GATES out of `pay_period_admin`** -- `PC-498`'s stated remedy, the developer's
   placement, mirroring `C3-a`. Shipped as `app/services/pay_period_gates.py`.

### `steps.md:82` is the REFUTED specification and misdirects a fresh session

It still reads *"Add the two-clause integrity check the convention makes statable (**R-PC55**)"* --
the design ruling 1 SUPERSEDED. A session doing what `CLAUDE.md` says (start at `steps.md`, take the
first row) would rebuild the check the developer ruled away, against a door that no longer needs it.

### Still owed ids and rows for pay_calendar

`C14-f`'s row rewrite, `P80`'s re-point, and four reported-not-fixed findings.
**The one that must not be softened:** the step NARROWS `N-493` / `N-494`, which are `C14-f`'s own
rows -- `generate` was the repair door for an owner whose stored `(cadence, shift)` pair became
illegal, and they now dispatch into `extend`, which hands the pair straight back.
