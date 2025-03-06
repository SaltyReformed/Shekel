// Complete drag-drop.js with running balance calculations

document.addEventListener('DOMContentLoaded', function() {
    // Get all draggable expense items and all expense-paycheck cells (drop targets)
    const expenseItems = document.querySelectorAll('.expense-item');
    const expenseCells = document.querySelectorAll('.expense-paycheck-cell');
    
    // Setup draggable expense items
    expenseItems.forEach(item => {
        // Ensure the item is draggable
        item.setAttribute('draggable', 'true');
        
        item.addEventListener('dragstart', function(e) {
            e.dataTransfer.setData('text/plain', this.getAttribute('data-expense-id'));
            this.classList.add('dragging');
            
            // Add drop-target styling to each valid drop target
            expenseCells.forEach(cell => {
                cell.classList.add('drop-target');
            });
        });
        
        item.addEventListener('dragend', function() {
            this.classList.remove('dragging');
            
            // Remove drop-target and drag-over styling from all cells
            expenseCells.forEach(cell => {
                cell.classList.remove('drop-target');
                cell.classList.remove('drag-over');
            });
        });
    });
    
    // Setup drop targets (expense-paycheck cells)
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
            this.classList.remove('drag-over');
            
            // Retrieve the expense ID from the dragged item
            const expenseId = e.dataTransfer.getData('text/plain');
            // Retrieve the target paycheck ID from the cell's data attribute
            const paycheckId = this.getAttribute('data-paycheck-id');
            
            // Show a loading state in the cell
            this.classList.add('loading');
            
            // Send AJAX (fetch) request to update the expense's assignment on the server
            fetch('/expenses/assign-to-paycheck', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCsrfToken() // Function defined below to get CSRF token
                },
                body: JSON.stringify({
                    expense_id: expenseId,
                    paycheck_id: paycheckId
                })
            })
            .then(response => response.json())
            .then(data => {
                // Remove loading state regardless of success or failure
                this.classList.remove('loading');
                
                if (data.success) {
                    // Show a success notification to the user
                    showNotification('Expense reassigned successfully', 'success');
                    
                    // Find the expense item element by its data attribute
                    const expenseItem = document.querySelector(`.expense-item[data-expense-id="${expenseId}"]`);
                    if (expenseItem) {
                        // IMPORTANT: Don't update the expense date display anymore
                        // The server is sending back the original date with a flag that
                        // indicates we should preserve the original date
                        
                        // Check if the target cell already contains a container for expenses;
                        // if not, create one.
                        let container = this.querySelector('.expense-in-paycheck');
                        if (!container) {
                            container = document.createElement('div');
                            container.className = 'expense-in-paycheck';
                            this.appendChild(container);
                        }
                        
                        // Move the expense element into the container of the target cell
                        container.appendChild(expenseItem);
                        
                        // Immediately update the totals in the UI
                        updatePaycheckTotals();
                    }
                } else {
                    // Show an error notification if something went wrong
                    showNotification(data.message || 'Failed to reassign expense', 'error');
                }
            })
            .catch(error => {
                this.classList.remove('loading');
                console.error('Error:', error);
                showNotification('An error occurred while reassigning the expense', 'error');
            });
        });
    });
    
    // Helper function to get CSRF token from the page (from a hidden form input or meta tag)
    function getCsrfToken() {
        const tokenInput = document.querySelector('input[name="csrf_token"]');
        if (tokenInput) return tokenInput.value;
        const metaToken = document.querySelector('meta[name="csrf-token"]');
        if (metaToken) return metaToken.getAttribute('content');
        return '';
    }
    
    // Function to show notifications to the user
    function showNotification(message, type = 'info') {
        const notification = document.getElementById('notification');
        if (!notification) return;
        
        // Clear any existing notification classes and add the new ones
        notification.className = 'notification';
        notification.classList.add(type, 'show');
        notification.textContent = message;
        
        // Automatically hide the notification after 3 seconds
        setTimeout(() => {
            notification.classList.remove('show');
        }, 3000);
    }
    
    // Function to update paycheck totals after an expense is moved
    window.updatePaycheckTotals = function() {
        // Get all paycheck columns; each column should have a data-paycheck-id attribute
        const paycheckColumns = document.querySelectorAll('.paycheck-column');
        
        paycheckColumns.forEach(column => {
            const paycheckId = column.getAttribute('data-paycheck-id');
            
            // Find all expense items inside the corresponding expense-paycheck cell
            const expenseItems = document.querySelectorAll(`.expense-paycheck-cell[data-paycheck-id="${paycheckId}"] .expense-item`);
            let totalExpenses = 0;
            expenseItems.forEach(item => {
                const amountEl = item.querySelector('.expense-amount');
                if (amountEl) {
                    // Remove the "$" and commas before parsing the number
                    const amountText = amountEl.textContent.replace('$', '').replace(/,/g, '');
                    totalExpenses += parseFloat(amountText);
                }
            });
            
            // Update the display of total expenses for this paycheck
            const totalEl = document.querySelector(`.paycheck-total[data-paycheck-id="${paycheckId}"]`);
            if (totalEl) {
                totalEl.textContent = `$${totalExpenses.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
            }
            
            // Get the original paycheck amount from its display
            let paycheckAmount = 0;
            const paycheckAmountEl = column.querySelector('.paycheck-amount');
            if (paycheckAmountEl) {
                const amtText = paycheckAmountEl.textContent.replace('$', '').replace(/,/g, '');
                paycheckAmount = parseFloat(amtText);
            }
            
            // Calculate the remaining amount (paycheck amount minus total expenses)
            const remaining = paycheckAmount - totalExpenses;
            const remainingEl = document.querySelector(`.paycheck-remaining[data-paycheck-id="${paycheckId}"]`);
            if (remainingEl) {
                remainingEl.textContent = `$${remaining.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
                if (remaining < 0) {
                    remainingEl.classList.add('negative');
                } else {
                    remainingEl.classList.remove('negative');
                }
            }
            
            // Also update any summary table expense amounts
            const expenseSummaryEl = document.querySelector(`.paycheck-expense[data-paycheck-id="${paycheckId}"] .expense-amount`);
            if (expenseSummaryEl) {
                expenseSummaryEl.textContent = `$${totalExpenses.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
            }
        });
        
        // Update running balances after expenses are moved
        calculateRunningBalance();
        
        // Dispatch custom event to notify that expenses have been updated
        document.dispatchEvent(new CustomEvent('expensesUpdated'));
    };
    
    // Function to update running balance
    function calculateRunningBalance() {
        const startingBalanceInput = document.getElementById('starting_balance');
        let startingBalance = parseFloat(startingBalanceInput ? startingBalanceInput.value : 0) || 0;
        
        const paycheckBalanceCells = document.querySelectorAll('.paycheck-balance');
        const paycheckRemainingCells = document.querySelectorAll('.paycheck-remaining');
        
        let runningBalance = startingBalance;
        
        // Update each balance cell based on the remaining amount of each paycheck
        for (let i = 0; i < paycheckRemainingCells.length; i++) {
            // Get the remaining amount text, strip the $ sign and commas
            const remainingAmountText = paycheckRemainingCells[i].textContent.replace('$', '').replace(/,/g, '');
            // Parse as float
            const remainingAmount = parseFloat(remainingAmountText);
            
            // Add to running balance
            runningBalance += remainingAmount;
            
            // Update the balance cell
            if (paycheckBalanceCells[i]) {
                paycheckBalanceCells[i].textContent = '$' + runningBalance.toLocaleString('en-US', {
                    minimumFractionDigits: 2,
                    maximumFractionDigits: 2
                });
                
                // Update data-balance attribute
                paycheckBalanceCells[i].setAttribute('data-balance', runningBalance);
                
                // Apply negative class if balance is negative
                if (runningBalance < 0) {
                    paycheckBalanceCells[i].classList.add('negative');
                } else {
                    paycheckBalanceCells[i].classList.remove('negative');
                }
            }
        }
    }
    
    // Setup "mark as paid" button event listeners
    const markPaidButtons = document.querySelectorAll('.mark-paid-btn');
    markPaidButtons.forEach(button => {
        button.addEventListener('click', function(e) {
            e.preventDefault();
            e.stopPropagation();
            
            const expenseId = this.getAttribute('data-expense-id');
            const expenseAmount = this.getAttribute('data-expense-amount');
            
            // Configure the payment form with the expense details
            const paymentForm = document.getElementById('paymentForm');
            paymentForm.action = "/expenses/" + expenseId + "/pay";
            document.getElementById('modal-payment-amount').value = expenseAmount;
            
            // Show the payment modal by adding the "show" class and setting display to block
            const modal = document.getElementById('paymentModal');
            modal.classList.add('show');
            modal.style.display = 'block';
        });
    });
    
    // Define global functions for closing the modal and submitting the payment form
    window.closePaymentModal = function() {
        const modal = document.getElementById('paymentModal');
        if (modal) {
            modal.classList.remove('show');
            modal.style.display = 'none';
        }
    };
    
    window.submitPaymentForm = function() {
        const form = document.getElementById('paymentForm');
        if (form) {
            form.submit();
        }
    };
    
    // Initial calculation of running balance when page loads
    calculateRunningBalance();
    
    // Add event listener to recalculate balance when starting balance changes
    const startingBalanceInput = document.getElementById('starting_balance');
    if (startingBalanceInput) {
        startingBalanceInput.addEventListener('input', function() {
            calculateRunningBalance();
        });
    }
    
    // Close modal when clicking outside of it
    window.addEventListener('click', function (event) {
        const modal = document.getElementById('paymentModal');
        if (event.target === modal) {
            closePaymentModal();
        }
    });
});