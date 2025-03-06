// running_balance.js - Script for calculating and updating running balances

document.addEventListener('DOMContentLoaded', function() {
    // Calculate running balance when page loads
    calculateRunningBalance();
    
    // Add event listener to recalculate balance when starting balance changes
    const startingBalanceInput = document.getElementById('starting_balance');
    if (startingBalanceInput) {
        startingBalanceInput.addEventListener('input', function() {
            calculateRunningBalance();
        });
    }
    
    // Listen for custom event when expenses are dragged and update running balance
    document.addEventListener('expensesUpdated', function() {
        calculateRunningBalance();
    });
    
    // Function to calculate and update running balance
    function calculateRunningBalance() {
        const startingBalanceInput = document.getElementById('starting_balance');
        let startingBalance = parseFloat(startingBalanceInput ? startingBalanceInput.value : 0) || 0;
        
        const paycheckBalanceCells = document.querySelectorAll('.paycheck-balance');
        const paycheckRemainingCells = document.querySelectorAll('.paycheck-remaining');
        
        // Initialize running balance with starting balance
        let runningBalance = startingBalance;
        
        // Update each balance cell based on the remaining amount of each paycheck
        for (let i = 0; i < paycheckRemainingCells.length; i++) {
            // Get the remaining amount text, strip the $ sign and commas
            const remainingAmountText = paycheckRemainingCells[i].textContent.replace('$', '').replace(/,/g, '');
            // Parse as float
            const remainingAmount = parseFloat(remainingAmountText);
            
            // For the first paycheck, add starting balance + remaining amount
            // For other paychecks, just add their remaining amount to the previous running balance
            if (i === 0) {
                runningBalance = startingBalance + remainingAmount;
            } else {
                runningBalance += remainingAmount;
            }
            
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
    
    // Extend the original updatePaycheckTotals function to also update running balance
    // This will ensure running balance is updated when expenses are moved via drag and drop
    const originalUpdatePaycheckTotals = window.updatePaycheckTotals;
    if (typeof originalUpdatePaycheckTotals === 'function') {
        window.updatePaycheckTotals = function() {
            if (originalUpdatePaycheckTotals) {
                originalUpdatePaycheckTotals.apply(this, arguments);
            }
            calculateRunningBalance();
            
            // Dispatch a custom event that running balance has been updated
            document.dispatchEvent(new CustomEvent('balanceUpdated'));
        };
    }
});
