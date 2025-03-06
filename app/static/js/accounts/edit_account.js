    document.addEventListener('DOMContentLoaded', function () {
        // Toggle initial balance handling based on account type selection
        const typeSelect = document.getElementById('type_id');
        const isDebtCheckbox = document.getElementById('is_debt_balance');

        if (typeSelect && isDebtCheckbox) {
            typeSelect.addEventListener('change', function () {
                const selectedOption = this.options[this.selectedIndex];
                const isDebtType = selectedOption.text.includes('(Debt)');
                isDebtCheckbox.checked = isDebtType;
            });

            // Trigger change event to set initial state
            typeSelect.dispatchEvent(new Event('change'));
        }
    });