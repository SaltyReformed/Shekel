// Drag and drop functionality for expense items
document.addEventListener('DOMContentLoaded', function() {
    // Get all draggable expense items
    const expenseItems = document.querySelectorAll('.expense-item');
    const expenseCells = document.querySelectorAll('.expense-paycheck-cell');
    
    // Setup draggable items
    expenseItems.forEach(item => {
        item.setAttribute('draggable', 'true');
        
        item.addEventListener('dragstart', function(e) {
            e.dataTransfer.setData('text/plain', this.getAttribute('data-expense-id'));
            this.classList.add('dragging');
            
            // Add drop target styling to all valid drop targets
            expenseCells.forEach(cell => {
                cell.classList.add('drop-target');
            });
        });
        
        item.addEventListener('dragend', function() {
            this.classList.remove('dragging');
            
            // Remove drop target styling
            expenseCells.forEach(cell => {
                cell.classList.remove('drop-target');
                cell.classList.remove('drag-over');
            });
        });
    });
    
    // Setup drop targets
    expenseCells.forEach(cell => {
        cell.addEventListener('dragover', function(e) {
            e.preventDefault(); // Allow drop
            this.classList.add('drag-over');
        });
        
        cell.addEventListener('dragleave', function() {
            this.classList.remove('drag-over');
        });
        
        cell.addEventListener('drop', function(e) {
            e.preventDefault();
            
            // Get the expense ID and target paycheck ID
            const expenseId = e.dataTransfer.getData('text/plain');
            const paycheckId = this.getAttribute('data-paycheck-id');
            
            // Show loading state
            this.classList.add('loading');
            
            // Send AJAX request to update the expense
            fetch('/expenses/assign-to-paycheck', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCsrfToken() // Function to get CSRF token
                },
                body: JSON.stringify({
                    expense_id: expenseId,
                    paycheck_id: paycheckId
                })
            })
            .then(response => response.json())
            .then(data => {
                // Remove loading state
                this.classList.remove('loading');
                
                if (data.success) {
                    // Show success notification
                    showNotification('Expense reassigned successfully', 'success');
                    
                    // Move the expense item to the new cell
                    const expenseItem = document.querySelector(`.expense-item[data-expense-id="${expenseId}"]`);
                    if (expenseItem) {
                        // Update the expense date if provided in the response
                        if (data.expense_date) {
                            const dateEl = expenseItem.querySelector('.expense-date');
                            if (dateEl) {
                                // Format date as MM/DD
                                const date = new Date(data.expense_date);
                                const month = date.getMonth() + 1;
                                const day = date.getDate();
                                dateEl.textContent = `${month.toString().padStart(2, '0')}/${day.toString().padStart(2, '0')}`;
                            }
                        }
                        
                        // Check if the target cell already has a container for expenses
                        let container = this.querySelector('.expense-in-paycheck');
                        
                        // If not, create one
                        if (!container) {
                            container = document.createElement('div');
                            container.className = 'expense-in-paycheck';
                            this.appendChild(container);
                        }
                        
                        // Move the expense item to the container
                        container.appendChild(expenseItem);
                        
                        // Update the totals (will be implemented in the next step)
                        updatePaycheckTotals();
                    }
                } else {
                    // Show error notification
                    showNotification(data.message || 'Failed to reassign expense', 'error');
                }
            })
            .catch(error => {
                // Remove loading state and show error
                this.classList.remove('loading');
                console.error('Error:', error);
                showNotification('An error occurred while reassigning the expense', 'error');
            });
        });
    });
    
    // Helper function to get CSRF token
    function getCsrfToken() {
        // First try to get it from the form
        const tokenInput = document.querySelector('input[name="csrf_token"]');
        if (tokenInput) return tokenInput.value;
        
        // If not found in form, try to get it from meta tag
        const metaToken = document.querySelector('meta[name="csrf-token"]');
        if (metaToken) return metaToken.getAttribute('content');
        
        return ''; // Return empty if not found
    }
    
    // Function to show notifications
    function showNotification(message, type = 'info') {
        const notification = document.getElementById('notification');
        
        // Clear any existing classes and add the new one
        notification.className = 'notification';
        notification.classList.add(type);
        notification.classList.add('show');
        
        // Set message
        notification.textContent = message;
        
        // Hide after 3 seconds
        setTimeout(() => {
            notification.classList.remove('show');
        }, 3000);
    }
    
    // Function to update paycheck totals after drag and drop
    function updatePaycheckTotals() {
        // Calculate new expense totals for each paycheck
        const paycheckColumns = document.querySelectorAll('.paycheck-column');
        
        paycheckColumns.forEach(column => {
            const paycheckId = column.getAttribute('data-paycheck-id');
            
            // Find all expense items in this paycheck column
            const expenseItems = document.querySelectorAll(`.expense-paycheck-cell[data-paycheck-id="${paycheckId}"] .expense-item`);
            
            // Calculate total expenses
            let totalExpenses = 0;
            expenseItems.forEach(item => {
                // Extract amount from the expense item
                const amountEl = item.querySelector('.expense-amount');
                if (amountEl) {
                    // Parse the amount text (remove $ and commas)
                    const amountText = amountEl.textContent.replace('$', '').replace(/,/g, '');
                    totalExpenses += parseFloat(amountText);
                }
            });
            
            // Update total expenses display
            const totalEl = document.querySelector(`.paycheck-total[data-paycheck-id="${paycheckId}"]`);
            if (totalEl) {
                totalEl.textContent = `$${totalExpenses.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
            }
            
            // Get the paycheck amount
            let paycheckAmount = 0;
            const paycheckAmountEl = column.querySelector('.paycheck-amount');
            if (paycheckAmountEl) {
                // Parse the amount text (remove $ and commas)
                const amountText = paycheckAmountEl.textContent.replace('$', '').replace(/,/g, '');
                paycheckAmount = parseFloat(amountText);
            }
            
            // Calculate remaining amount
            const remaining = paycheckAmount - totalExpenses;
            
            // Update remaining display
            const remainingEl = document.querySelector(`.paycheck-remaining[data-paycheck-id="${paycheckId}"]`);
            if (remainingEl) {
                remainingEl.textContent = `$${remaining.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
                
                // Update negative class
                if (remaining < 0) {
                    remainingEl.classList.add('negative');
                } else {
                    remainingEl.classList.remove('negative');
                }
            }
            
            // In the income-expenses view, also update the end balances
            updateEndBalances();
        });
    }
    
    // Function to update end balances in the income-expenses view
    function updateEndBalances() {
        // This function is specific to the income-expenses-by-paycheck view
        const summaryTable = document.querySelector('.paycheck-summary-table');
        if (!summaryTable) return; // Not on the income-expenses view
        
        // Get starting balance
        let startingBalance = 0;
        const startingBalanceInput = document.getElementById('starting_balance');
        if (startingBalanceInput) {
            startingBalance = parseFloat(startingBalanceInput.value || '0');
        }
        
        // Get all net amounts and calculate running balance
        const netAmounts = document.querySelectorAll('.net-amount');
        const balanceAmounts = document.querySelectorAll('.balance-amount');
        
        let runningBalance = startingBalance;
        
        // Update each balance amount
        for (let i = 0; i < netAmounts.length; i++) {
            // Parse the net amount
            const netText = netAmounts[i].textContent.replace('$', '').replace(/,/g, '');
            const netAmount = parseFloat(netText);
            
            // Calculate new running balance
            runningBalance += netAmount;
            
            // Update balance amount
            balanceAmounts[i].textContent = `$${runningBalance.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
            
            // Update negative class
            if (runningBalance < 0) {
                balanceAmounts[i].classList.add('negative');
            } else {
                balanceAmounts[i].classList.remove('negative');
            }
        }
    }
    
    // Setup mark paid buttons
    const markPaidButtons = document.querySelectorAll('.mark-paid-btn');
    markPaidButtons.forEach(button => {
        button.addEventListener('click', function(e) {
            e.preventDefault();
            e.stopPropagation();
            
            const expenseId = this.getAttribute('data-expense-id');
            const expenseAmount = this.getAttribute('data-expense-amount');
            
            // Set up the payment form
            const paymentForm = document.getElementById('paymentForm');
            paymentForm.action = "/expenses/" + expenseId + "/pay";
            
            // Set default amount
            document.getElementById('modal-payment-amount').value = expenseAmount;
            
            // Show the modal
            document.getElementById('paymentModal').classList.add('show');
        });
    });
    
    // Close payment modal
    window.closePaymentModal = function() {
        document.getElementById('paymentModal').classList.remove('show');
    };
    
    // Submit payment form
    window.submitPaymentForm = function() {
        document.getElementById('paymentForm').submit();
    };
});