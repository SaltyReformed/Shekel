document.addEventListener('DOMContentLoaded', function () {
        // Get references to form elements
        const salaryTypeRadios = document.getElementsByName('salary_type');
        const annualSalarySection = document.getElementById('annualSalarySection');
        const netPaycheckGroup = document.getElementById('netPaycheckGroup');
        const calculateButton = document.getElementById('calculateButton');
        const paycheckCalculation = document.getElementById('paycheckCalculation');
        const taxDeductionSection = document.getElementById('taxDeductionSection');
        const depositAllocationSection = document.getElementById('depositAllocationSection');
        const addAllocationButton = document.getElementById('add-allocation');

        // Form fields
        const grossAnnualSalary = document.getElementById('grossAnnualSalary');
        const netPaycheckAmount = document.getElementById('netPaycheckAmount');
        const payFrequency = document.getElementById('payFrequency');
        const federalTaxRate = document.getElementById('federalTaxRate');
        const stateTaxRate = document.getElementById('stateTaxRate');
        const retirementRate = document.getElementById('retirementRate');
        const healthInsurance = document.getElementById('healthInsurance');
        const otherDeductions = document.getElementById('otherDeductions');

        // Result display fields
        const calcGross = document.getElementById('calc-gross');
        const calcFederal = document.getElementById('calc-federal');
        const calcState = document.getElementById('calc-state');
        const calcRetirement = document.getElementById('calc-retirement');
        const calcHealth = document.getElementById('calc-health');
        const calcOther = document.getElementById('calc-other');
        const calcNet = document.getElementById('calc-net');

        // Function to update sections based on selected salary type
        function updateSalarySections() {
            // Find which radio is checked
            let selectedType = '';
            for (let radio of salaryTypeRadios) {
                if (radio.checked) {
                    selectedType = radio.value;
                    break;
                }
            }

            if (selectedType === 'annual') {
                annualSalarySection.style.display = 'block';
                netPaycheckGroup.style.display = 'none';
            } else if (selectedType === 'net_paycheck') {
                annualSalarySection.style.display = 'none';
                netPaycheckGroup.style.display = 'block';
            }

            // Hide calculation results when switching salary type
            paycheckCalculation.style.display = 'none';
        }

        // Set up event listeners for radio buttons
        for (let i = 0; i < salaryTypeRadios.length; i++) {
            salaryTypeRadios[i].addEventListener('change', updateSalarySections);
        }

        // Initialize the form state based on current radio selection
        updateSalarySections();

        // Add event listeners to tax and deduction fields to update calculation when they change
        [federalTaxRate, stateTaxRate, retirementRate, healthInsurance, otherDeductions].forEach(input => {
            if (input) {
                input.addEventListener('input', function () {
                    // If calculation is already shown, update it automatically
                    if (paycheckCalculation.style.display === 'block') {
                        calculateButton.click();
                    }
                });
            }
        });

        // Handle allocation type toggle buttons
        const setupAllocationToggles = () => {
            // Get all percentage and amount buttons
            const percentageButtons = document.querySelectorAll('.allocation-percentage-btn');
            const amountButtons = document.querySelectorAll('.allocation-amount-btn');

            // Set up event listeners for percentage buttons
            percentageButtons.forEach(button => {
                button.addEventListener('click', function () {
                    // Get the parent allocation row
                    const row = this.closest('.allocation-row');

                    // Update the hidden input value
                    const typeInput = row.querySelector('input[name$="allocation_type"]');
                    if (typeInput) typeInput.value = 'percentage';

                    // Show/hide the appropriate fields
                    const percentageField = row.querySelector('.allocation-percentage-field');
                    const amountField = row.querySelector('.allocation-amount-field');

                    if (percentageField) percentageField.style.display = 'block';
                    if (amountField) amountField.style.display = 'none';

                    // Update active state of buttons
                    this.classList.add('active');
                    const amountBtn = row.querySelector('.allocation-amount-btn');
                    if (amountBtn) amountBtn.classList.remove('active');
                });
            });

            // Set up event listeners for amount buttons
            amountButtons.forEach(button => {
                button.addEventListener('click', function () {
                    // Get the parent allocation row
                    const row = this.closest('.allocation-row');

                    // Update the hidden input value
                    const typeInput = row.querySelector('input[name$="allocation_type"]');
                    if (typeInput) typeInput.value = 'amount';

                    // Show/hide the appropriate fields
                    const percentageField = row.querySelector('.allocation-percentage-field');
                    const amountField = row.querySelector('.allocation-amount-field');

                    if (percentageField) percentageField.style.display = 'none';
                    if (amountField) amountField.style.display = 'block';

                    // Update active state of buttons
                    this.classList.add('active');
                    const percentageBtn = row.querySelector('.allocation-percentage-btn');
                    if (percentageBtn) percentageBtn.classList.remove('active');
                });
            });
        };

        // Set up initial allocation toggles
        setupAllocationToggles();

        // Function to add a new allocation row
        if (addAllocationButton) {
            addAllocationButton.addEventListener('click', function () {
                // Get the allocation container
                const container = document.querySelector('.deposit-allocations');
                if (!container) return;

                // Get the last allocation row
                const lastRow = container.querySelector('.allocation-row:last-child');
                if (!lastRow) return;

                // Get the current highest index from existing rows
                const allRows = container.querySelectorAll('.allocation-row');
                const newIndex = allRows.length; // This will be the next index (0-based)

                // Clone the row
                const newRow = lastRow.cloneNode(true);

                // Clear input values
                newRow.querySelectorAll('input[type="text"], input[type="number"]').forEach(input => {
                    if (!input.name.includes('allocation_type')) { // Don't clear the allocation type
                        input.value = '';
                    }
                });

                // Reset the allocation type to percentage
                const typeInput = newRow.querySelector('input[name$="allocation_type"]');
                if (typeInput) typeInput.value = 'percentage';

                // Update all input and select names with the new index
                newRow.querySelectorAll('input, select').forEach(element => {
                    if (element.name) {
                        // Replace the index number in the name attribute
                        // For example: deposit_allocations-0-account_id -> deposit_allocations-1-account_id
                        const newName = element.name.replace(/deposit_allocations-\d+/, `deposit_allocations-${newIndex}`);
                        element.name = newName;

                        // Also update IDs if they exist and follow the same pattern
                        if (element.id && element.id.match(/\d+/)) {
                            const newId = element.id.replace(/\d+/, newIndex);
                            element.id = newId;
                        }
                    }
                });

                // Show percentage field, hide amount field
                const percentageField = newRow.querySelector('.allocation-percentage-field');
                const amountField = newRow.querySelector('.allocation-amount-field');

                if (percentageField) percentageField.style.display = 'block';
                if (amountField) amountField.style.display = 'none';

                // Reset button active states
                const percentageBtn = newRow.querySelector('.allocation-percentage-btn');
                const amountBtn = newRow.querySelector('.allocation-amount-btn');

                if (percentageBtn) percentageBtn.classList.add('active');
                if (amountBtn) amountBtn.classList.remove('active');

                // Add a remove button if not already present
                if (!newRow.querySelector('.remove-allocation')) {
                    const removeButton = document.createElement('button');
                    removeButton.type = 'button';
                    removeButton.className = 'btn btn-outline-danger btn-sm remove-allocation';
                    removeButton.innerHTML = `
                <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" 
                    stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <line x1="18" y1="6" x2="6" y2="18"></line>
                    <line x1="6" y1="6" x2="18" y2="18"></line>
                </svg>
                Remove
            `;

                    // Add the remove button to the row
                    const buttonContainer = document.createElement('div');
                    buttonContainer.className = 'mt-2';
                    buttonContainer.appendChild(removeButton);
                    newRow.appendChild(buttonContainer);
                }

                // Set up the remove button functionality
                const removeButton = newRow.querySelector('.remove-allocation');
                if (removeButton) {
                    removeButton.addEventListener('click', function () {
                        newRow.remove();
                    });
                }

                // Add a divider before the new row if needed
                const divider = document.createElement('hr');
                divider.className = 'allocation-divider';

                // Add the new row to the container
                container.appendChild(divider);
                container.appendChild(newRow);

                // Set up the toggle buttons for the new row
                setupAllocationToggles();
            });
        }

        // Helper function to format currency
        function formatCurrency(value) {
            return '$' + parseFloat(value).toFixed(2).replace(/\d(?=(\d{3})+\.)/g, '$&,');
        }

        // Handle calculate button click
        if (calculateButton) {
            calculateButton.addEventListener('click', function () {
                const salaryType = document.querySelector('input[name="salary_type"]:checked').value;

                // Validate inputs before sending API request
                if (salaryType === 'annual' && (!grossAnnualSalary.value || parseFloat(grossAnnualSalary.value) <= 0)) {
                    alert('Please enter a valid annual salary amount.');
                    grossAnnualSalary.focus();
                    return;
                }

                if (salaryType === 'net_paycheck' && (!netPaycheckAmount.value || parseFloat(netPaycheckAmount.value) <= 0)) {
                    alert('Please enter a valid net paycheck amount.');
                    netPaycheckAmount.focus();
                    return;
                }

                // Create the payload object with common fields
                const payload = {
                    salary_type: salaryType,
                    pay_frequency: payFrequency.value,
                    federal_tax_rate: parseFloat(federalTaxRate.value) || 0,
                    state_tax_rate: parseFloat(stateTaxRate.value) || 0,
                    retirement_contribution_rate: parseFloat(retirementRate.value) || 0,
                    health_insurance_amount: parseFloat(healthInsurance.value) || 0,
                    other_deductions_amount: parseFloat(otherDeductions.value) || 0
                };

                // Add salary-type specific values
                if (salaryType === 'annual') {
                    payload.gross_annual_salary = parseFloat(grossAnnualSalary.value) || 0;
                } else {
                    payload.net_paycheck_amount = parseFloat(netPaycheckAmount.value) || 0;
                }

                // Show loading state
                calculateButton.disabled = true;
                calculateButton.textContent = 'Calculating...';

                // Call the API to calculate paycheck
                fetch('/income/calculate-paycheck', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify(payload)
                })
                    .then(response => response.json())
                    .then(data => {
                        // Update the calculation result fields
                        calcGross.textContent = formatCurrency(data.gross_salary);
                        calcFederal.textContent = formatCurrency(data.federal_tax);
                        calcState.textContent = formatCurrency(data.state_tax);
                        calcRetirement.textContent = formatCurrency(data.retirement);
                        calcHealth.textContent = formatCurrency(data.health_insurance);
                        calcOther.textContent = formatCurrency(data.other_deductions);
                        calcNet.textContent = formatCurrency(data.net_pay);

                        // If we calculated from net paycheck, update the estimated annual salary
                        if (salaryType === 'net_paycheck' && data.estimated_annual) {
                            grossAnnualSalary.value = data.estimated_annual;
                        }

                        // Show the calculation section
                        paycheckCalculation.style.display = 'block';

                        // Reset button state
                        calculateButton.disabled = false;
                        calculateButton.textContent = 'Calculate Paycheck';
                    })
                    .catch(error => {
                        console.error('Error calculating paycheck:', error);
                        alert('Error calculating paycheck. Please check your inputs and try again.');

                        // Reset button state
                        calculateButton.disabled = false;
                        calculateButton.textContent = 'Calculate Paycheck';
                    });
            });
        }
    });