// Initialize drag and drop functionality
document.addEventListener('DOMContentLoaded', function() {
    initDragAndDrop();
    
    // Also initialize the payment modal functionality
    initPaymentModal();
});

function initDragAndDrop() {
    // Get all expense rows
    const expenseRows = document.querySelectorAll('.expense-row');
    
    expenseRows.forEach(row => {
        // Make each row draggable
        row.setAttribute('draggable', true);
        
        // Add the expense ID as a data attribute for easy access
        const expenseId = row.getAttribute('data-expense-id');
        
        // Add drag event listeners
        row.addEventListener('dragstart', handleDragStart);
        row.addEventListener('dragend', handleDragEnd);
        
        // Add visual indication that rows are draggable
        row.classList.add('draggable');
        
        // Find all paycheck cells in this row
        const paycheckCells = row.querySelectorAll('.expense-paycheck-cell');
        
        // Add drop targets
        paycheckCells.forEach(cell => {
            cell.addEventListener('dragover', handleDragOver);
            cell.addEventListener('dragenter', handleDragEnter);
            cell.addEventListener('dragleave', handleDragLeave);
            cell.addEventListener('drop', handleDrop);
        });
    });
}

// Drag event handlers
function handleDragStart(e) {
    // Add a class to show the row is being dragged
    this.classList.add('dragging');
    
    // Store the expense ID and row reference
    e.dataTransfer.setData('text/plain', this.getAttribute('data-expense-id'));
    e.dataTransfer.effectAllowed = 'move';
    
    // Add a custom drag image if needed
    const dragImage = createDragImage(this);
    if (dragImage) {
        e.dataTransfer.setDragImage(dragImage, 20, 20);
    }
    
    // Highlight valid drop targets
    document.querySelectorAll('.expense-paycheck-cell').forEach(cell => {
        if (!cell.classList.contains('active')) {
            cell.classList.add('drop-target');
        }
    });
}

function handleDragEnd(e) {
    // Remove the dragging class
    this.classList.remove('dragging');
    
    // Remove drop target highlighting
    document.querySelectorAll('.expense-paycheck-cell').forEach(cell => {
        cell.classList.remove('drop-target');
        cell.classList.remove('drag-over');
    });
}

function handleDragOver(e) {
    // Prevent default to allow drop
    e.preventDefault();
    return false;
}

function handleDragEnter(e) {
    // Add hover effect
    if (!this.classList.contains('active')) {
        this.classList.add('drag-over');
    }
}

function handleDragLeave(e) {
    // Remove hover effect
    this.classList.remove('drag-over');
}

function handleDrop(e) {
    // Prevent default action
    e.preventDefault();
    
    // Get the expense ID from the dragged item
    const expenseId = e.dataTransfer.getData('text/plain');
    
    // Get the paycheck ID from the drop target
    const paycheckId = this.getAttribute('data-paycheck-id');
    
    // Only proceed if we're not dropping on an already active cell
    if (!this.classList.contains('active')) {
        // Call the API to assign the expense to this paycheck
        assignExpenseToPaycheck(expenseId, paycheckId, this);
    }
    
    // Remove hover effect
    this.classList.remove('drag-over');
    
    return false;
}

// Function to assign expense to paycheck via API
function assignExpenseToPaycheck(expenseId, paycheckId, dropTarget) {
    // Show loading state
    showLoadingState(dropTarget);
    
    // Make API request
    fetch('/expenses/assign-to-paycheck', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': document.querySelector('input[name="csrf_token"]').value
        },
        body: JSON.stringify({
            expense_id: expenseId,
            paycheck_id: paycheckId
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            // Update the UI to reflect the new assignment
            updateExpenseAssignment(expenseId, paycheckId);
            
            // Update the expense date display if returned
            if (data.expense_date) {
                updateExpenseDate(expenseId, data.expense_date);
            }
            
            // Show success message
            showNotification('Expense reassigned successfully', 'success');
        } else {
            // Show error message
            showNotification('Error: ' + data.message, 'error');
        }
    })
    .catch(error => {
        console.error('Error:', error);
        showNotification('An error occurred while reassigning the expense', 'error');
    })
    .finally(() => {
        // Remove loading state
        hideLoadingState(dropTarget);
    });
}

// Update UI to reflect new expense assignment
function updateExpenseAssignment(expenseId, paycheckId) {
    // Find the expense row
    const expenseRow = document.querySelector(`.expense-row[data-expense-id="${expenseId}"]`);
    
    // Remove 'active' class from all paycheck cells in this row
    expenseRow.querySelectorAll('.expense-paycheck-cell').forEach(cell => {
        cell.classList.remove('active');
        // Also remove any expense markers
        const marker = cell.querySelector('.expense-marker');
        if (marker) {
            cell.removeChild(marker);
        }
    });
    
    // Add 'active' class to the new paycheck cell
    const newCell = expenseRow.querySelector(`.expense-paycheck-cell[data-paycheck-id="${paycheckId}"]`);
    if (newCell) {
        newCell.classList.add('active');
        
        // Add marker to the new cell
        const marker = document.createElement('div');
        marker.innerHTML = `
        <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" 
            fill="none" stroke="currentColor" stroke-width="2" 
            stroke-linecap="round" stroke-linejoin="round" class="expense-marker">
            <circle cx="12" cy="12" r="10"></circle>
            <line x1="12" y1="8" x2="12" y2="16"></line>
            <line x1="8" y1="12" x2="16" y2="12"></line>
        </svg>`;
        newCell.appendChild(marker.firstElementChild);
        
        // Update totals and remaining amounts
        updatePaycheckTotals();
    }
}

