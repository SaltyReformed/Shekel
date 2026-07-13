# Fail-loud ledger authority: delete the schedule's answer for the past

**Status: C1 + C1b DONE (2026-07-13); FU-4 FIXED. C2-C6 pending.** Successor to
`implementation_plan_loan_resolution_context.md`, whose adversarial review (2026-07-13) produced the
findings below. Prerequisite reading: `recurring_loan_balance_root_cause.md` (the design rule this
plan finally enforces), `implementation_plan_loan_read_switch.md` (which made the loan's PAST
ledger-authoritative), and `followup_debt_schedule_attribute_fence.md`.

---

## 1. The one-sentence problem

A loan's balance for a past date can still be answered by TWO different producers that disagree, and
the pylint fence that is supposed to make that impossible reports 10.00/10 while a route renders a
loan balance straight out of the resolution context.

---

## 2. What is actually broken (plain language, with the numbers)

### 2a. The phantom-paydown fix was applied to one producer and not its twin

The loan-resolution-context arc found that when the genesis ledger cannot answer for a loan, the
scalar `amortizing_balance_at` fell back to walking the loan's WHOLE amortization schedule -- so
projected rows that were never actually paid still reduced the reported balance. A $240,000 loan
originated 18 months ago and never paid read as though 17 installments had been made. The arc fixed
that scalar (`net_worth_kernel.py:437-443`) by filtering the walk to CONFIRMED rows only.

It did not touch the per-period sibling. `_build_amortizing_balance_map` (`net_worth_kernel.py:873-877`)
still calls `compute_loan_period_balance_map` with the FULL schedule, and
`balance_from_schedule_at_date` (`account_projection.py:197-201`) reads every row regardless of
`is_confirmed`.

Measured (a $240,000 loan, 6%, 360 months, originated 548 days ago, never paid, ledger not opened):

| read path | producer | balance today |
|---|---|---|
| ledger open (what production does) | scalar | $240,000.00 |
| ledger open (what production does) | per-period map | $240,000.00 |
| **no ledger** (what every loan test does) | **scalar** | **$240,000.00** |
| **no ledger** (what every loan test does) | **per-period map** | **$236,544.21** |

A $3,455.79 divergence between two producers the arc's own docstrings call siblings that "split on
the one boundary the loan architecture turns on."

Driven end to end through `compute_dashboard_data`, that is one page contradicting itself:

```
AssertionError: the /savings loan tile and the net-worth trend's own 'today'
point disagree: tile=240000.00 trend=236544.21
```

The year-end summary has the identical split internally: its debt-progress section reads the scalar
(`year_end_summary_service/_net_worth.py:206`), its net-worth section reads the map (`:115`).

**Production money impact today: none.** Verified against the dev clone: both real loans
(Mortgage id=3, Van Loan id=8) carry `loan_opening` postings, so production takes the ledger path
where the two agree. This is latent on real data and LIVE in the test suite.

### 2b. The fence is green and does not hold

The arc fenced the three resolver FUNCTIONS (`resolve_account_loan` / `resolve_loan_seeded` /
`resolve_loan_bundle`) and then introduced `BalanceContext.loan()` and `.loan_state()`
(`resolution_context.py:138,165`) -- public METHODS that hand any caller the whole `ResolvedLoan`,
including `state.current_balance`, which IS a balance-at-today.

The checker cannot see them. `visit_call` matches names and neither `loan` nor `loan_state` is in a
producer set; `visit_functiondef` returns early unless the parent is a Module
(`balance_seam.py:471`), so methods are never classified at all.

`app/routes/accounts/detail.py:803-808` -- the Property detail route, which is NOT in
`_LOAN_RESOLVER_MODULES` -- does exactly this, reads `state.current_balance` at `:664` and `:679`,
and `property_equity_chart.py:214,218` renders the on-screen secured-debt line from
`row.remaining_balance`. Proven silent: a probe consumer added to `app/routes/companion.py` reading
`ctx.loan_state(account).current_balance` rates 10.00/10.

`LoanFigures` was built to close this "by CONSTRUCTION rather than by policing"
(`balance_at/_loan_figures.py:20`). It closed the front door and the same commit opened a back one.

### 2c. W9909's fail-closed check does not cover the surface this arc fenced

`balance_seam.py:213-229` argues that a name-keyed deny list "fails OPEN," that this shipped twice,
and that "two identical misses is a design defect in the FENCE." Then the same commit added
`_LOAN_RESOLVER_PRODUCERS` without adding its defining modules to `_FENCED_MODULE_RULINGS`. A test
at `tools/pylint/tests/test_shekel_checkers.py:1300` pins the ruling set to the engine cluster plus
`loan_posting_service`; `app.services.loan_resolution` and `app.services.resolution_context` are
absent.

Proven: a new public `loan_balance_right_now()` added to `loan_resolution.py` (returning
`resolved.state.current_balance`) rates 10.00/10. `contractual_schedule_from_origination` already
sits there public and unclassified.

### 2d. `debt_schedule_rows`'s stated premise is false

`net_worth_kernel.py:220-223` says handing consumers rows "is what makes the fence real," because
"the rows carry no balance." `AmortizationRow` carries `remaining_balance`
(`amortization_engine/_projection.py:220`). Every row is a balance. Removing `current_balance` from
the bundle was still worth doing; it did not close the hole by construction, and the docstring says
it did.

### 2e. The test fixtures do not model production, which is why none of this was caught

`create_loan_account` (`tests/_test_helpers.py:436-512`), used at 117 call sites across 25 files,
never opens the ledger. The real route does, in the same transaction as the LoanParams insert
(`app/routes/loan/params.py:125`). `insert_trueup_event` writes a `LoanAnchorEvent` and never
reconciles it into postings; the production writer always does
(`anchor_service.py:374-390` calls `sync_all_scenarios_or_duplicate`).

So the entire loan suite runs on the no-ledger fallback -- a path production never takes -- and does
not run on the genesis ledger path, which production always takes. The purpose-built cross-page
oracle should have caught 2a (it genuinely compares the savings tile against the net-worth trend),
but its fixture (`tests/conftest.py:1531-1537`) inserts a true-up dated TODAY, which re-anchors the
schedule to today-forward and leaves no past-dated unconfirmed rows to phantom-pay. It is the one
loan shape in which the defect cannot appear.

