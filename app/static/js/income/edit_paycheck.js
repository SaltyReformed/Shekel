document.addEventListener('DOMContentLoaded', function () {
        // References to form elements
        const grossSalaryInput = document.getElementById('gross_salary');
        const taxesInput = document.getElementById('taxes');
        const deductionsInput = document.getElementById('deductions');
        const netAmountDisplay = document.getElementById('net-amount');
        const paidCheckbox = document.getElementById('paid');
        const paymentSection = document.getElementById('payment-section');
        const toggleLabel = document.querySelector('.toggle-status');
        const addAllocationBtn = document.getElementById('add-allocation');
        const allocationContainer = document.querySelector('.allocation-container');
        const totalAllocatedEl = document.getElementById('total-allocated');
        const allocationPercentageEl = document.getElementById('allocation-percentage');
        const updateButton = document.getElementById('updatePaycheckBtn');

        // Calculate net amount when inputs change
        function calculateNet() {
            const gross = parseFloat(grossSalaryInput.value) || 0;
            const taxes = parseFloat(taxesInput.value) || 0;
            const deductions = parseFloat(deductionsInput.value) || 0;
            const net = gross - taxes - deductions;

            netAmountDisplay.textContent = '$' + net.toFixed(2);

            // Update allocation calculations based on new net amount
            updateAllocationTotals();

            return net;
        }

        // Update form when paid status changes
        function updatePaidStatus() {
            if (paidCheckbox.checked) {
                paymentSection.style.display = 'block';
                toggleLabel.textContent = 'Received';
            } else {
                paymentSection.style.display = 'none';
                toggleLabel.textContent = 'Pending';
            }
        }

        // Get the next allocation index for adding new allocations
        function getNextAllocationIndex() {
            const allocationRows = document.querySelectorAll('.allocation-row');
            return allocationRows.length;
        }

        // Clone and add a new allocation row
        function addAllocationRow() {
            const allocations = document.querySelectorAll('.allocation-row');
            const lastAllocation = allocations[allocations.length - 1];
            const newIndex = getNextAllocationIndex();

            // Clone the last allocation row
            const newAllocation = lastAllocation.cloneNode(true);
            newAllocation.dataset.index = newIndex;

            // Update input IDs and names
            const inputs = newAllocation.querySelectorAll('input, select');
            inputs.forEach(input => {
                const oldName = input.name;
                const oldId = input.id;

                // Parse the name to get the field name without the index
                const nameParts = oldName.split('-');
                if (nameParts.length === 3) {
                    const fieldName = nameParts[2];
                    const newName = `allocations-${newIndex}-${fieldName}`;
                    input.name = newName;

                    if (oldId) {
                        input.id = oldId.replace(/\d+/, newIndex);
                    }

                    // Clear values for the new allocation, except keep account_id
                    if (fieldName !== 'account_id' && !input.type.includes('hidden') && !input.type.includes('radio')) {
                        input.value = '';
                    }

                    // For payment_id, clear it for new allocations
                    if (fieldName === 'payment_id') {
                        input.value = '';
                    }
                }
            });

            // Set default values for the new allocation
            const allocationType = newAllocation.querySelector('[name$="-allocation_type"]');
            if (allocationType) {
                allocationType.value = 'percentage';
            }

            const percentageInput = newAllocation.querySelector('[name$="-percentage"]');
            if (percentageInput) {
                percentageInput.value = '0';
            }

            const amountInput = newAllocation.querySelector('[name$="-amount"]');
            if (amountInput) {
                amountInput.value = '0';
            }

            // Show/hide percentage/amount fields based on allocation type
            const percentageField = newAllocation.querySelector('.allocation-percentage-field');
            const amountField = newAllocation.querySelector('.allocation-amount-field');
            percentageField.style.display = 'block';
            amountField.style.display = 'none';

            // Update the allocation type buttons
            const percentageBtn = newAllocation.querySelector('.allocation-percentage-btn');
            const amountBtn = newAllocation.querySelector('.allocation-amount-btn');
            percentageBtn.classList.add('active');
            amountBtn.classList.remove('active');

            // Add event listeners to the new allocation row
            addAllocationEventListeners(newAllocation);

            // Add a remove button if not present
            let removeButton = newAllocation.querySelector('.remove-allocation');
            if (!removeButton) {
                const buttonCol = document.createElement('div');
                buttonCol.className = 'col-md-6';

                removeButton = document.createElement('button');
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

                buttonCol.appendChild(removeButton);

                const rowDiv = newAllocation.querySelector('.row:last-child');
                rowDiv.appendChild(buttonCol);
            }

            // Add a divider before the new allocation
            const divider = document.createElement('hr');
            divider.className = 'allocation-divider';
            allocationContainer.appendChild(divider);

            // Add the new allocation to the container
            allocationContainer.appendChild(newAllocation);

            // Update totals
            updateAllocationTotals();
        }

        // Remove an allocation row
        function removeAllocationRow(button) {
            const row = button.closest('.allocation-row');

            // Don't remove if it's the only row
            const allocationRows = document.querySelectorAll('.allocation-row');
            if (allocationRows.length <= 1) {
                return;
            }

            // If there's a divider before the row, remove it
            const prevSibling = row.previousElementSibling;
            if (prevSibling && prevSibling.classList.contains('allocation-divider')) {
                prevSibling.remove();
            } else {
                // Otherwise, remove the divider after the row
                const nextSibling = row.nextElementSibling;
                if (nextSibling && nextSibling.classList.contains('allocation-divider')) {
                    nextSibling.remove();
                }
            }

            // Remove the row
            row.remove();

            // Update totals
            updateAllocationTotals();
        }

        // Toggle between percentage and fixed amount fields
        function toggleAllocationType(row, type) {
            const percentageField = row.querySelector('.allocation-percentage-field');
            const amountField = row.querySelector('.allocation-amount-field');
            const allocationTypeInput = row.querySelector('[name$="-allocation_type"]');

            if (type === 'percentage') {
                percentageField.style.display = 'block';
                amountField.style.display = 'none';
                allocationTypeInput.value = 'percentage';
            } else {
                percentageField.style.display = 'none';
                amountField.style.display = 'block';
                allocationTypeInput.value = 'amount';
            }

            // Update totals
            updateAllocationTotals();
        }

        // Calculate and update total allocations
        function updateAllocationTotals() {
            const netPay = calculateNet();
            let totalFixed = 0;
            let totalPercentage = 0;
            let totalAllocated = 0;

            // Calculate totals from all allocation rows
            document.querySelectorAll('.allocation-row').forEach(row => {
                const allocationType = row.querySelector('[name$="-allocation_type"]').value;

                if (allocationType === 'percentage') {
                    const percentageInput = row.querySelector('[name$="-percentage"]');
                    const percentage = parseFloat(percentageInput.value) || 0;
                    totalPercentage += percentage;

                    // Calculate the dollar amount for this percentage
                    const amount = (netPay * percentage) / 100;
                    totalAllocated += amount;
                } else {
                    const amountInput = row.querySelector('[name$="-amount"]');
                    const amount = parseFloat(amountInput.value) || 0;
                    totalFixed += amount;
                    totalAllocated += amount;
                }
            });

            // Update the display
            totalAllocatedEl.textContent = '$' + totalAllocated.toFixed(2);

            // Calculate percentage of net pay
            const percentageOfNet = netPay > 0 ? (totalAllocated / netPay) * 100 : 0;
            allocationPercentageEl.textContent = percentageOfNet.toFixed(0) + '%';

            // Highlight issues with allocations
            if (Math.abs(totalPercentage - 100) > 0.01 && totalPercentage > 0) {
                // If percentage allocations don't sum to 100%
                allocationPercentageEl.style.color = 'var(--danger-color)';
                allocationPercentageEl.textContent += ' (Percentages must sum to 100%)';
            } else if (totalAllocated > netPay) {
                // If total allocation exceeds net pay
                totalAllocatedEl.style.color = 'var(--danger-color)';
                allocationPercentageEl.style.color = 'var(--danger-color)';
                allocationPercentageEl.textContent += ' (Exceeds net pay)';
            } else {
                // Reset styling
                totalAllocatedEl.style.color = '';
                allocationPercentageEl.style.color = '';
            }

            // Validate form
            validateAllocations();
        }

        // Validate allocations before form submission
        function validateAllocations() {
            const netPay = calculateNet();
            let totalFixed = 0;
            let totalPercentage = 0;
            let isValid = true;

            // Only validate if the paycheck is marked as paid
            if (!paidCheckbox.checked) {
                updateButton.disabled = false;
                return true;
            }

            // Calculate totals from all allocation rows
            document.querySelectorAll('.allocation-row').forEach(row => {
                const allocationType = row.querySelector('[name$="-allocation_type"]').value;
                const accountSelect = row.querySelector('[name$="-account_id"]');

                // Check if an account is selected
                if (accountSelect.value === '') {
                    isValid = false;
                    accountSelect.style.borderColor = 'var(--danger-color)';
                } else {
                    accountSelect.style.borderColor = '';
                }

                if (allocationType === 'percentage') {
                    const percentageInput = row.querySelector('[name$="-percentage"]');
                    const percentage = parseFloat(percentageInput.value) || 0;
                    totalPercentage += percentage;

                    // Validate percentage is between 0 and 100
                    if (percentage <= 0 || percentage > 100) {
                        isValid = false;
                        percentageInput.style.borderColor = 'var(--danger-color)';
                    } else {
                        percentageInput.style.borderColor = '';
                    }
                } else {
                    const amountInput = row.querySelector('[name$="-amount"]');
                    const amount = parseFloat(amountInput.value) || 0;
                    totalFixed += amount;

                    // Validate amount is greater than 0
                    if (amount <= 0) {
                        isValid = false;
                        amountInput.style.borderColor = 'var(--danger-color)';
                    } else {
                        amountInput.style.borderColor = '';
                    }
                }
            });

            // Check if percentage allocations sum to 100%
            const hasPercentageAllocations = document.querySelector('.allocation-row [name$="-allocation_type"][value="percentage"]') !== null;
            if (hasPercentageAllocations && Math.abs(totalPercentage - 100) > 0.01) {
                isValid = false;
            }

            // Check if total fixed amount exceeds net pay
            if (totalFixed > netPay) {
                isValid = false;
            }

            // Enable/disable the update button based on validation
            updateButton.disabled = !isValid;

            return isValid;
        }

        // Add event listeners to an allocation row
        function addAllocationEventListeners(row) {
            // Type toggle buttons
            const percentageBtn = row.querySelector('.allocation-percentage-btn');
            const amountBtn = row.querySelector('.allocation-amount-btn');

            percentageBtn.addEventListener('click', () => {
                percentageBtn.classList.add('active');
                amountBtn.classList.remove('active');
                toggleAllocationType(row, 'percentage');
            });

            amountBtn.addEventListener('click', () => {
                amountBtn.classList.add('active');
                percentageBtn.classList.remove('active');
                toggleAllocationType(row, 'amount');
            });

            // Remove button
            const removeBtn = row.querySelector('.remove-allocation');
            if (removeBtn) {
                removeBtn.addEventListener('click', () => removeAllocationRow(removeBtn));
            }

            // Input change events
            const percentageInput = row.querySelector('[name$="-percentage"]');
            const amountInput = row.querySelector('[name$="-amount"]');
            const accountSelect = row.querySelector('[name$="-account_id"]');

            if (percentageInput) {
                percentageInput.addEventListener('input', updateAllocationTotals);
            }

            if (amountInput) {
                amountInput.addEventListener('input', updateAllocationTotals);
            }

            if (accountSelect) {
                accountSelect.addEventListener('change', updateAllocationTotals);
            }
        }

        // Form submission handler
        function handleFormSubmit(e) {
            if (paidCheckbox.checked && !validateAllocations()) {
                e.preventDefault();
                alert('Please correct the errors in the deposit allocations before submitting.');
            }
        }

        // Initialize event listeners
        function initEventListeners() {
            // Basic paycheck calculation
            grossSalaryInput.addEventListener('input', calculateNet);
            taxesInput.addEventListener('input', calculateNet);
            deductionsInput.addEventListener('input', calculateNet);

            // Paid status toggle
            paidCheckbox.addEventListener('change', updatePaidStatus);

            // Add allocation button
            addAllocationBtn.addEventListener('click', addAllocationRow);

            // Add event listeners to existing allocation rows
            document.querySelectorAll('.allocation-row').forEach(row => {
                addAllocationEventListeners(row);
            });

            // Form submission
            document.getElementById('paycheckForm').addEventListener('submit', handleFormSubmit);
        }

        // Initialize the form
        function init() {
            calculateNet();
            updatePaidStatus();
            updateAllocationTotals();
            initEventListeners();
        }

        // Start initialization
        init();
    });