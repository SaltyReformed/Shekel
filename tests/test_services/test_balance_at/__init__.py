"""
Shekel Budget App -- Balance-at-T seam parity tests.

Structural-equality coverage for :mod:`app.services.balance_at`, the Level 1
balance seam.  The seam is new code that nothing calls yet (Commit 2 of the
balance-architecture plan), so these tests prove only ONE thing per account
kind: the seam's internal input assembly reproduces the EXISTING producer
path exactly.  Each test asserts ``seam output == existing producer called
with the same manually-assembled inputs`` -- so they need no hand-computed
money values for the parity itself (the kernel and resolver already own
those), only for the few sanity checks that confirm the right dispatch
branch ran.

**Parity is not the whole file, and must not be.**  A structural-equality test
proves two producers AGREE; it says nothing about whether either is right, and
where the two share code it degenerates to ``f(x) == f(x)``.  So the classes that
pin the seam's BEHAVIOR against an independent oracle -- the B-9 fix (an unpaid
overdue installment pays nothing down), the confirmed-present seeding
(:class:`TestForwardFoldSeedsFromTheConfirmedPresent`) -- are load-bearing in a
way the parity classes are not: they are what stands between a shared-code defect
and production.  The forward fold's exact ARITHMETIC is pinned by hand-computed
oracle in ``test_loan_plan_forward_oracle.py`` (fold values) and
``test_loan_plan_assembly.py`` (plan assembly); do not re-prove it here with a
producer.  Read the plan's Section 7.2 before adding a test that proves a producer
with a producer.

The five account kinds are seeded with the suite's established factory
patterns: a Checking (PLAIN), an HYSA + InterestParams (INTEREST), a
Mortgage + LoanParams + origination event/rate (AMORTIZING), a 401(k) +
InvestmentParams (INVESTMENT), and a Property + AssetAppreciationParams
(APPRECIATING).  ``seed_periods_today`` places today in period index 4 so
"which paycheck contains today" is deterministic and an account can be
anchored in the past (period 2) or at the current period (period 4).
"""
