// running_balance.js - Script for calculating and updating running balances
// Simplified version that uses account balance directly

document.addEventListener('DOMContentLoaded', function() {
    // Calculate running balance when page loads
    calculateRunningBalance();
    
    // Add account change event listener to recalculate balances
    const accountSelect = document.getElementById('account_id');
    if (accountSelect) {
        accountSelect.addEventListener('change', function() {
            calculateRunningBalance();
        });
    }
    
    // Listen for custom event when expenses are dragged and update running balance
    document.addEventListener('expensesUpdated', function() {
        calculateRunningBalance();
    });
    
    // Function to get the starting balance from the selected account
    function getStartingBalance() {
        // Default starting balance is 0
        let startingBalance = 0;
        
        // Get the account select element
        const accountSelect = document.getElementById('account_id');
        if (accountSelect && accountSelect.selectedIndex > 0) {
            // Get the selected option
            const selectedOption = accountSelect.options[accountSelect.selectedIndex];
            
            // Attempt to parse the balance from the option text
            const balanceMatch = selectedOption.text.match(/Balance: \$([\d,]+\.\d{2})/);
            if (balanceMatch && balanceMatch[1]) {
                // Parse the balance, removing commas
                startingBalance = parseFloat(balanceMatch[1].replace(/,/g, ''));
                if (isNaN(startingBalance)) {
                    startingBalance = 0;
                }
            }
        }
        
        return startingBalance;
    }
    
    // Function to calculate and update running balance
    function calculateRunningBalance() {
        // Get starting balance from selected account
        const startingBalance = getStartingBalance();
        
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
    
    // Expose the calculate function to the global scope so other scripts can trigger it
    window.recalculateRunningBalance = calculateRunningBalance;
});

// Add scrolling indicator for tables
document.addEventListener('DOMContentLoaded', function() {
    const tableContainers = document.querySelectorAll('.table-responsive');
    
    tableContainers.forEach(container => {
        // Check if scrolling is needed
        function checkScroll() {
            if (container.scrollWidth > container.clientWidth) {
                container.classList.add('scrolling');
            } else {
                container.classList.remove('scrolling');
            }
        }
        
        // Run on load
        checkScroll();
        
        // Run on resize
        window.addEventListener('resize', checkScroll);
        
        // Add scroll event listener
        container.addEventListener('scroll', function() {
            if (container.scrollLeft + container.clientWidth >= container.scrollWidth - 15) {
                container.classList.remove('scrolling');
            } else {
                container.classList.add('scrolling');
            }
        });
    });
});