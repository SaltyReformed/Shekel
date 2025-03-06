document.addEventListener('DOMContentLoaded', function () {
        // Set filter values from URL parameters
        const urlParams = new URLSearchParams(window.location.search);
        const typeParam = urlParams.get('type');
        const statusParam = urlParams.get('status');

        if (typeParam) {
            document.getElementById('type').value = typeParam;
        }

        if (statusParam) {
            document.getElementById('status').value = statusParam;
        }
    });