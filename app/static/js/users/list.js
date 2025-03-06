document.addEventListener('DOMContentLoaded', function () {
        // Modal functionality
        const modalToggles = document.querySelectorAll('[data-toggle="modal"]');
        const modalCloses = document.querySelectorAll('[data-dismiss="modal"]');

        modalToggles.forEach(toggle => {
            toggle.addEventListener('click', function () {
                const targetModal = document.querySelector(this.getAttribute('data-target'));
                if (targetModal) {
                    targetModal.classList.add('show');
                    document.body.style.overflow = 'hidden';
                }
            });
        });

        modalCloses.forEach(close => {
            close.addEventListener('click', function () {
                const modal = this.closest('.modal');
                if (modal) {
                    modal.classList.remove('show');
                    document.body.style.overflow = '';
                }
            });
        });

        // Close modal when clicking outside of it
        window.addEventListener('click', function (event) {
            if (event.target.classList.contains('modal')) {
                event.target.classList.remove('show');
                document.body.style.overflow = '';
            }
        });

        // Password validation for Add User form
        const addPasswordInput = document.getElementById('password');
        const addLengthCheck = document.getElementById('add-length-check');
        const addUppercaseCheck = document.getElementById('add-uppercase-check');
        const addLowercaseCheck = document.getElementById('add-lowercase-check');
        const addNumberCheck = document.getElementById('add-number-check');
        const addSymbolCheck = document.getElementById('add-symbol-check');
        const addStrengthBar = document.getElementById('add-strength-bar');
        const addPasswordFeedback = document.getElementById('add-password-feedback');

        if (addPasswordInput) {
            addPasswordInput.addEventListener('input', function () {
                validatePassword(this.value, {
                    lengthCheck: addLengthCheck,
                    uppercaseCheck: addUppercaseCheck,
                    lowercaseCheck: addLowercaseCheck,
                    numberCheck: addNumberCheck,
                    symbolCheck: addSymbolCheck,
                    strengthBar: addStrengthBar,
                    feedbackEl: addPasswordFeedback
                });
            });
        }

        // Add form submission validation
        document.getElementById('add-user-form').addEventListener('submit', function (e) {
            const password = addPasswordInput.value;
            if (!isPasswordValid(password)) {
                e.preventDefault();
                alert('Please ensure the password meets all requirements.');
            }
        });

        // Password validation for all Edit User forms
        const editForms = document.querySelectorAll('form[id^="edit-user-form-"]');

        editForms.forEach(form => {
            const userId = form.id.split('-').pop();
            const passwordInput = document.getElementById(`password${userId}`);
            const strengthBar = document.getElementById(`strength-bar${userId}`);
            const feedbackEl = document.getElementById(`password-feedback${userId}`);

            if (passwordInput) {
                passwordInput.addEventListener('input', function () {
                    // Only validate if a new password is being set
                    if (this.value.length > 0) {
                        validatePasswordSimple(this.value, strengthBar, feedbackEl);
                    } else {
                        // Clear feedback if password field is empty
                        strengthBar.className = 'strength-bar';
                        feedbackEl.textContent = '';
                    }
                });

                // Form submission for edit forms
                form.addEventListener('submit', function (e) {
                    const password = passwordInput.value;

                    // Only validate if a new password is being set
                    if (password.length > 0 && !isPasswordValid(password)) {
                        e.preventDefault();
                        alert('Please ensure the password meets all requirements: at least 16 characters, uppercase, lowercase, number, and symbol.');
                    }
                });
            }
        });

        // Password validation functions
        function validatePassword(password, elements) {
            let strength = 0;

            // Check length
            if (password.length >= 16) {
                elements.lengthCheck.classList.add('valid');
                strength++;
            } else {
                elements.lengthCheck.classList.remove('valid');
            }

            // Check for uppercase
            if (/[A-Z]/.test(password)) {
                elements.uppercaseCheck.classList.add('valid');
                strength++;
            } else {
                elements.uppercaseCheck.classList.remove('valid');
            }

            // Check for lowercase
            if (/[a-z]/.test(password)) {
                elements.lowercaseCheck.classList.add('valid');
                strength++;
            } else {
                elements.lowercaseCheck.classList.remove('valid');
            }

            // Check for numbers
            if (/[0-9]/.test(password)) {
                elements.numberCheck.classList.add('valid');
                strength++;
            } else {
                elements.numberCheck.classList.remove('valid');
            }

            // Check for symbols
            if (/[^A-Za-z0-9]/.test(password)) {
                elements.symbolCheck.classList.add('valid');
                strength++;
            } else {
                elements.symbolCheck.classList.remove('valid');
            }

            // Update strength meter
            updateStrengthMeter(strength, elements.strengthBar, elements.feedbackEl);
        }

        function validatePasswordSimple(password, strengthBar, feedbackEl) {
            let strength = 0;
            let feedback = '';

            // Check all requirements
            if (password.length >= 16) strength++;
            if (/[A-Z]/.test(password)) strength++;
            if (/[a-z]/.test(password)) strength++;
            if (/[0-9]/.test(password)) strength++;
            if (/[^A-Za-z0-9]/.test(password)) strength++;

            // Generate feedback
            if (strength < 5) {
                feedback = 'Password does not meet all requirements';
            }

            // Update strength meter
            updateStrengthMeter(strength, strengthBar, feedbackEl, feedback);
        }

        function updateStrengthMeter(strength, strengthBar, feedbackEl, customFeedback = null) {
            strengthBar.className = 'strength-bar';

            if (strength === 0) {
                // No strength
                feedbackEl.textContent = '';
            } else if (strength < 3) {
                strengthBar.classList.add('strength-weak');
                feedbackEl.textContent = customFeedback || 'Weak password';
                feedbackEl.style.color = 'var(--danger-color)';
            } else if (strength < 5) {
                strengthBar.classList.add('strength-fair');
                feedbackEl.textContent = customFeedback || 'Password needs improvement';
                feedbackEl.style.color = 'var(--warning-color)';
            } else {
                strengthBar.classList.add('strength-strong');
                feedbackEl.textContent = 'Strong password';
                feedbackEl.style.color = 'var(--success-color)';
            }
        }

        function isPasswordValid(password) {
            return password.length >= 16 &&
                /[A-Z]/.test(password) &&
                /[a-z]/.test(password) &&
                /[0-9]/.test(password) &&
                /[^A-Za-z0-9]/.test(password);
        }
    });