**I dry-ran the fix.** Patching `create_loan_account` to sync postings like production:

```
15 failed, 7352 passed
```

All 15 reduce to two causes:

* **Six paid-off / true-up tests + the two cross-page oracles** fail because the fixture writes anchor
  events without reconciling them, so once the ledger is authoritative the true-up is invisible and
  every surface reports the original principal. The oracle failure is the clearest statement of the
  problem in the whole codebase: `savings=240000.00, loan_detail=240000.00, year_end=240000.00,
  net_worth_trend=240000.00, schedule_table=240000.00` -- five surfaces in perfect agreement on a
  number the fixture's own true-up says is $200,000.
* **Five `test_*_unconfigured_loan_returns_none` reader tests plus one posting-count test** fail
  because their premise ("this loan has no ledger") is what the fixture change removes.

---

## 3. The design rule this plan enforces

> **The past belongs to the ledger. The future belongs to the projection. Neither may answer the
> other's question, and there is no third source. A configured loan whose baseline ledger has no
> opening posting is BROKEN, and a broken loan fails loud rather than producing a number.**

The codebase already states half of this in two places and then breaks it in two others:

* `confirmed_loan_balance_at` RAISES on a future date (`loan_posting_service/_reader.py:200`).
* `loan_owed_at_dates` RAISES on a past-or-today date (`net_worth_kernel.py:314-320`), with the
  explicit reason that an overdue unconfirmed payment "would report the balance net of a payment that
  was never made -- silently UNDERSTATING the debt."
* And then `amortizing_balance_at` and `_build_amortizing_balance_map` each fall back to the schedule
  for the PAST when the ledger is silent -- committing precisely the understatement
  `loan_owed_at_dates` refuses to commit, in two different ways.

**Why this is the right fix and not a narrower one.** The narrow fix (make the map filter to confirmed
rows, matching the scalar) makes today's numbers agree and leaves two hand-synchronised boundary rules
in place, which is the band-aid this project has spent six months paying for. Deleting the fallback
removes the boundary rule entirely, and with it:

* the possibility of the two producers ever diverging again (structural, not policed);
* `compute_loan_period_balance_map`'s and its `current_balance` fallback argument -- the exact argument
  the W9905 `shekel-original-principal-as-balance` checker exists to police;
* the reason the test fixtures could get away with not opening the ledger.

**Why fail-loud is safe.** A configured loan without an opening posting is not a legitimate state. The
opening is written in the same transaction as the LoanParams (`app/routes/loan/params.py:125`), the
Step-4 migration backfilled every pre-existing loan, and `pay_period_admin.reset_pay_periods` resyncs.
The project already fails loud on the sibling invariant: a loan account with no linked ledger account
raises `PostingError` rather than degrading, and `confirmed_loan_view`'s own docstring calls that "the
project's fail-loud rule" (`loan_payment_service.py:500-509`). This plan adds the second invariant to
that same list.

