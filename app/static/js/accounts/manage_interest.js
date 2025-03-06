    document.addEventListener('DOMContentLoaded', function () {
        // Get references to form elements
        const rateInput = document.getElementById('rate');
        const interestTypeSelect = document.getElementById('interest_type');
        const compoundFrequencySelect = document.getElementById('compound_frequency');
        const accrualDayInput = document.getElementById('accrual_day');
        const timePeriodInput = document.getElementById('time_period');
        const calculateBtn = document.getElementById('calculate_interest');
        const futureBalanceOutput = document.getElementById('future_balance');
        const interestEarnedOutput = document.getElementById('interest_earned');

        // Current balance from the server
        const currentBalance = {{ account.balance| float
    }};


    // Function to calculate future value with interest
    function calculateFutureValue() {
        const rate = parseFloat(rateInput.value) / 100; // Convert percentage to decimal
        const months = parseInt(timePeriodInput.value);
        const interestType = interestTypeSelect.value;
        const compoundFrequency = compoundFrequencySelect.value;

        let futureValue = 0;

        if (interestType === 'simple') {
            // Simple interest: A = P(1 + rt)
            const years = months / 12;
            futureValue = currentBalance * (1 + (rate * years));
        } else {
            // Compound interest: A = P(1 + r/n)^(nt)
            let periodsPerYear = 1;

            switch (compoundFrequency) {
                case 'daily':
                    periodsPerYear = 365;
                    break;
                case 'monthly':
                    periodsPerYear = 12;
                    break;
                case 'quarterly':
                    periodsPerYear = 4;
                    break;
                case 'annually':
                default:
                    periodsPerYear = 1;
            }

            const years = months / 12;
            futureValue = currentBalance * Math.pow((1 + (rate / periodsPerYear)), (periodsPerYear * years));
        }

        // Update results
        const interestEarned = futureValue - currentBalance;

        futureBalanceOutput.textContent = '$' + futureValue.toLocaleString('en-US', {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        });

        interestEarnedOutput.textContent = '$' + interestEarned.toLocaleString('en-US', {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        });

    }

    // Add event listener to calculate button
    calculateBtn.addEventListener('click', calculateFutureValue);

    // Initial calculation
    calculateFutureValue();

    // Show/hide accrual day input based on compound frequency
    compoundFrequencySelect.addEventListener('change', function () {
        if (this.value === 'monthly') {
            accrualDayInput.parentElement.style.display = 'block';
        } else {
            accrualDayInput.parentElement.style.display = 'none';
        }
    });

    // Trigger the change event to set initial visibility
    compoundFrequencySelect.dispatchEvent(new Event('change'));
    });