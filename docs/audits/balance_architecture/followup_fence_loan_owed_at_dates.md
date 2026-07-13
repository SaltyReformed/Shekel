# Follow-up: close the W9906 fence hole on `net_worth_kernel.loan_owed_at_dates`

**Status:** OPEN (not started). Found 2026-07-12 by the adversarial review of the loan due-date fix
(commit `3e63baa8`), which touched the fence's producer list and so surfaced this. Reported to the
developer, deliberately NOT fixed in that commit -- it predates the bug being fixed there, is not
producing a wrong number today, and needs one design decision (below).

**Enabled by / depends on:** the Level-1 balance-at seam (`app/services/balance_at.py`) and its
`shekel-balance-producer-bypass` (**W9906**) checker --
`tools/pylint/shekel_checkers/balance_seam.py`. See `implementation_plan_level1_balance_seam.md`.

**Size:** small (one seam entry + one call-site reroute + one producer-list entry + tests). The only
real work is the naming/shape decision in "Open decision" below.

---

## The invariant that is being violated

The Level-1 seam exists so there is exactly ONE public way to ask "what is this account's balance at
time T". W9906 enforces it mechanically with two lists in
`tools/pylint/shekel_checkers/balance_seam.py`:

- **`_BALANCE_PRODUCERS`** -- the low-level functions that actually compute a balance.
- **`_BALANCE_SEAM_MODULES`** -- the modules allowed to call them directly: `balance_at`,
  `balance_resolver`, `balance_calculator`, `account_projection`, `net_worth_kernel`,
  `net_worth_investment`, `daily_balance_series` (the seam plus the engine cluster it composes).

Any module outside that allowlist that calls a listed producer fires W9906 and fails the gate. Level-1
Commit 10 closed the last exception (`investment_base_balance_map`), and the fence is documented as
**zero-exception**.

## The hole

```text
app/services/savings_dashboard_service/_horizon.py:467
    net_worth_kernel.loan_owed_at_dates(loan_accounts, scenario_id, future_dates)
```

- `savings_dashboard_service._horizon` is a **consumer**. It is NOT in `_BALANCE_SEAM_MODULES`.
- `net_worth_kernel.loan_owed_at_dates` **is** a balance-at-T producer: it returns
  `{account_id: [owed at each sample date]}`, and the horizon liability band plots those numbers
  directly.
- But it is **not in `_BALANCE_PRODUCERS`**, so W9906 never fires.

So a consumer reaches past the seam straight into the kernel, and the fence is silent. This is not a
bypass of a listed rule; it is a producer that was never listed.

**Why it happened:** `loan_owed_at_dates` is new -- added 2026-07-12 in the P-AC1 Loop B P1 work (the
horizon band itself). It was correctly placed INSIDE the kernel (the right home, beside
`generate_debt_schedules` whose output it walks), and its docstring says it "stays fenced with" its
per-period sibling. But living in an allowlisted module only exempts it from CALLING producers -- it
does nothing to stop OTHERS calling it. The second half (adding it to `_BALANCE_PRODUCERS` and giving
it a seam entry) was missed.

## Why this is more than tidiness

This is the exact shape of the HIGH bug the fence caught during Level-1 Commit 10: the main dashboard
had been wired to the kind-correct `balance_map` instead of the cash-flow view, so a HYSA grid account
accrued interest into the SPENDING RUNWAY -- inflating the trough and hiding a real future dip. It
shipped silently because every dashboard test used a PLAIN account.

The same trap is live here, and the loan due-date fix (`3e63baa8`) sharpened it. `loan_owed_at_dates`
is now an explicitly **FORWARD-ONLY projection**: for `as_of <= today` it returns `current_balance`
held flat, which is **not** the ledger truth. That is correct for its sole caller (the horizon band
passes only future dates and prepends `current_balance` itself as the today point), and the domain is
documented in the function's docstring. But **nothing enforces it**. A future consumer that reaches
for `loan_owed_at_dates` with a PAST date gets a plausible-looking number that silently ignores the
genesis ledger -- including any balance TRUE-UP, which has no schedule row at all. That is precisely
the bug class `3e63baa8` eliminated (it read the real Van Loan $3.94 above what it owed), reopened
through a side door.

## Open decision (the only real design question)

The seam currently exposes two coherent VIEWS, and that duality is load-bearing (Level-1 Commit 8):

