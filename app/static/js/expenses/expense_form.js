    document.addEventListener('DOMContentLoaded', function () {
        const isPaidCheckbox = document.getElementById('is_paid');
        const paymentSection = document.getElementById('payment-section');

        // Function to toggle payment section visibility
        function togglePaymentSection() {
            if (isPaidCheckbox.checked) {
                paymentSection.style.display = 'block';
            } else {
                paymentSection.style.display = 'none';
            }
        }

        // Set initial state
        togglePaymentSection();

        // Add event listener
        isPaidCheckbox.addEventListener('change', togglePaymentSection);
    });