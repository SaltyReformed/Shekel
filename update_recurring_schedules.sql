-- Add new columns to recurring_schedules table

ALTER TABLE recurring_schedules ADD COLUMN category_type VARCHAR(20),
                                                         ADD COLUMN category_id INTEGER, ADD COLUMN default_account_id INTEGER REFERENCES accounts(id);

-- Add comment to explain purpose of columns
COMMENT ON COLUMN recurring_schedules.category_type IS 'Type of category - either "income" or "expense"';

COMMENT ON COLUMN recurring_schedules.category_id IS 'ID of the category (references income_categories or expense_categories depending on category_type)';

COMMENT ON COLUMN recurring_schedules.default_account_id IS 'Default account to use for payments/deposits';

-- Create an index for performance on recurring_schedules (optional but recommended)

CREATE INDEX idx_recurring_schedules_category ON recurring_schedules(category_type, category_id);


CREATE INDEX idx_recurring_schedules_account ON recurring_schedules(default_account_id);

-- Update existing records (if you have any)
-- For expense type records:

UPDATE recurring_schedules
SET category_type = 'expense'
FROM schedule_types
WHERE recurring_schedules.type_id = schedule_types.id
    AND schedule_types.name = 'expense';

-- For income type records:

UPDATE recurring_schedules
SET category_type = 'income'
FROM schedule_types
WHERE recurring_schedules.type_id = schedule_types.id
    AND schedule_types.name = 'income';

-- Add a NOT NULL constraint if appropriate
-- Note: Only add this if you're sure all records have been properly updated
-- ALTER TABLE recurring_schedules ALTER COLUMN category_type SET NOT NULL;