- **kind-correct** -- `balance_map` / `balance_at` / `build_maps` (net-worth surfaces).
- **cash-flow** -- `cash_balance_map` / `cash_balance_at` / `cash_daily_balance_series`
  (single-account cash-flow surfaces).
- (plus the grid/obligations `grid_balance_view`, added by the kind-correct-grid follow-up.)

A loan-only, multi-date, FORWARD-ONLY projection is a **third shape**, and it should not be bolted on
carelessly. Options:

1. **A named seam entry (recommended).** Add e.g. `balance_at.loan_forward_owed_at_dates(...)` -- a
   thin pass-through whose NAME states the domain, so a past-date call reads as obviously wrong at the
   call site, and whose docstring pins the contract ("FORWARD dates only; the past belongs to
   `confirmed_loan_balance_at`"). Route `_horizon` through it.
2. **Generalise the seam entry to all liabilities.** `_liability_band` also holds non-amortizing debts
   (a revolving Credit Card) flat at `abs(current_balance)`. A seam entry could own that whole rule
   instead of leaving half of it in `_horizon`. Larger, and arguably the seam taking on presentation
   logic -- probably a step too far, but worth 5 minutes of thought.
3. **Make it raise on a past date.** Orthogonal to 1/2 and cheap: `loan_owed_at_dates` could raise
   `ValueError` for any `sample_date <= today`, mirroring how `confirmed_loan_balance_at` raises for a
   FUTURE date (`_reader.py`). That makes the domain a runtime invariant, not just a docstring. The
   only caller already passes `frame.sample_dates[1:]` (all strictly future), so this is a no-op for
   production. Recommended IN ADDITION to option 1.

Recommendation: **1 + 3**.

## Work plan

1. Add `"loan_owed_at_dates"` to `_BALANCE_PRODUCERS` in
   `tools/pylint/shekel_checkers/balance_seam.py`.
2. Confirm it now fires W9906 at `app/services/savings_dashboard_service/_horizon.py:467` (it must --
   that is the proof the checker binds).
3. Add the seam entry in `app/services/balance_at.py` (see Open decision), with `_require_scenario`
   like every other public seam entry, and a docstring pinning the FORWARD-only domain and pointing at
   `confirmed_loan_balance_at` for the past.
4. Reroute `_horizon.py:467` to the seam entry; drop its now-unneeded `net_worth_kernel` import if
   nothing else in the module uses it.
5. (If taking option 3) Add the past-date guard to `loan_owed_at_dates` and a test for it.
6. Tests:
   - Checker: extend the register-bound loops in `tools/pylint/tests/test_shekel_checkers.py` (they
     already assert EVERY producer is flagged from a consumer and EVERY allowlisted module is exempt,
     so adding the name to the set may be enough -- verify, do not assume).
   - Behaviour: the horizon band must be byte-identical after the reroute. `compute_net_worth_horizon`
     already has coverage; assert the liability band series is unchanged.
7. Gates: `pylint app/` 10.00 with all `--fail-on`; `pylint tools/pylint/shekel_checkers/`
   `--fail-under=10`; `pytest tools/pylint/tests -c /dev/null`; full suite.

## Pointers

- Producer + hole: `app/services/net_worth_kernel.py` (`loan_owed_at_dates`), called from
  `app/services/savings_dashboard_service/_horizon.py:467` (inside `_liability_band`).
- Checker: `tools/pylint/shekel_checkers/balance_seam.py` (`_BALANCE_PRODUCERS`,
  `_BALANCE_SEAM_MODULES`; W9906 binds on BOTH the call site and the import, closing the
  aliased-import evasion).
- Seam: `app/services/balance_at.py` (the two-view split; `_require_scenario` fail-loud guard).
- The past-vs-future loan rule this protects: `net_worth_kernel.amortizing_balance_at` and
  `account_projection.forward_balance_at_date` (both added in `3e63baa8`) -- ledger for
  `as_of <= today`, forward projection after. See also
  `~/.claude` memory `project_loan_due_date_is_a_posting_input`.
- Prior art for exactly this class of miss: Level-1 Commit 10's fence-hole closure
  (`investment_base_balance_map` -> wrapped by `balance_at.investment_seed_map`) in
  `implementation_plan_level1_balance_seam.md` -- the same pattern applies here.
