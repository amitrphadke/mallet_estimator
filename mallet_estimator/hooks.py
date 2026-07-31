app_name = "mallet_estimator"
app_title = "Mallet Estimator"
app_publisher = "Mallet Crafts"
app_description = "SKU execution estimator (material + labor + machinery + rent + design) for ERPNext"
app_email = "amitrameshphadke@gmail.com"
app_license = "MIT"

# Depends on ERPNext (Customer, Item, Quotation).
required_apps = ["erpnext"]

# Show a tile on the /apps launcher grid that opens the Mallet Estimator workspace.
add_to_apps_screen = [
    {
        "name": "mallet_estimator",
        "logo": "/assets/mallet_estimator/images/logo.svg",
        "title": "Mallet Estimator",
        "route": "/app/mallet-estimator",
        "has_permission": "mallet_estimator.install.has_app_permission",
    }
]

# Seed default Estimate Settings (rates + machines) and the client print format.
after_install = "mallet_estimator.install.after_install"
after_migrate = "mallet_estimator.install.after_migrate"

# Client scripts loaded on the respective form.
doctype_js = {
    "Estimate Settings": "public/js/estimate_settings.js",
    "Estimate SKU": "public/js/estimate_sku.js",
    "Estimate": "public/js/estimate.js",
}
