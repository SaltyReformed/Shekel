-- SQL Migration Script for PostgreSQL to update ExpenseCategory and Expense models
-- Start a transaction
BEGIN;
-- Check if the color column exists in expense_categories table before adding it
DO $$ BEGIN IF NOT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_name = 'expense_categories'
        AND column_name = 'color'
) THEN -- Add color column
ALTER TABLE expense_categories
ADD COLUMN color VARCHAR(7) DEFAULT '#6c757d';
RAISE NOTICE 'Added color column to expense_categories table';
ELSE RAISE NOTICE 'Column color already exists in expense_categories table';
END IF;
END $$;
-- Check if the monthly_budget column exists in expense_categories table before adding it
DO $$ BEGIN IF NOT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_name = 'expense_categories'
        AND column_name = 'monthly_budget'
) THEN -- Add monthly_budget column
ALTER TABLE expense_categories
ADD COLUMN monthly_budget NUMERIC(10, 2) DEFAULT NULL;
RAISE NOTICE 'Added monthly_budget column to expense_categories table';
ELSE RAISE NOTICE 'Column monthly_budget already exists in expense_categories table';
END IF;
END $$;
-- Check if the icon column exists in expense_categories table before adding it
DO $$ BEGIN IF NOT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_name = 'expense_categories'
        AND column_name = 'icon'
) THEN -- Add icon column
ALTER TABLE expense_categories
ADD COLUMN icon VARCHAR(100) DEFAULT NULL;
RAISE NOTICE 'Added icon column to expense_categories table';
ELSE RAISE NOTICE 'Column icon already exists in expense_categories table';
END IF;
END $$;
-- Check if the notes column exists in expenses table before adding it
DO $$ BEGIN IF NOT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_name = 'expenses'
        AND column_name = 'notes'
) THEN -- Add notes column
ALTER TABLE expenses
ADD COLUMN notes TEXT DEFAULT NULL;
RAISE NOTICE 'Added notes column to expenses table';
ELSE RAISE NOTICE 'Column notes already exists in expenses table';
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
WHERE table_name IN ('expense_categories', 'expenses')
    AND column_name IN ('color', 'monthly_budget', 'icon', 'notes')
ORDER BY table_name,
    column_name;