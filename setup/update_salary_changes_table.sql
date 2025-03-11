-- Add new columns to the salary_changes table
ALTER TABLE salary_changes
ADD COLUMN federal_tax_rate NUMERIC(5, 2) DEFAULT 22.0,
    ADD COLUMN state_tax_rate NUMERIC(5, 2) DEFAULT 5.0,
    ADD COLUMN retirement_contribution_rate NUMERIC(5, 2) DEFAULT 6.0,
    ADD COLUMN health_insurance_amount NUMERIC(10, 2) DEFAULT 249.0,
    ADD COLUMN other_deductions_amount NUMERIC(10, 2) DEFAULT 100.0,
    ADD COLUMN notes TEXT;
-- Comment on new columns
COMMENT ON COLUMN salary_changes.federal_tax_rate IS 'Federal tax rate as a percentage';
COMMENT ON COLUMN salary_changes.state_tax_rate IS 'State tax rate as a percentage';
COMMENT ON COLUMN salary_changes.retirement_contribution_rate IS 'Retirement contribution rate as a percentage';
COMMENT ON COLUMN salary_changes.health_insurance_amount IS 'Health insurance amount per paycheck';
COMMENT ON COLUMN salary_changes.other_deductions_amount IS 'Other deductions amount per paycheck';
COMMENT ON COLUMN salary_changes.notes IS 'Additional notes about the salary';
-- Update existing records with default values
UPDATE salary_changes
SET federal_tax_rate = 22.0,
    state_tax_rate = 5.0,
    retirement_contribution_rate = 6.0,
    health_insurance_amount = 249.0,
    other_deductions_amount = 100.0;