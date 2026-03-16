Read tests/TEST_PLAN.md to understand the testing categories (HP, SP, IDOR, 
BE, SM, IDEM, FIN) and priority matrix (P0-P3). Then systematically audit 
every test file under tests/. For each file, produce a markdown report with:

1. ASSERTION DEPTH: Flag any test where the only assertion is a status code 
   check (e.g., assert response.status_code == 200) without verifying response 
   body content, database state, or side effects. These are shallow tests.

2. MISSING CATEGORY COVERAGE: Cross-reference each test file against the 
   TEST_PLAN categories. For a P0 service like balance_calculator, if there 
   are HP and BE tests but no FIN tests verifying penny-level Decimal 
   accuracy across 52+ periods, flag it.

3. REALISTIC DATA GAPS: Flag any test module that only seeds 1-3 records 
   when the production scenario involves dozens or hundreds (e.g., testing 
   recurrence_engine with 2 periods when production generates 52+, or 
   balance_calculator with 2 transactions when a real period has 15-20).

4. MISSING NEGATIVE PATHS: For every route test file, check whether there 
   are tests for: unauthenticated access, invalid IDs (nonexistent and 
   wrong-user), malformed POST data, and concurrent modification.

5. ASSERTION SMELL CHECK: Flag tests that assert only "not None" or 
   "len > 0" without verifying specific expected values. Flag tests that 
   use approximate comparisons on Decimal financial values.

Write the report to docs/test_audit_report.md organized by priority 
(P0 first). For each finding, include the test function name, file path, 
what is missing, and a concrete recommendation.