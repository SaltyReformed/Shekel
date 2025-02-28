from app import create_app
from models import db, User, Role
from werkzeug.security import generate_password_hash

app = create_app()
with app.app_context():
    # Create tables if they don't exist
    db.create_all()

    # Create the admin role if it doesn't already exist
    admin_role = Role.query.filter_by(name="ADMIN").first()
    if not admin_role:
        admin_role = Role(name="ADMIN", description="Administrator role")
        db.session.add(admin_role)
        db.session.commit()

    # Create the admin user if not already present
    admin = User.query.filter_by(username="admin").first()
    if not admin:
        admin = User(
            username="josh",
            password_hash=generate_password_hash(
                "Cosmos9-Antiques1-Arson8-Rearview4-Matron0"
            ),
            email="grubbj@pm.me",
            role=admin_role,  # assign the admin role
        )
        db.session.add(admin)
        db.session.commit()

    print("Admin user created.")
