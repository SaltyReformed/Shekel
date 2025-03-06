document.addEventListener('DOMContentLoaded', function () {
    const passwordInput = document.getElementById('new_password');
    const strengthBar = document.querySelector('.strength-bar');
    const feedbackDiv = document.querySelector('.password-feedback');
    const submitButton = document.getElementById('update-profile-btn');

    // Password strength checker
    passwordInput.addEventListener('input', function () {
        const password = this.value;
        let strength = 0;
        let feedback = [];

        // Only check if there's a password value
        if (password.length > 0) {
            // Length check
            if (password.length < 16) {
                feedback.push('Password must be at least 16 characters');
            } else {
                strength += 1;
            }

            // Uppercase check
            if (!/[A-Z]/.test(password)) {
                feedback.push('Include at least one uppercase letter');
            } else {
                strength += 1;
            }

            // Lowercase check
            if (!/[a-z]/.test(password)) {
                feedback.push('Include at least one lowercase letter');
            } else {
                strength += 1;
            }

            // Number check
            if (!/[0-9]/.test(password)) {
                feedback.push('Include at least one number');
            } else {
                strength += 1;
            }

            // Symbol check
            if (!/[^A-Za-z0-9]/.test(password)) {
                feedback.push('Include at least one symbol');
            } else {
                strength += 1;
            }
        }

        // Update UI based on strength
        strengthBar.className = 'strength-bar';
        if (strength === 0) {
            feedbackDiv.textContent = 'Enter a password';
        } else if (strength === 5) {
            strengthBar.classList.add('strength-strong');
            feedbackDiv.textContent = 'Strong password!';
            feedbackDiv.style.color = 'var(--success-color)';
        } else if (strength >= 3) {
            strengthBar.classList.add('strength-good');
            feedbackDiv.textContent = feedback[0] || 'Good password, but could be stronger';
            feedbackDiv.style.color = 'var(--accent-color)';
        } else if (strength >= 2) {
            strengthBar.classList.add('strength-fair');
            feedbackDiv.textContent = feedback[0] || 'Fair password, needs improvement';
            feedbackDiv.style.color = 'var(--warning-color)';
        } else {
            strengthBar.classList.add('strength-weak');
            feedbackDiv.textContent = feedback[0] || 'Weak password';
            feedbackDiv.style.color = 'var(--danger-color)';
        }
    });

    // Form validation before submit
    document.querySelector('form').addEventListener('submit', function (e) {
        const password = passwordInput.value;

        // Only validate if a new password is provided
        if (password && password.length > 0) {
            // Check length
            if (password.length < 16) {
                e.preventDefault();
                alert('Password must be at least 16 characters long');
                return false;
            }

            // Check for uppercase, lowercase, numbers, and symbols
            if (!/[A-Z]/.test(password) ||
                !/[a-z]/.test(password) ||
                !/[0-9]/.test(password) ||
                !/[^A-Za-z0-9]/.test(password)) {
                e.preventDefault();
                alert('Password must include uppercase, lowercase, numbers, and symbols');
                return false;
            }
        }

        return true;
    });
});