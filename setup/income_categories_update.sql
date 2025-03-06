-- SQL Migration Script for PostgreSQL to update IncomeCategory and Income models
-- Start a transaction
BEGIN;
-- Check if the color column exists in income_categories table before adding it
DO $$ BEGIN IF NOT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_name = 'income_categories'
        AND column_name = 'color'
) THEN -- Add color column
ALTER TABLE income_categories
ADD COLUMN color VARCHAR(7) DEFAULT NULL;
RAISE NOTICE 'Added color column to income_categories table';
ELSE RAISE NOTICE 'Column color already exists in income_categories table';
END IF;
END $$;
-- Check if the icon column exists in income_categories table before adding it
DO $$ BEGIN IF NOT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_name = 'income_categories'
        AND column_name = 'icon'
) THEN -- Add icon column
ALTER TABLE income_categories
ADD COLUMN icon VARCHAR(100) DEFAULT NULL;
RAISE NOTICE 'Added icon column to income_categories table';
ELSE RAISE NOTICE 'Column icon already exists in income_categories table';
END IF;
END $$;
-- Commit the transaction
COMMIT;
-- Verify the changes
SELECT table_name,
    column_name,
    data_type,
    column_default,
    is_nullable
FROM information_schema.columns
WHERE table_name IN ('income_categories')
    AND column_name IN ('color', 'icon')
ORDER BY table_name,
    column_name;