-- SQL Migration Script for PostgreSQL
-- Adds is_percentage and percentage columns to income_payments table
-- Start a transaction
BEGIN;
-- Check if the is_percentage column exists before adding it
DO $$ BEGIN IF NOT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_name = 'income_payments'
        AND column_name = 'is_percentage'
) THEN -- Add is_percentage column
ALTER TABLE income_payments
ADD COLUMN is_percentage BOOLEAN DEFAULT FALSE;
RAISE NOTICE 'Added is_percentage column to income_payments table';
ELSE RAISE NOTICE 'Column is_percentage already exists in income_payments table';
END IF;
END $$;
-- Check if the percentage column exists before adding it
DO $$ BEGIN IF NOT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_name = 'income_payments'
        AND column_name = 'percentage'
) THEN -- Add percentage column with numeric type (precision 5, scale 2)
ALTER TABLE income_payments
ADD COLUMN percentage NUMERIC(5, 2);
RAISE NOTICE 'Added percentage column to income_payments table';
ELSE RAISE NOTICE 'Column percentage already exists in income_payments table';
END IF;
END $$;
-- Update existing records to set is_percentage to FALSE if it's NULL
UPDATE income_payments
SET is_percentage = FALSE
WHERE is_percentage IS NULL;
-- Commit the transaction
COMMIT;
-- Verify the changes
SELECT column_name,
    data_type,
    column_default
FROM information_schema.columns
WHERE table_name = 'income_payments'
    AND (
        column_name = 'is_percentage'
        OR column_name = 'percentage'
    );