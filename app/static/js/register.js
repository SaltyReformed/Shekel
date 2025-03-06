document.addEventListener('DOMContentLoaded', function () {
    const passwordInput = document.getElementById('password');
    const confirmInput = document.getElementById('confirm_password');
    const submitBtn = document.getElementById('submit-btn');
    const lengthCheck = document.getElementById('length-check');
    const uppercaseCheck = document.getElementById('uppercase-check');
    const lowercaseCheck = document.getElementById('lowercase-check');
    const numberCheck = document.getElementById('number-check');
    const symbolCheck = document.getElementById('symbol-check');
    const strengthBar = document.querySelector('.strength-bar');
    const matchFeedback = document.getElementById('password-match-feedback');

    // Password validation
    passwordInput.addEventListener('input', function () {
        const password = this.value;
        let strength = 0;

        // Check length
        if (password.length >= 16) {
            lengthCheck.classList.add('valid');
            strength++;
        } else {
            lengthCheck.classList.remove('valid');
        }

        // Check for uppercase
        if (/[A-Z]/.test(password)) {
            uppercaseCheck.classList.add('valid');
            strength++;
        } else {
            uppercaseCheck.classList.remove('valid');
        }

        // Check for lowercase
        if (/[a-z]/.test(password)) {
            lowercaseCheck.classList.add('valid');
            strength++;
        } else {
            lowercaseCheck.classList.remove('valid');
        }

        // Check for numbers
        if (/[0-9]/.test(password)) {
            numberCheck.classList.add('valid');
            strength++;
        } else {
            numberCheck.classList.remove('valid');
        }

        // Check for symbols
        if (/[^A-Za-z0-9]/.test(password)) {
            symbolCheck.classList.add('valid');
            strength++;
        } else {
            symbolCheck.classList.remove('valid');
        }

        // Update strength meter
        strengthBar.className = 'strength-bar';
        if (strength === 0) {
            // No strength
        } else if (strength < 3) {
            strengthBar.classList.add('strength-weak');
        } else if (strength < 5) {
            strengthBar.classList.add('strength-fair');
        } else {
            strengthBar.classList.add('strength-strong');
        }

        // Check if passwords match
        checkPasswordMatch();
    });

    // Confirm password validation
    confirmInput.addEventListener('input', checkPasswordMatch);

    function checkPasswordMatch() {
        const password = passwordInput.value;
        const confirmPassword = confirmInput.value;

        if (confirmPassword === '') {
            matchFeedback.textContent = '';
            return;
        }

        if (password === confirmPassword) {
            matchFeedback.textContent = 'Passwords match';
            matchFeedback.style.color = 'var(--success-color)';
        } else {
            matchFeedback.textContent = 'Passwords do not match';
            matchFeedback.style.color = 'var(--danger-color)';
        }
    }

    // Form validation
    document.getElementById('registration-form').addEventListener('submit', function (e) {
        const password = passwordInput.value;
        const confirmPassword = confirmInput.value;

        // Check if all password requirements are met
        if (
            password.length < 16 ||
            !/[A-Z]/.test(password) ||
            !/[a-z]/.test(password) ||
            !/[0-9]/.test(password) ||
            !/[^A-Za-z0-9]/.test(password)
        ) {
            e.preventDefault();
            alert('Please ensure your password meets all the requirements.');
            return;
        }

        // Check if passwords match
        if (password !== confirmPassword) {
            e.preventDefault();
            alert('Passwords do not match. Please try again.');
            return;
        }
    });
});