// Update the displayed date for an expense
function updateExpenseDate(expenseId, dateString) {
    const expenseRow = document.querySelector(`.expense-row[data-expense-id="${expenseId}"]`);
    const dateElement = expenseRow.querySelector('.expense-date');
    
    if (dateElement) {
        // Format the date for display
        const date = new Date(dateString);
        const options = { month: 'short', day: 'numeric', year: 'numeric' };
        dateElement.textContent = date.toLocaleDateString('en-US', options);
    }
}

// Update paycheck totals and remaining amounts
function updatePaycheckTotals() {
    // Get all paychecks
    const paycheckColumns = document.querySelectorAll('.paycheck-column');
    
    paycheckColumns.forEach((column, index) => {
        const paycheckId = column.getAttribute('data-paycheck-id');
        
        // Get all active expense cells for this paycheck
        const expenseCells = document.querySelectorAll(`.expense-paycheck-cell[data-paycheck-id="${paycheckId}"].active`);
        
        // Sum up the expenses
        let totalExpenses = 0;
        expenseCells.forEach(cell => {
            const expenseRow = cell.closest('.expense-row');
            const amountText = expenseRow.querySelector('.expense-amount').textContent.replace('$', '').replace(',', '');
            totalExpenses += parseFloat(amountText);
        });
        
        // Get the paycheck amount
        const paycheckAmountText = column.querySelector('.paycheck-amount').textContent.replace('$', '').replace(',', '');
        const paycheckAmount = parseFloat(paycheckAmountText);
        
        // Update the total cell
        const totalCell = document.querySelector(`.paycheck-total[data-paycheck-id="${paycheckId}"]`);
        if (totalCell) {
            totalCell.textContent = '$' + totalExpenses.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        }
        
        // Update the remaining cell
        const remainingCell = document.querySelector(`.paycheck-remaining[data-paycheck-id="${paycheckId}"]`);
        if (remainingCell) {
            const remaining = paycheckAmount - totalExpenses;
            remainingCell.textContent = '$' + remaining.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
            
            // Update class for negative values
            if (remaining < 0) {
                remainingCell.classList.add('negative');
            } else {
                remainingCell.classList.remove('negative');
            }
        }
    });
}

// Loading state functions
function showLoadingState(element) {
    element.classList.add('loading');
}

function hideLoadingState(element) {
    element.classList.remove('loading');
}

// Create a custom drag image
function createDragImage(row) {
    // Get the expense description and amount
    const description = row.querySelector('.expense-description').textContent.trim();
    const amount = row.querySelector('.expense-amount').textContent.trim();
    
    // Create a simple element for the drag image
    const dragImage = document.createElement('div');
    dragImage.className = 'drag-image';
    dragImage.innerHTML = `<strong>${description}</strong> ${amount}`;
    dragImage.style.backgroundColor = 'var(--primary-light)';
    dragImage.style.color = 'var(--primary-color)';
    dragImage.style.padding = '8px 12px';
    dragImage.style.borderRadius = '4px';
    dragImage.style.boxShadow = '0 2px 8px rgba(0,0,0,0.1)';
    dragImage.style.position = 'absolute';
    dragImage.style.top = '-1000px';  // Position off-screen initially
    
    // Add to the document temporarily
    document.body.appendChild(dragImage);
    
    return dragImage;
}

// Notification functions
function showNotification(message, type = 'info') {
    // Create notification element if it doesn't exist
    let notification = document.getElementById('notification');
    if (!notification) {
        notification = document.createElement('div');
        notification.id = 'notification';
        notification.className = 'notification';
        document.body.appendChild(notification);
    }
    
    // Set content and class based on type
    notification.textContent = message;
    notification.className = `notification ${type}`;
    
    // Show the notification
    notification.classList.add('show');
    
    // Hide after a delay
    setTimeout(() => {
        notification.classList.remove('show');
    }, 3000);
}

// Payment modal functionality
function initPaymentModal() {
    const markPaidButtons = document.querySelectorAll('.mark-paid-btn');
    markPaidButtons.forEach(button => {
        button.addEventListener('click', function() {
            const expenseId = this.getAttribute('data-expense-id');
            const expenseAmount = this.getAttribute('data-expense-amount');
            
            // Set up the payment form
            const paymentForm = document.getElementById('paymentForm');
            paymentForm.action = "/expenses/" + expenseId + "/pay";
            
            // Set the amount
            document.getElementById('modal-payment-amount').value = expenseAmount;
            
            // Show the modal
            document.getElementById('paymentModal').classList.add('show');
            document.body.style.overflow = 'hidden';
        });
    });
}

function closePaymentModal() {
    document.getElementById('paymentModal').classList.remove('show');
    document.body.style.overflow = '';
}

function submitPaymentForm() {
    document.getElementById('paymentForm').submit();
}