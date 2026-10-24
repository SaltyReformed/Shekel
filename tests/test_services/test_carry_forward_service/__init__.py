"""
Dedicated unit tests for app.services.carry_forward_service.

Fills coverage gaps not addressed by existing carry-forward tests in
test_credit_workflow.py, test_workflows.py, test_hostile_qa.py, and
test_idempotency.py.  Focuses on:

  - Non-template transactions preserving is_override=False
  - Settled status exclusion (added in WU-05)
  - actual_amount preservation across carry forward
  - scenario_id preservation across carry forward
  - Comprehensive mixed-status test covering all 6 statuses + deleted
  - Envelope branch (Phase 4 of carry-forward-aftermath plan):
    settle source at entries-sum and roll the unspent leftover into
    the target period's canonical row, with one row per (template,
    period) so cell == subtotal == balance.
"""
