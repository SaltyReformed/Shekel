function setupPaymentModal(expenseId, defaultAccountId) {
        // Set up the payment form
        const paymentForm = document.getElementById('paymentForm');
        paymentForm.action = "/expenses/" + expenseId + "/pay";

        // Set default account if provided
        if (defaultAccountId) {
            const accountSelect = document.getElementById('modal-account-id');
            if (accountSelect) {
                accountSelect.value = defaultAccountId;
            }
        }

        // Open the modal
        document.getElementById('paymentModal').classList.add('show');
    }