**Where the fail-loud guard lives, and where it must NOT.** It goes in the READ seam
(`net_worth_kernel`'s loan producers). It must NOT go in the resolver: `contractual_schedule_from_origination`
(`loan_resolution.py:287-298`) deliberately passes `confirmed_view=None` to build the property chart's
pure contractual back-projection, so the resolver's None path is a legitimate, used API. The resolver
keeps its fallback; nothing can READ a balance out of it once C3 lands.

---

## 4. The standard of proof for changing a test

**Developer ruling, 2026-07-13:** *"The tests should test for the current state of the code. You may
change the tests when you can prove the tests are incorrect or do not match the code."*

That is the authority this plan operates under, and it is narrower than it sounds. It is permission to
correct a test that is WRONG. It is not permission to make a red test green. So every test this plan
touches carries its proof, in the commit message and in the test's own docstring:

1. **What it asserts**, quoted.
2. **What code path it actually exercises** -- named, and shown to be a path production does not take
   (that is the defect in almost every case here: the fixture never opened the ledger).
3. **Why the current assertion is wrong**, or why it pins behaviour this plan deliberately deletes.
4. **The recomputed expected value, by hand**, with the arithmetic shown -- for any assertion whose
   NUMBER changes.

**The hard stop stays.** If a financial assertion's number has to move and I cannot prove from the
code and the data that the old number was wrong, I stop and bring it to you with the arithmetic rather
than editing it. A red test I cannot explain is a finding, not a chore.

Against that standard, the 15 dry-run failures fall out as follows.

**F1. Six paid-off / true-up tests: fixture repair, numbers expected to hold.**
`TestPaidOffFlag::test_paid_off_true_when_confirmed_covers_balance`,
`TestDebtSummary::{test_debt_summary_all_paid_off, test_debt_summary_excludes_paid_off}`,
`TestDebtPrincipalProgress::{test_fraction_one_when_all_loans_paid_off, test_fraction_monotonic_one_paid_one_partial}`,
and `TestDTI::test_dti_zero_debt` mark a loan paid off by inserting a $0.00 true-up EVENT that is never
posted. **Proof they do not match the code:** production's true-up writer
(`anchor_service.py:374-390`) writes the event AND calls `sync_all_scenarios_or_duplicate`; the fixture
(`_test_helpers.py::insert_trueup_event`) writes the event only. Under the ledger an unposted true-up
does not exist, so `is_paid_off` correctly reads False. Repairing the FIXTURE (C1) makes the loan
genuinely paid off, and I expect every asserted number to hold unchanged. If one moves, rule (4) above
applies.

**F2. `TestUnpaidScheduleRowsNeverReduceTheDebt` is deleted, not repaired.** **Proof:** it pins the
behaviour of the no-ledger schedule fallback, and its own body asserts
`confirmed_loan_balance_at(...) is None` as its PREMISE. C2 deletes that fallback, so the test asserts
the behaviour of code that no longer exists. Replaced by `TestBrokenLoanFailsLoud`, which asserts the
raise, and by `TestScalarAndMapAgree`, which is the invariant the deleted test was a one-sided proxy
for.

**F3. Five reader tests keep their contract, and state their premise.** The
`test_*_unconfigured_loan_returns_none` cases test the READER's `None` contract, which is unchanged
(the reader still returns `None`; the SEAM is what raises). They currently get an unconfigured loan by
ACCIDENT, because the fixture never opened the ledger. They get `create_loan_account(...,
open_ledger=False)` so the premise is stated. No assertion changes.

**F4. Audit the rest of the loan suite for fallback-only assertions.** The 15 failures are the tests
that BREAK. There may be tests that still PASS while asserting a number that is only correct on the
no-ledger path. C1 includes a sweep of the 117 `create_loan_account` call sites for assertions whose
expected value depends on the money-blind anchor replay rather than the ledger. Anything found is
reported with its proof before it is touched.

---

## 5. The commits

Each commit is independently green (full suite + `pylint app/` 10.00 with the full `--fail-on` set)
and independently revertable.

### C1 -- `test(loan): make the loan fixtures write through production's reconcile path` -- **DONE**

**Test-only. No `app/` change (verified: `git diff app/` empty). This is the prerequisite for
everything else: it is what puts the suite on the production code path.**

**AS BUILT (differs from the plan as written -- three developer rulings, 2026-07-13):**

* `tests/_test_helpers.py::_sync_loan_ledger` (new, private) -- the shared write-through step,
  calling `loan_posting_service.sync_loan_postings_all_scenarios`, flushing and leaving the commit to
  the caller (production's contract). It calls the PLAIN sync, deliberately NOT production's
  `sync_all_scenarios_or_duplicate`: that wrapper translates a user's double-click into idempotent
  success **by rolling back**, which in a fixture would silently discard the test's setup. A
  duplicate anchor written by a fixture is a fixture bug and must fail loud.
* `create_loan_account` -- **always** opens the ledger; **no `open_ledger` flag**. A boolean opt-out
  would leave a casual escape hatch back onto the fallback path this arc exists to delete. The sync
  runs after the `RateHistory` insert, because the genesis walk resolves rate periods and the
  resolver raises on an empty rate feed.
* `insert_trueup_event` / `insert_tracking_start_event` -- reconcile into postings in the same
  transaction, as `anchor_service._append_loan_anchor_and_sync` does for both.
* `clear_loan_ledger(loan_account_id)` (new, public) -- the **breaker**: the exact inverse of the
  sync, deleting the `loan_opening` / `loan_trueup` / `loan_payment` entries on the loan's own ledger
  accounts (raw SQL; the ORM blocks deletes on the append-only ledger), leaving the Step-2/3 CASH
  entries untouched. It is the ONE way to build a ledger-less loan, so the broken state is always
  explicit at the call site -- and C2's `TestBrokenLoanFailsLoud` reuses it rather than inventing a
  second mechanism. (Implementation note: it resolves the entry ids to a Python list FIRST, because
  the predicate reaches each entry THROUGH its postings; deleting the postings first empties the
  predicate and strands the entry headers. That bug was caught by `TestUserScopedResync`, which counts
  entries rather than postings.)
* The five reader tests + `TestUserScopedResync::test_resync_posts_only_the_users_genesis` +
  `TestUnpaidScheduleRowsNeverReduceTheDebt` call `clear_loan_ledger` and state the premise in their
  docstrings (F3). The last of those is still C2's to delete.
* `tests/conftest.py::cross_page_loan_unpaid_ctx` (new) + `TestLoanCrossPageEquality::
  test_unpaid_loan_owes_its_opening_on_every_surface` -- the never-paid shape 2a lives in, asserting
  non-vacuously (the schedule really does carry unpaid past-dated rows) that all five surfaces AND
  both producers report the full $240,000 opening.

**Measured, not predicted.** The plan's dry-run patched only `create_loan_account` and reported 15
failures. Patching all three writers gives **10 failed, 7357 passed**: the six paid-off / true-up
tests (F1) resolve on their own with every asserted number unchanged, exactly as predicted, because
the true-up now reconciles.

**Three failures the plan did NOT predict** (all one root cause; this is the F4 class, found rather
than theorised): `TestBalanceMapLoan::test_pre_first_payment_uses_current_balance`,
`TestMultiLoanIsolation::test_two_loans_keep_distinct_current_balances`, and the cross-page oracle's
`test_all_surfaces_equal` each asserted **$200,000 at a period BEFORE the true-up**, where the ledger
says **$240,000**. Proven against the dev clone that the ledger's answer is production's: the
Mortgage's past periods step down at each recorded event (178,103.41 -> 177,829.83 -> 177,554.69 ->
177,277.97) rather than carrying today's balance flat backward. The three assertions pinned the
no-ledger fallback. Their guard (PR #44 / `aba0242`) was against `compute_loan_period_balance_map`
being **seeded with the original principal** -- a defect inside the very function C2 deletes.

**Developer ruling:** re-point, do not re-value. Each test now asserts the trued-up balance at a
period at/after the anchor (**number unchanged, $200,000**) and additionally pins the ledger's
pre-anchor answer ($240,000). The PR #44 fence moves to where it actually applies -- the loan's
CURRENT balance -- and the tests become a positive statement of "the past belongs to the ledger"
instead of a contradiction of it.

**Verified:** full suite **7368 passed**; `pylint app/` 10.00/10 with the full `--fail-on` set; zero
new lint findings on the touched test files; the dev clone still reads Mortgage **$177,277.97** and
Van Loan **$15,663.59** to the cent (C1 changes no `app/` code, so this holds by construction).

**Deliberately NOT in C1:** `insert_origination_event` writes a `LoanAnchorEvent` with source
`origination` that no reader reads -- `load_loan_anchor_facts` queries only `user_trueup` and
`tracking_start` and synthesises the opening from the immutable params
(`loan_loaders.py:181-204`). Removing it is dead-code cleanup, and it belongs in C5 with the other
documentation corrections, not in the commit that changes what the suite exercises.

### C1 adversarial review -- what it changed

An adversarial review of C1 (before commit) found two real holes in the work and three false claims in
its own docstrings. All are fixed, each with a negative control proving the guard now bites.

1. **`TestMultiLoanIsolation` had stopped testing isolation.** Once the fixtures open the ledger, every
   BEGUN period is answered by `confirmed_loan_balance_map(account.id, ...)` -- a per-account read that
   is correct even if `build_maps` hands loan A the loan B `DebtSchedule`. Only the FORWARD tail
   consumes that bundle. Both of C1's first assertions were on begun periods, so a crossed schedule
   would have passed. A future-period assertion is added; negative control (swapping the two schedules
   in `_kind_correct.build_maps`) makes loan A report **$47,385.23** -- loan B's balance -- and the test
   now fails. Same trap applies to any future ledger-era test: **assert the forward tail, or you are not
   testing the bundle at all.**

2. **A fixture was writing a ledger shape production forbids.**
   `test_pre_trueup_payment_is_split_from_origination` set a true-up dated 2026-04-15 while the
   `test_services` autouse clock is frozen at 2026-03-20. The sync bounds its walk at `date.today()`
   and DROPS a later anchor, so the loan got its opening and no true-up: it LOOKED ledger-backed and was
   not -- the exact divergence class this arc deletes. Production rejects a future `anchor_date`
   outright (`schemas/validation/loans.py::validate_not_future`). `_sync_loan_ledger` now **fails loud**
   when any anchor post-dates the sync's as-of (reading the clock from the sync's own module, since
   `freeze_today` patches `date` per-module). Sweeping the whole suite with that guard armed found
   exactly ONE offender, now re-dated to 2026-03-15 with every asserted number unchanged.

3. Three docstrings asserted things that were not true (a delete-ordering "safety property" that was
   backwards -- the balanced-entry trigger is `AFTER INSERT OR UPDATE`, never DELETE; "before the caller
   commits", when `_append_loan_anchor_and_sync` commits itself; and an over-claim that the helpers reach
   the whole loan suite, when ~20 modules hand-roll loans). Corrected, since this project treats a false
   docstring as a defect.

### C1 adversarial review -- one finding that PREDATES this arc

**The cross-page oracle's "PR #44 boundary lock" can no longer catch PR #44's bug.** Found while
adversarially reviewing C1; not caused by it. The read switch retired the lock silently.

PR #44 / `aba0242` passed `original_principal` where the schedule map's `current_balance` SEED
belongs. That seed is read in exactly one situation --
`account_projection.balance_from_schedule_at_date`, "used when *target* precedes the first scheduled
payment" (`:190-191`). Before the read switch, EVERY period came from the schedule walk, so a
pre-payment period was a live probe of the seed. Now the confirmed ledger owns every BEGUN period,
and the oracle's fixture (true-up dated today) puts the first payment inside the very next period, so
there is no FUTURE pre-first-payment period either. The seed is therefore invisible to every
assertion the oracle makes.

**Proven, not argued:** reintroducing the defect (seeding `compute_forward_loan_period_balance_map`
with `original_principal`) leaves the oracle GREEN. The schedule ROWS carry the balances.

The W9905 `shekel-original-principal-as-balance` checker -- a build failure -- is now the only fence
on that argument. That is a decent fence, and C2 makes it structural by deleting
`compute_loan_period_balance_map` outright, which removes the argument. Recorded so nobody reads the
oracle's old docstring and believes the test is guarding something it is not; the test's docstring now
says so itself.

### C1b -- `fix(balance): the ledger's domain is a fact; stop reading $0 outside it` -- **DONE**

Began as the test-only fixture prerequisite below. Migrating the ~30 hand-rolled loan builders onto
production's path immediately turned the suite red in ways that were NOT fixture artifacts -- they were
two real production bugs, both since confirmed on the dev clone and both fixed here. The suite was
green only because it never ran the code production runs. That is the whole thesis of this arc,
demonstrated.

**BUG 1 -- an old anchor's date was a lie.** A journal entry carries an ``entry_date`` (its true civil
date) and a NOT NULL ``pay_period_id``. When an anchor predates every pay period the user has,
``_anchors._resolve_anchor_pay_period`` is forced to file it under the EARLIEST period -- which can only
ever push it LATER than it happened. The readers bounded by period start and therefore believed it. A
loan originated 2025-01-01 whose owner's periods begin 2026-01-02 read as owing **nothing for the whole
of 2025**.

