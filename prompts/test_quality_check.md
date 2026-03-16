# Recurring Test Quality Check -- run before each release

## Step 1: Smoke check
Run: pytest --tb=short -q
Verify all tests pass. If any fail, stop and fix before proceeding.

## Step 2: Coverage with branch analysis
Run: pytest --cov=app --cov-report=term-missing --cov-branch
Flag any P0 module (balance_calculator, paycheck_calculator, 
recurrence_engine, tax_calculator, interest_projection) below 95% 
branch coverage. Flag any P1 module below 85%.

## Step 3: Assertion density check
For each test file under tests/, count:
- Total test functions
- Total assert statements
- Assertions that only check status_code or "is not None"
Calculate an "assertion depth ratio" (meaningful assertions / total tests).
Flag any file below 2.0 (meaning fewer than 2 meaningful assertions per 
test on average).

## Step 4: Spot-check surviving mutants
If docs/mutation_results.md exists, read it and check whether the 
surviving mutants from the last run have been addressed. If the file 
is stale (> 2 weeks old based on git log), re-run mutmut on P0 modules.

## Step 5: New code coverage gap check
Run: git diff main --name-only -- app/
For each changed app/ file, verify there is a corresponding test file 
with tests covering the changed functions. Flag any new or modified 
service function, route handler, or model method that lacks a 
corresponding test.

## Step 6: Report
Write a summary to stdout with:
- Overall pass/fail for each step
- Top 5 most urgent findings
- Suggested next actions