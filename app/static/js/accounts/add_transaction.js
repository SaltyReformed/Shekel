    document.addEventListener('DOMContentLoaded', function () {
        const transactionTypeRadios = document.getElementsByName('transaction_type');
        const toAccountGroup = document.getElementById('to_account_group');
        const accountLabel = document.getElementById('account_label');
        const toAccountId = document.getElementById('to_account_id');
        const accountId = document.getElementById('account_id');

        // Function to update form based on transaction type
        function updateFormForType(type) {
            if (type === 'transfer') {
                toAccountGroup.style.display = 'block';
                accountLabel.textContent = 'From Account';
                toAccountId.required = true;
            } else {
                toAccountGroup.style.display = 'none';
                accountLabel.textContent = 'Account';
                toAccountId.required = false;
            }
        }

        // Add event listeners for radio buttons
        for (let i = 0; i < transactionTypeRadios.length; i++) {
            transactionTypeRadios[i].addEventListener('change', function () {
                updateFormForType(this.value);
            });
        }

        // Prevent selecting the same account for from and to in transfers
        accountId.addEventListener('change', function () {
            if (document.getElementById('transfer').checked) {
                // Get all options from to_account_id
                const options = toAccountId.options;

                // Enable all options first
                for (let i = 0; i < options.length; i++) {
                    options[i].disabled = false;
                }

                // Disable the option that matches the selected account_id
                for (let i = 0; i < options.length; i++) {
                    if (options[i].value === this.value) {
                        options[i].disabled = true;
                    }
                }

                // If the currently selected to_account is the same as account_id, reset it
                if (toAccountId.value === this.value) {
                    toAccountId.value = '';
                }
            }
        });

        // Same check when to_account changes
        toAccountId.addEventListener('change', function () {
            if (document.getElementById('transfer').checked) {
                // Get all options from account_id
                const options = accountId.options;

                // Enable all options first
                for (let i = 0; i < options.length; i++) {
                    options[i].disabled = false;
                }

                // Disable the option that matches the selected to_account_id
                for (let i = 0; i < options.length; i++) {
                    if (options[i].value === this.value) {
                        options[i].disabled = true;
                    }
                }

                // If the currently selected account is the same as to_account_id, reset it
                if (accountId.value === this.value) {
                    accountId.value = '';
                }
            }
        });

        // Set initial state
        updateFormForType('deposit');
    });