document.addEventListener('DOMContentLoaded', function() {
    var closeBtn = document.getElementById('modalCloseBtn');
    var cancelBtn = document.getElementById('modalCancelBtn');
    var markPaidBtn = document.getElementById('modalMarkPaidBtn');

    if (closeBtn) {
        closeBtn.addEventListener('click', closePaymentModal);
    }
    if (cancelBtn) {
        cancelBtn.addEventListener('click', closePaymentModal);
    }
    if (markPaidBtn) {
        markPaidBtn.addEventListener('click', submitPaymentForm);
    }
});

function closePaymentModal() {
    var modal = document.getElementById('paymentModal');
    if (modal) {
        modal.classList.remove('show');
        modal.style.display = 'none';
    }
}

function submitPaymentForm() {
    var form = document.getElementById('paymentForm');
    if (form) {
        form.submit();
    }
}
