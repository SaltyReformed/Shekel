import os

from main import create_app

app = create_app()

# Enable verbose error messages
app.config["DEBUG"] = True
app.config["EXPLAIN_TEMPLATE_LOADING"] = True

if __name__ == "__main__":
    # Print some debug info
    print(f"Running app with:")
    print(f"  Template folder: {app.template_folder}")
    print(f"  Static folder: {app.static_folder}")

    # List all routes
    print("\nAvailable routes:")
    for rule in app.url_map.iter_rules():
        print(f"  {rule}")

    app.run(debug=True, host="0.0.0.0")