*Fix:* new `loan_posting_service/_asof.py`. ``effective_date()`` bounds an ANCHOR by
``LEAST(entry_date, period.start)`` and CASH by ``period.start``. The split follows the NATURE of the
fact: an anchor ASSERTS a date and has no budget dimension; cash IS budgeted to a period, and the
"an early-settled payment must not show until its period begins" rule is deliberate and must survive.
Both readers take the key from that one place, so ``map[P] == balance_at(P.start)`` still holds by
construction. Verified against the clone: of its 66 anchor entries the ONLY two the ``LEAST`` moves are
superseded openings that net to exactly $0.00, so **no production number moves.**

**BUG 2 -- $0.00 outside the ledger's domain was being spent as money.** For a mid-life import (opening
is a ``tracking_start`` years after origination) the ledger genuinely has no record before that date,
and ``confirmed_loan_balance_at`` returns ``$0.00`` -- meaning "no record", NOT "no debt". The year-end
debt-progress read its opening balance at Dec-31-of-the-prior-year, subtracted a real year-end balance
from that fabricated zero, and reported the borrower ADDING debt they had been paying down. **Live on
real data:**

| | before | after |
|---|---|---|
| Mortgage | `jan1=0.00 dec31=175,870.41` **`paid=-175,870.41`** | `jan1=178,375.43` **`paid=+2,505.02`** |
| Van Loan | `jan1=0.00 dec31=12,883.20` **`paid=-12,883.20`** | `jan1=17,134.85` **`paid=+4,251.65`** |

*Fix:* new `loan_posting_service/_domain.py`. ``confirmed_loan_ledger_domain()`` returns the ledger's
``(start_date, opening_balance)``; ``_compute_debt_progress`` clamps its window to it and reports
``tracked_from`` so the surface can say "since 2026-03-31" instead of implying a calendar year. The
principle, and the one to hold on to: **a producer with no evidence must say so, and a caller must never
spend a non-answer as money.** ``opening_balance`` is the OPENING-kind posting sum, deliberately not
``balance_at(start_date)`` -- a payment sharing the opening's pay period would otherwise be netted into
the opening balance and hide $272.02 of principal paid inside the window.

