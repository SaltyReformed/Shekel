from main import create_app
from models import db, AccountType

app = create_app()
with app.app_context():
    # Create tables if they don't exist
    db.create_all()

    # Default asset account types
    asset_types = [
        {"name": "Checking", "is_debt": False},
        {"name": "Savings", "is_debt": False},
        {"name": "Cash", "is_debt": False},
        {"name": "Money Market", "is_debt": False},
        {"name": "Certificate of Deposit", "is_debt": False},
        {"name": "Investment", "is_debt": False},
        {"name": "Retirement", "is_debt": False},
        {"name": "FSA", "is_debt": False},
        {"name": "Other Asset", "is_debt": False},
    ]

    # Default debt account types
    debt_types = [
        {"name": "Credit Card", "is_debt": True},
        {"name": "Mortgage", "is_debt": True},
        {"name": "Auto Loan", "is_debt": True},
        {"name": "Student Loan", "is_debt": True},
        {"name": "Personal Loan", "is_debt": True},
        {"name": "Line of Credit", "is_debt": True},
        {"name": "Medical Debt", "is_debt": True},
        {"name": "Other Debt", "is_debt": True},
    ]

    # Combine all account types
    all_types = asset_types + debt_types

    # Add each account type if it doesn't already exist
    for type_data in all_types:
        existing_type = AccountType.query.filter_by(type_name=type_data["name"]).first()
        if not existing_type:
            account_type = AccountType(
                type_name=type_data["name"], is_debt=type_data["is_debt"]
            )
            db.session.add(account_type)
            print(f"Added account type: {type_data['name']}")

    db.session.commit()
    print("Account types setup complete.")
