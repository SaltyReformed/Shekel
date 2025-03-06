function showDeleteConfirmation() {
        document.getElementById('deleteConfirmationModal').classList.add('show');
        document.body.style.overflow = 'hidden';
    }

    function closeDeleteModal() {
        document.getElementById('deleteConfirmationModal').classList.remove('show');
        document.body.style.overflow = '';
    }

    function confirmDelete() {
        // Set the hidden input based on checkbox
        const deletePaychecks = document.getElementById('deletePaychecksCheck').checked;
        document.getElementById('deletePaychecks').value = deletePaychecks ? "1" : "0";

        // Submit the form
        document.getElementById('deleteSalaryForm').submit();
    }

    function confirmSalaryDelete() {
        // This function is needed for form's onsubmit, but we'll handle via modal
        return false;
    }

    // Close modal when clicking outside of it
    window.addEventListener('click', function (event) {
        const modal = document.getElementById('deleteConfirmationModal');
        if (event.target === modal) {
            closeDeleteModal();
        }
    });