**Test repairs, each with its proof (Section 4).** Four tests demanded the RIGHT number and the code was
wrong (Bug 2); they now pass with their original expected values untouched. Three pinned the phantom
paydown as CORRECT -- most starkly
``test_current_period_point_diverges_from_hero_for_amortizing_loan``, which asserted that the /savings
hero and the net-worth trend MUST disagree about the same loan on the same day. That disagreement is the
symptom this arc opened with. It is now
``test_current_period_point_agrees_with_hero_for_amortizing_loan``. One test
(``test_hard_delete_account_with_params``) had a VACUOUS assertion: it checked
``b"permanently deleted" in resp.data``, which the ARCHIVE flash also satisfies ("cannot be permanently
deleted"). It could not have failed either way.

**Verified:** full suite 7370 passed; `pylint app/` 10.00/10 with the full `--fail-on` set; checker unit
tests 146 passed; the dev clone still reads Mortgage **$177,277.97** and Van Loan **$15,663.59** to the
cent.

### 2a is now PROVEN, with a number -- and the map fallback has NO coverage until C2

C1b's adversarial review asked for the map-level twin of `TestUnpaidScheduleRowsNeverReduceTheDebt`
(the scalar's guard). Written, it FAILS. On a $240,000 loan originated 548 days ago, never paid, with
its ledger removed:

```
period 2026-01-19 reports 235771.76, below the $240,000 owed;
unpaid schedule rows are paying the debt down
```

That is section 2a, demonstrated rather than argued. `compute_loan_period_balance_map` walks the FULL
schedule, so ~17 purely PROJECTED installments pay down principal the borrower never paid.

**It cannot be fixed narrowly.** The scalar sibling was fixed (`7b7c909b`) by filtering its walk to
CONFIRMED rows, which is safe because it answers ONE date and RAISES for the future. This map answers
the past AND the future in a single walk, so the same filter would flatten the forward projection to
the last confirmed balance. Fixing it properly means splicing confirmed + forward -- which is precisely
what the ledger path already does. **C2 deletes the branch; that is the fix.** The test is therefore not
committed (a red test, or a band-aid, would both be worse), and the branch carries no coverage until
then.

**Reachability today: none.** After C1b every configured loan opens its ledger at params-create, and
production runs a single baseline scenario, so `confirmed_loan_balance_map` never returns `None` for a
real loan. The defect is latent, not live. C2 must delete `compute_loan_period_balance_map` outright --
not merely stop calling it.

### C2 DESIGN REQUIREMENT -- a not-yet-originated loan is not a broken loan

Surfaced by C1b's exit gate (below). ``origination_date`` carries NO not-future validator (unlike a
true-up's ``anchor_date``), so a loan configured before it originates -- a mortgage closing next month --
is a legitimate, reachable production state. Its origination anchor post-dates the sync's as-of, so it
posts NO opening, and C2's fail-loud would RAISE on it: `/savings` and the year-end page would 500 for a
user who did nothing wrong.

C2 must therefore fork on WHY the opening is missing:

* ``origination_date > today`` -- not yet originated. Legitimate. It owes nothing yet; answer $0.00 (here
  the zero is TRUE, unlike Bug 2's).
* ``origination_date <= today`` and still no opening -- BROKEN. Raise ``LoanLedgerNotOpenedError``.

`test_year_end_summary_service.py::TestMortgageInterest::test_mortgage_interest_partial_year` is the live
test that forces this fork; it is the ONE remaining failure under the C2 simulation.

### C1b EXIT GATE -- the C2 simulation (measured)

Making `_build_amortizing_balance_map` raise instead of falling back, and running the full suite:

| | failures |
|---|---|
| before C1b | **45** across 8 files |
| after the factory migration | 4 |
| after the FU-4 fix + test repairs | **1** |

The last one is the not-yet-originated loan above -- a C2 design requirement, not a fixture defect. C2
therefore lands against a suite that is otherwise already green under its own semantics.

### C2 PREREQUISITE -- the off-factory loan builders (measured, 2026-07-13)

**C1 does NOT put the whole suite on the production path, and the plan is wrong to imply it does.**
Nineteen test files construct `LoanParams` directly instead of going through `create_loan_account`,
so C1's helper fix never reaches them and their loans still carry no ledger.

Simulating C2 (making `_build_amortizing_balance_map` raise instead of falling back) breaks **45
tests across 8 files** -- and that is the MAP producer alone; the scalar's raise adds more:

| file | failures |
|---|---|
| `test_services/test_savings_dashboard_service.py` | 17 |
| `test_services/test_year_end_summary_service.py` | 11 |
| `test_routes/test_savings.py` | 8 |
| `test_routes/test_accounts_dashboard.py` | 3 |
| `test_services/test_net_worth_kernel.py` | 2 |
| `test_services/test_balance_at.py` | 2 |
| `test_routes/test_debt_strategy.py` | 1 |
| `test_integration/test_loan_resolver_single_source.py` | 1 |

C2 therefore needs its own fixture step FIRST: route those local builders through the shared factory
(they are a DRY violation regardless -- `test_balance_at.py::_make_mortgage` is a verbatim
re-implementation of `create_loan_account` differing only in account type), or open their ledgers.
Not doing this first means C2 lands as a 45-test red wall with no way to tell a real regression from
a fixture that never modelled production.

### C2 -- `fix(balance): the ledger owns a loan's past; a broken loan fails loud`

**The correctness commit.**

* New `LoanLedgerNotOpenedError(PostingError)` in `app/services/posting_reads.py` (beside
  `PostingError`, which already models exactly this family of invariant violation). Its message names
  the account, the scenario, and the repair (`sync_loan_postings_all_scenarios`).
* `net_worth_kernel.amortizing_balance_at` -- when `as_of <= ctx.as_of` and
  `confirmed_loan_balance_at` returns `None` for a loan that HAS a resolvable schedule, RAISE. Delete
  the confirmed-rows walk added by `7b7c909b` (lines 437-443) and its 20-line comment block. Keep the
  `debt_schedule is None` branch (that means no `LoanParams`, i.e. not a configured loan, which
  legitimately routes to the cash producer -- it is not a boundary-rule fork).
* `net_worth_kernel._build_amortizing_balance_map` -- when `confirmed_loan_balance_map` returns
  `None`, RAISE. Delete the `compute_loan_period_balance_map` fallback (lines 873-877).
* `account_projection.compute_loan_period_balance_map` -- now dead. DELETE it. Confirm with a
  repo-wide grep that `_build_amortizing_balance_map` was its only caller.
* `tools/pylint/shekel_checkers/` -- W9905 (`shekel-original-principal-as-balance`) currently guards
  `compute_loan_period_balance_map` AND `balance_from_schedule_at_date`. Drop the dead name, keep the
  live one. Update the checker's tests.
* Keep `DebtSchedule.current_balance`: after this change it is read ONLY as the FORWARD projection's
  seed (`compute_forward_loan_period_balance_map`, `forward_balance_at_date`), and it is
  ledger-seeded. Add a docstring line saying so, since its old job (the pre-first-payment fallback) is
  gone.
* Tests:
  * `TestBrokenLoanFailsLoud` (new, in `test_net_worth_kernel.py`) -- a configured loan with its
    opening posting deleted raises `LoanLedgerNotOpenedError` from BOTH the scalar and the map.
    Replaces `TestUnpaidScheduleRowsNeverReduceTheDebt` (F2).
  * `TestScalarAndMapAgree` (new, the structural invariant) -- for a matrix of loan shapes
    (never-paid, mid-life-imported with a tracking start, trued-up, overpaid, short-paid, paid-off),
    assert `balance_map(loan, ctx, periods)[p.id] == balance_at(loan, ctx, p.end_date)` for EVERY
    period. This is the test that would have caught 2a, and it is the durable guard.
  * The cross-page oracle gains the `cross_page_loan_unpaid_ctx` fixture from C1.

### C3 -- `fix(balance): fence the context's loan handle; a route cannot hold a LoanState`

**Closes 2b and 2c.**

* `resolution_context.py` -- rename `BalanceContext.loan` to `resolved_loan` and `.loan_state` to
  `resolved_loan_state`. The rename is load-bearing, not cosmetic: `_called_name_in` matches the
  ATTRIBUTE name of a call (`_common.py:31`), so a distinctive name is what makes
  `ctx.resolved_loan_state(...)` catchable, where the generic `loan` would collide with unrelated
  code and force a false-positive-ridden fence.
* `balance_seam.py` -- add both names to `_LOAN_RESOLVER_PRODUCERS`. Allowlist
  (`_LOAN_RESOLVER_MODULES`) gains only `app.services.balance_at` and `app.services.net_worth_kernel`
  (the seam and the cluster that legitimately compose the memo). Routes and dashboards get nothing.
* `app/routes/debt_strategy.py:148` -- `ctx.loan(account).params` is used for exactly one thing,
  `params.is_arm`. Add `is_arm: bool` to `LoanFigures` (rich projection detail, not a balance) and
  drop the context call.
* `app/services/home_equity_service.py:126` -- `ctx.loan(loan) is None` is a configured-loan test.
  Replace with `balance_at.loan_figures(loan, ctx) is None`, which is already a seam entry and already
  returns `None` for a non-loan.
* `app/routes/accounts/detail.py` -- the real work. Move `_secured_loan_series` OUT of the route and
  INTO the seam as `balance_at.secured_loan_series(property_account, ctx) -> list[SecuredLoanSeries]`,
  sourcing each loan's `current_balance` from `balance_at.balance_at` rather than
  `LoanState.current_balance`. The chart's debt line IS a balance-at-T series, so it belongs to the
  seam by the seam's own charter. The route then holds no `LoanState` at all and
  `property_equity_chart` stays pure. (This also sets up follow-up FU-2 below.)
* `balance_seam.py` W9909 -- extend the fail-closed check to the surface this arc fenced:
  * Add `app.services.loan_resolution` and `app.services.resolution_context` to
    `_FENCED_MODULE_RULINGS`, with `contractual_schedule_from_origination` classified as a
    non-producer (pure contractual rows from immutable params; it reads no ledger and answers no
    balance-at-T) and a comment saying why.
  * Extend `visit_functiondef` to classify public METHODS of public classes in a fenced module, not
    just module-level functions. The current early return at `:471` is exactly what let
    `BalanceContext.loan` become a hole. Update `_ENGINE_CLUSTER_MODULES` /
    `_LOAN_LEDGER_DEFINING_MODULES` and the set-equality test at
    `tools/pylint/tests/test_shekel_checkers.py:1300`.
* Tests: checker cases for (a) a route calling `ctx.resolved_loan_state(...)` is flagged, (b) the seam
  calling it is not, (c) a new unclassified public function in `loan_resolution` is flagged, (d) a new
  unclassified public METHOD on `BalanceContext` is flagged.

### C4 -- `fix(balance): a read pass cannot be pinned to a future as-of`

* `BalanceContext.__post_init__` raises `ValueError` when `as_of > date.today()`. Rationale, in the
  docstring: `confirmed_loan_view` returns `None` for any as-of after today
  (`loan_payment_service.py:529`), so a future-pinned pass silently resolves every loan from the
  anchor replay -- the producer this cluster's own docstrings call "BLIND TO MONEY." Verified against
  both real dev loans that a +1-day as-of takes that path today; it returns the same number only
  because the user's true-ups have re-anchored the replay, which is agreement by luck.
* The correct way to value a loan in the future is unchanged and stays: `ctx.as_of = today`,
  `balance_at.balance_at(account, ctx, future_date)`.
* Test: `BalanceContext.build(user_id, as_of=tomorrow)` raises; `as_of=yesterday` does not (the Taxes
  tab's display-tz today can legitimately be yesterday in UTC).

### C5 -- `docs(balance): correct the claims the review found false`

No behaviour change. Every item here is a statement in the code that a future reader would rely on and
that is not true.

* `net_worth_kernel.debt_schedule_rows` (`:220-223`) -- "the rows carry no balance" is false;
  `AmortizationRow.remaining_balance` exists. Say what is actually true: the bundle's balance-at-today
  is gone, the rows remain rich detail, and the fence on the rows is the C3 method fence plus the
  seam's ownership of every rendered balance.
* `savings_dashboard_service/_projections.py` -- three claims that the loan tile "reads the resolver
  directly ... never the seam" (`:47`, `:181-186`, and the inline comment at `:212-214`). All three
  are the opposite of what `_compute_loan_account` now does.
* `home_equity_service.resolve_home_equity` (`:117-118`) -- "With no baseline scenario each secured
  loan still resolves ... exactly as before" is false since `7b7c909b`; `balance_at.balance_at` calls
  `_require_scenario`, which raises. State the raise.
* `balance_at/_loan_figures.py:104` -- `_is_paid_off(resolved)` has no type annotation. Add
  `resolved: ResolvedLoan` (imports cleanly, no cycle).
* `TestOneResolutionPerLoanPerReadPass` (`test_savings_dashboard_service.py:4577-4579`) -- the spy sits
  on `resolution_context.resolve_loan_bundle`, so it counts resolutions routed THROUGH the context,
  not "every resolution anywhere in the pass regardless of which module asked."
* `tests/_test_helpers.py::insert_origination_event` -- delete it and its call site. Its docstring
  claims "the resolver raises ValueError on an empty anchor-event list," which the read switch retired;
  `load_loan_anchor_facts` never reads an `origination`-source row. Verify by removal, not by
  assertion.
* `implementation_plan_loan_resolution_context.md` -- append a "Corrections (2026-07-13 review)"
  section pointing here. Do not rewrite its history; record what it got wrong.

### C6 -- `refactor(balance): one clock per read pass` (may be deferred to its own arc)

The arc's "one clock" claim holds for the loan resolver only. The same read pass still reads
`date.today()` independently at `savings_dashboard_service/_metrics.py:232` (the committed expense
floor) and `:416` (escrow monthly as-of), `_goals.py:227`, and `debt_strategy.py:308` (the strategy
start date, against a context built separately at `:138`). Thread `ctx.as_of` into all four. The
midnight-crossing argument `liability_owed_at_dates` makes for its own sample axis
(`balance_at/_liability.py:126-137`) applies verbatim.

This is correctness-adjacent rather than correctness-critical, and it touches escrow. If C1-C5 are
green and you want to ship, C6 can land separately without weakening anything above.

---

## 6. Verification

**Per commit:** targeted tests, then `pylint app/ --fail-on=<the full CLAUDE.md set>`, then the full
suite. Show real terminal output, not summaries.

**The regression baseline that must not move.** Recorded from the dev clone before any change, and
CONFIRMED CORRECT by the developer (2026-07-13):

| loan | account id | current principal balance | status |
|---|---|---|---|
| Mortgage | 3 | **$177,277.97** | correct; history correct |
| Van Loan | 8 | **$15,663.59** | correct; **history is NOT** (see FU-1) |

Both current balances are correctness oracles. After C2 and C3, `/savings`, `/accounts/<id>` (loan
detail), the property detail page, `/debt-strategy`, and the analytics Taxes tab must each still report
**$177,277.97** and **$15,663.59** to the cent. Any movement in either is a defect in this plan, not a
correction, and the commit stops.

**The Van Loan's HISTORY is not an oracle, and this plan must not change it either.** Its past-period
balances are known-wrong (FU-1). C2 does not touch them: with the ledger open, begun periods ALREADY
read the confirmed ledger through
`splice_confirmed_and_projected_loan_balances`, and C2 only deletes the branch taken when the ledger is
absent. So the Van Loan's historical balances should come out of this plan byte-identical -- still
wrong, and fixed separately. If they move, C2 changed something it should not have.

That asymmetry is the point of the fail-loud design and worth stating plainly: **the Van Loan proves a
correct current balance can sit on top of a wrong history.** Its current balance is right only because
the 2026-06-23 true-up asserts it. A design that lets the schedule answer for the past is exactly how a
wrong history hides behind a right present.

**Live verification (not just tests).** Against the dev clone, render each of the five surfaces above
and read the number off the page, per CLAUDE.md rule 9. A green suite on a fixture-driven path is
precisely what failed us here.

**The new structural guards** (these are the deliverable, as much as the fix):

1. `TestScalarAndMapAgree` -- scalar == map at every period, across six loan shapes.
2. `TestBrokenLoanFailsLoud` -- a loan with no opening posting raises from both producers.
3. `cross_page_loan_unpaid_ctx` -- the cross-page oracle now covers a loan with past-dated unpaid rows.
4. The C3 checker cases -- a route holding a `LoanState` is a build failure.

---

## 7. Out of scope (follow-ups)

### FU-1 -- The Van Loan's HISTORY is wrong (its current balance is right); investigate separately

**Developer ruling, 2026-07-13: the Van Loan's current principal of $15,663.59 is CORRECT. Its history
is not.** This plan does not attempt a correction, but the review turned up the artifacts, recorded
here so the follow-up does not start from zero.

**The anchor events** (`budget.loan_anchor_events`, dev clone):

| account | source | anchor_date | anchor_balance |
|---|---|---|---|
| Mortgage | origination | 2018-12-01 | 202,000.00 |
| Mortgage | tracking_start | 2026-03-31 | 178,375.43 |
| Mortgage | user_trueup | 2026-05-22 | 177,829.83 |
| Van Loan | origination | 2023-02-14 | 32,402.45 |
| **Van Loan** | **tracking_start** | **2026-04-11** | **17,020.47** |
| **Van Loan** | **tracking_start** | **2026-04-11** | **17,134.85** |
| Van Loan | user_trueup | 2026-05-11 | 16,575.68 |
| **Van Loan** | **user_trueup** | **2026-05-22** | **17,020.47** |
| **Van Loan** | **user_trueup** | **2026-05-22** | **16,123.31** |
| Van Loan | user_trueup | 2026-06-23 | 15,663.59 |

The Mortgage has a clean single-row chain. The Van Loan carries two `tracking_start` rows on one date
($114.38 apart) and two `user_trueup` rows on one date ($897.16 apart). One of the same-day true-ups
($17,020.47) is a verbatim repeat of the earlier tracking-start value, which smells like a
mis-entered assertion rather than a code fault.

**What is NOT the bug (checked, so the follow-up does not chase it):**

* The unique index is not failing. `uq_loan_anchor_events_acct_date_bal_day` keys on
  `(account_id, anchor_date, anchor_balance, created_at::date)` -- it blocks an IDENTICAL same-day
  re-assertion, and two same-day rows with DIFFERENT balances are permitted by design (that is how an
  operator corrects a same-day mistake in an append-only table).
* The duplicates are not being double-counted into the balance. `_opening_anchor_fact`
  (`loan_loaders.py:108-131`) resolves multiple tracking-starts by latest `created_at`, and the ledger
  reconciles to that target with a correcting delta rather than stacking: the two `loan_opening` legs
  on 2026-04-11 are `-17,020.47` and `-114.38`, netting to `-17,134.85`, which is exactly the winning
  tracking-start. That is the correcting-entry pattern working as designed.

**The actual artifact to chase.** The confirmed ledger balance over time:

| date | confirmed balance | note |
|---|---|---|
| 2026-04-11 | 17,134.85 | opening (later tracking-start wins) |
| 2026-04-23 | 16,683.84 | payment: 451.01 principal |
| 2026-05-11 | 16,575.68 | true-up |
| **2026-05-21** | **16,123.31** | **drops 452.37 with NO payment in the window** |
| 2026-05-22 | 16,123.31 | true-up date |
| 2026-06-23 | 15,663.59 | true-up; correct today |

Van Loan payments are dated 04-23, 05-23, 06-23. The balance falls by $452.37 between 2026-05-11 and
2026-05-21 with no payment in that interval. Two candidates worth measuring first: the 2026-05-22
true-up corrections are being attributed to a PAY PERIOD that starts before their entry date (the
reader is period-assigned, `_reader.py:239-244`), or the pair of same-day true-ups is generating
correcting legs that land on the wrong side of the 05-22 boundary. Either way the current balance still
lands right because the 2026-06-23 true-up pins it -- which is precisely how a wrong history hides
behind a right present.

### FU-4 -- A period BEFORE a loan's opening renders $0 owed -- **FIXED in C1b**

**Found 2026-07-13 while verifying C1. Turned out to be far worse than "one period, one loan": it
inverted the year-end debt-progress section on real data (see C1b). Fixed there -- the seam now exposes
the ledger's DOMAIN and the year-end clamps its window to it. The net-worth trend / grid display
question below is the remaining open piece.**

`confirmed_loan_balance_map` returns `Decimal("0.00")` for any period preceding the loan's opening
posting (`_reader.py:257`, documented as "nothing confirmed yet as of that date"), and
`splice_confirmed_and_projected_loan_balances` hands every BEGUN period to that map. So a begun
period earlier than the opening renders the loan as **debt-free**.

Measured on the dev clone. The Van Loan's opening is its `tracking_start` (2026-04-11); the user's
first-ever pay period starts 2026-03-26:

| period start | rendered owed |
|---|---|
| **2026-03-26** | **$0.00** -- WRONG |
| 2026-04-09 | $17,134.85 |
| 2026-04-23 | $16,683.84 |

Net worth is overstated by **$17,134.85** in that period on the grid, the net-worth trend's first
point, and the 2026 year-end. It is bounded (one period, one loan: the Mortgage's 2026-03-31 opening
lands INSIDE period 1, so it is clean), but it is precisely the sin the design rule forbids --
silently understating the debt -- and it is the same family as FU-1.

**C2's fail-loud does not catch it**: C2 raises when a loan has NO opening. Here the opening exists,
it is merely dated later than the period, so the ledger answers $0 and no guard fires.

The fix needs a design decision, which is why it is not folded into C2:

* **Back-project contractually** from origination for the pre-tracking months. The codebase already
  has the primitive -- `loan_resolution.contractual_schedule_from_origination` exists for exactly
  this (it is what fills a tracking-start loan's pre-tracking months on the property chart, per FU-2),
  and `synthesize_origination_anchor` seeds it. This is the honest answer: the loan DID exist and DID
  owe something.
* **Or omit the loan** from periods before its opening entirely, so no number is asserted where no
  evidence exists.

Measure the error against real data before designing it, and note the overlap with FU-2 -- both are
"what does a loan's history look like before we started tracking it".

### FU-2 -- The property equity chart draws its confirmed past from schedule rows, not the ledger

`property_equity_chart.py:216-218` tiers the past months of the secured-debt line from
`row.remaining_balance` on `is_confirmed` schedule rows. `forward_balance_at_date`'s own docstring
(`account_projection.py:345-351`) says confirmed rows are "an INCOMPLETE record of the past" because a
true-up is a ledger event with no schedule row, and cites a real $3.94 divergence from exactly this.
The Mortgage has 3 true-up postings and the Van Loan has 10, so the chart's historical debt line is
very likely wrong by the true-up amounts. C3 puts the assembly behind a seam entry, which is where
the fix goes: the confirmed tier should read `confirmed_loan_balance_map`. Measure the error against
real data before designing it.

### FU-3 -- `resolve_loan_seeded` applies TODAY's standing overpayment at any as-of

`loan_standing_extra_for_account(account_id)` (`loan_resolution.py:141`) takes no as-of, so a pass
pinned to a historical date resolves the loan with today's overpayment plan. Harmless while every
caller pins as_of at or near today (C4 now enforces "not future"), but it is a latent wrong answer for
any historical read and should be dated when one is built.

---

## 8. Rollback

C1 is test-only. C3-C6 are fence and documentation. **C2 is the only commit that changes what a
production surface computes**, and its change is "raise instead of returning a number." Reverting it
restores the fallback and the divergence; nothing else depends on it having landed. The Mortgage's
$177,277.97 is the single check that tells you whether the arc is behaving.
