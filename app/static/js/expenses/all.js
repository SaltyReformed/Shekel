document.addEventListener('DOMContentLoaded', function () {
        // Mark as Paid Modal
        const markPaidModal = document.getElementById('markPaidModal');
        const accountSelect = document.getElementById('modal-account-select');
        const confirmMarkPaidBtn = document.getElementById('confirmMarkPaidBtn');
        let currentForm = null;

        // Set up event listeners for mark paid buttons
        const markPaidButtons = document.querySelectorAll('.mark-paid-btn');
        markPaidButtons.forEach(button => {
            button.addEventListener('click', function () {
                // Store reference to the form
                currentForm = this.closest('form');

                // Get default account ID if it exists
                const defaultAccountId = this.getAttribute('data-default-account');

                // Set the account select to default value if available
                if (defaultAccountId && accountSelect) {
                    accountSelect.value = defaultAccountId;
                }

                // Show the modal
                markPaidModal.classList.add('show');
                document.body.style.overflow = 'hidden';
            });
        });

        // Confirm mark as paid
        confirmMarkPaidBtn.addEventListener('click', function () {
            if (currentForm) {
                // Update the account_id input in the form
                const accountInput = currentForm.querySelector('input[name="account_id"]');
                accountInput.value = accountSelect.value;

                // Submit the form
                currentForm.submit();
            }

            // Close the modal
            closeMarkPaidModal();
        });

        // Close when clicking outside modal
        window.addEventListener('click', function (event) {
            if (event.target === markPaidModal) {
                closeMarkPaidModal();
            }
        });
    });

    // Function to close the modal
    function closeMarkPaidModal() {
        const markPaidModal = document.getElementById('markPaidModal');
        markPaidModal.classList.remove('show');
        document.body.style.overflow = '';
    }