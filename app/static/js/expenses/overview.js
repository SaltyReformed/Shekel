document.addEventListener('DOMContentLoaded', function () {
        // Set up mark-paid buttons
        const payButtons = document.querySelectorAll('.mark-paid-btn');
        payButtons.forEach(button => {
            button.addEventListener('click', function (e) {
                e.preventDefault();

                const expenseId = this.getAttribute('data-expense-id');
                const expenseAmount = this.getAttribute('data-expense-amount');

                // Set up the payment form action
                const paymentForm = document.getElementById('paymentForm');
                paymentForm.action = "/expenses/" + expenseId + "/pay";

                // Set default amount
                document.getElementById('modal-payment-amount').value = expenseAmount;

                // Open the modal
                document.getElementById('paymentModal').classList.add('show');
                document.body.style.overflow = 'hidden';
            });
        });
    });

    function closePaymentModal() {
        document.getElementById('paymentModal').classList.remove('show');
        document.body.style.overflow = '';
    }

    function submitPaymentForm() {
        document.getElementById('paymentForm').submit();
    }

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

    // Close modal when clicking outside of it
    window.addEventListener('click', function (event) {
        const modal = document.getElementById('paymentModal');
        if (event.target === modal) {
            closePaymentModal();
        }
    });