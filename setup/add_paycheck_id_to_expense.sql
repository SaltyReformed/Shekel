-- SQL script to add paycheck_id column to expenses table and create foreign key constraint
-- Step 1: Add the new column (nullable at first)
ALTER TABLE expenses
ADD COLUMN paycheck_id INTEGER;
-- Step 2: Add foreign key constraint
ALTER TABLE expenses
ADD CONSTRAINT fk_expenses_paycheck FOREIGN KEY (paycheck_id) REFERENCES paychecks(id) ON DELETE
SET NULL;
-- When a paycheck is deleted, set the reference to NULL rather than deleting the expense
-- Step 3: Create an index on the new column to improve performance of lookups and joins
CREATE INDEX idx_expenses_paycheck_id ON expenses(paycheck_id);
-- Step 4: Pre-populate the paycheck_id based on the current logic
-- Find the most recent paycheck before each expense's scheduled_date
-- This is the initial assignment logic we want to apply
WITH expense_paycheck_mapping AS (
    SELECT e.id AS expense_id,
        (
            SELECT p.id
            FROM paychecks p
            WHERE p.user_id = e.user_id
                AND p.scheduled_date <= e.scheduled_date
            ORDER BY p.scheduled_date DESC
            LIMIT 1
        ) AS matching_paycheck_id
    FROM expenses e
)
UPDATE expenses e
SET paycheck_id = m.matching_paycheck_id
FROM expense_paycheck_mapping m
WHERE e.id = m.expense_id;
-- Step 5: For expenses that didn't match a previous paycheck, assign to first future paycheck
WITH expense_future_paycheck_mapping AS (
    SELECT e.id AS expense_id,
        (
            SELECT p.id
            FROM paychecks p
            WHERE p.user_id = e.user_id
                AND p.scheduled_date > e.scheduled_date
            ORDER BY p.scheduled_date ASC
            LIMIT 1
        ) AS matching_paycheck_id
    FROM expenses e
    WHERE e.paycheck_id IS NULL
)
UPDATE expenses e
SET paycheck_id = m.matching_paycheck_id
FROM expense_future_paycheck_mapping m
WHERE e.id = m.expense_id;
-- Commit the transaction
COMMIT;