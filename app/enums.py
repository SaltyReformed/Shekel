"""
Shekel Budget App -- Reference Table Enums

Python Enums whose members correspond 1:1 with rows in the ref schema
lookup tables.  The *value* of each member is the database ``name``
column after the Commit #1 migration runs.

These enums are the single source of truth for valid reference values.
The ref_cache module maps each member to its database integer ID at
startup, so application code never needs to query by name at runtime.
"""

# ------------------------------------------------------------------
# Implementation Plan Discrepancies (Commit #1)
# ------------------------------------------------------------------
# - TransactionType name capitalization deferred to Commit #2.
#   The plan originally had it in Commit #1's migration, but this
#   creates a broken intermediate state because routes/services still
#   use filter_by(name="income") and filter_by(name="expense") until
#   Commit #2 replaces those calls.  TxnTypeEnum values therefore
#   remain lowercase to match the current database names.
# ------------------------------------------------------------------

import enum


class StatusEnum(enum.Enum):
    """Transaction status values.

    Values match ``ref.statuses.name`` after the Commit #1 migration
    renames the display names.
    """

    PROJECTED = "Projected"
    DONE = "Paid"          # Renamed from "done" -- expense has been paid
    RECEIVED = "Received"  # Income has been deposited
    CREDIT = "Credit"      # Paid via credit card, not checking
    CANCELLED = "Cancelled"
    SETTLED = "Settled"    # Archived / fully reconciled


class TxnTypeEnum(enum.Enum):
    """Transaction type values.

    Values match ``ref.transaction_types.name``.  These are still
    lowercase because the database rename happens in Commit #2
    alongside the code changes that replace filter_by(name=...) calls.
    """

    INCOME = "income"
    EXPENSE = "expense"
